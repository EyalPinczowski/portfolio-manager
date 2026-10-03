"""Alembic: the migrated schema equals the SQLModel metadata, on SQLite and on Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, SQLModel

import app.models  # noqa: F401
from app.config import Settings
from app.db import (
    current_revision,
    downgrade_migrations,
    head_revision,
    init_db,
    make_engine,
    normalize_database_url,
    prepare_database,
    run_migrations,
)
from app.models import Holding, Invite, Portfolio, User
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)


@pytest.fixture(params=["sqlite", "postgres"])
def empty_engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    else:
        eng = make_engine(request.getfixturevalue("pg_url"))
    yield eng
    eng.dispose()


def _shape(engine: Engine) -> dict[str, dict[str, bool]]:
    """table -> {column: nullable}, ignoring the alembic bookkeeping table."""
    insp = inspect(engine)
    return {
        t: {c["name"]: bool(c["nullable"]) for c in insp.get_columns(t)}
        for t in insp.get_table_names()
        if t != "alembic_version"
    }


def _metadata_shape() -> dict[str, dict[str, bool]]:
    return {
        t.name: {c.name: bool(c.nullable) for c in t.columns}
        for t in SQLModel.metadata.sorted_tables
    }


def _fk_rules(engine: Engine) -> set[tuple[str, str, str, str | None]]:
    insp = inspect(engine)
    out: set[tuple[str, str, str, str | None]] = set()
    for t in insp.get_table_names():
        for fk in insp.get_foreign_keys(t):
            rule = fk["options"].get("ondelete")
            out.add((t, fk["constrained_columns"][0], fk["referred_table"], rule))
    return out


def test_upgrade_head_matches_the_sqlmodel_metadata(empty_engine: Engine) -> None:
    run_migrations(empty_engine)
    assert current_revision(empty_engine) == head_revision()
    assert _shape(empty_engine) == _metadata_shape()  # tables, columns, nullability
    with empty_engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), SQLModel.metadata
        )
    assert diff == []  # indexes, uniques, FKs and types too


def test_ondelete_rules_are_in_the_schema(empty_engine: Engine) -> None:
    run_migrations(empty_engine)
    rules = _fk_rules(empty_engine)
    for child in ("portfolio:owner_id", "session:user_id", "price_alert:user_id"):
        t, c = child.split(":")
        assert (t, c, "user", "CASCADE") in rules
    assert ("notification", "user_id", "user", "CASCADE") in rules
    assert ("invite", "created_by", "user", "CASCADE") in rules
    assert ("invite", "used_by", "user", "SET NULL") in rules
    for t in ("holding", "transaction", "portfolio_snapshot", "holdings_snapshot", "import_draft"):
        assert (t, "portfolio_id", "portfolio", "CASCADE") in rules


def test_initial_revision_has_the_security_columns(empty_engine: Engine) -> None:
    run_migrations(empty_engine)
    insp = inspect(empty_engine)
    assert "verified" in {c["name"] for c in insp.get_columns("security")}
    session_cols = {c["name"]: c for c in insp.get_columns("session")}
    assert {"id", "token_hash", "csrf_token", "last_seen_at"} <= set(session_cols)
    pk = insp.get_pk_constraint("session")["constrained_columns"]
    assert pk == ["id"]
    assert any(
        i["unique"] and i["column_names"] == ["token_hash"] for i in insp.get_indexes("session")
    )


def test_downgrade_to_base_and_up_again(empty_engine: Engine) -> None:
    run_migrations(empty_engine)
    downgrade_migrations(empty_engine, "base")
    assert _shape(empty_engine) == {}
    assert current_revision(empty_engine) is None
    run_migrations(empty_engine)  # still works afterwards
    assert _shape(empty_engine) == _metadata_shape()


def test_upgrade_twice_is_a_no_op(empty_engine: Engine) -> None:
    run_migrations(empty_engine)
    run_migrations(empty_engine)
    assert current_revision(empty_engine) == head_revision()


def _seed_tree(eng: Engine) -> tuple[int, int]:
    """alice (portfolio, holding) invited bob; returns (alice id, bob id)."""
    with Session(eng) as db:
        alice = User(email="a@x.co", password_hash="h")
        bob = User(email="b@x.co", password_hash="h")
        db.add(alice)
        db.add(bob)
        db.flush()
        assert alice.id is not None and bob.id is not None
        p = Portfolio(owner_id=alice.id, name="P", tracking_started_at=date(2026, 1, 1))
        db.add(p)
        db.flush()
        assert p.id is not None
        db.add(Holding(portfolio_id=p.id, symbol="AAPL", quantity=1))
        db.add(
            Invite(
                code="c1",
                created_by=alice.id,
                used_by=bob.id,
                expires_at=datetime(2030, 1, 1),
            )
        )
        db.commit()
        return alice.id, bob.id


def test_user_delete_cascades_on_a_migrated_database(empty_engine: Engine) -> None:
    run_migrations(empty_engine)
    alice, bob = _seed_tree(empty_engine)
    with empty_engine.begin() as conn:  # raw SQL: the database enforces it, not the ORM
        conn.execute(text('DELETE FROM "user" WHERE id = :i'), {"i": bob})
    with empty_engine.connect() as conn:
        used_by = conn.execute(text("SELECT used_by FROM invite WHERE code = 'c1'")).scalar()
    assert used_by is None  # SET NULL
    with empty_engine.begin() as conn:
        conn.execute(text('DELETE FROM "user" WHERE id = :i'), {"i": alice})
    with empty_engine.connect() as conn:
        for table in ("portfolio", "holding", "invite"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def test_auto_migrate_defaults_follow_the_environment() -> None:
    assert _settings(env="dev").auto_migrate is True
    assert _settings(env="production").auto_migrate is False
    assert _settings(env="production", auto_migrate=True).auto_migrate is True
    assert _settings(env="dev", auto_migrate=False).auto_migrate is False


def test_prepare_database_migrates_when_auto_migrate_is_on(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
    prepare_database(eng, _settings(env="dev"))
    assert current_revision(eng) == head_revision()


def test_production_does_not_create_or_migrate_silently(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    prod = _settings(env="production", auto_migrate=False, secret_key="x" * 40)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        prepare_database(eng, prod)
    assert inspect(eng).get_table_names() == []  # nothing was created behind our back
    run_migrations(eng)  # the release step
    prepare_database(eng, prod)  # now it starts


def test_dev_without_auto_migrate_does_nothing(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'n.db'}")
    prepare_database(eng, _settings(env="dev", auto_migrate=False))
    assert inspect(eng).get_table_names() == []


def test_a_create_all_database_is_not_migrated_blindly(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    init_db(eng)
    with pytest.raises(RuntimeError, match="alembic stamp head"):
        run_migrations(eng)


def test_database_url_forms_are_normalised() -> None:
    assert normalize_database_url("postgres://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
    assert normalize_database_url("postgresql://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalize_database_url("postgresql+psycopg://u@h/d") == "postgresql+psycopg://u@h/d"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_cli_migrate_upgrades_the_configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import main
    from app.config import get_settings
    from app.db import get_engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("ENV", "production")  # the release step works with auto-migrate off
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        assert main(["migrate"]) == 0
        assert current_revision(get_engine()) == head_revision()
        assert "up to date" in capsys.readouterr().out
    finally:
        get_engine().dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
