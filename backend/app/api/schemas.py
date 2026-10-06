"""Request/response schemas (the API contract shared with the frontend)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.importer.diff import ProposedChange
from app.importer.parse import SYMBOL_PATTERN, ParsedRow, norm_symbol
from app.portfolio.xray_rules import XrayRuleResult
from app.scoring.risk import PresetName, StopType
from app.signals.base import Explanation

Horizon = Literal["1w", "1m", "3m", "6m", "1y"]
MarketKey = Literal["US", "TASE", "CRYPTO"]
AssetType = Literal["stock", "etf", "crypto", "fund", "bond", "cash"]
Locale = Literal["he", "en"]

BIG = 1e12  # sanity ceiling for quantities, prices and costs

# A ticker as Yahoo spells it (AAPL, TEVA.TA, BRK-B, ^GSPC, ILS=X); trimmed and upper-cased first.
Symbol = Annotated[str, BeforeValidator(norm_symbol), Field(pattern=SYMBOL_PATTERN)]
Pct = Annotated[float, Field(gt=0, le=100, allow_inf_nan=False, strict=True)]
Positive = Annotated[float, Field(gt=0, le=BIG, allow_inf_nan=False, strict=True)]
NonNegative = Annotated[float, Field(ge=0, le=BIG, allow_inf_nan=False, strict=True)]
ExpectedReturnPct = Annotated[float, Field(ge=-100, le=500, allow_inf_nan=False, strict=True)]
ExpectedHorizonMonths = Annotated[int, Field(ge=1, le=120, strict=True)]


class Body(BaseModel):
    """Base of every request body: unknown fields are rejected, never silently stored."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- auth
class SignupIn(Body):
    invite_code: str = Field(min_length=1, max_length=128)
    email: EmailStr
    password: str = Field(max_length=1024)
    accept_disclaimer: bool
    locale: Locale = "he"

    @field_validator("accept_disclaimer")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("You must accept the disclaimer to sign up")
        return v


class LoginIn(Body):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    # Cloudflare Turnstile response, needed only after the API answered 403 `turnstile_required`.
    turnstile_token: str | None = Field(default=None, max_length=2048)


class PasswordBody(Body):
    password: str = Field(max_length=1024)


class SessionOut(BaseModel):
    id: int
    created_at: datetime
    last_seen_at: datetime
    current: bool


class MeOut(BaseModel):
    id: int
    email: str
    locale: Locale
    disclaimer_accepted: bool
    ocr_consent: bool
    terms_accepted: bool
    csrf_token: str


# ---------------------------------------------------------------- portfolios
class RiskFilterIn(Body):
    """A risk filter as the client may send it: every field optional and bounded.

    Percentages are in (0, 100]; `min_rr` is a ratio in (0, 100]. Presets fill the missing fields.
    """

    preset: PresetName | None = None
    max_position_pct: Pct | None = None
    max_sector_pct: Pct | None = None
    max_country_pct: Pct | None = None
    max_loss_per_position_pct: Pct | None = None
    max_portfolio_risk_per_trade_pct: Pct | None = None
    max_total_portfolio_risk_pct: Pct | None = None
    min_rr: Pct | None = None
    stop_type: StopType | None = None
    drawdown_defensive_pct: Pct | None = None


class RiskOverride(RiskFilterIn):
    """A per-holding override: it wins over the portfolio's filter (CLAUDE.md, product decisions)."""


class PortfolioCreate(Body):
    name: str = Field(min_length=1, max_length=100)
    base_currency: Literal["ILS", "USD"] = "ILS"


class PortfolioPatch(Body):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_currency: Literal["ILS", "USD"] | None = None
    risk_filter: RiskFilterIn | None = None
    # Both together, or both null to clear. Never defaulted by the server.
    expected_return_pct: ExpectedReturnPct | None = None
    expected_return_horizon_months: ExpectedHorizonMonths | None = None

    @model_validator(mode="after")
    def _expectation_is_a_pair(self) -> PortfolioPatch:
        fields = self.model_fields_set & {"expected_return_pct", "expected_return_horizon_months"}
        if fields and (
            len(fields) != 2
            or (self.expected_return_pct is None) != (self.expected_return_horizon_months is None)
        ):
            raise ValueError(
                "send expected_return_pct and expected_return_horizon_months together "
                "(both numbers, or both null to clear)"
            )
        return self


