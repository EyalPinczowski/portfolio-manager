"""Rollback safety: an older build keeps running on a database that a newer expand-only release
already migrated; unsafe "ahead" states are refused with a clear message (docs/migrations.md)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.config import Settings
from app.db import (
    ahead_verdict,
    check_schema,
    current_revision,
    head_revision,
    make_engine,
    prepare_database,
    revision_number,
    run_migrations,
)
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)

PROD = {"env": "production", "auto_migrate": False, "secret_key": "x" * 40}


@pytest.fixture(params=["sqlite", "postgres"])
def migrated(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'r.db'}")
    else:
        eng = make_engine(request.getfixturevalue("pg_url"))
    run_migrations(eng)
    yield eng
    eng.dispose()


def _next_id(suffix: str, ahead: int = 1) -> str:
    n = revision_number(head_revision())
    assert n is not None
    return f"{n + ahead:04d}_{suffix}"


def stamp(engine: Engine, revision: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})


def test_revision_numbers() -> None:
    assert revision_number("0003_add_thing") == 3
    assert revision_number("0007") == 7
    assert revision_number("abc123") is None
    assert revision_number(head_revision()) is not None  # our ids are numbered


def test_one_expand_revision_ahead_is_accepted_in_production(
    migrated: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    ahead = _next_id("add_paper_calls")
    stamp(migrated, ahead)
    with caplog.at_level("WARNING", logger="app.db"):
        prepare_database(migrated, Settings(_env_file=None, **PROD))  # type: ignore[arg-type]
    assert "ahead of this build" in caplog.text
    assert current_revision(migrated) == ahead  # untouched: an old build never downgrades the DB


def test_migrate_on_start_does_not_crash_or_touch_a_newer_database(migrated: Engine) -> None:
    """`python -m app.cli migrate` runs in the image CMD: after a rollback it must be a no-op."""
    ahead = _next_id("add_llm_usage")
    stamp(migrated, ahead)
    run_migrations(migrated)
    prepare_database(migrated, Settings(_env_file=None, env="dev", auto_migrate=True))
    assert current_revision(migrated) == ahead


@pytest.mark.parametrize(
    ("revision", "needle"),
    [
        ("{n1}_contract_drop_old_column", "contract"),
        ("{n2}_add_two_more", "2 revisions ahead"),
        ("zzz_unknown", "numeric prefix"),
        ("0000_older_unknown", "not a later revision"),
    ],
)
def test_unsafe_ahead_states_are_refused_with_a_reason(
    migrated: Engine, revision: str, needle: str
) -> None:
    stamp(migrated, revision.format(n1=_next_id("x")[:4], n2=_next_id("x", 2)[:4]))
    with pytest.raises(RuntimeError, match=needle):
        prepare_database(migrated, Settings(_env_file=None, **PROD))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match=needle):
        run_migrations(migrated)


def test_the_tolerance_and_an_explicit_accept_list_are_config(migrated: Engine) -> None:
    two = _next_id("add_two_more", 2)
    stamp(migrated, two)
    wide = Settings(_env_file=None, db_ahead_max_revisions=2, **PROD)  # type: ignore[arg-type]
    prepare_database(migrated, wide)
    narrow = Settings(_env_file=None, **PROD)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        prepare_database(migrated, narrow)
    listed = Settings(_env_file=None, db_accepted_ahead_revisions=[two], **PROD)  # type: ignore[arg-type]
    prepare_database(migrated, listed)
    contract = _next_id("contract_drop_x")
    stamp(migrated, contract)
    listed_contract = Settings(  # an operator may still override on purpose
        _env_file=None,
        db_accepted_ahead_revisions=[contract],
        **PROD,  # type: ignore[arg-type]
    )
    prepare_database(migrated, listed_contract)


def test_check_schema_states(migrated: Engine) -> None:
    assert check_schema(migrated) == "at_head"
    stamp(migrated, _next_id("expand"))
    assert check_schema(migrated) == "ahead_ok"


def test_behind_and_empty_still_behave_as_before(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'e.db'}")
    assert check_schema(eng) == "empty"
    run_migrations(eng, "0001")
    assert check_schema(eng) == "behind"
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        prepare_database(eng, Settings(_env_file=None, **PROD))  # type: ignore[arg-type]


def test_ahead_verdict_explains_itself() -> None:
    ok, why = ahead_verdict(_next_id("expand"), Settings(_env_file=None))
    assert ok and "expand-only" in why
