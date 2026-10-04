"""Time helpers. The DB stores naive UTC datetimes; the API emits timezone-aware ones."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings


def utcnow() -> datetime:
    """Naive UTC now (what the database stores)."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    """Mark a naive-UTC datetime as UTC so it serialises with a Z/offset."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def local_today(now: datetime | None = None) -> date:
    """Today's date in the scheduler timezone (Asia/Jerusalem)."""
    tz = ZoneInfo(get_settings().scheduler_timezone)
    base = as_utc(now) if now else datetime.now(UTC)
    return base.astimezone(tz).date()
