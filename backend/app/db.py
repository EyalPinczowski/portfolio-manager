"""Database engine, migrations and session helpers (SQLite dev, Postgres cloud)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Engine, event, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Point bare Postgres URLs (what Supabase / Render hand out) at the psycopg 3 driver."""
    for bare in ("postgresql://", "postgres://"):
        if url.startswith(bare):
            return "postgresql+psycopg://" + url[len(bare) :]
    return url


def is_postgres(url: str) -> bool:
    return normalize_database_url(url).startswith("postgresql")


def make_engine(url: str, settings: Settings | None = None) -> Engine:
    """SQLite gets WAL + busy_timeout + foreign keys. Postgres gets a small pre-pinged pool and,
    optionally, no prepared statements (Supabase's transaction pooler). TLS is chosen in the URL
    (`?sslmode=require`)."""
    url = normalize_database_url(url)
    s = settings or get_settings()
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
    elif url.startswith("postgresql"):
        kwargs.update(
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_pre_ping=s.db_pool_pre_ping,
            pool_recycle=s.db_pool_recycle_seconds,
            pool_timeout=s.db_pool_timeout_seconds,
        )
        if "psycopg" in url.split("://", 1)[0]:
            kwargs["connect_args"] = {"prepare_threshold": s.database_prepare_threshold}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


def init_db(engine: Engine | None = None) -> None:
    """`create_all`: for tests and throwaway databases only. The app uses `prepare_database`."""
    import app.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine or get_engine())


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


_REVISION_NUMBER = re.compile(r"^(\d+)(?:_|$)")


def revision_number(revision: str) -> int | None:
    """The numeric prefix of a revision id (`0003` or `0003_add_x` -> 3), or None for other styles."""
    m = _REVISION_NUMBER.match(revision)
    return int(m.group(1)) if m else None


def is_known_revision(revision: str) -> bool:
    script = ScriptDirectory.from_config(alembic_config())
    try:
        return script.get_revision(revision) is not None
    except CommandError:
        return False


def ahead_verdict(current: str, settings: Settings | None = None) -> tuple[bool, str]:
    """For a database at `current`, a revision this build does not know: may this build run on it?

    Yes only when the newer schema is still expand-only for us (see docs/migrations.md):
    - the revision is listed in `db_accepted_ahead_revisions`, or
    - it is numbered, at most `db_ahead_max_revisions` above this build's head, and not a
      `contract` revision (those drop or rename things an older build still uses).
    Returns (accepted, reason).
    """
    s = settings or get_settings()
    if current in s.db_accepted_ahead_revisions:
        return True, f"revision {current} is in DB_ACCEPTED_AHEAD_REVISIONS"
    cur_n, head_n = revision_number(current), revision_number(head_revision())
    if cur_n is None or head_n is None:
        return False, f"revision {current} is unknown and has no numeric prefix to compare"
    if cur_n <= head_n:
        return False, f"revision {current} is unknown to this build (not a later revision)"
    if "contract" in current.lower():
        return False, f"revision {current} is a contract migration: older code may not work on it"
    if cur_n - head_n > s.db_ahead_max_revisions:
        return False, (
            f"database is {cur_n - head_n} revisions ahead (at most {s.db_ahead_max_revisions} "
            "are accepted)"
        )
    return True, f"database is {cur_n - head_n} expand-only revision(s) ahead of this build"


def check_schema(engine: Engine, settings: Settings | None = None) -> str:
    """Compare the database with this build. Returns "at_head", "behind", "empty" or "ahead_ok"
    (a newer, expand-only schema this build can run on); raises RuntimeError for an unsafe
    "ahead" state."""
    current = current_revision(engine)
    if current is None:
        return "empty"
    if current == head_revision():
        return "at_head"
    if is_known_revision(current):
        return "behind"
    accepted, reason = ahead_verdict(current, settings)
    if not accepted:
        raise RuntimeError(
            f"Database schema is at {current}, which this build ({head_revision()}) does not "
            f"know: {reason}. Deploy the newer release again, or restore a backup."
        )
    log.warning(
        "database is ahead of this build (%s vs %s): %s. Running anyway; roll forward soon.",
        current,
        head_revision(),
        reason,
    )
    return "ahead_ok"


def _run_alembic(engine: Engine, action: str, revision: str) -> None:
    cfg = alembic_config()
    with engine.connect() as conn:  # env.py commits (and, on SQLite, handles the FK pragma)
        cfg.attributes["connection"] = conn
        getattr(command, action)(cfg, revision)
        conn.commit()


def run_migrations(
    engine: Engine | None = None, revision: str = "head", settings: Settings | None = None
) -> None:
    """`alembic upgrade <revision>` on `engine` (default: the app engine)."""
    import app.models  # noqa: F401

    eng = engine or get_engine()
    if check_schema(eng, settings) == "ahead_ok":
        return  # a newer expand-only schema: this (older) build must not try to migrate it
    if current_revision(eng) is None and inspect(eng).has_table("user"):
        raise RuntimeError(
            "The database has tables but no alembic_version (it was created by create_all). "
            "Delete a throwaway dev database, or run `alembic stamp head` if it matches the "
            "current models."
        )
    _run_alembic(eng, "upgrade", revision)


def downgrade_migrations(engine: Engine, revision: str = "base") -> None:
    _run_alembic(engine, "downgrade", revision)


def prepare_database(engine: Engine | None = None, settings: Settings | None = None) -> None:
    """Startup schema step for the API, the scheduler and the CLI. Never calls `create_all`.

    - `auto_migrate` on (dev default): upgrade to head.
    - off in production: refuse to start unless the schema is already at head.
    - off elsewhere (tests create tables themselves): do nothing.
    """
    s = settings or get_settings()
    eng = engine or get_engine()
    if s.auto_migrate:
        run_migrations(eng, settings=s)
    elif s.env == "production":
        current = current_revision(eng)
        if check_schema(eng, s) == "ahead_ok":
            return
        if current != head_revision():
            raise RuntimeError(
                f"Database schema is at {current or 'nothing'}, the app needs {head_revision()}. "
                "Run `alembic upgrade head` (or set AUTO_MIGRATE=true) before starting."
            )


def get_db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def new_session() -> Session:
    """A standalone session for background tasks and the scheduler (caller closes it)."""
    return Session(get_engine())
