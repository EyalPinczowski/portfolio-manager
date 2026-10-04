"""Flow bookkeeping for manual holding edits: deferred flows and the valuation's currency."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import new_session
from app.models import Portfolio, PriceQuote, Transaction
from app.portfolio.quotes import refresh_symbols
from app.portfolio.valuation import flow_ils, take_snapshot
from app.timeutil import local_today, utcnow
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio, set_fx, shift_tracking_back

SignupFn = Callable[..., TestClient]


def _txs(pid: int) -> list[Transaction]:
    with new_session() as db:
        return list(db.exec(select(Transaction).where(Transaction.portfolio_id == pid)).all())


def _started_portfolio(c: TestClient, quotes: FakeQuotes) -> int:
    set_fx(3.5)
    quotes.set("MSFT", 400.0, "USD")
    pid = make_portfolio(c)
    r = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "MSFT", "quantity": 10})
    assert r.status_code == 201, r.text
    shift_tracking_back(pid, days=3, value_ils=14000.0)
    return pid


def test_unpriced_holding_is_a_deferred_flow_not_profit(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    """Verified hole (a): no quote and no cost -> no flow; the quote then showed +1,666 %."""
    c = signup()
    pid = _started_portfolio(c, quotes)
    r = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 100})
    assert r.status_code == 201, r.text
    # while unpriced it is worth 0: out of the value and out of the flows
    assert c.get(f"/api/portfolios/{pid}/summary").json()["value"]["ils"] == pytest.approx(14000.0)
    assert not [t for t in _txs(pid) if t.type in ("buy", "deposit")]

    quotes.set("NEWCO", 50.0, "USD")
    with new_session() as db:
        assert refresh_symbols(db, ["NEWCO"], quotes) == 1
    s = c.get(f"/api/portfolios/{pid}/summary").json()
    assert s["value"]["ils"] == pytest.approx(14000.0 + 100 * 50 * 3.5)
    assert s["since_start_pnl"]["ils"] == pytest.approx(0.0)
    assert s["since_start_pnl"]["pct"] == pytest.approx(0.0)
    buys = [t for t in _txs(pid) if t.type == "buy"]
    assert len(buys) == 1 and buys[0].currency == "USD" and buys[0].amount == pytest.approx(5000.0)
    # a second quote refresh must not record the flow again
    with new_session() as db:
        refresh_symbols(db, ["NEWCO"], quotes)
    assert len([t for t in _txs(pid) if t.type == "buy"]) == 1


def test_unpriced_holding_settles_at_the_daily_snapshot_and_deleting_drops_the_marker(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = _started_portfolio(c, quotes)
    hid = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 1}).json()[
        "id"
    ]
    assert [t.type for t in _txs(pid)] == ["pending_buy"]
    assert c.delete(f"/api/portfolios/{pid}/holdings/{hid}").status_code == 204
    assert _txs(pid) == []

    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 2})
    with new_session() as db:  # the quote lands without going through refresh_symbols
        db.merge(PriceQuote(symbol="NEWCO", price=10.0, currency="USD", as_of=utcnow()))
        db.commit()
        p = db.get(Portfolio, pid)
        assert p is not None
        snap = take_snapshot(db, p, local_today())
        assert snap is not None and snap.net_flow_ils == pytest.approx(2 * 10 * 3.5)


def test_flow_uses_the_valuation_currency_not_the_security_currency(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    """Verified hole (b): AAPL with an ILS price of 700 became a 7,000 USD flow (25,200 ILS).

    2.0-F changed the premise: a *cost* price is never a flow (it is out of the TWR, see
    `tests/test_money_phantom.py`), so the currency rule is tested with a real quote in ILS.
    """
    c = signup()
    pid = _started_portfolio(c, quotes)
    quotes.set("AAPL", 700.0, "ILS")
    with new_session() as db:
        refresh_symbols(db, ["AAPL"], quotes)
    r = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "AAPL", "quantity": 10, "avg_cost": 700, "cost_currency": "ILS"},
    )
    assert r.status_code == 201, r.text
    buys = [t for t in _txs(pid) if t.type == "buy"]
    assert len(buys) == 1
    assert buys[0].currency == "ILS" and buys[0].amount == pytest.approx(7000.0)
    assert flow_ils(buys[0]) == pytest.approx(7000.0)
    hid = r.json()["id"]
    # selling half while still valued at cost: -3,500 ILS, again in ILS
    assert c.patch(f"/api/portfolios/{pid}/holdings/{hid}", json={"quantity": 5}).status_code == 200
    sells = [t for t in _txs(pid) if t.type == "sell"]
    assert (
        len(sells) == 1
        and sells[0].currency == "ILS"
        and flow_ils(sells[0]) == pytest.approx(-3500.0)
    )
