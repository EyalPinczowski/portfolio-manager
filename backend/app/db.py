"""Database engine, migrations and session helpers (SQLite dev, Postgres cloud)."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, event, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings


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


def _run_alembic(engine: Engine, action: str, revision: str) -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, action)(cfg, revision)


def run_migrations(engine: Engine | None = None, revision: str = "head") -> None:
    """`alembic upgrade <revision>` on `engine` (default: the app engine)."""
    import app.models  # noqa: F401

    eng = engine or get_engine()
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
        run_migrations(eng)
    elif s.env == "production":
        current = current_revision(eng)
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