class RiskFilterOut(BaseModel):
    preset: str | None = None
    max_position_pct: float
    max_sector_pct: float
    max_country_pct: float
    max_loss_per_position_pct: float
    max_portfolio_risk_per_trade_pct: float
    max_total_portfolio_risk_pct: float
    min_rr: float
    stop_type: StopType
    drawdown_defensive_pct: float


class PortfolioOut(BaseModel):
    id: int
    name: str
    base_currency: Literal["ILS", "USD"]
    risk_filter: RiskFilterOut
    tracking_started_at: str | None = None
    expected_return_pct: float | None = None
    expected_return_horizon_months: int | None = None
    # Last confirmed screenshot import (null: never), and whether it is older than the nudge
    # setting (`screenshot_update_nudge_days`, default 7).
    last_screenshot_update_at: datetime | None = None
    screenshot_update_stale: bool = False
    # When the user saved a risk level themselves; null while the server default is in place.
    risk_chosen_at: datetime | None = None
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
    week_start: date  # the first day of the week (Sunday by default)
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
    # The (oldest) last screenshot update among the portfolios in this view, and whether any of
    # them is past the nudge age.
    last_screenshot_update_at: datetime | None = None
    screenshot_update_stale: bool = False
    week_start: date  # Sunday of the current week in Asia/Jerusalem (setting `week_start_day`)
    fx_stale: bool  # USD/ILS is old or a fallback: the ILS/USD figures are approximate
    fx_source: str | None = None  # yfinance | frankfurter | boi
    fx_basis: Literal["live", "last_close"] = "live"  # last_close: a daily reference rate
    fx_as_of: datetime | None = None
    price_sources: list[str] = []  # providers behind the holdings' prices (for the footer credit)
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


class FundHoldingOut(BaseModel):
    """What the app knows about a fund holding (symbol `GEMEL-<fund id>`). The value is the user's
    own entry (`manual_value`): GemelNet has monthly returns, not a unit price."""

    fund_id: str
    track: str | None = None
    value_basis: Literal["manual_value", "cost_only", "no_value"]
    manual_value_ils: float | None = None
    manual_value_as_of: date | None = None
    flags: list[str]  # data gaps in plain codes (monthly_data_only, no_value, ...)
    credit: str


class HoldingOut(BaseModel):
    id: int
    symbol: str
    name_en: str
    name_he: str
    asset_type: AssetType
    market: MarketKey
    quantity: float
    price: float
    currency: str
    day_change_pct: float
    value_ils: float
    pnl: Pnl | None = None
    weight_pct: float
    horizon: Horizon | None = None
    stop_tp_status: Literal["missing", "needs_horizon", "no_levels"]  # no_levels: a fund
    score_card: ScoreCardMini
    price_stale: bool = False
    # Where the price came from and how current it is (the UI shows "Delayed" / "Last close, <date>").
    price_source: str | None = None  # yfinance | finnhub | coingecko | ... ; null: cost/screenshot
    price_basis: Literal["live", "last_close"] = "live"
    price_as_of: datetime | None = None
    price_is_fresh: bool = False  # true only for a fresh live quote (the exit-level gate)
    price_flag: str | None = None  # e.g. price_disagreement
    fund: FundHoldingOut | None = None  # set for an Israeli fund holding only


FundText = Annotated[str, Field(min_length=1, max_length=120)]


class HoldingCreate(Body):
    symbol: Symbol
    quantity: Positive
    avg_cost: NonNegative | None = None
    cost_currency: Literal["ILS", "USD"] | None = None
    horizon: Horizon | None = None
    # Fund holdings only (symbol `GEMEL-<fund id>`); a 422 on any other symbol.
    manual_value_ils: Positive | None = None  # the whole position in ILS, from the statement
    manual_value_as_of: date | None = None  # default: today
    fund_name: FundText | None = None
    track: FundText | None = None


