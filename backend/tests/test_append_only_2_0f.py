"""Append-only paper_call / backtest_run on SQLite and Postgres (2.0-F item 7).

Two layers are tested separately: the database triggers (the migration; raw DBAPI cursor, so no
SQLAlchemy event can help) and the SQLAlchemy-level guard (Core, raw `text()` SQL, bulk and ORM
deletes on an engine whose tables have no triggers at all)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, text, update
from sqlmodel import Session, SQLModel

from app.config import Settings
from app.db import make_engine, run_migrations
from app.launchgate import weights_fingerprint
from app.models import BacktestRun, PaperCall, User
from app.models.guards import AppendOnlyError
from app.papertrading import paper_metrics, record_backtest, record_call, resolve_call
from app.signals.base import Explanation
from app.timeutil import utcnow
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)

S = Settings(_env_file=None)
WH = weights_fingerprint(S.signal_weights)


@pytest.fixture(params=["sqlite", "postgres"])
def migrated(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    """A database built by the migrations, so the triggers exist."""
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'ao.db'}")
    else:
        eng = make_engine(request.getfixturevalue("pg_url"))
    run_migrations(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def untriggered(tmp_path: Path) -> Iterator[Engine]:
    """SQLite built by `create_all`: no triggers, only the SQLAlchemy-level guard protects it."""
    eng = make_engine(f"sqlite:///{tmp_path / 'plain.db'}")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def seed(eng: Engine, *, user: bool = False) -> tuple[int, int, int | None]:
    """(global call id, backtest id, user id) inserted through the repository functions."""
    with Session(eng) as db:
        uid = None
        if user:
            u = User(email="owner@example.com", password_hash="x")
            db.add(u)
            db.commit()
            uid = u.id
            record_call(
                db, symbol="MSFT", side="buy", horizon="1m", entry=10.0, stop=None, targets=[11.0],
                explanation=Explanation(summary="mine"), model_hash="m", prompt_hash="p",
                weights_hash=WH, user_id=uid,
            )  # fmt: skip
        call = record_call(
            db, symbol="AAPL", side="buy", horizon="1m", entry=100.0, stop=92.0, targets=[110.0],
            explanation=Explanation(summary="g"), model_hash="m", prompt_hash="p",
            weights_hash=WH, is_global=True,
        )  # fmt: skip
        run = record_backtest(
            db, weights_hash=WH, period_start=date(2020, 1, 1), period_end=date(2025, 1, 1),
            metrics={"sharpe": 1.0}, passed=True,
        )  # fmt: skip
        assert call.id is not None and run.id is not None
        return call.id, run.id, uid


def raw_dbapi(eng: Engine, sql: str) -> None:
    """Straight to the driver: SQLAlchemy events are not involved, so only a trigger can refuse."""
    conn = eng.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- the triggers (both databases)
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE paper_call SET entry = 1",
        "UPDATE paper_call SET symbol = 'X'",
        "UPDATE paper_call SET weights_hash = 'other'",
        "UPDATE paper_call SET is_global = false",
        "UPDATE backtest_run SET passed = false",
        "UPDATE backtest_run SET metrics = '{}'",
        "DELETE FROM paper_call",
        "DELETE FROM backtest_run",
        "DELETE FROM paper_call WHERE id > 0",
    ],
)
def test_the_database_refuses_update_and_delete_by_trigger(migrated: Engine, sql: str) -> None:
    seed(migrated)
    with pytest.raises(Exception, match=r"append-only|immutable|final|cannot be deleted"):
        raw_dbapi(migrated, sql)
    with Session(migrated) as db:
        assert db.get(PaperCall, 1) is not None and db.get(BacktestRun, 1) is not None


def test_the_trigger_allows_one_resolution_and_nothing_after(migrated: Engine) -> None:
    cid, _, _ = seed(migrated)
    raw_dbapi(
        migrated,
        "UPDATE paper_call SET resolved_at = CURRENT_TIMESTAMP, outcome = 'horizon_end', "
        f"outcome_price = 101 WHERE id = {cid}",
    )
    with Session(migrated) as db:
        assert db.get(PaperCall, cid).outcome == "horizon_end"  # type: ignore[union-attr]
    with pytest.raises(Exception, match=r"final|append-only"):
        raw_dbapi(migrated, f"UPDATE paper_call SET outcome = 'stop_hit' WHERE id = {cid}")


def test_deleting_the_owner_still_removes_their_private_calls_but_not_global_ones(
    migrated: Engine,
) -> None:
    """Account deletion (a privacy duty) cascades to the user's own paper calls."""
    cid, _, uid = seed(migrated, user=True)
    if migrated.dialect.name == "postgresql":  # SQLite triggers cannot look at `user` (see 0007)
        with pytest.raises(Exception, match=r"append-only|cannot be deleted"):
            raw_dbapi(migrated, "DELETE FROM paper_call WHERE user_id IS NOT NULL")
    with Session(migrated) as db:
        db.delete(db.get(User, uid))
        db.commit()
        left = db.exec(text("SELECT id FROM paper_call")).all()  # type: ignore[call-overload]
    assert [r[0] for r in left] == [cid]  # the global call survived


