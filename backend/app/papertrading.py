"""Paper-trading and backtest records: the append-only repository and the launch-gate stores.

`PaperCall` is append-only (see `app/models/guards.py`): `record_call` inserts, `resolve_call`
sets the resolution fields once. Nothing here edits a call. `DbPaper` / `DbBacktests` are what
`LaunchGate` reads; a database error makes them return nothing, so the gate stays closed.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlmodel import Session, col, select

from app.config import HORIZONS, Settings, get_settings
from app.launchgate import BacktestRecord, PaperMetrics
from app.models import BacktestRun, PaperCall
from app.models.guards import AppendOnlyError
from app.signals.base import Explanation
from app.timeutil import utcnow

log = logging.getLogger(__name__)

Side = Literal["buy", "sell"]
Outcome = Literal["target_hit", "stop_hit", "horizon_end", "error"]
SIDES: tuple[str, ...] = ("buy", "sell")
OUTCOMES: tuple[str, ...] = ("target_hit", "stop_hit", "horizon_end", "error")


def _finite(value: float, what: str, positive: bool = False) -> float:
    if not isinstance(value, int | float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{what} must be a finite number{' > 0' if positive else ''}")
    return float(value)


def record_call(
    db: Session,
    *,
    symbol: str,
    side: Side,
    horizon: str,
    entry: float,
    stop: float | None,
    targets: list[float],
    explanation: Explanation,
    model_hash: str,
    prompt_hash: str,
    weights_hash: str,
    user_id: int | None = None,
    is_global: bool = False,
    created_at: datetime | None = None,
) -> PaperCall:
    """Append one call. Exactly one scope: a user's paper portfolio, or the global (gate) track."""
    if is_global == (user_id is not None):
        raise ValueError("a paper call is either global or owned by one user, not both or neither")
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    if not symbol.strip() or not all(h.strip() for h in (model_hash, prompt_hash, weights_hash)):
        raise ValueError("symbol and the model/prompt/weights hashes are required")
    call = PaperCall(
        symbol=symbol.upper(),
        side=side,
        horizon=horizon,
        entry=_finite(entry, "entry", positive=True),
        stop=None if stop is None else _finite(stop, "stop", positive=True),
        targets=[_finite(t, "target", positive=True) for t in targets],
        explanation=explanation.model_dump(mode="json"),
        model_hash=model_hash,
        prompt_hash=prompt_hash,
        weights_hash=weights_hash,
        user_id=user_id,
        is_global=is_global,
        created_at=created_at or utcnow(),
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def resolve_call(
    db: Session,
    call_id: int,
    *,
    outcome: Outcome,
    outcome_price: float | None,
    benchmark_returns: dict[str, float] | None = None,
    resolved_at: datetime | None = None,
) -> PaperCall:
    """Set the resolution of an open call. A second resolution raises `AppendOnlyError`."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    call = db.get(PaperCall, call_id)
    if call is None:
        raise LookupError(f"paper call {call_id} does not exist")
    if call.resolved_at is not None:
        raise AppendOnlyError("paper_call is already resolved; a resolution is final")
    if outcome != "error" and outcome_price is None:
        raise ValueError("a resolution other than 'error' needs the outcome price")
    call.resolved_at = resolved_at or utcnow()
    call.outcome = outcome
    call.outcome_price = None if outcome_price is None else _finite(outcome_price, "outcome_price")
    call.benchmark_returns = (
        None
        if benchmark_returns is None
        else {k: _finite(v, f"benchmark {k}") for k, v in benchmark_returns.items()}
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def record_backtest(
    db: Session,
    *,
    weights_hash: str,
    period_start: date,
    period_end: date,
    metrics: dict[str, Any],
    passed: bool,
) -> BacktestRun:
    if not weights_hash.strip() or period_end < period_start:
        raise ValueError(
            "a backtest needs a weights hash and a period that does not end before it starts"
        )
    run = BacktestRun(
        weights_hash=weights_hash,
        period_start=period_start,
        period_end=period_end,
        metrics=metrics,
        passed=passed,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------- launch-gate reads
def _return_pct(call: PaperCall) -> float | None:
    if call.outcome_price is None or call.entry <= 0:
        return None
    raw = (call.outcome_price / call.entry - 1.0) * 100.0
    return raw if call.side == "buy" else -raw


def paper_metrics(db: Session, settings: Settings, now: datetime | None = None) -> PaperMetrics:
    """What the gate needs from the global paper calls (zeros when there are none)."""
    now = now or utcnow()
    calls = list(db.exec(select(PaperCall).where(col(PaperCall.is_global).is_(True))).all())
    if not calls:
        return PaperMetrics(weeks_running=0.0, critical_errors=0, resolved_calls_1m=0)
    first = min(c.created_at for c in calls)
    window = timedelta(days=settings.launch_paper_window_days)
    counted = [
        c
        for c in calls
        if c.resolved_at is not None and c.outcome != "error" and c.created_at + window <= now
    ]
    excess: dict[str, float] = {}
    for bench in settings.launch_paper_must_beat:
        diffs = [
            ret - c.benchmark_returns[bench]
            for c in counted
            if c.benchmark_returns and bench in c.benchmark_returns
            if (ret := _return_pct(c)) is not None
        ]
        if diffs:
            excess[bench] = sum(diffs) / len(diffs)
    return PaperMetrics(
        weeks_running=max(0.0, (now - first).total_seconds() / (7 * 86400)),
        critical_errors=sum(1 for c in calls if c.outcome == "error"),
        resolved_calls_1m=len(counted),
        excess_return_pct=excess,
        calls_recorded=len(calls),
    )


class DbPaper:
    """`PaperStore` over the `paper_call` table (own short session; unreadable DB -> None)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def metrics(self) -> PaperMetrics | None:
        from app.db import new_session

        try:
            with new_session() as db:
                return paper_metrics(db, self.settings)
        except Exception:
            log.warning("launch gate: paper-trading tables unreadable", exc_info=True)
            return None


class DbBacktests:
    """`BacktestStore` over `backtest_run`: the newest run for the active weights, else the newest."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def latest(self) -> BacktestRecord | None:
        from app.db import new_session
        from app.launchgate import weights_fingerprint

        active = weights_fingerprint(self.settings.signal_weights)
        try:
            with new_session() as db:
                newest = col(BacktestRun.created_at).desc(), col(BacktestRun.id).desc()
                row = (
                    db.exec(
                        select(BacktestRun)
                        .where(BacktestRun.weights_hash == active)
                        .order_by(*newest)
                    ).first()
                    or db.exec(select(BacktestRun).order_by(*newest)).first()
                )
                if row is None:
                    return None
                return BacktestRecord(row.weights_hash, row.passed, row.created_at)
        except Exception:
            log.warning("launch gate: backtest table unreadable", exc_info=True)
            return None
