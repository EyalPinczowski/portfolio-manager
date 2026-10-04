"""Per-user settings: defaults, the effective values and the quiet-hours rule.

A user without a `user_settings` row has the defaults from `Settings`; the row is created on the
first PATCH. Nothing here picks a value the user must choose: the Buy-alerts filter stays `None`
(not set) until the user saves it, and consumers treat `None` as "send nothing".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

from sqlmodel import Session

from app.config import Settings
from app.models import User, UserSettings


@dataclass(frozen=True)
class EffectiveSettings:
    theme: str
    main_currency: str
    number_format: str
    week_start_day: str
    price_alerts_enabled: bool
    weekly_review_enabled: bool
    weekly_review_day: str
    weekly_review_time: str
    quiet_hours: dict[str, Any] | None
    idea_alerts: dict[str, Any] | None
    last_weekly_review_week: Any  # date | None
    has_row: bool


def default_row(user_id: int, settings: Settings) -> UserSettings:
    return UserSettings(
        user_id=user_id,
        week_start_day=settings.week_start_day if settings.week_start_day == "monday" else "sunday",
        weekly_review_enabled=settings.weekly_review_default_enabled,
        weekly_review_day=settings.weekly_review_default_day,
        weekly_review_time=settings.weekly_review_default_time,
    )


def row_or_default(db: Session, user: User, settings: Settings) -> UserSettings:
    """The user's row, or an unsaved one holding the defaults (not added to the session)."""
    assert user.id is not None
    return db.get(UserSettings, user.id) or default_row(user.id, settings)


def effective(db: Session, user: User, settings: Settings) -> EffectiveSettings:
    assert user.id is not None
    saved = db.get(UserSettings, user.id)
    r = saved or default_row(user.id, settings)
    return EffectiveSettings(
        theme=r.theme,
        main_currency=r.main_currency,
        number_format=r.number_format,
        week_start_day=r.week_start_day,
        price_alerts_enabled=r.price_alerts_enabled,
        weekly_review_enabled=r.weekly_review_enabled,
        weekly_review_day=r.weekly_review_day,
        weekly_review_time=r.weekly_review_time,
        quiet_hours=r.quiet_hours,
        idea_alerts=r.idea_alerts,
        last_weekly_review_week=r.last_weekly_review_week,
        has_row=saved is not None,
    )


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def in_quiet_hours(quiet: dict[str, Any] | None, local_time: time) -> bool:
    """True when `local_time` is inside the window [start, end). A window may wrap midnight.

    No window (None) means never quiet. `start == end` is rejected on input, but is treated as
    "not quiet" here so a bad row can never silence everything.
    """
    if not quiet:
        return False
    start, end = parse_hhmm(quiet["start"]), parse_hhmm(quiet["end"])
    if start == end:
        return False
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end
