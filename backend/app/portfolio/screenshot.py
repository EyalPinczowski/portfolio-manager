"""When was a portfolio last updated from a screenshot, and is it time to nudge the user?"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from app.config import Settings, get_settings
from app.models import Portfolio
from app.timeutil import as_utc, utcnow


def update_is_stale(
    portfolio: Portfolio, settings: Settings | None = None, now: datetime | None = None
) -> bool:
    """True when the last screenshot update is older than `screenshot_update_nudge_days`.

    A portfolio that was never updated from a screenshot is not stale: there is nothing to be
    older than (a user who enters holdings by hand is not nagged).
    """
    last = portfolio.last_screenshot_update_at
    if last is None:
        return False
    s = settings or get_settings()
    return as_utc(now or utcnow()) - as_utc(last) > timedelta(days=s.screenshot_update_nudge_days)


def oldest_update(portfolios: Iterable[Portfolio]) -> datetime | None:
    """The oldest last-update among portfolios that have one (the combined view's date)."""
    stamps = [p.last_screenshot_update_at for p in portfolios if p.last_screenshot_update_at]
    return min(stamps) if stamps else None
