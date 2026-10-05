"""Reporting-only calibration of past calls: Brier score and a reliability table.

Never feeds back into scores, weights, ranking confidence or the launch gate.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel


class ReliabilityBin(BaseModel):
    low: float
    high: float
    count: int
    mean_predicted: float
    observed_rate: float


class Calibration(BaseModel):
    count: int
    brier: float
    base_rate: float
    brier_baseline: float  # Brier of always predicting the base rate (lower = better)
    bins: list[ReliabilityBin]


def score_to_prob(score: float) -> float:
    """Map a |score| in [0, 100] to a stated chance of success in [0.5, 1.0].

    A fixed, documented mapping (the app has no calibrated probability yet): 0 -> 50%, 100 -> 100%.
    """
    return 0.5 + min(100.0, abs(score)) / 200.0


def brier_score(probs: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    if not probs or len(probs) != len(outcomes):
        return None
    return sum((p - float(o)) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def reliability_table(
    probs: Sequence[float], outcomes: Sequence[bool], n_bins: int = 5
) -> list[ReliabilityBin]:
    """Equal-width bins over [0, 1]; empty bins are left out."""
    out: list[ReliabilityBin] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        idx = [k for k, p in enumerate(probs) if lo <= p < hi or (i == n_bins - 1 and p == 1.0)]
        if not idx:
            continue
        out.append(
            ReliabilityBin(
                low=lo,
                high=hi,
                count=len(idx),
                mean_predicted=round(sum(probs[k] for k in idx) / len(idx), 4),
                observed_rate=round(sum(outcomes[k] for k in idx) / len(idx), 4),
            )
        )
    return out


def calibration(
    probs: Sequence[float], outcomes: Sequence[bool], n_bins: int = 5
) -> Calibration | None:
    """None when there are no resolved calls."""
    brier = brier_score(probs, outcomes)
    if brier is None:
        return None
    base = sum(outcomes) / len(outcomes)
    return Calibration(
        count=len(probs),
        brier=round(brier, 4),
        base_rate=round(base, 4),
        brier_baseline=round(base * (1 - base), 4),
        bins=reliability_table(probs, outcomes, n_bins),
    )
