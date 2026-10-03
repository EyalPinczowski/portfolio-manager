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
) -> PaperCall:
    """Append one call, stamped with the time it is made (there is no way to backdate it).

    Exactly one scope: a user's paper portfolio, or the global (gate) track."""
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
    when = resolved_at or utcnow()
    if when < call.created_at or when > utcnow() + timedelta(minutes=5):
        raise ValueError("resolved_at must lie between the time of the call and now")
    if outcome != "error" and outcome_price is None:
        raise ValueError("a resolution other than 'error' needs the outcome price")
    call.resolved_at = when
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
def _raw_return_pct(call: PaperCall) -> float | None:
    """The asset's own % move from the entry to the outcome price (not signed by the side)."""
    if call.outcome_price is None or call.entry <= 0:
        return None
    return (call.outcome_price / call.entry - 1.0) * 100.0


def excess_return_pct(call: PaperCall, benchmark_return_pct: float) -> float | None:
    """How far the call beat the benchmark, in percentage points, in the call's own direction.

    A buy is right when the asset beats the benchmark: `asset - benchmark`. A sell is right when
    the asset *lags* it: `benchmark - asset`. So a sell that merely fell with the market has no
    edge (0), and one that fell while the market rose has the full gap. (The old formula
    `-asset - benchmark` credited a sell for the market's own fall and charged it for the market's
    rise.)
    """
    raw = _raw_return_pct(call)
    if raw is None:
        return None
    gap = raw - benchmark_return_pct
    return gap if call.side == "buy" else -gap


def paper_metrics(db: Session, settings: Settings, now: datetime | None = None) -> PaperMetrics:
    """What the gate needs from the global paper calls (zeros when there are none).

    Only calls made with the active weights config (and, when `launch_paper_model_hash` is set, the
    active model) count: a track record of another configuration says nothing about this one.
    Errors are operational events: only an `error` outcome recorded inside the last
    `launch_paper_min_weeks` counts as a critical error, so one Yahoo glitch months ago cannot keep
    the gate closed for good (the rows themselves are append-only and stay).
    """
    from app.launchgate import weights_fingerprint

    now = now or utcnow()
    query = select(PaperCall).where(
        col(PaperCall.is_global).is_(True),
        PaperCall.weights_hash == weights_fingerprint(settings.signal_weights),
    )
    if settings.launch_paper_model_hash:
        query = query.where(PaperCall.model_hash == settings.launch_paper_model_hash)
    calls = list(db.exec(query).all())
    if not calls:
        return PaperMetrics(weeks_running=0.0, critical_errors=0, resolved_calls_1m=0)
    first = min(c.created_at for c in calls)
    window = timedelta(days=settings.launch_paper_window_days)
    counted = [
        c
        for c in calls
        if c.resolved_at is not None and c.outcome != "error" and c.created_at + window <= now
    ]
    error_window = timedelta(weeks=settings.launch_paper_min_weeks)
    errors = [
        c
        for c in calls
        if c.outcome == "error"
        and c.resolved_at is not None
        and now - c.resolved_at <= error_window
    ]
    excess: dict[str, float] = {}
    for bench in settings.launch_paper_must_beat:
        diffs = [
            diff
            for c in counted
            if c.benchmark_returns and bench in c.benchmark_returns
            if (diff := excess_return_pct(c, c.benchmark_returns[bench])) is not None
        ]
        if diffs:
            excess[bench] = sum(diffs) / len(diffs)
    return PaperMetrics(
        weeks_running=max(0.0, (now - first).total_seconds() / (7 * 86400)),
        critical_errors=len(errors),
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