class HoldingPatch(Body):
    quantity: Positive | None = None
    avg_cost: NonNegative | None = None
    cost_currency: Literal["ILS", "USD"] | None = None
    horizon: Horizon | None = None
    risk_override: RiskOverride | None = None
    # Fund holdings only: a 422 on any other holding. Explicit null clears a manual value.
    manual_value_ils: Positive | None = None
    manual_value_as_of: date | None = None
    fund_name: FundText | None = None
    track: FundText | None = None


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
    # Toggleable informational rules (breach / ok / off) with an Explanation each.
    rules: list[XrayRuleResult]


class HeatmapItem(BaseModel):
    symbol: str
    sector: str
    weight_pct: float
    day_change_pct: float


# ---------------------------------------------------------------- imports
ImportFlag = Literal[
    "missing_fields",  # quantity, price or value could not be read
    "value_mismatch",  # quantity x price differs from the value
    "unmatched",  # no security chosen (symbol is null)
    "low_confidence_match",  # weak name match: pick one of `candidates` (symbol stays null)
    "currency_changed",  # the row currency differs from the security's: confirm before import
    "unit_mismatch",  # agorot shown for a non-shekel security
    "quantity_uncertain",  # no quantity (value too small to infer it): the user must enter one
    "quantity_fractional",  # value / price is not a whole number: worth a look
    "cost_inferred",  # `cost` was derived from the broker's P&L %, not read from the screen
    "duplicate_removed",  # the same card was in two screenshots and is counted once
    "conflict",  # two screenshots disagree; the later one was kept, see `conflict`
    "price_unit_100x",  # quantity x price is ~100x (or 1/100) of the value: agorot vs shekels?
    "total_mismatch",  # the rows do not add up to the total shown on the screen
    "ocr_low_confidence",  # the reader marked a field of this row as unclear
]


class ImportRowModel(ParsedRow):
    model_config = ConfigDict(extra="forbid")
    flags: list[ImportFlag] = Field(default_factory=list, max_length=12)  # type: ignore[assignment]


ImportScope = Literal["partial", "full"]


class ImportDraftOut(BaseModel):
    id: int
    portfolio_id: int
    status: Literal["draft", "confirmed", "discarded"]
    # partial (default): holdings missing from the screenshots stay untouched and produce no
    # proposed changes. full: they are proposed as `row_index = -1` changes whose `type` is the
    # user's choice (`sell`, `withdrawal` or `keep`, default `keep`); nothing is applied for them
    # unless the user picks sold or withdrawn.
    scope: ImportScope = "partial"
    rows: list[ImportRowModel]
    proposed_changes: list[ProposedChange]
    expires_at: datetime  # an unconfirmed draft is deleted then (24 h after creation)


class ImportRowsBody(Body):
    """On-device OCR: the browser parsed the screenshot, only the stock rows are sent."""

    rows: list[ImportRowModel] = Field(max_length=200)
    scope: ImportScope = "partial"


class ImportPatch(Body):
    rows: list[ImportRowModel] | None = Field(default=None, max_length=200)
    proposed_changes: list[ProposedChange] | None = Field(default=None, max_length=400)
    scope: ImportScope | None = None  # switching it recomputes the proposed changes


class LaunchGateOut(BaseModel):
    open: bool
    reasons: list[str]


class SecurityHit(BaseModel):
    symbol: str
    name_en: str
    name_he: str
    market: MarketKey


# ---------------------------------------------------------------- scorecard
class SignalBreakdownOut(BaseModel):
    name: str
    score: float
    confidence: float
    weight: float
    nominal_weight: float | None = None
    reasons: list[str]
    data_as_of: str
    explanation: Explanation


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
    explanation: Explanation
    disclaimer: str


# ---------------------------------------------------------------- alerts / notifications
class AlertCreate(Body):
    symbol: Symbol
    op: Literal["above", "below"]
    price: Positive


class AlertOut(BaseModel):
    id: int
    symbol: str
    op: Literal["above", "below"]
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
