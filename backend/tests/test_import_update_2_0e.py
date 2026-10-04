"""Block 2.0-E item 5: "Update my portfolio from screenshots" (partial vs full scope, atomic
confirm, per-portfolio screenshot prices, the last-updated timestamp and the nudge)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import Settings
from app.db import new_session
from app.models import (
    Holding,
    HoldingsSnapshot,
    ImportDraft,
    Portfolio,
    PriceQuote,
    Transaction,
)
from app.portfolio.screenshot import update_is_stale
from app.timeutil import utcnow
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]


def row(symbol: str, name: str, qty: float, price: float, **kw: Any) -> dict[str, Any]:
    return {
        "name": name,
        "symbol": symbol,
        "quantity": qty,
        "price": price,
        "value": qty * price,
        "currency": "USD",
        "unit": "USD",
        **kw,
    }


AAPL, MSFT, NVDA = ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia")


def post(c: TestClient, pid: int, rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows, **extra})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def confirm(c: TestClient, draft_id: int) -> Any:
    return c.post(f"/api/imports/{draft_id}/confirm")


@pytest.fixture
def existing(signup: SignupFn, quotes: FakeQuotes) -> tuple[TestClient, int]:
    """A portfolio that was imported once: AAPL 10 and MSFT 5, tracking started."""
    c = signup()
    pid = make_portfolio(c)
    d = post(c, pid, [row(*AAPL, 10, 200.0), row(*MSFT, 5, 400.0)])
    assert confirm(c, d["id"]).status_code == 200
    return c, pid


def holdings(pid: int) -> dict[str, float]:
    with new_session() as db:
        rows = db.exec(select(Holding).where(Holding.portfolio_id == pid)).all()
        return {h.symbol: h.quantity for h in rows}


def count(model: Any) -> int:
    with new_session() as db:
        return len(db.exec(select(model)).all())


# ------------------------------------------------------------------ scope
def test_scope_defaults_to_partial_and_is_returned(existing: tuple[TestClient, int]) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 200.0)])
    assert d["scope"] == "partial"
    assert c.get(f"/api/imports/{d['id']}").json()["scope"] == "partial"
    full = post(c, pid, [row(*AAPL, 10, 200.0)], scope="full")
    assert full["scope"] == "full"
    bad = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [], "scope": "everything"})
    assert bad.status_code == 422


def test_partial_update_changes_one_quantity_adds_one_holding_and_leaves_the_rest(
    existing: tuple[TestClient, int],
) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 12, 210.0), row(*NVDA, 4, 120.0)])
    changes = {(x["symbol"], x["type"]): x for x in d["proposed_changes"]}
    assert set(changes) == {("AAPL", "buy"), ("NVDA", "buy")}  # MSFT is not mentioned at all
    assert changes[("AAPL", "buy")]["quantity"] == 2
    assert all(x["row_index"] >= 0 for x in d["proposed_changes"])
    assert confirm(c, d["id"]).status_code == 200
    assert holdings(pid) == {"AAPL": 12, "MSFT": 5, "NVDA": 4}  # MSFT untouched
    with new_session() as db:
        txs = db.exec(select(Transaction).where(Transaction.type != "pending_buy")).all()
        assert {(t.symbol, t.type) for t in txs} == {("AAPL", "buy"), ("NVDA", "buy")}


def test_full_update_lists_missing_holdings_as_a_choice_that_defaults_to_keep(
    existing: tuple[TestClient, int],
) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 200.0)], scope="full")
    (missing,) = d["proposed_changes"]
    assert missing["row_index"] == -1 and missing["symbol"] == "MSFT"
    assert missing["type"] == "keep"  # never applied automatically
    assert missing["quantity"] == 5
    assert confirm(c, d["id"]).status_code == 200
    assert holdings(pid) == {"AAPL": 10, "MSFT": 5}


@pytest.mark.parametrize("choice", ["sell", "withdrawal"])
def test_full_update_sold_or_withdrawn_removes_the_holding_with_a_transaction(
    existing: tuple[TestClient, int], choice: str
) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 200.0)], scope="full")
    (missing,) = d["proposed_changes"]
    patched = c.patch(
        f"/api/imports/{d['id']}", json={"proposed_changes": [{**missing, "type": choice}]}
    )
    assert patched.status_code == 200, patched.text
    assert confirm(c, d["id"]).status_code == 200
    assert holdings(pid) == {"AAPL": 10}
    with new_session() as db:
        tx = db.exec(
            select(Transaction).where(Transaction.symbol == "MSFT", Transaction.type == choice)
        ).one()
        assert tx.quantity == 5 and tx.inferred is True


def test_a_row_edit_keeps_the_users_choice_for_missing_holdings(
    existing: tuple[TestClient, int],
) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 200.0)], scope="full")
    (missing,) = d["proposed_changes"]
    c.patch(f"/api/imports/{d['id']}", json={"proposed_changes": [{**missing, "type": "sell"}]})
    edited = c.patch(f"/api/imports/{d['id']}", json={"rows": d["rows"]}).json()
    (still,) = [x for x in edited["proposed_changes"] if x["row_index"] == -1]
    assert still["type"] == "sell"


def test_switching_the_scope_recomputes_the_changes(existing: tuple[TestClient, int]) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 200.0)])
    assert d["proposed_changes"] == []
    full = c.patch(f"/api/imports/{d['id']}", json={"scope": "full"}).json()
    assert full["scope"] == "full" and [x["symbol"] for x in full["proposed_changes"]] == ["MSFT"]
    back = c.patch(f"/api/imports/{d['id']}", json={"scope": "partial"}).json()
    assert back["proposed_changes"] == []


def test_not_in_these_screenshots_choices_are_validated(existing: tuple[TestClient, int]) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 12, 200.0)], scope="full")
    (missing,) = [x for x in d["proposed_changes"] if x["row_index"] == -1]
    # "keep" only exists for a holding that is missing from the screenshots
    bad = c.patch(
        f"/api/imports/{d['id']}",
        json={"proposed_changes": [{**missing, "row_index": 0, "type": "keep"}]},
    )
    assert bad.status_code == 422
    # a choice for a holding the portfolio does not have is refused at confirm
    ghost = {**missing, "symbol": "KO", "type": "sell"}
    c.patch(f"/api/imports/{d['id']}", json={"proposed_changes": [ghost]})
    assert confirm(c, d["id"]).status_code == 422
    assert holdings(pid) == {"AAPL": 10, "MSFT": 5}
    # and in a partial update there is no such choice at all
    p = post(c, pid, [row(*AAPL, 10, 200.0)])
    c.patch(f"/api/imports/{p['id']}", json={"proposed_changes": [{**missing, "type": "sell"}]})
    assert confirm(c, p["id"]).status_code == 422


# ------------------------------------------------------------------ prices
def test_price_only_update_stores_the_screenshot_price_per_portfolio_never_a_quote(
    existing: tuple[TestClient, int],
) -> None:
    c, pid = existing
    quotes_before = count(PriceQuote)
    d = post(c, pid, [row(*AAPL, 10, 250.0)])
    assert d["proposed_changes"] == []  # same quantity: price only
    assert confirm(c, d["id"]).status_code == 200
    assert count(PriceQuote) == quotes_before  # nothing global was written
    with new_session() as db:
        snap = db.exec(
            select(HoldingsSnapshot)
            .where(HoldingsSnapshot.portfolio_id == pid)
            .order_by(HoldingsSnapshot.id.desc())  # type: ignore[attr-defined]
        ).first()
        assert snap is not None and snap.source == "screenshot"
        assert snap.rows == [
            {"symbol": "AAPL", "quantity": 10, "price_native": 250.0, "currency": "USD"}
        ]
    held = {h["symbol"]: h for h in c.get(f"/api/portfolios/{pid}/holdings").json()}
    assert held["AAPL"]["price"] == 250.0 and held["AAPL"]["price_stale"] is True
    assert held["MSFT"]["price"] == 400.0  # from the older screenshot, still this portfolio's


def test_another_user_never_sees_a_screenshot_price(
    existing: tuple[TestClient, int], signup: SignupFn
) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 999.0)])
    confirm(c, d["id"])
    other = signup("other@mail.com")
    opid = make_portfolio(other)
    other.post(f"/api/portfolios/{opid}/holdings", json={"symbol": "AAPL", "quantity": 1})
    price = other.get(f"/api/portfolios/{opid}/holdings").json()[0]["price"]
    assert price != 999.0


def test_cost_inferred_is_not_forced_onto_an_existing_cost(
    existing: tuple[TestClient, int],
) -> None:
    c, pid = existing
    with new_session() as db:
        h = db.exec(select(Holding).where(Holding.symbol == "AAPL")).one()
        h.avg_cost, h.cost_currency = 150.0, "USD"
        db.add(h)
        db.commit()
    inferred = row(*AAPL, 10, 200.0, cost=190.0, flags=["cost_inferred"])
    d = post(c, pid, [inferred])
    assert confirm(c, d["id"]).status_code == 200
    with new_session() as db:
        assert db.exec(select(Holding).where(Holding.symbol == "AAPL")).one().avg_cost == 150.0
    # the user accepts the estimate by removing the flag (an explicit cost)
    d2 = post(c, pid, [row(*AAPL, 10, 200.0, cost=190.0)])
    assert confirm(c, d2["id"]).status_code == 200
    with new_session() as db:
        assert db.exec(select(Holding).where(Holding.symbol == "AAPL")).one().avg_cost == 190.0


def test_cost_inferred_is_applied_to_a_holding_without_a_cost(
    existing: tuple[TestClient, int],
) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 10, 200.0, cost=190.0, flags=["cost_inferred"])])
    assert confirm(c, d["id"]).status_code == 200
    with new_session() as db:
        assert db.exec(select(Holding).where(Holding.symbol == "AAPL")).one().avg_cost == 190.0


# ------------------------------------------------------------------ atomic confirm
def snapshot_of_state(pid: int) -> dict[str, Any]:
    with new_session() as db:
        p = db.get(Portfolio, pid)
        assert p is not None
        return {
            "holdings": sorted(
                (h.symbol, h.quantity, h.avg_cost, h.cost_currency)
                for h in db.exec(select(Holding).where(Holding.portfolio_id == pid)).all()
            ),
            "snapshots": len(db.exec(select(HoldingsSnapshot)).all()),
            "transactions": sorted(
                (t.symbol, t.type, t.quantity)
                for t in db.exec(select(Transaction).where(Transaction.portfolio_id == pid)).all()
            ),
            "tracking": p.tracking_started_at,
            "last_update": p.last_screenshot_update_at,
        }


@pytest.mark.parametrize("target", ["sync_pending_flows", "get_usd_ils"])
def test_confirm_is_atomic_a_failure_midway_changes_nothing(
    existing: tuple[TestClient, int], monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    c, pid = existing
    # a new unseen ticker (verified later by a quote), a quantity change and a removal
    d = post(
        c,
        pid,
        [row(*AAPL, 14, 210.0), row("ZZZW", "Warrant", 100, 2.0, exchange="NASDAQ")],
        scope="full",
    )
    (missing,) = [x for x in d["proposed_changes"] if x["row_index"] == -1]
    c.patch(f"/api/imports/{d['id']}", json={"proposed_changes": [
        *[x for x in d["proposed_changes"] if x["row_index"] != -1], {**missing, "type": "sell"}
    ]})  # fmt: skip
    before = snapshot_of_state(pid)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(f"app.importer.service.{target}", boom)
    with pytest.raises(RuntimeError, match="injected"):
        confirm(c, d["id"])
    assert snapshot_of_state(pid) == before
    with new_session() as db:
        draft = db.get(ImportDraft, d["id"])
        assert draft is not None and draft.status == "draft" and draft.rows
        assert draft.proposed_changes
    assert holdings(pid) == {"AAPL": 10, "MSFT": 5}


def test_a_failure_while_writing_holdings_changes_nothing(
    existing: tuple[TestClient, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.importer import service

    c, pid = existing
    d = post(c, pid, [row(*AAPL, 14, 210.0), row(*NVDA, 4, 120.0)])
    before = snapshot_of_state(pid)
    real = service.row_security
    calls = {"n": 0}

    def flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # the first row is already written
            raise RuntimeError("injected failure")
        return real(*a, **k)

    monkeypatch.setattr(service, "row_security", flaky)
    with pytest.raises(RuntimeError, match="injected"):
        confirm(c, d["id"])
    assert snapshot_of_state(pid) == before


def test_a_failing_quote_refresh_after_the_commit_does_not_undo_the_import(
    existing: tuple[TestClient, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, pid = existing

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("quote provider down")

    monkeypatch.setattr("app.importer.service.refresh_symbols", boom)
    d = post(c, pid, [row(*AAPL, 10, 200.0), row("ZZZW", "Warrant", 100, 2.0, exchange="NASDAQ")])
    assert confirm(c, d["id"]).status_code == 200
    assert holdings(pid)["ZZZW"] == 100


def test_first_import_is_atomic_too(
    signup: SignupFn, quotes: FakeQuotes, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = signup()
    pid = make_portfolio(c)
    d = post(c, pid, [row(*AAPL, 10, 200.0)])

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("injected failure")

    monkeypatch.setattr("app.importer.service.ensure_tracking_started", boom)
    with pytest.raises(RuntimeError, match="injected"):
        confirm(c, d["id"])
    assert holdings(pid) == {}
    state = snapshot_of_state(pid)
    assert state["tracking"] is None and state["snapshots"] == 0 and state["last_update"] is None
    monkeypatch.undo()
    assert confirm(c, d["id"]).status_code == 200  # the draft is still there and now works
    assert holdings(pid) == {"AAPL": 10}


def test_confirming_twice_is_a_409_and_writes_once(existing: tuple[TestClient, int]) -> None:
    c, pid = existing
    d = post(c, pid, [row(*AAPL, 12, 200.0)])
    assert confirm(c, d["id"]).status_code == 200
    assert confirm(c, d["id"]).status_code == 409
    assert holdings(pid)["AAPL"] == 12


# ------------------------------------------------------------------ last update and nudge
def test_confirm_sets_the_last_screenshot_update_and_it_is_exposed(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = make_portfolio(c)
    assert c.get(f"/api/portfolios/{pid}").json()["last_screenshot_update_at"] is None
    d = post(c, pid, [row(*AAPL, 10, 200.0)])
    confirm(c, d["id"])
    stamp = c.get(f"/api/portfolios/{pid}").json()["last_screenshot_update_at"]
    assert stamp is not None
    s = c.get(f"/api/portfolios/{pid}/summary").json()
    assert s["last_screenshot_update_at"] == stamp and s["screenshot_update_stale"] is False
    combined = c.get("/api/portfolios/combined/summary").json()
    assert combined["last_screenshot_update_at"] == stamp
    assert [x["last_screenshot_update_at"] for x in c.get("/api/portfolios").json()] == [stamp]


def test_update_is_stale_follows_the_nudge_setting() -> None:
    now = utcnow()
    p = Portfolio(owner_id=1, name="p")
    s = Settings()
    assert s.screenshot_update_nudge_days == 7
    assert update_is_stale(p, s, now) is False  # never updated: nothing to nudge about
    p.last_screenshot_update_at = now - timedelta(days=6, hours=23)
    assert update_is_stale(p, s, now) is False
    p.last_screenshot_update_at = now - timedelta(days=7, minutes=1)
    assert update_is_stale(p, s, now) is True
    assert update_is_stale(p, Settings(screenshot_update_nudge_days=14), now) is False
    assert update_is_stale(p, Settings(screenshot_update_nudge_days=1), now) is True


def test_combined_summary_is_stale_when_any_portfolio_is(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    p1, p2 = make_portfolio(c, "a"), make_portfolio(c, "b")
    for pid in (p1, p2):
        confirm(c, post(c, pid, [row(*AAPL, 10, 200.0)])["id"])
    with new_session() as db:
        p = db.get(Portfolio, p2)
        assert p is not None
        p.last_screenshot_update_at = utcnow() - timedelta(days=30)
        db.add(p)
        db.commit()
    s = c.get("/api/portfolios/combined/summary").json()
    assert s["screenshot_update_stale"] is True
    assert c.get(f"/api/portfolios/{p1}/summary").json()["screenshot_update_stale"] is False
