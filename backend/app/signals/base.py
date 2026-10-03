"""Signal contract: every signal returns a SignalResult with human-readable reasons."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.timeutil import utcnow

NOT_AVAILABLE_YET = "Not available yet (planned for Phase 2)."


class Explanation(BaseModel):
    summary: str
    inputs: dict[str, float | str] = Field(default_factory=dict)
    rules_applied: list[str] = Field(default_factory=list)


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
            explanation=Explanation(summary=reason, inputs={}, rules_applied=[]),
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
