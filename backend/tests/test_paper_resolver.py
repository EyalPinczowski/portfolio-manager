"""The paper-resolve job: due calls resolve once, others stay open, missing data never fakes."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from sqlmodel import Session

from app.models import PaperCall
from app.paper_resolver import resolve_due_calls
from app.scheduler import jobs
from app.scheduler.setup import JOB_IDS, build_scheduler
from app.timeutil import utcnow
from tests.conftest import FakeHistory
from tests.test_paper_metrics_2_0f import put

GSPC, TA125 = "^GSPC", "^TA125.TA"


def _frame(days: int, start: float, step: float) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp(utcnow().date()), periods=days, freq="D")
    close = [start + step * i for i in range(days)]
    return pd.DataFrame({"Close": close, "High": close, "Low": close}, index=idx)


def _hist(symbol: str = "AAPL", **kw: float) -> FakeHistory:
    h = FakeHistory()
    h.frames[symbol] = _frame(120, kw.get("start", 100.0), kw.get("step", 0.0))
    h.frames[GSPC] = _frame(120, 100.0, 0.1)
    h.frames[TA125] = _frame(120, 200.0, 0.2)
    return h


def test_due_call_resolves_at_the_horizon_close(db: Session) -> None:
    call = put(db, age_days=45, horizon="1m", targets=[500.0])
    run = resolve_due_calls(db, _hist(step=0.1))
    assert run.resolved == 1 and run.skipped == {}
    db.refresh(call)
    assert call.outcome == "horizon_end" and call.resolved_at is not None
    assert call.resolved_at == call.created_at + timedelta(days=30)
    assert call.outcome_price is not None and call.outcome_price > 100
    assert set(call.benchmark_returns or {}) == {GSPC, TA125}


def test_a_target_touched_before_the_horizon_resolves_as_target_hit(db: Session) -> None:
    call = put(db, age_days=45, horizon="1m", targets=[103.0], stop=50.0)
    resolve_due_calls(db, _hist(step=0.5))
    db.refresh(call)
    assert call.outcome == "target_hit" and call.outcome_price == 103.0


def test_not_due_call_is_untouched(db: Session) -> None:
    call = put(db, age_days=5, horizon="1m")
    run = resolve_due_calls(db, _hist())
    assert run.resolved == 0 and run.not_due == 1
    db.refresh(call)
    assert call.resolved_at is None and call.outcome is None


def test_missing_history_leaves_the_call_open_with_a_reason(db: Session) -> None:
    call = put(db, age_days=45, horizon="1m")
    empty = FakeHistory()  # no frames at all
    run = resolve_due_calls(db, empty)
    assert run.resolved == 0 and call.id in run.skipped
    db.refresh(call)
    assert call.resolved_at is None and call.outcome_price is None


def test_stale_history_is_not_used_for_the_horizon_price(db: Session) -> None:
    call = put(db, age_days=45, horizon="1m")
    h = _hist()
    old = h.frames["AAPL"]
    h.frames["AAPL"] = old[old.index < pd.Timestamp(call.created_at.date()) + pd.Timedelta(days=10)]
    run = resolve_due_calls(db, h)
    assert run.resolved == 0 and "no close within" in run.skipped[call.id or 0]
    db.refresh(call)
    assert call.resolved_at is None


def test_a_failing_provider_leaves_the_call_open(db: Session) -> None:
    class Boom:
        def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
            raise RuntimeError("down")

    call = put(db, age_days=45, horizon="1m")
    run = resolve_due_calls(db, Boom())
    assert run.resolved == 0 and "failed" in run.skipped[call.id or 0]


def test_second_run_is_a_no_op(db: Session) -> None:
    call = put(db, age_days=45, horizon="1m")
    h = _hist()
    assert resolve_due_calls(db, h).resolved == 1
    db.refresh(call)
    first = (call.resolved_at, call.outcome, call.outcome_price, call.benchmark_returns)
    again = resolve_due_calls(db, h)
    assert again.resolved == 0 and again.skipped == {}
    db.refresh(call)
    assert (call.resolved_at, call.outcome, call.outcome_price, call.benchmark_returns) == first
    assert db.get(PaperCall, call.id) is not None


def test_job_wrapper_and_registration(db: Session) -> None:
    put(db, age_days=45, horizon="1m")
    assert jobs.run_paper_resolve_job(db, _hist()) == 1
    assert "paper_resolve" in JOB_IDS
    assert "paper_resolve" in {j.id for j in build_scheduler().get_jobs()}
