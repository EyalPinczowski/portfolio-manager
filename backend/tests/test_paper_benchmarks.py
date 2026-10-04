"""Benchmark returns filled when a paper call is resolved: math, missing data, append-only, and the
track-record page showing the aggregates. Fixture history only (no live calls)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import new_session
from app.models import PaperCall
from app.models.guards import AppendOnlyError
from app.papertrading import benchmark_returns_for, resolve_call_with_benchmarks
from app.timeutil import utcnow
from tests.conftest import FakeHistory
from tests.test_paper_metrics_2_0f import put

SignupFn = Callable[..., TestClient]
GSPC, TA125 = "^GSPC", "^TA125.TA"


def _series(days: int, start: float, daily_pct: float) -> pd.DataFrame:
    """A calendar-day series ending today: close = start * (1 + pct) ** i."""
    idx = pd.date_range(end=pd.Timestamp(utcnow().date()), periods=days, freq="D")
    close = [start * (1 + daily_pct / 100) ** i for i in range(days)]
    return pd.DataFrame({"Close": close}, index=idx)


@pytest.fixture
def hist() -> FakeHistory:
    h = FakeHistory()
    h.frames[GSPC] = _series(120, 100.0, 0.1)
    h.frames[TA125] = _series(120, 200.0, 0.2)
    return h


def test_benchmark_math_from_stored_history(hist: FakeHistory) -> None:
    made = utcnow().date() - timedelta(days=40)
    ended = utcnow().date() - timedelta(days=10)
    out = benchmark_returns_for(hist, made, ended)
    assert out.missing == {}
    assert out.returns[GSPC] == pytest.approx(((1.001**30) - 1) * 100, abs=1e-3)
    assert out.returns[TA125] == pytest.approx(((1.002**30) - 1) * 100, abs=1e-3)


def test_a_weekend_start_uses_the_last_close_before_it() -> None:
    h = FakeHistory()
    idx = pd.to_datetime(["2026-09-04", "2026-09-07", "2026-09-30"])
    h.frames[GSPC] = pd.DataFrame({"Close": [100.0, 102.0, 110.0]}, index=idx)
    h.frames[TA125] = h.frames[GSPC]
    out = benchmark_returns_for(
        h, pd.Timestamp("2026-09-06").date(), pd.Timestamp("2026-09-30").date()
    )
    assert out.returns[GSPC] == pytest.approx(10.0)  # 06 Sep (Sunday) -> close of Friday the 4th


def test_missing_benchmark_data_is_left_out_with_a_reason_never_zero(hist: FakeHistory) -> None:
    made = utcnow().date() - timedelta(days=40)
    ended = utcnow().date() - timedelta(days=10)
    del hist.frames[TA125]  # no history at all
    hist.frames[GSPC] = _series(20, 100.0, 0.1)  # starts after the call was made
    out = benchmark_returns_for(hist, made, ended)
    assert out.returns == {}
    assert out.missing[TA125] == "no price history available"
    assert "no close within" in out.missing[GSPC]


def test_a_failing_provider_is_a_reason_not_an_error(hist: FakeHistory) -> None:
    class Boom:
        def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
            raise RuntimeError("down")

    out = benchmark_returns_for(Boom(), utcnow().date() - timedelta(days=9), utcnow().date())
    assert out.returns == {} and "RuntimeError" in out.missing[GSPC]


def test_resolution_stores_returns_once_and_stays_append_only(
    db: Session, hist: FakeHistory
) -> None:
    call = put(db, age_days=40)
    assert call.id is not None
    done, bench = resolve_call_with_benchmarks(
        db, call.id, hist, outcome="horizon_end", outcome_price=110.0,
        resolved_at=call.created_at + timedelta(days=30),
    )  # fmt: skip
    assert done.benchmark_returns == bench.returns and set(bench.returns) == {GSPC, TA125}
    assert done.outcome_price == 110.0 and done.entry == 100.0  # nothing else changed
    with pytest.raises(AppendOnlyError, match="final"):
        resolve_call_with_benchmarks(db, call.id, hist, outcome="stop_hit", outcome_price=90.0)
    again = db.get(PaperCall, call.id)
    assert again is not None and again.benchmark_returns == bench.returns


def test_no_benchmark_data_resolves_with_null_returns(db: Session) -> None:
    call = put(db, age_days=40)
    assert call.id is not None
    done, bench = resolve_call_with_benchmarks(
        db, call.id, FakeHistory(), outcome="horizon_end", outcome_price=105.0,
        resolved_at=call.created_at + timedelta(days=30),
    )  # fmt: skip
    assert done.benchmark_returns is None and set(bench.missing) == {GSPC, TA125}
    assert done.resolved_at is not None


def test_the_track_record_page_shows_aggregates_from_resolved_benchmarks(
    signup: SignupFn, hist: FakeHistory
) -> None:
    c = signup()
    with new_session() as db:
        for sym, price in (("AAA", 110.0), ("BBB", 101.0)):
            call = put(db, age_days=40, symbol=sym)
            assert call.id is not None
            resolve_call_with_benchmarks(
                db, call.id, hist, outcome="horizon_end", outcome_price=price,
                resolved_at=call.created_at + timedelta(days=30),
            )  # fmt: skip
    body = c.get("/api/track-record").json()
    assert body["state"] == "ready" and body["count"] == 2
    row = next(r for r in body["rows"] if r["symbol"] == "AAA")
    assert {b["name"] for b in row["benchmarks"]} == {GSPC, TA125}
    assert all(b["excess_pct"] is not None for b in row["benchmarks"])
