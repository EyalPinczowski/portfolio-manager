"""Revision 0005: `transaction.holding_id` backfilled from (portfolio, symbol), duplicate markers
reduced to the oldest, and one outstanding marker per holding enforced (SQLite and Postgres)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.db import make_engine, run_migrations
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    else:
        eng = make_engine(request.getfixturevalue("pg_url"))
    yield eng
    eng.dispose()


LEGACY_MARKER = text(
    'INSERT INTO "transaction" (portfolio_id, symbol, type, quantity, amount, currency, fx_to_ils, '
    "date, inferred) VALUES (1, :s, 'pending_buy', :q, 0, 'ILS', 1, :d, :t)"
)


def test_0005_backfills_dedupes_and_enforces_one_marker_per_holding(engine: Engine) -> None:
    run_migrations(engine, "0004_llm_usage")
    day = date(2026, 1, 1)
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO "user" (id, email, password_hash, locale, is_admin, created_at) '
                "VALUES (1, 'a@x.co', 'h', 'he', :f, :c)"
            ),
            {"f": False, "c": datetime(2026, 1, 1)},
        )
        conn.execute(
            text(
                "INSERT INTO security (symbol, name_en, name_he, asset_type, market, currency, "
                "sector, country, verified) VALUES "
                "('AAA', 'A', 'A', 'stock', 'US', 'USD', 'Tech', 'US', :t), "
                "('GONE', 'G', 'G', 'stock', 'US', 'USD', 'Tech', 'US', :t)"
            ),
            {"t": True},
        )
        conn.execute(
            text(
                "INSERT INTO portfolio (id, owner_id, name, base_currency, created_at) "
                "VALUES (1, 1, 'P', 'ILS', :c)"
            ),
            {"c": datetime(2026, 1, 1)},
        )
        conn.execute(
            text(
                "INSERT INTO holding (id, portfolio_id, symbol, quantity, cost_currency) "
                "VALUES (7, 1, 'AAA', 5, 'USD')"
            )
        )
        for sym, qty in (("AAA", 5), ("AAA", 8), ("GONE", 1)):  # a race made two for AAA
            conn.execute(LEGACY_MARKER, {"s": sym, "q": qty, "d": day, "t": True})
    run_migrations(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT symbol, holding_id, quantity FROM "transaction" ORDER BY id')
        ).all()
    assert [(r[0], r[1], r[2]) for r in rows if r[0] == "AAA"] == [("AAA", 7, 5)]  # oldest kept
    assert [r[1] for r in rows if r[0] == "GONE"] == [None]  # no holding: left, settle drops it
    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                'INSERT INTO "transaction" (portfolio_id, symbol, holding_id, type, quantity, '
                "amount, currency, fx_to_ils, date, inferred) VALUES "
                "(1, 'AAA', 7, 'pending_buy', 1, 0, 'ILS', 1, :d, :t)"
            ),
            {"d": day, "t": True},
        )
    with engine.begin() as conn:  # the index covers markers only: ordinary rows may share an id
        for _ in range(2):
            conn.execute(
                text(
                    'INSERT INTO "transaction" (portfolio_id, symbol, holding_id, type, quantity, '
                    "amount, currency, fx_to_ils, date, inferred) VALUES "
                    "(1, 'AAA', 7, 'buy', 1, 1, 'ILS', 1, :d, :t)"
                ),
                {"d": day, "t": False},
            )
