"""Per-user settings: `GET /api/settings`, `PATCH /api/settings` (docs/settings-spec.md).

Strict bodies (`extra="forbid"`), bounded values. No setting silently fills in something the user
must choose: the Buy-alerts filter has no defaults and reads `not_set` until it is saved. The
language lives on `User.locale` (the same field `/auth/me` returns).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, BeforeValidator, Field, StrictBool, model_validator

from app.api.schemas import AssetType, Body, Horizon, Locale, MarketKey
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.errors import ApiError
from app.models import UserSettings
from app.scoring.risk import PresetName
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, utcnow
from app.usersettings import default_row, row_or_default

router = APIRouter(tags=["settings"], route_class=StrictJsonRoute)

Theme = Literal["system", "light", "dark"]
MainCurrency = Literal["ILS", "USD"]
NumberFormat = Literal["full", "compact"]
WeekStart = Literal["sunday", "monday"]
Weekday = Literal["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


def _unique(v: object) -> object:
    if isinstance(v, list) and len(set(map(str, v))) != len(v):
        raise ValueError("values must be unique")
    return v


class QuietHours(Body):
    """No pushes between `start` and `end` (Asia/Jerusalem). The window may wrap midnight."""

    start: HHMM
    end: HHMM

    @model_validator(mode="after")
    def _not_empty_or_all_day(self) -> QuietHours:
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


class IdeaAlertsFilter(Body):
    """The filter a candidate must match before a push. Every field is required: no defaults."""

    min_confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    horizon: Horizon
    risk_preset: PresetName
    markets: Annotated[list[MarketKey], BeforeValidator(_unique)] = Field(
        min_length=1, max_length=3
    )
    asset_types: Annotated[list[AssetType], BeforeValidator(_unique)] = Field(
        min_length=1, max_length=6
    )
    max_per_day: int = Field(ge=1, le=20, strict=True)
    quiet_hours: QuietHours | None  # required key; null = no quiet hours for these alerts


class WeeklyReviewPatch(Body):
    enabled: StrictBool | None = None
    day: Weekday | None = None
    time: HHMM | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> WeeklyReviewPatch:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class SettingsPatch(Body):
    language: Locale | None = None
    theme: Theme | None = None
    main_currency: MainCurrency | None = None
    number_format: NumberFormat | None = None
    week_start_day: WeekStart | None = None
    price_alerts_enabled: StrictBool | None = None
    weekly_review: WeeklyReviewPatch | None = None
    quiet_hours: QuietHours | None = None  # explicit null clears
    idea_alerts: IdeaAlertsFilter | None = None  # explicit null clears (back to "not set")

    @model_validator(mode="after")
    def _only_clearable_fields_may_be_null(self) -> SettingsPatch:
        if not self.model_fields_set:
            raise ValueError("nothing to change")
        for name in self.model_fields_set - {"quiet_hours", "idea_alerts"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class WeeklyReviewOut(BaseModel):
    enabled: bool
    day: Weekday
    time: str
    timezone: str


class IdeaAlertsOut(BaseModel):
    state: Literal["not_set", "set"]
    filter: IdeaAlertsFilter | None = None


class SettingsOut(BaseModel):
    language: Locale
    theme: Theme
    main_currency: MainCurrency
    number_format: NumberFormat
    week_start_day: WeekStart
    price_alerts_enabled: bool
    weekly_review: WeeklyReviewOut
    quiet_hours: QuietHours | None
    idea_alerts: IdeaAlertsOut
    telegram_linked: bool
    updated_at: datetime | None


def _out(user_locale: str, row: UserSettings, saved: bool, linked: bool, tz: str) -> SettingsOut:
    return SettingsOut(
        language="en" if user_locale == "en" else "he",
        theme=row.theme,  # type: ignore[arg-type]
        main_currency=row.main_currency,  # type: ignore[arg-type]
        number_format=row.number_format,  # type: ignore[arg-type]
        week_start_day=row.week_start_day,  # type: ignore[arg-type]
        price_alerts_enabled=row.price_alerts_enabled,
        weekly_review=WeeklyReviewOut(
            enabled=row.weekly_review_enabled,
            day=row.weekly_review_day,  # type: ignore[arg-type]
            time=row.weekly_review_time,
            timezone=tz,
        ),
        quiet_hours=QuietHours.model_validate(row.quiet_hours) if row.quiet_hours else None,
        idea_alerts=IdeaAlertsOut(
            state="set" if row.idea_alerts else "not_set",
            filter=IdeaAlertsFilter.model_validate(row.idea_alerts) if row.idea_alerts else None,
        ),
        telegram_linked=linked,
        updated_at=as_utc(row.updated_at) if saved else None,
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings_route(user: UserDep, db: DbDep, settings: SettingsDep) -> SettingsOut:
    assert user.id is not None
    saved = db.get(UserSettings, user.id)
    row = saved or default_row(user.id, settings)
    return _out(
        user.locale, row, saved is not None, user.telegram_chat_id is not None,
        settings.scheduler_timezone,
    )  # fmt: skip


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(
    body: SettingsPatch, user: UserDep, db: DbDep, settings: SettingsDep
) -> SettingsOut:
    assert user.id is not None
    row = row_or_default(db, user, settings)
    sent = body.model_fields_set
    if "language" in sent and body.language is not None:
        user.locale = body.language
        db.add(user)
    for name in ("theme", "main_currency", "number_format", "week_start_day"):
        if name in sent:
            setattr(row, name, getattr(body, name))
    if "price_alerts_enabled" in sent and body.price_alerts_enabled is not None:
        row.price_alerts_enabled = body.price_alerts_enabled
    if body.weekly_review is not None:
        w = body.weekly_review
        if not w.model_fields_set:
            raise ApiError(422, "empty_weekly_review", "weekly_review has nothing to change.")
        if w.enabled is not None:
            row.weekly_review_enabled = w.enabled
        if w.day is not None:
            row.weekly_review_day = w.day
        if w.time is not None:
            row.weekly_review_time = w.time
    if "quiet_hours" in sent:
        row.quiet_hours = body.quiet_hours.model_dump() if body.quiet_hours else None
    if "idea_alerts" in sent:
        row.idea_alerts = body.idea_alerts.model_dump() if body.idea_alerts else None
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(
        user.locale, row, True, user.telegram_chat_id is not None, settings.scheduler_timezone
    )
