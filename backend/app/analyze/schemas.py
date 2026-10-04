"""Typed hand-offs of the analysis (docs/analysis-committee.md): Scout, Chartist, portfolio fit.

Every model is a plain Pydantic model so the same shapes can be stored in the cache, returned by
the API and handed to later roles. Nothing here is a buy/sell verdict: field names and enum values
avoid verdict words (`tests/verdict_contract.py` checks the OpenAPI schema).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import DISCLAIMER
from app.scoring.exit_levels import ExitLevelsResult
from app.scoring.screener import SizeOut
from app.signals.base import ChartAnnotation, Explanation

FINITE = ConfigDict(allow_inf_nan=False)

NOT_VALIDATED_NOTICE = (
    "Scores are not yet validated by a backtest and paper trading, so this page shows neutral "
    "analysis for you to research, not an instruction to trade. Not financial advice."
)


# ---------------------------------------------------------------- Scout
class PriceInfo(BaseModel):
    model_config = FINITE

    price: float
    currency: str
    as_of: datetime
    source: str
    basis: Literal["live", "last_close"]
    flag: str | None = None
    change_pct: float | None = None
    is_fresh: bool  # fresh enough for exit levels (the same test as the exit-levels engine)


class MissingInput(BaseModel):
    """A signal or data field the Scout has nothing for: reported, never counted as neutral."""

    name: str
    confidence: float = 0.0
    reason: str


class ScoutReport(BaseModel):
    model_config = FINITE

    symbol: str
    name_en: str
    name_he: str
    market: str
    asset_type: str
    sector: str
    country: str
    currency: str
    verified: bool  # a seeded security, or one the providers returned data for
    price: PriceInfo | None = None
    price_reason: str | None = None  # why there is no price
    bars: int  # daily bars behind the chart report
    history_as_of: datetime | None = None
    not_available: list[MissingInput] = Field(default_factory=list)
    explanation: Explanation


# ---------------------------------------------------------------- Chartist
class SignalLine(BaseModel):
    model_config = FINITE

    name: str
    available: bool
    score: float
    confidence: float
    weight: float  # effective weight in percent after redistribution
    nominal_weight: float
    reasons: list[str]
    data_as_of: datetime


class Level(BaseModel):
    model_config = FINITE

    kind: Literal["support", "resistance"]
    price: float
    distance_pct: float  # from the last close (negative: below it)
    touches: int


class ChartReport(BaseModel):
    model_config = FINITE

    available: bool
    score: float  # combined technical + patterns score in [-100, 100]; 0 with `available` false
    confidence: float
    breakdown: list[SignalLine]
    indicators: dict[str, float] = Field(default_factory=dict)
    levels: list[Level] = Field(default_factory=list)
    annotations: list[ChartAnnotation] = Field(default_factory=list)
    data_as_of: datetime | None = None
    bars: int
    explanation: Explanation


# ---------------------------------------------------------------- portfolio fit
NeedsInput = Literal["portfolio_id", "amount", "currency", "horizon"]
RuleStatus = Literal["pass", "fail", "not_evaluated"]
PositionMode = Literal["new_position", "increase_existing"]
FitStatus = Literal["fits", "fits_smaller", "does_not_fit", "incomplete"]


class ExposureCheck(BaseModel):
    model_config = FINITE

    dimension: Literal["position", "sector", "country"]
    name: str
    rule: Literal["max_position_pct", "max_sector_pct", "max_country_pct"]
    applies: bool  # false for unrankable buckets ("Diversified", "Global") and an empty portfolio
    limit_pct: float
    limit_source: str  # risk_filter | holding_override
    before_pct: float
    after_pct: float | None = None  # after spending the requested amount (null: no amount given)
    breaks: bool | None = None
    headroom_ils: float | None = None  # most ILS that fits under this cap (null: no cap applies)
    reason: str


class RuleResult(BaseModel):
    model_config = FINITE

    rule: str
    status: RuleStatus
    value: float | None = None
    limit: float | None = None
    reason: str


class MaxPositionSize(BaseModel):
    model_config = FINITE

    position_limit_pct: float
    limit_source: str  # risk_filter | holding_override
    max_additional_ils: float | None = None  # null: no cap binds
    max_additional_usd: float | None = None
    binding_rule: str | None = None
    reason: str


class HeldPosition(BaseModel):
    model_config = FINITE

    holding_id: int | None = None
    quantity: float
    value_ils: float
    weight_pct: float
    horizon: str | None = None
    via_dual_listing: bool = False  # value comes from the other listing of the same company


class PortfolioFit(BaseModel):
    model_config = FINITE

    status: FitStatus
    needs_input: list[NeedsInput] = Field(default_factory=list)
    summary: str
    portfolio_id: int | None = None
    mode: PositionMode | None = None
    held: HeldPosition | None = None
    risk_preset: str | None = None
    risk_source: str | None = None  # request | holding_override | portfolio
    horizon: str | None = None
    horizon_source: Literal["request", "holding"] | None = None
    amount: float | None = None
    currency: str | None = None
    amount_ils: float | None = None
    portfolio_value_ils: float | None = None
    max_position_size: MaxPositionSize | None = None
    exposures: list[ExposureCheck] = Field(default_factory=list)
    caps_broken_at_requested_amount: list[str] = Field(default_factory=list)  # rule names
    rules: list[RuleResult] = Field(default_factory=list)
    rules_not_checked: list[str] = Field(default_factory=list)
    suggested_size: SizeOut | None = None
    entry: float | None = None
    levels: ExitLevelsResult | None = None
    levels_unavailable_reason: str | None = None
    explanation: Explanation


# ---------------------------------------------------------------- the response
class CandidateInfo(BaseModel):
    """Neutral facts only: no verdict while the launch gate is closed (and none from this route)."""

    model_config = FINITE

    score: float
    confidence: float
    score_available: bool
    fit_passes: bool | None = None  # null while the fit is incomplete
    launch_gate_open: bool
    launch_gate_reasons: list[str]
    notice: str = NOT_VALIDATED_NOTICE


class AnalyzeOut(BaseModel):
    symbol: str
    generated_at: datetime
    cached: bool
    scout: ScoutReport
    chart: ChartReport
    portfolio_fit: PortfolioFit
    candidate_info: CandidateInfo
    summary: str  # template text (no AI involved)
    llm_used: bool = False
    needs_input: list[NeedsInput] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)


class AskOut(BaseModel):
    symbol: str
    answer: str  # a template built from the computed data; never an AI answer
    grounded_in: list[str]  # the computed fields the answer quotes
    llm_used: bool = False
    notes_stored: bool = False
    question_declined: bool = False  # the question asked for an instruction to trade
    privacy_note: str = (
        "Your question and notes stay on the server and are never sent to a free AI provider."
    )
    disclaimer: str = DISCLAIMER
