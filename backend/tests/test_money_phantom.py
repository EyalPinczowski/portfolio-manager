"""Phantom P&L paths found by the Phase 2.0 diff review (block 2.0-F item 2).

Every test goes through the real API (TestClient) and asserts the since-start percentage: a holding
whose price arrives later, or whose cost is edited, must never look like profit or loss.
Rule under test: a holding that is not priced by a real quote or screenshot price is outside the
time-weighted return; its quantity waits in one `pending_buy` marker (keyed by holding_id) which
becomes a buy flow at the first real price.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db import new_session
from app.models import Holding, Portfolio, PriceQuote, Transaction
from app.portfolio.quotes import refresh_symbols
from app.portfolio.valuation import PENDING_BUY, take_snapshot
from app.timeutil import local_today
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio, set_fx, shift_tracking_back

SignupFn = Callable[..., TestClient]


def _start(c: TestClient, quotes: FakeQuotes) -> int:
    set_fx(3.5)
    quotes.set("MSFT", 400.0, "USD")
    pid = make_portfolio(c)
    r = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "MSFT", "quantity": 10})
    assert r.status_code == 201, r.text
    shift_tracking_back(pid, days=3, value_ils=14000.0)
    return pid


def _since(c: TestClient, pid: int) -> float:
    return float(c.get(f"/api/portfolios/{pid}/summary").json()["since_start_pnl"]["pct"])


def _txs(pid: int, kind: str | None = None) -> list[Transaction]:
    with new_session() as db:
        rows = db.exec(select(Transaction).where(Transaction.portfolio_id == pid)).all()
        return [t for t in rows if kind is None or t.type == kind]


def _quote_arrives(quotes: FakeQuotes, symbol: str, price: float) -> None:
    quotes.set(symbol, price, "USD")
    with new_session() as db:
        assert refresh_symbols(db, [symbol], quotes) == 1


def test_path1_a_cost_is_not_a_real_price(signup: SignupFn, quotes: FakeQuotes) -> None:
    """Added with avg_cost 10 and no quote; the quote of 100 used to show +180 % since start."""
    c = signup()
    pid = _start(c, quotes)
    r = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "NEWCO", "quantity": 100, "avg_cost": 10},
    )
    assert r.status_code == 201, r.text
    assert _since(c, pid) == pytest.approx(0.0)
    assert [t.type for t in _txs(pid)] == [PENDING_BUY]  # no flow at the cost price
    _quote_arrives(quotes, "NEWCO", 100.0)
    assert _since(c, pid) == pytest.approx(0.0)
    buys = _txs(pid, "buy")
    assert len(buys) == 1 and buys[0].amount == pytest.approx(100 * 100.0)  # the real price
    assert not _txs(pid, PENDING_BUY)


def test_path2_removing_and_restoring_the_cost_does_not_buy_twice(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = _start(c, quotes)
    hid = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "NEWCO", "quantity": 100, "avg_cost": 10},
    ).json()["id"]
    url = f"/api/portfolios/{pid}/holdings/{hid}"
    assert c.patch(url, json={"avg_cost": None}).status_code == 200
    assert _since(c, pid) == pytest.approx(0.0)
    assert c.patch(url, json={"avg_cost": 10}).status_code == 200
    assert _since(c, pid) == pytest.approx(0.0)
    assert not _txs(pid, "buy") and len(_txs(pid, PENDING_BUY)) == 1
    _quote_arrives(quotes, "NEWCO", 12.0)
    assert _since(c, pid) == pytest.approx(0.0)
    assert len(_txs(pid, "buy")) == 1


def test_path3_changing_the_cost_currency_is_not_a_gain_or_loss(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = _start(c, quotes)
    hid = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "NEWCO", "quantity": 100, "avg_cost": 10, "cost_currency": "USD"},
    ).json()["id"]
    r = c.patch(f"/api/portfolios/{pid}/holdings/{hid}", json={"cost_currency": "ILS"})
    assert r.status_code == 200, r.text
    assert _since(c, pid) == pytest.approx(0.0)
    r = c.patch(f"/api/portfolios/{pid}/holdings/{hid}", json={"cost_currency": "USD"})
    assert _since(c, pid) == pytest.approx(0.0)
    _quote_arrives(quotes, "NEWCO", 10.0)
    assert _since(c, pid) == pytest.approx(0.0)


def test_path4_a_second_marker_for_one_holding_is_refused_by_the_database(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    """Two markers (a race) both settled as buys: -35.7 %. Now the index forbids the second."""
    c = signup()
    pid = _start(c, quotes)
    hid = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 100}
    ).json()["id"]
    with new_session() as db:
        db.add(
            Transaction(
                portfolio_id=pid,
                symbol="NEWCO",
                holding_id=hid,
                type=PENDING_BUY,
                quantity=100,
                amount=0.0,
                date=local_today(),
                inferred=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    # settled markers become buys: the same holding may get a new marker later
    _quote_arrives(quotes, "NEWCO", 50.0)
    assert _since(c, pid) == pytest.approx(0.0)
    with new_session() as db:  # settling twice is a no-op
        p = db.get(Portfolio, pid)
        assert p is not None
        take_snapshot(db, p, local_today())
        take_snapshot(db, p, local_today())
    assert len(_txs(pid, "buy")) == 1 and _since(c, pid) == pytest.approx(0.0)


def test_path5_a_priced_holding_that_drops_out_and_returns_makes_no_marker(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    """MSFT is in the baseline; its quote loses its currency; the snapshot job must not create a
    marker that later settles as a 'buy' of the whole position (-50 %)."""
    c = signup()
    pid = _start(c, quotes)
    with new_session() as db:
        q = db.get(PriceQuote, "MSFT")
        assert q is not None
        q.currency = ""
        db.add(q)
        db.commit()
        p = db.get(Portfolio, pid)
        assert p is not None
        snap = take_snapshot(db, p, local_today())
        assert snap is not None and snap.net_flow_ils == pytest.approx(0.0)
    assert not _txs(pid, PENDING_BUY)
    quotes.set("MSFT", 400.0, "USD")
    with new_session() as db:
        refresh_symbols(db, ["MSFT"], quotes)
        p = db.get(Portfolio, pid)
        assert p is not None
        take_snapshot(db, p, local_today())
    assert not _txs(pid, PENDING_BUY) and not _txs(pid, "buy")
    assert _since(c, pid) == pytest.approx(0.0)


def test_quantity_change_on_an_unpriced_holding_updates_its_one_marker(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = _start(c, quotes)
    hid = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 100}
    ).json()["id"]
    assert (
        c.patch(f"/api/portfolios/{pid}/holdings/{hid}", json={"quantity": 150}).status_code == 200
    )
    markers = _txs(pid, PENDING_BUY)
    assert len(markers) == 1 and markers[0].quantity == pytest.approx(150)
    _quote_arrives(quotes, "NEWCO", 10.0)
    assert _since(c, pid) == pytest.approx(0.0)
    (buy,) = _txs(pid, "buy")
    assert buy.quantity == pytest.approx(150) and buy.amount == pytest.approx(1500.0)


def test_deleting_an_unpriced_holding_removes_its_marker(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = _start(c, quotes)
    hid = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 100, "avg_cost": 3}
    ).json()["id"]
    assert c.delete(f"/api/portfolios/{pid}/holdings/{hid}").status_code == 204
    assert _txs(pid) == [] and _since(c, pid) == pytest.approx(0.0)
    with new_session() as db:
        assert db.exec(select(Holding).where(Holding.symbol == "NEWCO")).first() is None


def test_tracking_start_registers_a_marker_for_a_cost_priced_holding(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    """The baseline day counts only priced holdings; the cost-priced one arrives as a flow."""
    set_fx(3.5)
    c = signup()
    pid = make_portfolio(c)
    r = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "NEWCO", "quantity": 100, "avg_cost": 5}
    )
    assert r.status_code == 201, r.text  # first holding: tracking starts here
    (marker,) = _txs(pid, PENDING_BUY)
    assert marker.quantity == pytest.approx(100) and marker.holding_id == r.json()["id"]
    assert c.get(f"/api/portfolios/{pid}/summary").json()["value"]["ils"] > 0  # shown at cost
    _quote_arrives(quotes, "NEWCO", 9.0)
    (buy,) = _txs(pid, "buy")
    assert buy.amount == pytest.approx(900.0) and not _txs(pid, PENDING_BUY)
