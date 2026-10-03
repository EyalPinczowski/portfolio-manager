"""Market-hours calendars. Never hard-codes UTC offsets: zoneinfo handles DST per exchange.

TASE uses the config override (Mon-Thu 09:59-17:25, Fri 09:59-13:50, Asia/Jerusalem).
US uses America/New_York 09:30-16:00 plus a configurable holiday list.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _t(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now


def is_tase_open(now: datetime | None = None, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    local = _aware(now).astimezone(ZoneInfo(s.tase_timezone))
    if local.date() in s.tase_holidays:
        return False
    hours = s.tase_hours.get(WEEKDAYS[local.weekday()])
    if hours is None:
        return False
    return _t(hours[0]) <= local.time() < _t(hours[1])


def is_us_open(now: datetime | None = None, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    local = _aware(now).astimezone(ZoneInfo(s.us_timezone))
    if local.weekday() >= 5 or local.date() in s.us_holidays:
        return False
    return _t(s.us_open) <= local.time() < _t(s.us_close)


def is_market_open(
    market: str, now: datetime | None = None, settings: Settings | None = None
) -> bool:
    if market == "TASE":
        return is_tase_open(now, settings)
    if market == "US":
        return is_us_open(now, settings)
    return True  # CRYPTO trades 24/7


def markets_status(
    now: datetime | None = None, settings: Settings | None = None
) -> dict[str, dict[str, bool]]:
    return {m: {"open": is_market_open(m, now, settings)} for m in ("US", "TASE", "CRYPTO")}
