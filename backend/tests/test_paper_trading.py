"""Paper-trading tables: append-only enforcement, and the launch gate reading them."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from app.config import Settings
from app.db import make_engine, run_migrations
from app.launchgate import LaunchGate, get_launch_gate, weights_fingerprint
from app.models import BacktestRun, PaperCall, User
from app.models.guards import AppendOnlyError
from app.papertrading import (
    DbBacktests,
    DbPaper,
    paper_metrics,
    record_backtest,
    record_call,
    resolve_call,
)
from app.signals.base import Explanation
from app.timeutil import utcnow
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)

S = Settings(_env_file=None)
WH = weights_fingerprint(S.signal_weights)


def make_call(db: Session, **kw: object) -> PaperCall:
    args: dict[str, object] = {
        "symbol": "AAPL",
        "side": "buy",
        "horizon": "1m",
        "entry": 100.0,
        "stop": 92.0,
        "targets": [110.0, 120.0],
        "explanation": Explanation(summary="test call"),
        "model_hash": "m1",
        "prompt_hash": "p1",
        "weights_hash": WH,
        "is_global": True,
    }
    args.update(kw)
    created = args.pop("created_at", None)
    if created is None:
        return record_call(db, **args)  # type: ignore[arg-type]
    # The repository cannot backdate (2.0-F): tests that need an old call insert the row directly.
    expl = args.pop("explanation")
    row = PaperCall(**args, explanation=expl.model_dump(mode="json"), created_at=created)  # type: ignore[attr-defined,arg-type]
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- append-only
def test_a_call_is_stored_with_its_explanation_and_hashes(db: Session) -> None:
    c = make_call(db)
    row = db.get(PaperCall, c.id)
    assert row is not None and row.is_global and row.user_id is None
    assert row.explanation["version"] == 1 and row.targets == [110.0, 120.0]
    assert (row.model_hash, row.prompt_hash, row.weights_hash) == ("m1", "p1", WH)
    assert row.resolved_at is None and row.outcome is None


@pytest.mark.parametrize(
    "field", ["entry", "stop", "symbol", "side", "horizon", "model_hash", "created_at", "is_global"]
)
def test_orm_update_of_a_non_resolution_field_is_refused(db: Session, field: str) -> None:
    c = make_call(db)
    new = {
        "entry": 1.0,
        "stop": 1.0,
        "symbol": "MSFT",
        "side": "sell",
        "horizon": "1w",
        "model_hash": "x",
        "created_at": utcnow() - timedelta(days=90),
        "is_global": False,
    }[field]
    setattr(c, field, new)
    db.add(c)
    with pytest.raises(AppendOnlyError, match="append-only"):
        db.commit()
    db.rollback()
    assert getattr(db.get(PaperCall, c.id), field) != new


def test_mutating_the_targets_or_explanation_is_refused(db: Session) -> None:
    c = make_call(db)
    c.targets = [999.0]
    c.explanation = {"summary": "rewritten history"}
    db.add(c)
    with pytest.raises(AppendOnlyError):
        db.commit()
    db.rollback()
    fresh = db.get(PaperCall, c.id)
    assert fresh is not None and fresh.targets == [110.0, 120.0]


def test_bulk_update_statement_is_refused(db: Session) -> None:
    c = make_call(db)
    with pytest.raises(AppendOnlyError, match="bulk UPDATE"):
        db.execute(update(PaperCall).where(PaperCall.id == c.id).values(entry=1.0))  # type: ignore[arg-type]
    db.rollback()
    with pytest.raises(AppendOnlyError):  # even a resolution field: use resolve_call
        db.execute(update(PaperCall).values(outcome="target_hit"))
    db.rollback()


def test_resolution_fields_can_be_set_once(db: Session) -> None:
    c = make_call(db)
    assert c.id is not None
    done = resolve_call(
        db, c.id, outcome="target_hit", outcome_price=111.0, benchmark_returns={"^GSPC": 1.0}
    )
    assert done.resolved_at is not None and done.outcome == "target_hit"
    assert done.outcome_price == 111.0 and done.benchmark_returns == {"^GSPC": 1.0}
    assert done.entry == 100.0 and done.targets == [110.0, 120.0]  # nothing else moved
    with pytest.raises(AppendOnlyError, match="already resolved"):
        resolve_call(db, c.id, outcome="stop_hit", outcome_price=90.0)
    done.outcome = "stop_hit"  # the ORM guard also stops a manual second resolution
    db.add(done)
    with pytest.raises(AppendOnlyError, match="final"):
        db.commit()
    db.rollback()
    assert db.get(PaperCall, c.id).outcome == "target_hit"  # type: ignore[union-attr]


def test_backtest_runs_are_immutable(db: Session) -> None:
    run = record_backtest(
        db,
        weights_hash=WH,
        period_start=date(2020, 1, 1),
        period_end=date(2025, 1, 1),
        metrics={"sharpe": 1.1},
        passed=True,
    )
    run.passed = False
    db.add(run)
    with pytest.raises(AppendOnlyError, match="immutable"):
        db.commit()
    db.rollback()
    assert db.get(BacktestRun, run.id).passed is True  # type: ignore[union-attr]


def test_scope_and_input_validation(db: Session) -> None:
    user = User(email="a@example.com", password_hash="x")
    db.add(user)
    db.commit()
    assert user.id is not None
    assert make_call(db, is_global=False, user_id=user.id).user_id == user.id
    for bad in (
        {"is_global": False, "user_id": None},
        {"is_global": True, "user_id": user.id},
        {"entry": float("nan")},
        {"entry": -1.0},
        {"stop": float("inf")},
        {"targets": [float("nan")]},
        {"side": "hold"},
        {"horizon": "2d"},
        {"weights_hash": " "},
    ):
        with pytest.raises(ValueError):
            make_call(db, **bad)
    assert len(db.exec(select(PaperCall)).all()) == 1


def test_the_database_check_constraint_blocks_a_scopeless_row(db: Session) -> None:
    db.add(
        PaperCall(
            symbol="X",
            side="buy",
            horizon="1m",
            entry=1.0,
            model_hash="m",
            prompt_hash="p",
            weights_hash="w",
            is_global=False,
        )
    )
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_postgres_trigger_blocks_raw_sql_updates(pg_url: str) -> None:  # noqa: F811
    eng: Engine = make_engine(pg_url)
    run_migrations(eng)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO paper_call (created_at, is_global, symbol, side, horizon, entry,"
                    " targets, explanation, model_hash, prompt_hash, weights_hash)"
                    " VALUES (now(), true, 'AAPL', 'buy', '1m', 100, '[1]', '{}', 'm', 'p', 'w')"
                )
            )
        # since 2.0-F the SQLAlchemy guard refuses these before the trigger sees them; the trigger
        # itself is tested through a raw driver cursor in tests/test_append_only_2_0f.py
        with pytest.raises((DBAPIError, AppendOnlyError), match="append-only"), eng.begin() as conn:
            conn.execute(text("UPDATE paper_call SET entry = 1"))
        with pytest.raises((DBAPIError, AppendOnlyError), match="append-only"), eng.begin() as conn:
            conn.execute(text("UPDATE paper_call SET targets = '[2]'"))
        with eng.begin() as conn:  # a resolution is allowed once
            conn.execute(
                text(
                    "UPDATE paper_call SET resolved_at = now(), outcome = 'horizon_end', outcome_price = 101"
                )
            )
        with pytest.raises(DBAPIError, match="final"), eng.begin() as conn:
            conn.execute(text("UPDATE paper_call SET outcome = 'stop_hit'"))
    finally:
        eng.dispose()


# ---------------------------------------------------------------- the launch gate reads the tables
def reasons(db: Session) -> str:
    return " | ".join(LaunchGate(S, DbBacktests(S), DbPaper(S)).evaluate().reasons)


def test_empty_tables_close_the_gate_with_counts(db: Session) -> None:
    gate = LaunchGate(S, DbBacktests(S), DbPaper(S)).evaluate()
    assert gate.open is False
    text_ = " | ".join(gate.reasons)
    assert "No backtest" in text_
    assert "0.0 of 4 required weeks" in text_
    assert "Only 0 of 50 required calls are resolved at 1 month" in text_
    assert "no result against ^GSPC" in text_


def test_default_gate_dependency_reads_the_database(db: Session) -> None:
    assert get_launch_gate().evaluate().open is False
    assert any("0 of 50" in r for r in get_launch_gate().evaluate().reasons)


def test_backtest_for_other_weights_or_failed_keeps_it_closed(db: Session) -> None:
    kw = {"period_start": date(2020, 1, 1), "period_end": date(2025, 1, 1), "metrics": {}}
    record_backtest(db, weights_hash="other", passed=True, **kw)  # type: ignore[arg-type]
    assert "different weights" in reasons(db)
    record_backtest(db, weights_hash=WH, passed=False, **kw)  # type: ignore[arg-type]
    assert "did not pass" in reasons(db)
    record_backtest(db, weights_hash=WH, passed=True, **kw)  # type: ignore[arg-type]
    assert "backtest" not in reasons(db).lower()


def seed_resolved(db: Session, n: int, age_days: int, edge: float) -> None:
    created = utcnow() - timedelta(days=age_days)
    for _ in range(n):
        c = make_call(db, created_at=created)
        assert c.id is not None
        resolve_call(
            db,
            c.id,
            outcome="horizon_end",
            outcome_price=100.0 * (1 + (2.0 + edge) / 100),  # +2% + edge, benchmarks +2%
            benchmark_returns={"^GSPC": 2.0, "^TA125.TA": 2.0},
            resolved_at=min(created + timedelta(days=30), utcnow()),  # never in the future
        )


def test_gate_opens_only_when_the_tables_meet_every_threshold(db: Session) -> None:
    record_backtest(
        db, weights_hash=WH, period_start=date(2020, 1, 1), period_end=date(2025, 1, 1),
        metrics={}, passed=True,
    )  # fmt: skip
    seed_resolved(db, 49, age_days=40, edge=1.5)
    text_ = reasons(db)
    assert "Only 49 of 50 required calls are resolved at 1 month" in text_
    assert "weeks" not in text_  # 40 days is >= 4 weeks
    seed_resolved(db, 1, age_days=40, edge=1.5)
    gate = LaunchGate(S, DbBacktests(S), DbPaper(S)).evaluate()
    assert gate.open is True, gate.reasons
    m = paper_metrics(db, S)
    assert m.resolved_calls_1m == 50 and m.calls_recorded == 50
    assert m.excess_return_pct["^GSPC"] == pytest.approx(1.5)


def test_young_calls_unresolved_calls_and_losing_edge_do_not_count(db: Session) -> None:
    seed_resolved(db, 3, age_days=10, edge=1.0)  # resolved, but the 1-month window has not passed
    make_call(db, created_at=utcnow() - timedelta(days=60))  # still open
    m = paper_metrics(db, S)
    assert m.resolved_calls_1m == 0 and m.calls_recorded == 4
    assert m.weeks_running == pytest.approx(60 / 7, abs=0.01)
    seed_resolved(db, 2, age_days=45, edge=-3.0)
    m = paper_metrics(db, S)
    assert m.resolved_calls_1m == 2 and m.excess_return_pct["^GSPC"] == pytest.approx(-3.0)
    assert "does not beat ^GSPC" in reasons(db)


def test_an_error_outcome_is_a_critical_error_and_not_a_resolved_call(db: Session) -> None:
    c = make_call(db, created_at=utcnow() - timedelta(days=45))
    assert c.id is not None
    resolve_call(db, c.id, outcome="error", outcome_price=None)
    m = paper_metrics(db, S)
    assert m.critical_errors == 1 and m.resolved_calls_1m == 0
    assert "critical error" in reasons(db)


def test_per_user_calls_do_not_feed_the_gate(db: Session) -> None:
    user = User(email="u@example.com", password_hash="x")
    db.add(user)
    db.commit()
    make_call(db, is_global=False, user_id=user.id, created_at=utcnow() - timedelta(days=60))
    m = paper_metrics(db, S)
    assert m.calls_recorded == 0 and m.weeks_running == 0.0


def test_an_unreadable_database_keeps_the_gate_closed(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.db as appdb

    def boom() -> Session:
        raise RuntimeError("db down")

    monkeypatch.setattr(appdb, "new_session", boom)
    gate = LaunchGate(S, DbBacktests(S), DbPaper(S)).evaluate()
    assert gate.open is False
    assert any("not started" in r for r in gate.reasons)
