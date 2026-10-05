"""Analyst signal, "revisions" variant: the change in the buy-share, not the static consensus.

Selected by `analyst_signal_mode`. "consensus" (the default) keeps the slot unimplemented, as it
was before. The revisions variant scores the change in the share of buy/strong-buy ratings between
the latest month and one `analyst_revision_months` earlier, mapped through tanh to [-100, 100].
Rating dispersion (std of the 1-5 scale) and a small analyst count lower confidence. No data
(TASE, no coverage, fewer than two months) gives confidence 0.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time

from app.config import Settings, get_settings
from app.providers.analyst_trends import RecommendationTrend
from app.signals.base import (
    Explanation,
    ExplanationSource,
    SignalResult,
    as_float_inputs,
    clamp,
    not_implemented_signal,
)

NAME = "analysts"


def _month_index(d: date) -> int:
    return d.year * 12 + d.month


def buy_share(t: RecommendationTrend) -> float | None:
    return (t.strong_buy + t.buy) / t.total if t.total > 0 else None


def rating_dispersion(t: RecommendationTrend) -> float | None:
    """Standard deviation of the 1 (strong sell) .. 5 (strong buy) rating across analysts."""
    n = t.total
    if n <= 0:
        return None
    pts = ((5, t.strong_buy), (4, t.buy), (3, t.hold), (2, t.sell), (1, t.strong_sell))
    mean = sum(p * c for p, c in pts) / n
    return math.sqrt(sum(c * (p - mean) ** 2 for p, c in pts) / n)


def analysts_signal(
    trends: list[RecommendationTrend] | None,
    settings: Settings | None = None,
    *,
    symbol: str = "",
) -> SignalResult:
    s = settings or get_settings()
    if s.analyst_signal_mode != "revisions":
        return not_implemented_signal(NAME)
    if not trends:
        return SignalResult.missing(NAME, "No analyst coverage found (e.g. TASE or small caps).")
    rows = sorted(trends, key=lambda t: t.period, reverse=True)
    latest = rows[0]
    as_of = datetime.combine(latest.period, time.min)
    target = _month_index(latest.period) - s.analyst_revision_months
    older = [t for t in rows[1:] if _month_index(t.period) < _month_index(latest.period)]
    if not older or latest.total <= 0:
        return SignalResult.missing(
            NAME, "Fewer than two months of analyst ratings: no revision can be measured.", as_of
        )
    ref = min(older, key=lambda t: abs(_month_index(t.period) - target))
    now_share, then_share = buy_share(latest), buy_share(ref)
    if now_share is None or then_share is None:
        return SignalResult.missing(NAME, "Analyst rating counts are empty.", as_of)
    months = _month_index(latest.period) - _month_index(ref.period)
    delta = now_share - then_share
    score = clamp(100.0 * math.tanh(delta / s.analyst_revision_scale))
    disp = rating_dispersion(latest) or 0.0
    cut = s.analyst_dispersion_max_cut * min(1.0, disp / s.analyst_dispersion_ref)
    breadth = min(1.0, latest.total / s.analyst_full_confidence_analysts)
    confidence = round(max(0.05, min(1.0, breadth * (1.0 - cut))), 3)
    direction = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
    reasons = [
        f"The share of buy ratings moved {delta * 100:+.0f} points over {months} month(s) "
        f"({then_share * 100:.0f}% to {now_share * 100:.0f}%): revisions {direction}.",
        f"Ratings from {latest.total} analyst(s) differ with a spread of {disp:.2f} on a 1-5 scale"
        + (
            "; high disagreement lowers confidence."
            if cut > 0.5 * s.analyst_dispersion_max_cut
            else "."
        ),
    ]
    return SignalResult(
        name=NAME,
        score=round(score, 2),
        confidence=confidence,
        reasons=reasons,
        data_as_of=as_of,
        explanation=Explanation(
            summary=f"Analyst revisions score {score:+.0f} (buy-share change {delta * 100:+.0f} pts).",
            inputs=as_float_inputs(
                {
                    "buy_share_now": now_share,
                    "buy_share_then": then_share,
                    "months": months,
                    "analysts": latest.total,
                    "dispersion": disp,
                }
            ),
            rules_applied=[
                "score = 100 x tanh(change in buy-share / analyst_revision_scale)",
                "confidence = analyst breadth x (1 - dispersion cut)",
            ],
            as_of=as_of,
            invalidation_risks=["Analyst ratings lag prices and can be revised back."],
            sources=[
                ExplanationSource(
                    name="Analyst recommendation trends",
                    as_of=as_of,
                    detail="Monthly rating counts (Finnhub free tier or yfinance).",
                )
            ],
        ),
    )
