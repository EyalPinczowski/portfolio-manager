"""Block 2.0-E item 2: exchange + ticker and TASE security numbers as matching keys."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import new_session
from app.models import Holding, PriceQuote, Security
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]


def us_row(**kw: Any) -> dict[str, Any]:
    base = {
        "name": "Zeta Zed Warrant",
        "symbol": "ZZZW",
        "exchange": "NASDAQ",
        "quantity": 100,
        "price": 2.0,
        "value": 200.0,
        "currency": "USD",
        "unit": "USD",
    }
    return {**base, **kw}


def tlv_row(**kw: Any) -> dict[str, Any]:
    base = {
        "name": "מחקה מדד דמה 125",
        "symbol": None,
        "tase_number": "1234567",
        "quantity": 489,
        "price": 6272,
        "value": 30670.08,
        "cost": 6020,
        "currency": "ILS",
        "unit": "agorot",
    }
    return {**base, **kw}


def post(c: TestClient, pid: int, rows: list[dict[str, Any]]) -> Any:
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows})
    assert r.status_code == 201, r.text
    return r.json()


def security(symbol: str) -> Security | None:
    with new_session() as db:
        return db.get(Security, symbol)


def test_unseen_us_ticker_is_accepted_as_unverified_until_a_quote_verifies_it(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = make_portfolio(c)
    draft = post(c, pid, [us_row()])
    row = draft["rows"][0]
    assert row["symbol"] == "ZZZW" and "unmatched" not in row["flags"]
    assert security("ZZZW") is None  # nothing is created before the user confirms
    assert c.post(f"/api/imports/{draft['id']}/confirm").status_code == 200
    sec = security("ZZZW")
    assert sec is not None and sec.verified is False  # no quote for it: stays unverified
    assert sec.market == "US" and sec.currency == "USD"
    # the next screenshot matches it again: it is now this user's own holding
    again = post(c, pid, [us_row()])["rows"][0]
    assert again["symbol"] == "ZZZW" and "unmatched" not in again["flags"]


def test_a_quote_verifies_the_new_security(signup: SignupFn, quotes: FakeQuotes) -> None:
    quotes.set("ZZZW", 2.0, "USD")
    c = signup()
    pid = make_portfolio(c)
    draft = post(c, pid, [us_row()])
    assert c.post(f"/api/imports/{draft['id']}/confirm").status_code == 200
    sec = security("ZZZW")
    assert sec is not None and sec.verified is True


def test_an_unseen_ticker_without_an_exchange_is_not_accepted(signup: SignupFn) -> None:
    c = signup()
    pid = make_portfolio(c)
    row = post(c, pid, [us_row(exchange=None, name="")])["rows"][0]
    assert row["symbol"] is None and "unmatched" in row["flags"]


@pytest.mark.parametrize("bad", ["TOOLONG", "A1", "BRK.B"])
def test_the_ticker_must_be_one_to_five_capital_letters(signup: SignupFn, bad: str) -> None:
    c = signup()
    pid = make_portfolio(c)
    row = post(c, pid, [us_row(symbol=bad, name="")])["rows"][0]
    assert "unmatched" in row["flags"]


def test_an_exchange_ticker_never_falls_back_to_a_name_match(signup: SignupFn) -> None:
    c = signup()
    pid = make_portfolio(c)
    # the name says Apple, the broker's ticker says otherwise: the ticker is the identity
    row = post(c, pid, [us_row(name="Apple", symbol="APLX")])["rows"][0]
    assert row["symbol"] == "APLX"


def test_a_seeded_ticker_with_an_exchange_matches_the_seed(signup: SignupFn) -> None:
    c = signup()
    pid = make_portfolio(c)
    row = post(c, pid, [us_row(symbol="AAPL", name="x", price=200.0, value=20000.0)])["rows"][0]
    assert row["symbol"] == "AAPL" and row["matched_name"]


def test_unverified_security_is_scoped_to_the_user_who_imported_it(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa, pb = make_portfolio(a), make_portfolio(b)
    draft = post(a, pa, [us_row()])
    assert a.post(f"/api/imports/{draft['id']}/confirm").status_code == 200
    # B: the same ticker without the exchange evidence stays unmatched, is never suggested, and
    # does not show up in search
    row = post(b, pb, [us_row(exchange=None, name="Zeta Zed Warrant")])["rows"][0]
    assert row["symbol"] is None and "unmatched" in row["flags"]
    assert not {c["symbol"] for c in row["candidates"]} & {"ZZZW"}
    hits = b.get("/api/securities/search", params={"q": "ZZZW"}).json()
    assert all(h["symbol"] != "ZZZW" for h in hits)


def test_tlv_row_matches_a_seeded_security_on_its_tase_number(signup: SignupFn) -> None:
    c = signup()
    pid = make_portfolio(c)
    row = post(
        c,
        pid,
        [tlv_row(tase_number="629014", name="", quantity=100, price=6500, value=6500.0)],
    )["rows"][0]
    assert row["symbol"] == "TEVA.TA" and "unmatched" not in row["flags"]


def test_unknown_tase_number_needs_a_manual_symbol_then_is_user_scoped(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup()
    pid = make_portfolio(c)
    draft = post(c, pid, [tlv_row()])
    row = draft["rows"][0]
    assert row["symbol"] is None and "unmatched" in row["flags"]
    assert row["tase_number"] == "1234567"  # kept: it is the identity of the row
    blocked = c.post(f"/api/imports/{draft['id']}/confirm")
    assert blocked.status_code == 422 and "matched" in blocked.text
    # a symbol that is not Yahoo's `<id>.TA` form is refused
    bad = c.patch(f"/api/imports/{draft['id']}", json={"rows": [{**row, "symbol": "FUNDX"}]})
    assert bad.json()["rows"][0]["symbol"] is None
    # the user supplies the symbol
    ok = c.patch(f"/api/imports/{draft['id']}", json={"rows": [{**row, "symbol": "1234567.TA"}]})
    assert ok.status_code == 200, ok.text
    fixed = ok.json()["rows"][0]
    assert fixed["symbol"] == "1234567.TA" and "unmatched" not in fixed["flags"]
    assert c.post(f"/api/imports/{draft['id']}/confirm").status_code == 200
    sec = security("1234567.TA")
    assert sec is not None
    assert sec.verified is False and sec.market == "TASE" and sec.currency == "ILS"
    assert sec.tase_number == "1234567"
    assert sec.name_en == "1234567.TA"  # OCR text is never copied into the shared table
    with new_session() as db:
        h = db.exec(select(Holding).where(Holding.symbol == "1234567.TA")).one()
        assert h.avg_cost == pytest.approx(60.2)  # agorot cost -> ILS
        assert db.get(PriceQuote, "1234567.TA") is None
    # the next screenshot of the same fund matches on the TLV number by itself
    nxt = post(c, pid, [tlv_row()])["rows"][0]
    assert nxt["symbol"] == "1234567.TA" and "unmatched" not in nxt["flags"]


def test_another_user_does_not_match_a_user_scoped_tase_number(signup: SignupFn) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa, pb = make_portfolio(a), make_portfolio(b)
    draft = post(a, pa, [tlv_row()])
    row = draft["rows"][0]
    a.patch(f"/api/imports/{draft['id']}", json={"rows": [{**row, "symbol": "1234567.TA"}]})
    assert a.post(f"/api/imports/{draft['id']}/confirm").status_code == 200
    other = post(b, pb, [tlv_row(name="")])["rows"][0]
    assert other["symbol"] is None and "unmatched" in other["flags"]


def test_a_user_scoped_entry_never_shadows_a_seeded_tase_number(signup: SignupFn) -> None:
    from app.importer.match import SecurityIndex

    seeded = Security(symbol="TEVA.TA", name_en="Teva", tase_number="629014", verified=True)
    junk = Security(symbol="0AAA.TA", name_en="x", tase_number="629014", verified=False)
    assert SecurityIndex([junk, seeded]).by_tase["629014"].symbol == "TEVA.TA"
    assert SecurityIndex([seeded, junk]).by_tase["629014"].symbol == "TEVA.TA"
