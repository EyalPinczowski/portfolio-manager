"""Market-hours calendars. Never hard-codes UTC offsets: zoneinfo handles DST per exchange.

Holidays and early closes come from `exchange_calendars` (XTAE for TASE, XNYS for the US). The
config wins where the library is wrong or silent:

- TASE regular hours are the config override (Mon-Thu 09:59-17:25, Fri 09:59-13:50,
  Asia/Jerusalem), because the library models a slightly different close (17:15).
- `tase_holidays` / `us_holidays` force a day closed; `tase_extra_open_days` / `us_extra_open_days`
  force a day open (for example when the library is stale about a one-off closure).
- A date outside the library's range is treated as a normal trading day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import Settings, get_settings

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
LIBRARY_CODES = {"TASE": "XTAE", "US": "XNYS"}


def _t(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now


@lru_cache
def _calendar(code: str) -> Any:
    import exchange_calendars as xc

    return xc.get_calendar(code)


def library_session(market: str, day: date) -> tuple[bool, time | None] | None:
    """(is_session, early_close_local_time or None) from exchange_calendars; None if unknown.

    `None` means the library is silent (date out of its range or not installed correctly).
    """
    code = LIBRARY_CODES.get(market)
    if code is None:
        return None
    try:
        cal = _calendar(code)
        ts = pd.Timestamp(day)
        if ts < cal.first_session or ts > cal.last_session:
            return None
        if not cal.is_session(ts):
            return (False, None)
        early: time | None = None
        if ts in cal.early_closes:
            close = cal.session_close(ts).tz_convert(str(cal.tz))
            early = close.time().replace(second=0, microsecond=0)
        return (True, early)
    except Exception:
        return None


def _hours_for(market: str, day: date, s: Settings) -> tuple[time, time] | None:
    """Regular (open, close) for the local `day`, or None when the market is closed all day."""
    if market == "TASE":
        forced_closed, forced_open = s.tase_holidays, s.tase_extra_open_days
        hours = s.tase_hours.get(WEEKDAYS[day.weekday()])
        if hours is None:
            return None
        opens, closes = _t(hours[0]), _t(hours[1])
    else:
        forced_closed, forced_open = s.us_holidays, s.us_extra_open_days
        if day.weekday() >= 5:
            return None
        opens, closes = _t(s.us_open), _t(s.us_close)
    if day in forced_closed:
        return None
    if day in forced_open:
        return opens, closes
    lib = library_session(market, day)
    if lib is None:
        return opens, closes
    is_session, early = lib
    if not is_session:
        return None
    if early is not None and early < closes:
        closes = early  # half-days / the eve of a holiday
    return opens, closes


def _tz(market: str, s: Settings) -> ZoneInfo:
    return ZoneInfo(s.tase_timezone if market == "TASE" else s.us_timezone)


def session_close(market: str, day: date, settings: Settings | None = None) -> datetime | None:
    """Aware close time of the local `day`'s session, None on a day the market is closed."""
    s = settings or get_settings()
    hours = _hours_for(market, day, s)
    if hours is None:
        return None
    return datetime.combine(day, hours[1], tzinfo=_tz(market, s))


def session_close_utc_naive(
    market: str, day: date, settings: Settings | None = None
) -> datetime | None:
    """`session_close` converted to naive UTC (the storage convention); None on a closed day."""
    close = session_close(market, day, settings)
    return None if close is None else close.astimezone(UTC).replace(tzinfo=None)


def current_session_date(
    market: str, now: datetime | None = None, settings: Settings | None = None
) -> date | None:
    """Date of the session whose prices are "current": today's while it is open, otherwise the
    most recent session that has closed. None for 24/7 markets or when none was found."""
    if market not in LIBRARY_CODES:
        return None
    s = settings or get_settings()
    if _is_open(market, now, s):
        return _aware(now).astimezone(_tz(market, s)).date()
    close = last_session_close(market, now, s)
    return None if close is None else close.astimezone(_tz(market, s)).date()


def last_session_close(
    market: str, now: datetime | None = None, settings: Settings | None = None
) -> datetime | None:
    """Aware time of the most recent session close at or before `now` (None for 24/7 markets or
    when no session was found in the last 10 days)."""
    if market not in LIBRARY_CODES:
        return None
    s = settings or get_settings()
    at = _aware(now)
    day = at.astimezone(_tz(market, s)).date()
    for back in range(11):
        close = session_close(market, day - timedelta(days=back), s)
        if close is not None and close <= at:
            return close
    return None


def _is_open(market: str, now: datetime | None, s: Settings) -> bool:
    local = _aware(now).astimezone(_tz(market, s))
    hours = _hours_for(market, local.date(), s)
    return hours is not None and hours[0] <= local.time() < hours[1]


def is_tase_open(now: datetime | None = None, settings: Settings | None = None) -> bool:
    return _is_open("TASE", now, settings or get_settings())


def is_us_open(now: datetime | None = None, settings: Settings | None = None) -> bool:
    return _is_open("US", now, settings or get_settings())


def is_market_open(
    market: str, now: datetime | None = None, settings: Settings | None = None
) -> bool:
    if market == "TASE":
        return is_tase_open(now, settings)
    if market == "US":
        return is_us_open(now, settings)
    return True  # CRYPTO trades 24/7


def is_post_close_fetch_due(
    market: str, now: datetime | None = None, settings: Settings | None = None
) -> bool:
    """True in the one quote-cycle window that starts `post_close_fetch_delay_minutes` after a close.

    Quotes stop at the close, so the stored "close" could be minutes stale. The window is one
    quote interval long, so exactly one extra fetch happens per session.
    """
    s = settings or get_settings()
    if market not in LIBRARY_CODES:
        return False
    at = _aware(now)
    local_date = at.astimezone(_tz(market, s)).date()
    close = session_close(market, local_date, s)
    if close is None:
        return False
    start = close + timedelta(minutes=s.post_close_fetch_delay_minutes)
    return start <= at < start + timedelta(minutes=s.quotes_interval_minutes)


def markets_status(
    now: datetime | None = None, settings: Settings | None = None
) -> dict[str, dict[str, bool]]:
    return {m: {"open": is_market_open(m, now, settings)} for m in ("US", "TASE", "CRYPTO")}
