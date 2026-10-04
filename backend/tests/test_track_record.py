"""Members-only track record: login, scope (global only), ended calls only, benchmark math."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import new_session
from app.models import User
from app.timeutil import utcnow
from tests.test_paper_metrics_2_0f import put, resolve

SignupFn = Callable[..., TestClient]
BENCH = {"^GSPC": 2.0, "^TA125.TA": 4.0}


def _user_id(db: Session) -> int:
    from sqlmodel import select

    uid = db.exec(select(User.id)).first()
    assert uid is not None
    return uid


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/track-record").status_code == 401


def test_empty_state_says_paper_trading_has_not_started(signup: SignupFn) -> None:
    body = signup().get("/api/track-record").json()
    assert body["state"] == "not_started"
    assert "not started" in body["message"]
    assert body["count"] == 0 and body["rows"] == [] and body["resolved_at_1m"] == 0
    assert body["gate"]["open"] is False and body["gate"]["recorded"] == 0
    assert body["methodology"]


def test_only_ended_global_resolved_calls_are_shown(signup: SignupFn) -> None:
    c = signup()
    with new_session() as db:
        uid = _user_id(db)
        done = put(db, age_days=40, symbol="AAPL", horizon="1m")
        resolve(db, done, 110.0, BENCH, 30)
        open_ended = put(db, age_days=40, symbol="MSFT", horizon="1m")  # ended, no resolution
        assert open_ended.id is not None
        running = put(db, age_days=5, symbol="NVDA", horizon="1m")  # horizon still running
        resolve(db, running, 120.0, BENCH, 2)
        # a user's own paper call is never part of the global record
        put(db, age_days=40, symbol="TSLA", horizon="1m", is_global=False, user_id=uid)
    body = c.get("/api/track-record").json()
    assert body["state"] == "ready" and body["count"] == 1
    assert [r["symbol"] for r in body["rows"]] == ["AAPL"]
    assert body["awaiting_resolution"] == 1 and body["excluded_errors"] == 0
    assert "TSLA" not in str(body) and "NVDA" not in str(body)


def test_only_running_calls_gives_the_no_ended_state(signup: SignupFn) -> None:
    c = signup()
    with new_session() as db:
        put(db, age_days=3, horizon="3m")
    body = c.get("/api/track-record").json()
    assert body["state"] == "none_ended" and body["rows"] == []


def test_benchmark_math_and_aggregates(signup: SignupFn) -> None:
    c = signup()
    with new_session() as db:
        a = put(db, age_days=40, symbol="AAA", side="buy")
        resolve(db, a, 110.0, BENCH, 30)  # +10% vs 2 / 4 -> +8 / +6
        b = put(db, age_days=40, symbol="BBB", side="buy")
        resolve(db, b, 101.0, BENCH, 30)  # +1% -> -1 / -3
        s = put(db, age_days=40, symbol="CCC", side="sell")
        resolve(db, s, 95.0, BENCH, 30)  # -5% for a sell vs market +2 / +4 -> +7 / +9
    body = c.get("/api/track-record").json()
    assert body["count"] == 3
    rows = {r["symbol"]: r for r in body["rows"]}
    assert rows["AAA"]["return_pct"] == 10.0
    assert {b["name"]: b["excess_pct"] for b in rows["AAA"]["benchmarks"]} == {
        "^GSPC": 8.0,
        "^TA125.TA": 6.0,
    }
    assert {b["name"]: b["excess_pct"] for b in rows["CCC"]["benchmarks"]} == {
        "^GSPC": 7.0,
        "^TA125.TA": 9.0,
    }
    agg = {a["name"]: a for a in body["benchmarks"]}
    assert agg["^GSPC"]["count"] == 3 and agg["^GSPC"]["hit_rate_pct"] == 66.7
    assert agg["^GSPC"]["avg_excess_pct"] == round((8 - 1 + 7) / 3, 2)
    assert agg["^TA125.TA"]["avg_excess_pct"] == round((6 - 3 + 9) / 3, 2)
    assert body["resolved_at_1m"] == 3


def test_errors_are_counted_not_listed(signup: SignupFn) -> None:
    from app.papertrading import resolve_call

    c = signup()
    with new_session() as db:
        e = put(db, age_days=40)
        assert e.id is not None
        resolve_call(db, e.id, outcome="error", outcome_price=None, resolved_at=utcnow())
    body = c.get("/api/track-record").json()
    assert body["rows"] == [] and body["excluded_errors"] == 1


def test_no_call_text_or_direction_is_exposed(signup: SignupFn) -> None:
    c = signup()
    with new_session() as db:
        resolve(db, put(db, age_days=40), 110.0, BENCH, 30)
    row = c.get("/api/track-record").json()["rows"][0]
    assert set(row) == {
        "symbol", "made_on", "horizon", "horizon_ended_on", "resolved_on", "outcome",
        "return_pct", "active_weights", "benchmarks",
    }  # fmt: skip
