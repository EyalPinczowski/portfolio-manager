"""Block 2.0-E item 6: `POST /api/portfolios/{id}/holdings` (add a holding by hand)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import new_session
from app.models import Holding, Security, Transaction
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]


def add(c: TestClient, pid: int, **body: Any) -> Any:
    return c.post(f"/api/portfolios/{pid}/holdings", json=body)


def test_adds_a_seeded_holding_and_never_fills_in_a_horizon(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    quotes.set("AAPL", 200.0, "USD")
    c = signup()
    pid = make_portfolio(c)
    r = add(c, pid, symbol="aapl", quantity=3, avg_cost=150, cost_currency="USD")
    assert r.status_code == 201, r.text
    h = r.json()
    assert (h["symbol"], h["quantity"], h["horizon"]) == ("AAPL", 3, None)
    assert h["stop_tp_status"] == "needs_horizon"
    kept = add(c, pid, symbol="MSFT", quantity=1, horizon="3m").json()
    assert kept["horizon"] == "3m"
    with new_session() as db:
        stored = db.exec(select(Holding).where(Holding.symbol == "AAPL")).one()
        assert stored.avg_cost == 150 and stored.cost_currency == "USD"


def test_a_duplicate_is_a_409(signup: SignupFn) -> None:
    c = signup()
    pid = make_portfolio(c)
    assert add(c, pid, symbol="AAPL", quantity=1).status_code == 201
    assert add(c, pid, symbol="AAPL", quantity=2).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"symbol": "AAPL", "quantity": 0},
        {"symbol": "AAPL", "quantity": -1},
        {"symbol": "AAPL", "quantity": "5"},
        {"symbol": "AAPL", "quantity": 1e308},
        {"symbol": "AAPL", "quantity": 1, "avg_cost": -3},
        {"symbol": "AAPL", "quantity": 1, "cost_currency": "EUR"},
        {"symbol": "AAPL", "quantity": 1, "horizon": "2y"},
        {"symbol": "A B", "quantity": 1},
        {"symbol": "", "quantity": 1},
        {"symbol": "AAPL", "quantity": 1, "price": 5},  # unknown field
        {"quantity": 1},
    ],
)
def test_strict_body(signup: SignupFn, body: dict[str, Any]) -> None:
    c = signup()
    pid = make_portfolio(c)
    assert c.post(f"/api/portfolios/{pid}/holdings", json=body).status_code == 422
    assert c.get(f"/api/portfolios/{pid}/holdings").json() == []


def test_nan_is_rejected(signup: SignupFn) -> None:
    c = signup()
    pid = make_portfolio(c)
    r = c.post(
        f"/api/portfolios/{pid}/holdings",
        content='{"symbol": "AAPL", "quantity": NaN}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


def test_an_unseen_ticker_is_a_user_scoped_unverified_security_until_a_quote_verifies_it(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa, pb = make_portfolio(a), make_portfolio(b)
    assert add(a, pa, symbol="ZZZW", quantity=100).status_code == 201
    with new_session() as db:
        sec = db.get(Security, "ZZZW")
        assert sec is not None and sec.verified is False
    # B cannot find it, and B's import does not match or suggest it
    assert all(h["symbol"] != "ZZZW" for h in b.get("/api/securities/search?q=ZZZW").json())
    row = {
        "name": "ZZZW",
        "quantity": 1,
        "price": 2.0,
        "value": 2.0,
        "currency": "USD",
        "unit": "USD",
    }
    out = b.post(f"/api/portfolios/{pb}/imports/rows", json={"rows": [row]}).json()["rows"][0]
    assert out["symbol"] is None and "unmatched" in out["flags"]
    # the first quote verifies it
    quotes.set("ZZZW", 2.0, "USD")
    assert add(a, pa, symbol="ZZZY", quantity=1).status_code == 201  # unrelated
    quotes.set("ZZZY", 1.0, "USD")
    assert add(b, pb, symbol="ZZZW", quantity=1).status_code == 201  # quotes are fetched on add
    with new_session() as db:
        sec = db.get(Security, "ZZZW")
        assert sec is not None and sec.verified is True


def test_an_unpriced_holding_waits_in_one_marker(signup: SignupFn, quotes: FakeQuotes) -> None:
    quotes.set("AAPL", 200.0, "USD")
    c = signup()
    pid = make_portfolio(c)
    assert add(c, pid, symbol="AAPL", quantity=1).status_code == 201  # starts tracking
    r = add(c, pid, symbol="ZZZW", quantity=100)  # no quote, no cost: unpriced
    assert r.status_code == 201
    with new_session() as db:
        markers = db.exec(select(Transaction).where(Transaction.type == "pending_buy")).all()
        assert [(m.symbol, m.quantity) for m in markers] == [("ZZZW", 100)]
        assert markers[0].holding_id is not None


def test_another_users_portfolio_is_a_404_and_creates_nothing(signup: SignupFn) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa = make_portfolio(a)
    assert add(b, pa, symbol="QWERT", quantity=1).status_code == 404
    with new_session() as db:
        assert db.get(Security, "QWERT") is None  # the ownership check runs first
        assert db.exec(select(Holding)).all() == []


def test_import_endpoints_of_2_0e_are_owner_scoped(signup: SignupFn, quotes: FakeQuotes) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa = make_portfolio(a)
    make_portfolio(b)
    row = {"name": "Apple", "symbol": "AAPL", "quantity": 1, "price": 2.0, "value": 2.0,
           "currency": "USD", "unit": "USD"}  # fmt: skip
    d = a.post(f"/api/portfolios/{pa}/imports/rows", json={"rows": [row], "scope": "full"}).json()
    assert (
        b.post(
            f"/api/portfolios/{pa}/imports/rows", json={"rows": [row], "scope": "full"}
        ).status_code
        == 404
    )
    assert b.patch(f"/api/imports/{d['id']}", json={"scope": "partial"}).status_code == 404
    assert b.patch(f"/api/imports/{d['id']}", json={"proposed_changes": []}).status_code == 404
    assert b.post(f"/api/imports/{d['id']}/confirm").status_code == 404
    assert a.get(f"/api/imports/{d['id']}").json()["scope"] == "full"


def test_manual_add_is_rate_limited_per_user(
    monkeypatch: pytest.MonkeyPatch, signup: SignupFn
) -> None:
    monkeypatch.setenv("HOLDING_ADD_RATE_LIMIT_PER_HOUR", "3")
    get_settings.cache_clear()
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa, pb = make_portfolio(a), make_portfolio(b)
    for sym in ("AAPL", "MSFT", "NVDA"):
        assert add(a, pa, symbol=sym, quantity=1).status_code == 201
    r = add(a, pa, symbol="KO", quantity=1)
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert add(b, pb, symbol="AAPL", quantity=1).status_code == 201  # another user is unaffected


def test_patch_and_delete_of_a_holding_still_work_and_are_owner_scoped(signup: SignupFn) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa = make_portfolio(a)
    hid = add(a, pa, symbol="AAPL", quantity=2).json()["id"]
    assert a.patch(f"/api/portfolios/{pa}/holdings/{hid}", json={"quantity": 5}).status_code == 200
    assert b.patch(f"/api/portfolios/{pa}/holdings/{hid}", json={"quantity": 9}).status_code == 404
    assert b.delete(f"/api/portfolios/{pa}/holdings/{hid}").status_code == 404
    assert a.delete(f"/api/portfolios/{pa}/holdings/{hid}").status_code == 204
    assert a.get(f"/api/portfolios/{pa}/holdings").json() == []
