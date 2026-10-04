"""Resolve paper calls whose horizon has ended (the scheduler's `paper_resolve` job).

Reads daily history through the `HistoryProvider` interface only. For each open call past its
horizon (`track_record_horizon_days`) it walks the daily bars after the day the call was made: the
stop level, then the first target, decide `stop_hit` / `target_hit` (a bar that touches both counts
as the stop: the cautious reading); otherwise the call ends `horizon_end` at the last close on or
before the horizon's last day. Missing or stale data leaves the call open (reason logged, retried
next run); nothing is ever estimated. The write goes through `resolve_call_with_benchmarks`, so a
call is resolved once and the append-only guard stays in force.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import PaperCall
from app.models.guards import AppendOnlyError
from app.papertrading import (
    Outcome,
    _close_on_or_before,
    _closes_by_date,
    resolve_call_with_benchmarks,
)
from app.providers.base import HistoryProvider
from app.timeutil import utcnow

log = logging.getLogger(__name__)


class ResolveRun(BaseModel):
    resolved: int = 0
    not_due: int = 0
    skipped: dict[int, str] = Field(default_factory=dict)  # call id -> why it stays open


def horizon_end(call: PaperCall, settings: Settings) -> datetime | None:
    days = settings.track_record_horizon_days.get(call.horizon)
    return None if days is None else call.created_at + timedelta(days=days)


def _decide(
    call: PaperCall, df: pd.DataFrame, end: datetime, now: datetime, s: Settings
) -> tuple[Outcome, float, datetime] | str:
    """(outcome, price, resolved_at) or a plain-words reason the call cannot be resolved yet."""
    closes = _closes_by_date(df)
    if closes is None:
        return "no price history available"
    highs = (
        _closes_by_date(df[["High"]].rename(columns={"High": "Close"})) if "High" in df else closes
    )
    lows = _closes_by_date(df[["Low"]].rename(columns={"Low": "Close"})) if "Low" in df else closes
    first_day = pd.Timestamp(call.created_at.date()) + pd.Timedelta(days=1)
    last_day = pd.Timestamp(end.date())
    target = call.targets[0] if call.targets else None
    buy = call.side == "buy"
    for day in closes.index[(closes.index >= first_day) & (closes.index <= last_day)]:
        hi = float(highs.get(day, closes[day])) if highs is not None else float(closes[day])
        lo = float(lows.get(day, closes[day])) if lows is not None else float(closes[day])
        when = min(day.to_pydatetime(), now)
        if call.stop is not None and (lo <= call.stop if buy else hi >= call.stop):
            return "stop_hit", call.stop, when
        if target is not None and (hi >= target if buy else lo <= target):
            return "target_hit", target, when
    price = _close_on_or_before(closes, end.date(), s.paper_resolve_max_gap_days)
    if price is None:
        return f"no close within {s.paper_resolve_max_gap_days} days before {end.date()}"
    return "horizon_end", price, min(end, now)


def resolve_due_calls(
    db: Session,
    history: HistoryProvider,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ResolveRun:
    """One pass over the open calls. Safe to repeat: a resolved call is never selected again."""
    s = settings or get_settings()
    now = now or utcnow()
    run = ResolveRun()
    open_calls = db.exec(
        select(PaperCall)
        .where(col(PaperCall.resolved_at).is_(None))
        .order_by(col(PaperCall.created_at), col(PaperCall.id))
        .limit(s.paper_resolve_batch_size)
    ).all()
    for call in open_calls:
        assert call.id is not None
        end = horizon_end(call, s)
        if end is None:
            run.skipped[call.id] = f"unknown horizon {call.horizon!r}"
        elif end > now:
            run.not_due += 1
            continue
        else:
            try:
                days = (
                    now.date() - call.created_at.date()
                ).days + s.paper_benchmark_history_padding_days
                df = history.get_history(call.symbol, max(days, 1))
            except Exception as exc:  # a provider failure leaves the call open
                run.skipped[call.id] = f"history lookup failed ({type(exc).__name__})"
                df = None
            else:
                if df is None or df.empty:
                    run.skipped[call.id] = "no price history available"
                else:
                    decision = _decide(call, df, end, now, s)
                    if isinstance(decision, str):
                        run.skipped[call.id] = decision
                    else:
                        outcome, price, when = decision
                        try:
                            resolve_call_with_benchmarks(
                                db, call.id, history, outcome=outcome, outcome_price=price,
                                resolved_at=when, settings=s,
                            )  # fmt: skip
                            run.resolved += 1
                        except AppendOnlyError:  # resolved by another process meanwhile
                            db.rollback()
                        except Exception as exc:
                            db.rollback()
                            run.skipped[call.id] = (
                                f"could not store the resolution ({type(exc).__name__})"
                            )
        if call.id in run.skipped:
            log.warning("paper call %s left open: %s", call.id, run.skipped[call.id])
    return run
