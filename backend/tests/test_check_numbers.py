"""`value_native`, `pnl_native` and the `check_numbers` guard on the holdings list."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import new_session
from app.models import PriceQuote
from app.timeutil import utcnow
from tests.conftest import FakeQuotes

SignupFn = Callable[..., TestClient]


def _setup(c: TestClient, quotes: FakeQuotes, rows: list[dict[str, Any]]) -> tuple[int, list[Any]]:
    with new_session() as db:
        db.merge(PriceQuote(symbol="ILS=X", price=3.5, currency="ILS", as_of=utcnow()))
        db.commit()
    for r in rows:
        if "price" in r:
            quotes.set(r["symbol"], r.pop("price"), r.pop("cur"))
    pid = int(c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"])
    for r in rows:
        assert c.post(f"/api/portfolios/{pid}/holdings", json=r).status_code == 201
    return pid, c.get(f"/api/portfolios/{pid}/holdings").json()


def _by(rows: list[Any]) -> dict[str, Any]:
    return {r["symbol"]: r for r in rows}


def test_native_value_and_pnl_in_the_holdings_own_currency(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    _, rows = _setup(
        c,
        quotes,
        [
            {"symbol": "AAPL", "quantity": 10, "avg_cost": 150, "price": 200.0, "cur": "USD"},
            {"symbol": "TEVA.TA", "quantity": 100, "avg_cost": 50, "price": 62.0, "cur": "ILS"},
        ],
    )
    by = _by(rows)
    assert by["AAPL"]["value_native"] == pytest.approx(2000.0)
    assert by["AAPL"]["pnl_native"] == pytest.approx(500.0)  # USD, not shekels
    assert by["AAPL"]["pnl"]["ils"] == pytest.approx(1750.0)
    assert by["TEVA.TA"]["value_native"] == pytest.approx(6200.0)  # shekels, price already in ILS
    assert by["TEVA.TA"]["pnl_native"] == pytest.approx(1200.0)
    assert not by["AAPL"]["check_numbers"] and not by["TEVA.TA"]["check_numbers"]


def test_no_cost_means_no_native_pnl(signup: SignupFn, quotes: FakeQuotes) -> None:
    c = signup()
    _, rows = _setup(c, quotes, [{"symbol": "AAPL", "quantity": 2, "price": 100.0, "cur": "USD"}])
    assert rows[0]["pnl_native"] is None and rows[0]["value_native"] == pytest.approx(200.0)


def test_guard_flags_huge_pnl_and_unit_mixup_without_changing_values(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    _, rows = _setup(
        c,
        quotes,
        [
            {"symbol": "AAPL", "quantity": 10, "avg_cost": 20, "price": 200.0, "cur": "USD"},
            {"symbol": "TEVA.TA", "quantity": 100, "avg_cost": 6000, "price": 62.0, "cur": "ILS"},
        ],
    )
    by = _by(rows)
    assert by["AAPL"]["check_numbers"] is True  # +900 %, and price is 10x cost
    assert by["TEVA.TA"]["check_numbers"] is True  # cost 100x the price: agorot vs shekels
    assert by["AAPL"]["value_native"] == pytest.approx(2000.0)  # values are never altered
    assert by["TEVA.TA"]["price"] == pytest.approx(62.0)


def test_guard_thresholds_are_strict(signup: SignupFn, quotes: FakeQuotes) -> None:
    c = signup()
    # +400 % and price 5x cost: below both thresholds
    _, rows = _setup(
        c, quotes, [{"symbol": "AAPL", "quantity": 1, "avg_cost": 40, "price": 200.0, "cur": "USD"}]
    )
    assert rows[0]["check_numbers"] is False


def test_guard_weight_needs_three_holdings(signup: SignupFn, quotes: FakeQuotes) -> None:
    c = signup()
    _, rows = _setup(
        c,
        quotes,
        [
            {"symbol": "AAPL", "quantity": 100, "price": 200.0, "cur": "USD"},
            {"symbol": "MSFT", "quantity": 1, "price": 100.0, "cur": "USD"},
        ],
    )
    assert not any(r["check_numbers"] for r in rows)  # two holdings: the weight rule is off
    c2 = signup("bob@mail.com")
    _, rows = _setup(
        c2,
        quotes,
        [
            {"symbol": "AAPL", "quantity": 100, "price": 200.0, "cur": "USD"},
            {"symbol": "MSFT", "quantity": 1, "price": 100.0, "cur": "USD"},
            {"symbol": "NVDA", "quantity": 1, "price": 100.0, "cur": "USD"},
        ],
    )
    by = _by(rows)
    assert by["AAPL"]["check_numbers"] is True
    assert by["MSFT"]["check_numbers"] is False and by["NVDA"]["check_numbers"] is False


def test_another_users_portfolio_holdings_are_not_readable(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    a = signup()
    pid, _ = _setup(a, quotes, [{"symbol": "AAPL", "quantity": 1, "price": 10.0, "cur": "USD"}])
    b = signup("bob@mail.com")
    assert b.get(f"/api/portfolios/{pid}/holdings").status_code == 404
