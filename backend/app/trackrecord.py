"""Members-only track record: the global paper calls whose horizon has ended, against the benchmarks.

Read-only over the append-only `paper_call` table. Global calls only (never a user's own paper
calls), so nothing here is personal. No call text (explanation, entry, stop, targets) and no
direction of the call is exposed: the page shows how the app's past calls did, not what to do now.
Excess return is measured in the direction the call was made (`papertrading.excess_return_pct`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.config import Settings
from app.launchgate import GateStatus, weights_fingerprint
from app.models import PaperCall
from app.papertrading import _raw_return_pct, excess_return_pct, paper_metrics
from app.timeutil import utcnow

TrackState = Literal["not_started", "none_ended", "ready"]
CallOutcome = Literal["reached_goal", "reached_limit", "horizon_ended"]
# Neutral names for the stored outcomes (the stored words are verdict-like and stay internal).
_OUTCOME_NAMES: dict[str, CallOutcome] = {
    "target_hit": "reached_goal",
    "stop_hit": "reached_limit",
    "horizon_end": "horizon_ended",
}


class BenchmarkResult(BaseModel):
    name: str
    return_pct: float  # holding the index unchanged over the same span, as recorded when the call was resolved
    excess_pct: float | None  # the call's edge over it, in percentage points


class TrackCallRow(BaseModel):
    symbol: str
    made_on: date
    horizon: str
    horizon_ended_on: date
    resolved_on: date
    outcome: CallOutcome
    return_pct: float  # the asset's own move from the call price to the outcome price
    active_weights: bool  # made with the weights config in use today
    benchmarks: list[BenchmarkResult]


class BenchmarkAggregate(BaseModel):
    name: str
    count: int  # calls that have a result against this benchmark
    hit_rate_pct: float | None  # share of those with a positive excess return
    avg_excess_pct: float | None


class GateProgress(BaseModel):
    open: bool
    reasons: list[str]
    weeks_running: float
    weeks_required: int
    resolved_at_1m: int
    resolved_required: int
    critical_errors: int
    recorded: int


class TrackRecordOut(BaseModel):
    state: TrackState
    message: str
    as_of: datetime
    count: int
    resolved_at_1m: int
    awaiting_resolution: int  # horizon ended but no resolution recorded yet
    excluded_errors: int  # horizon ended, resolution was an operational error (no price)
    benchmarks: list[BenchmarkAggregate]
    gate: GateProgress
    methodology: list[str]
    rows: list[TrackCallRow]
    truncated: bool


def _methodology(s: Settings) -> list[str]:
    return [
        "Only calls made by the app itself (global calls) are shown. Nothing here comes from "
        "any member's portfolio.",
        "A call appears only after its horizon has ended: "
        + ", ".join(f"{h} = {d} days" for h, d in s.track_record_horizon_days.items())
        + ".",
        "Calls are recorded when they are made and are never edited afterwards; the outcome is "
        "added once.",
        "Return is the asset's own move from the price at the call to the price at the outcome. "
        "The benchmark return is the return of simply holding the index over the same span.",
        "Excess return is measured in the direction the call was made, in percentage points. A "
        "call is a hit against a benchmark when its excess return is above zero.",
        "Calls that ended in an operational error, and calls still waiting for a resolution, are "
        "counted separately and never dropped silently.",
        "Prices come from free data sources and can be delayed. This is a record of past calls, "
        "not financial advice and not a promise of future results.",
    ]


def build_track_record(
    db: Session, settings: Settings, gate: GateStatus, now: datetime | None = None
) -> TrackRecordOut:
    now = now or utcnow()
    active = weights_fingerprint(settings.signal_weights)
    calls = list(
        db.exec(
            select(PaperCall)
            .where(col(PaperCall.is_global).is_(True))
            .order_by(col(PaperCall.created_at).desc(), col(PaperCall.id).desc())
        ).all()
    )
    metrics = paper_metrics(db, settings, now)
    progress = GateProgress(
        open=gate.open,
        reasons=gate.reasons,
        weeks_running=round(metrics.weeks_running, 2),
        weeks_required=settings.launch_paper_min_weeks,
        resolved_at_1m=metrics.resolved_calls_1m,
        resolved_required=settings.launch_paper_min_resolved_calls,
        critical_errors=metrics.critical_errors,
        recorded=metrics.calls_recorded,
    )
    rows: list[TrackCallRow] = []
    awaiting = errors = 0
    names = list(settings.launch_paper_must_beat)
    excess: dict[str, list[float]] = {n: [] for n in names}
    for c in calls:
        days = settings.track_record_horizon_days.get(c.horizon)
        if days is None:
            continue
        ends = c.created_at + timedelta(days=days)
        if ends > now:
            continue  # the horizon has not ended: not part of the record yet
        if c.resolved_at is None:
            awaiting += 1
            continue
        if c.outcome not in _OUTCOME_NAMES:
            errors += 1
            continue
        ret = _raw_return_pct(c)
        if ret is None:
            errors += 1
            continue
        bench_rows: list[BenchmarkResult] = []
        for name, value in (c.benchmark_returns or {}).items():
            edge = excess_return_pct(c, value)
            bench_rows.append(
                BenchmarkResult(
                    name=name,
                    return_pct=round(value, 2),
                    excess_pct=None if edge is None else round(edge, 2),
                )
            )
            if edge is not None:
                excess.setdefault(name, []).append(edge)
        rows.append(
            TrackCallRow(
                symbol=c.symbol,
                made_on=c.created_at.date(),
                horizon=c.horizon,
                horizon_ended_on=ends.date(),
                resolved_on=c.resolved_at.date(),
                outcome=_OUTCOME_NAMES[c.outcome],
                return_pct=round(ret, 2),
                active_weights=c.weights_hash == active,
                benchmarks=sorted(bench_rows, key=lambda b: b.name),
            )
        )
    aggregates = [
        BenchmarkAggregate(
            name=n,
            count=len(v),
            hit_rate_pct=round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None,
            avg_excess_pct=round(sum(v) / len(v), 2) if v else None,
        )
        for n, v in excess.items()
    ]
    if not calls:
        state: TrackState = "not_started"
        message = (
            "Paper trading has not started yet: no calls have been recorded, so there is no "
            "track record to show. This page fills in as calls are made and their horizons end."
        )
    elif not rows:
        state = "none_ended"
        message = (
            f"{len(calls)} call(s) recorded, but none has reached the end of its horizon with a "
            "result yet. Nothing is shown until a horizon has ended."
        )
    else:
        state = "ready"
        message = f"{len(rows)} call(s) whose horizon has ended."
    cap = settings.track_record_max_rows
    return TrackRecordOut(
        state=state,
        message=message,
        as_of=now,
        count=len(rows),
        resolved_at_1m=metrics.resolved_calls_1m,
        awaiting_resolution=awaiting,
        excluded_errors=errors,
        benchmarks=aggregates,
        gate=progress,
        methodology=_methodology(settings),
        rows=rows[:cap],
        truncated=len(rows) > cap,
    )
