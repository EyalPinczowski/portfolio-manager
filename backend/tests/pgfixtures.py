"""Shared helpers for tests that need a real (empty) Postgres: an isolated schema per test.

Set DATABASE_URL (the whole suite on Postgres) or TEST_POSTGRES_URL (only the tests that ask for it)
to a throwaway `postgresql+psycopg://...` database. Without either, Postgres variants are skipped.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from app.db import make_engine


def postgres_url() -> str | None:
    for name in ("TEST_POSTGRES_URL", "DATABASE_URL"):
        v = os.environ.get(name, "")
        if v.startswith(("postgresql", "postgres:")):
            return v
    return None


@pytest.fixture
def pg_url() -> Iterator[str]:
    """URL of a brand-new empty schema (search_path pinned to it), dropped afterwards."""
    base = postgres_url()
    if base is None:
        pytest.skip("no Postgres: set TEST_POSTGRES_URL (or DATABASE_URL)")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = make_engine(base)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(base)
    url = url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url
    url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    yield url.render_as_string(hide_password=False)
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def pg_engine(url: str) -> Engine:
    return make_engine(url)
