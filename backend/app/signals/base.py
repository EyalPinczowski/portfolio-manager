"""Signal contract: every signal returns a SignalResult with human-readable reasons."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.timeutil import as_utc, utcnow

NOT_AVAILABLE_YET = "Not available yet (planned for Phase 2)."


EXPLANATION_VERSION = 1

# Short free text (names, labels, one reason) and the summary have length caps: an explanation is
# stored with every scored object and shown in the UI, so one hostile or buggy 100 KB string must
# not be able to bloat the database or the page.
ShortText = Annotated[str, StringConstraints(max_length=500)]
RawText = Annotated[str, StringConstraints(max_length=200)]
SummaryText = Annotated[str, StringConstraints(max_length=2000)]
RawValue = float | RawText | None
# Naive datetimes are UTC (the database convention); an explanation always carries an offset.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]
# Every number in an explanation is finite: NaN/Infinity would be stored as JSON `null` and the
# stored "Why?" could then never be read back (`model_validate_json` fails).
FINITE = ConfigDict(allow_inf_nan=False)
MAX_ITEMS = 50


class SignalContribution(BaseModel):
    """One signal's part in a combined score: what it said and how much it counted."""

    model_config = FINITE

    name: ShortText
    score: float = Field(ge=-100.0, le=100.0)
    weight: float = Field(ge=0.0, le=100.0)  # effective weight in percent after redistribution
    confidence: float = Field(ge=0.0, le=1.0)
    raw: dict[ShortText, RawValue] = Field(default_factory=dict, max_length=MAX_ITEMS)


class ChartAnnotation(BaseModel):
    """Something the chart should draw: a level, a moving average or a detected pattern."""

    model_config = FINITE

    kind: Literal["support", "resistance", "moving_average", "pattern"]
    label: ShortText
    price: float | None = None
    as_of: UtcDatetime | None = None


class ExplanationSource(BaseModel):
    """Where a number came from and how fresh it is."""

    model_config = FINITE

    name: ShortText
    as_of: UtcDatetime | None = None
    detail: SummaryText = ""


class Explanation(BaseModel):
    """The "Why?" of a scored object (CLAUDE.md shape), versioned and stored with it.

    `summary`, `inputs` and `rules_applied` are the v0 fields the frontend already reads; they stay
    as part of v1. Every field after them has a default, so payloads cached before v1 still load.
    """

    model_config = FINITE

    version: int = EXPLANATION_VERSION
    summary: SummaryText
    inputs: dict[ShortText, float | RawText] = Field(default_factory=dict, max_length=100)
    rules_applied: list[ShortText] = Field(default_factory=list, max_length=MAX_ITEMS)
    as_of: UtcDatetime | None = None
    contributions: list[SignalContribution] = Field(default_factory=list, max_length=MAX_ITEMS)
    annotations: list[ChartAnnotation] = Field(default_factory=list, max_length=MAX_ITEMS * 2)
    risk_rules_applied: list[ShortText] = Field(default_factory=list, max_length=MAX_ITEMS)
    invalidation_risks: list[ShortText] = Field(default_factory=list, max_length=MAX_ITEMS)
    sources: list[ExplanationSource] = Field(default_factory=list, max_length=MAX_ITEMS)


class SignalResult(BaseModel):
    model_config = FINITE

    name: str = ""
    score: float = Field(ge=-100.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[ShortText]
    data_as_of: datetime
    explanation: Explanation

    @model_validator(mode="after")
    def _reasons_required(self) -> SignalResult:
        if not self.reasons:
            raise ValueError("a SignalResult must carry at least one human-readable reason")
        return self

    @classmethod
    def missing(cls, name: str, reason: str, as_of: datetime | None = None) -> SignalResult:
        """No data: confidence 0 (never a neutral score at full confidence)."""
        return cls(
            name=name,
            score=0.0,
            confidence=0.0,
            reasons=[reason],
            data_as_of=as_of or utcnow(),
            explanation=Explanation(summary=reason, inputs={}, rules_applied=[], as_of=as_of),
        )


def clamp(value: float, low: float = -100.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def not_implemented_signal(name: str) -> SignalResult:
    return SignalResult.missing(name, NOT_AVAILABLE_YET)


def as_float_inputs(values: dict[str, Any]) -> dict[str, float | str]:
    out: dict[str, float | str] = {}
    for key, val in values.items():
        if val is None:
            continue
        out[key] = round(float(val), 4) if isinstance(val, int | float) else str(val)
    return out


def price_history_source(as_of: datetime | None) -> ExplanationSource:
    """The source line shared by the chart-based signals."""
    return ExplanationSource(
        name="Daily price history (OHLCV)",
        as_of=as_of,
        detail="Provider bars, split/dividend adjusted; the last bar's date is `as_of`.",
    )