# ---------------------------------------------------------------- the SQLAlchemy-level guard
def test_core_update_is_refused_without_triggers(untriggered: Engine) -> None:
    seed(untriggered)
    with untriggered.begin() as conn, pytest.raises(AppendOnlyError):
        conn.execute(update(PaperCall.__table__).values(entry=1.0))  # type: ignore[attr-defined]
    with untriggered.begin() as conn, pytest.raises(AppendOnlyError):
        conn.execute(update(BacktestRun.__table__).values(passed=False))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE paper_call SET entry = 1",
        "update  \"paper_call\" set created_at = '2020-01-01'",
        "UPDATE paper_call SET outcome = 'x', entry = 1",  # one resolution field is not enough
        "UPDATE backtest_run SET passed = 0",
        "DELETE FROM paper_call",
        "delete from backtest_run where id = 1",
        "INSERT OR REPLACE INTO paper_call (id, symbol) VALUES (1, 'X')",
        "REPLACE INTO backtest_run (id) VALUES (1)",
        "INSERT INTO backtest_run (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET passed = 0",
        "/* hi */ UPDATE paper_call SET entry = 2",
        "WITH x AS (SELECT 1) UPDATE paper_call SET entry = 2",
    ],
)
def test_raw_sql_is_refused_without_triggers(untriggered: Engine, sql: str) -> None:
    seed(untriggered)
    with pytest.raises(AppendOnlyError), untriggered.begin() as conn:
        conn.execute(text(sql))
    with Session(untriggered) as db:
        assert db.get(PaperCall, 1).entry == 100.0  # type: ignore[union-attr]
        assert db.get(BacktestRun, 1).passed is True  # type: ignore[union-attr]


def test_orm_and_bulk_deletes_are_refused_without_triggers(untriggered: Engine) -> None:
    cid, _, _ = seed(untriggered)
    with Session(untriggered) as db:
        db.delete(db.get(PaperCall, cid))
        with pytest.raises(AppendOnlyError):
            db.commit()
        db.rollback()
        with pytest.raises(AppendOnlyError):
            db.exec(delete(BacktestRun))  # type: ignore[call-overload]
        db.rollback()
    with untriggered.begin() as conn, pytest.raises(AppendOnlyError):
        conn.execute(delete(PaperCall.__table__))  # type: ignore[attr-defined]


def test_the_resolution_statement_is_still_allowed_by_the_guard(untriggered: Engine) -> None:
    cid, _, _ = seed(untriggered)
    with Session(untriggered) as db:
        resolve_call(db, cid, outcome="horizon_end", outcome_price=105.0)
        with pytest.raises(AppendOnlyError, match="final"):
            resolve_call(db, cid, outcome="stop_hit", outcome_price=90.0)


def test_account_deletion_is_not_blocked_by_the_guard(untriggered: Engine) -> None:
    _, _, uid = seed(untriggered, user=True)
    with Session(untriggered) as db:
        db.delete(db.get(User, uid))
        db.commit()


# ---------------------------------------------------------------- no backdating
def test_record_call_has_no_created_at_argument(untriggered: Engine) -> None:
    with Session(untriggered) as db, pytest.raises(TypeError):
        record_call(
            db, symbol="A", side="buy", horizon="1m", entry=1.0, stop=None, targets=[],
            explanation=Explanation(summary="x"), model_hash="m", prompt_hash="p",
            weights_hash=WH, is_global=True, created_at=utcnow() - timedelta(days=90),
        )  # type: ignore[call-arg]  # fmt: skip


def test_one_backdated_row_cannot_be_made_through_the_repository(untriggered: Engine) -> None:
    seed(untriggered)
    with Session(untriggered) as db:
        m = paper_metrics(db, S)
    assert m.weeks_running < 0.01


def test_resolved_at_must_lie_between_the_call_and_now(untriggered: Engine) -> None:
    cid, _, _ = seed(untriggered)
    with Session(untriggered) as db:
        with pytest.raises(ValueError):
            resolve_call(
                db, cid, outcome="horizon_end", outcome_price=1.0,
                resolved_at=utcnow() - timedelta(days=400),
            )  # fmt: skip
        with pytest.raises(ValueError):
            resolve_call(
                db, cid, outcome="horizon_end", outcome_price=1.0,
                resolved_at=utcnow() + timedelta(days=2),
            )  # fmt: skip
