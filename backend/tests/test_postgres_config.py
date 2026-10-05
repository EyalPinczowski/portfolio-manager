"""Postgres readiness: pooler-friendly engine settings and URL forms (no server needed, except
the last tests, which use one when TEST_POSTGRES_URL / DATABASE_URL points at Postgres)."""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import make_engine
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)

# The SQLite CI job installs only the dev extra; the Postgres job (and prod image) has the driver.
needs_psycopg = pytest.mark.skipif(
    importlib.util.find_spec("psycopg") is None, reason="psycopg (postgres extra) not installed"
)


def _s(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def test_pool_defaults_fit_a_512mb_host() -> None:
    s = _s()
    assert s.db_pool_size + s.db_max_overflow <= 5  # small connection limit
    assert s.db_pool_pre_ping is True
    assert 0 < s.db_pool_recycle_seconds <= 3600  # poolers drop idle connections
    assert s.database_prepare_threshold == 5


@needs_psycopg
def test_postgres_engine_uses_the_configured_pool() -> None:
    eng = make_engine(
        "postgresql+psycopg://u:p@h:5432/d",
        _s(db_pool_size=2, db_max_overflow=1, db_pool_recycle_seconds=600),
    )
    pool = eng.pool
    assert pool.size() == 2  # type: ignore[attr-defined]
    assert pool._max_overflow == 1  # type: ignore[attr-defined]
    assert pool._pre_ping is True  # type: ignore[attr-defined]
    assert pool._recycle == 600  # type: ignore[attr-defined]


def _connect_kwargs(engine: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The keyword arguments the engine would hand to `psycopg.connect` (no server needed)."""
    import psycopg

    seen: dict[str, Any] = {}

    def fake_connect(*_a: Any, **kw: Any) -> Any:
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    with pytest.raises(Exception, match="stop"):
        engine.connect()
    return seen


@needs_psycopg
def test_prepared_statements_can_be_disabled_for_the_transaction_pooler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql://postgres.ref:pw@aws-0-eu.pooler.supabase.com:6543/postgres?sslmode=require"
    eng = make_engine(url, _s(database_prepare_threshold=None))
    kwargs = _connect_kwargs(eng, monkeypatch)
    assert kwargs["prepare_threshold"] is None
    assert kwargs["port"] == 6543
    assert kwargs["sslmode"] == "require"  # TLS comes from the URL
    assert eng.url.drivername == "postgresql+psycopg"  # bare `postgresql://` gets the right driver


@needs_psycopg
def test_prepare_threshold_default_keeps_psycopg_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = make_engine("postgresql+psycopg://u:p@h/d", _s())
    kwargs = _connect_kwargs(eng, monkeypatch)
    assert kwargs["prepare_threshold"] == 5


def test_none_in_the_environment_means_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_PREPARE_THRESHOLD", "none")
    assert Settings(_env_file=None).database_prepare_threshold is None


def test_sqlite_engine_ignores_the_postgres_knobs(tmp_path: Any) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'x.db'}", _s(database_prepare_threshold=None))
    with eng.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_real_postgres_connection_honours_the_settings(pg_url: str) -> None:  # noqa: F811
    eng = make_engine(pg_url, _s(database_prepare_threshold=None, db_pool_size=1))
    with eng.connect() as conn:
        raw: Any = conn.connection.dbapi_connection
        assert raw.prepare_threshold is None
        assert conn.execute(text("SELECT 1")).scalar() == 1
    eng.dispose()


def test_json_and_datetime_columns_round_trip_on_this_database(db: Any) -> None:
    """The suite's own database (SQLite or Postgres): JSON dicts/lists and naive datetimes."""
    from datetime import datetime

    from app.models import HoldingsSnapshot, Portfolio, User

    u = User(email="j@x.co", password_hash="h")
    db.add(u)
    db.flush()
    p = Portfolio(owner_id=u.id, name="P", risk_filter={"preset": "balanced", "max": [1, 2.5]})
    db.add(p)
    db.flush()
    snap = HoldingsSnapshot(
        portfolio_id=p.id, taken_at=datetime(2026, 1, 2, 3, 4, 5), rows=[{"symbol": "א", "q": 1.5}]
    )
    db.add(snap)
    db.commit()
    db.expire_all()
    got = db.get(HoldingsSnapshot, snap.id)
    assert got.rows == [{"symbol": "א", "q": 1.5}]
    assert got.taken_at == datetime(2026, 1, 2, 3, 4, 5)
    assert db.get(Portfolio, p.id).risk_filter == {"preset": "balanced", "max": [1, 2.5]}
