"""Request/response schemas (the API contract shared with the frontend)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.importer.diff import ProposedChange
from app.importer.parse import ParsedRow

Horizon = Literal["1w", "1m", "3m", "6m", "1y"]
MarketKey = Literal["US", "TASE", "CRYPTO"]


# ---------------------------------------------------------------- auth
class SignupIn(BaseModel):
    invite_code: str = Field(min_length=1)
    email: EmailStr
    password: str
    accept_disclaimer: bool
    locale: Literal["he", "en"] = "he"

    @field_validator("accept_disclaimer")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("You must accept the disclaimer to sign up")
        return v


class LoginIn(BaseModel):
    email: str
    password: str


class MeOut(BaseModel):
    id: int
    email: str
    locale: str
    disclaimer_accepted: bool
    ocr_consent: bool
    csrf_token: str


# ---------------------------------------------------------------- portfolios
class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_currency: Literal["ILS", "USD"] = "ILS"


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_currency: Literal["ILS", "USD"] | None = None
    risk_filter: dict[str, Any] | None = None


class RiskFilterOut(BaseModel):
    preset: str | None = None
    max_position_pct: float
    max_sector_pct: float
    max_country_pct: float
    max_loss_per_position_pct: float
    max_portfolio_risk_per_trade_pct: float
    max_total_portfolio_risk_pct: float
    min_rr: float
    stop_type: Literal["fixed", "trailing", "both"]
    drawdown_defensive_pct: float


class PortfolioOut(BaseModel):
    id: int
    name: str
    base_currency: str
    risk_filter: RiskFilterOut
    tracking_started_at: str | None = None
    created_at: datetime


class RiskPresetOut(RiskFilterOut):
    name: str


# ---------------------------------------------------------------- summary
class Money(BaseModel):
    ils: float
    usd: float


class Pnl(BaseModel):
    ils: float
    usd: float
    pct: float


class WeeklyBar(BaseModel):
    week_start: str
    pnl_ils: float
    pct: float


class MonthlyBar(BaseModel):
    month: str
    pnl_ils: float
    pct: float


class SeriesPoint(BaseModel):
    date: str
    pct: float
    sp500_pct: float | None = None
    ta125_pct: float | None = None


class MarketOpen(BaseModel):
    open: bool


class Markets(BaseModel):
    US: MarketOpen
    TASE: MarketOpen
    CRYPTO: MarketOpen


class SummaryOut(BaseModel):
    value: Money
    day_pnl: Pnl
    week_pnl: Pnl
    month_pnl: Pnl
    since_start_pnl: Pnl
    since_start_date: str | None = None
    weekly_bars: list[WeeklyBar]
    monthly_bars: list[MonthlyBar]
    since_start_series: list[SeriesPoint]
    as_of: datetime
    markets: Markets


# ---------------------------------------------------------------- holdings
class ScoreCardMini(BaseModel):
    total: float
    technical: float
    patterns: float
    confidence: float


class HoldingOut(BaseModel):
    id: int
    symbol: str
    name_en: str
    name_he: str
    asset_type: str
    market: str
    quantity: float
    price: float
    currency: str
    day_change_pct: float
    value_ils: float
    pnl: Pnl | None = None
    weight_pct: float
    horizon: Horizon | None = None
    stop_tp_status: Literal["missing", "needs_horizon"]
    score_card: ScoreCardMini
    price_stale: bool = False


class HoldingCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: float = Field(gt=0)
    avg_cost: float | None = Field(default=None, ge=0)
    cost_currency: Literal["ILS", "USD"] | None = None
    horizon: Horizon | None = None


class HoldingPatch(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    avg_cost: float | None = Field(default=None, ge=0)
    cost_currency: Literal["ILS", "USD"] | None = None
    horizon: Horizon | None = None
    risk_override: dict[str, Any] | None = None


class ExposureItem(BaseModel):
    name: str
    weight_pct: float


class Breach(BaseModel):
    rule: str
    value: float
    limit: float
    why: str
    symbol: str | None = None


class XrayOut(BaseModel):
    concentration: list[dict[str, Any]]
    currency_exposure: list[ExposureItem]
    country_exposure: list[ExposureItem]
    sector_exposure: list[ExposureItem]
    home_bias: dict[str, Any]
    breaches: list[Breach]


class HeatmapItem(BaseModel):
    symbol: str
    sector: str
    weight_pct: float
    day_change_pct: float


# ---------------------------------------------------------------- imports
class ImportRowModel(ParsedRow):
    model_config = ConfigDict(extra="ignore")


class ImportDraftOut(BaseModel):
    id: int
    portfolio_id: int
    status: Literal["draft", "confirmed", "discarded"]
    rows: list[ImportRowModel]
    proposed_changes: list[ProposedChange]


class ImportPatch(BaseModel):
    rows: list[ImportRowModel] | None = None
    proposed_changes: list[ProposedChange] | None = None


class SecurityHit(BaseModel):
    symbol: str
    name_en: str
    name_he: str
    market: str


# ---------------------------------------------------------------- scorecard
class SignalBreakdownOut(BaseModel):
    name: str
    score: float
    confidence: float
    weight: float
    nominal_weight: float | None = None
    reasons: list[str]
    data_as_of: str
    explanation: dict[str, Any]


class ScoreCardDetail(BaseModel):
    holding_id: int
    portfolio_id: int
    symbol: str
    name_en: str
    name_he: str
    horizon: Horizon | None = None
    total: float
    confidence: float
    available: bool
    validated: bool
    signals: list[SignalBreakdownOut]
    explanation: dict[str, Any]
    disclaimer: str


# ---------------------------------------------------------------- alerts / notifications
class AlertCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    op: Literal["above", "below"]
    price: float = Field(gt=0)


class AlertOut(BaseModel):
    id: int
    symbol: str
    op: str
    price: float
    active: bool
    triggered_at: datetime | None = None


class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    created_at: datetime
    read: bool
