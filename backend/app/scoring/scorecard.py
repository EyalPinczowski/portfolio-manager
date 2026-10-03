"""Score cards: technical + patterns signals combined (no buy/sell verdict, launch gate)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import SignalCache
from app.providers.base import HistoryProvider
from app.scoring.combine import combine_signals
from app.signals.base import Explanation, SignalResult
from app.signals.patterns import patterns_signal
from app.signals.technical import technical_signal
from app.timeutil import as_utc, utcnow

NOT_VALIDATED = (
    "Scores are not yet validated by a backtest, so no buy/sell verdict is shown. "
    "Not financial advice."
)


def compute_scorecard(
    symbol: str, history: HistoryProvider | None, settings: Settings | None = None
) -> dict[str, Any]:
    s = settings or get_settings()
    df = None
    if history is not None:
        try:
            df = history.get_history(symbol, s.history_days)
        except Exception:
            df = None
    results: dict[str, SignalResult] = {
        "technical": technical_signal(df, s),
        "patterns": patterns_signal(df, s),
    }
    combined = combine_signals(results, s)
    from app.signals.base import not_implemented_signal

    signals: list[dict[str, Any]] = []
    for w in combined.breakdown:
        res = results.get(w.name) or not_implemented_signal(w.name)
        signals.append(
            {
                "name": w.name,
                "score": res.score,
                "confidence": res.confidence,
                "weight": w.effective_weight,
                "nominal_weight": w.nominal_weight,
                "reasons": res.reasons,
                "data_as_of": as_utc(res.data_as_of).isoformat(),
                "explanation": res.explanation.model_dump(),
            }
        )
    active = [w for w in combined.breakdown if w.effective_weight > 0]
    summary = (
        f"Total score {combined.total:+.0f} from {len(active)} signal(s) with data; "
        f"weights of signals without data ({', '.join(combined.missing) or 'none'}) were redistributed."
        if combined.available
        else "No signal has data for this security yet."
    )
    explanation = Explanation(
        summary=summary,
        inputs={
            w.name: f"{w.effective_weight:.1f}% effective (nominal {w.nominal_weight:.1f}%)"
            for w in combined.breakdown
        },
        rules_applied=[
            "Total = sum(weight x confidence x score) / sum(weight x confidence).",
            "Signals with confidence 0 are excluded and their weight is redistributed.",
            NOT_VALIDATED,
        ],
    )
    return {
        "total": combined.total,
        "technical": results["technical"].score,
        "patterns": results["patterns"].score,
        "confidence": combined.confidence,
        "available": combined.available,
        "validated": False,
        "signals": signals,
        "explanation": explanation.model_dump(),
    }


def get_cached_scorecard(db: Session, symbol: str) -> dict[str, Any] | None:
    row = db.get(SignalCache, symbol)
    return row.payload if row is not None else None


def is_fresh(db: Session, symbol: str, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    row = db.get(SignalCache, symbol)
    if row is None:
        return False
    age_min = (utcnow() - row.computed_at).total_seconds() / 60.0
    return age_min < s.score_cache_ttl_minutes


def refresh_scorecard(
    db: Session, symbol: str, history: HistoryProvider | None, settings: Settings | None = None
) -> dict[str, Any]:
    payload = compute_scorecard(symbol, history, settings)
    row = db.get(SignalCache, symbol)
    now: datetime = utcnow()
    if row is None:
        db.add(SignalCache(symbol=symbol, computed_at=now, payload=payload))
    else:
        row.payload, row.computed_at = payload, now
        db.add(row)
    db.commit()
    return payload
