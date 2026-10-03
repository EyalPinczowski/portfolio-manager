"""Combine independent signals with configurable weights.

A signal with confidence 0 gets no weight; its share is spread over the others in proportion to
their own weight x confidence. Missing data is never a neutral score at full confidence.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.config import SIGNAL_NAMES, Settings, get_settings
from app.signals.base import SignalResult, not_implemented_signal


class SignalWeight(BaseModel):
    name: str
    nominal_weight: float  # configured weight, % of total
    effective_weight: float  # after confidence-based redistribution, % of total
    score: float
    confidence: float


class CombinedScore(BaseModel):
    total: float  # 0.0 when no signal has data (check `available`)
    confidence: float  # share of the nominal weight that actually had data, in [0, 1]
    available: bool
    breakdown: list[SignalWeight]
    missing: list[str]


def combine_signals(
    results: dict[str, SignalResult], settings: Settings | None = None
) -> CombinedScore:
    s = settings or get_settings()
    weights = {name: float(s.signal_weights.get(name, 0.0)) for name in SIGNAL_NAMES}
    full: dict[str, SignalResult] = {
        name: results.get(name) or not_implemented_signal(name) for name in SIGNAL_NAMES
    }
    nominal_total = sum(weights.values()) or 1.0
    raw = {name: weights[name] * full[name].confidence for name in SIGNAL_NAMES}
    raw_total = sum(raw.values())
    breakdown: list[SignalWeight] = []
    for name in SIGNAL_NAMES:
        eff = (raw[name] / raw_total * 100.0) if raw_total > 0 else 0.0
        breakdown.append(
            SignalWeight(
                name=name,
                nominal_weight=round(weights[name] / nominal_total * 100.0, 4),
                effective_weight=round(eff, 4),
                score=full[name].score,
                confidence=full[name].confidence,
            )
        )
    if raw_total <= 0:
        return CombinedScore(
            total=0.0,
            confidence=0.0,
            available=False,
            breakdown=breakdown,
            missing=[n for n in SIGNAL_NAMES if full[n].confidence <= 0],
        )
    total = sum(full[n].score * raw[n] for n in SIGNAL_NAMES) / raw_total
    return CombinedScore(
        total=round(total, 2),
        confidence=round(raw_total / nominal_total, 4),
        available=True,
        breakdown=breakdown,
        missing=[n for n in SIGNAL_NAMES if full[n].confidence <= 0],
    )
