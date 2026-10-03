"""Signal contract: every signal returns a SignalResult with human-readable reasons."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

from app.timeutil import as_utc, utcnow

NOT_AVAILABLE_YET = "Not available yet (planned for Phase 2)."


EXPLANATION_VERSION = 1

RawValue = float | str | None
# Naive datetimes are UTC (the database convention); an explanation always carries an offset.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


class SignalContribution(BaseModel):
    """One signal's part in a combined score: what it said and how much it counted."""

    name: str
    score: float
    weight: float  # effective weight in percent after confidence-based redistribution
    confidence: float = Field(ge=0.0, le=1.0)
    raw: dict[str, RawValue] = Field(default_factory=dict)


class ChartAnnotation(BaseModel):
    """Something the chart should draw: a level, a moving average or a detected pattern."""

    kind: Literal["support", "resistance", "moving_average", "pattern"]
    label: str
    price: float | None = None
    as_of: UtcDatetime | None = None


class ExplanationSource(BaseModel):
    """Where a number came from and how fresh it is."""

    name: str
    as_of: UtcDatetime | None = None
    detail: str = ""


class Explanation(BaseModel):
    """The "Why?" of a scored object (CLAUDE.md shape), versioned and stored with it.

    `summary`, `inputs` and `rules_applied` are the v0 fields the frontend already reads; they stay
    as part of v1. Every field after them has a default, so payloads cached before v1 still load.
    """

    version: int = EXPLANATION_VERSION
    summary: str
    inputs: dict[str, float | str] = Field(default_factory=dict)
    rules_applied: list[str] = Field(default_factory=list)
    as_of: UtcDatetime | None = None
    contributions: list[SignalContribution] = Field(default_factory=list)
    annotations: list[ChartAnnotation] = Field(default_factory=list)
    risk_rules_applied: list[str] = Field(default_factory=list)
    invalidation_risks: list[str] = Field(default_factory=list)
    sources: list[ExplanationSource] = Field(default_factory=list)


class SignalResult(BaseModel):
    name: str = ""
    score: float = Field(ge=-100.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str]
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
