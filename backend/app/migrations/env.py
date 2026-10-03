"""Alembic environment.

The connection comes from, in order: a connection handed in by `app.db.run_migrations`
(`config.attributes["connection"]`), then `sqlalchemy.url` if set, then `Settings.database_url`.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, text
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers every table on SQLModel.metadata)
from app.config import get_settings
from app.db import make_engine

config = context.config
target_metadata = SQLModel.metadata

# Postgres: two instances that boot together must not run the same migration at once.
_MIGRATION_LOCK_ID = 0x504D4D49  # "PMMI"


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )


def _run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _MIGRATION_LOCK_ID})
        context.run_migrations()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _run(supplied)
        return
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            _run(connection)
            connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
