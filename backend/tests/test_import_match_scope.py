"""Matching only sees verified securities and the importing user's own symbols (2.0-A item 3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]


def _row(**kw: Any) -> dict[str, Any]:
    base = {"quantity": 10, "price": 5.0, "value": 50.0, "currency": "USD", "unit": "USD"}
    return {**base, **kw}


def _import(c: TestClient, pid: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows})
    assert r.status_code == 201, r.text
    return list(r.json()["rows"])


def test_another_users_unverified_ticker_is_never_matched_or_suggested(signup: SignupFn) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pa, pb = make_portfolio(a), make_portfolio(b)
    for sym in ("PALANTIR", "APPLE"):  # junk tickers A typed in: unverified Security rows
        r = a.post(f"/api/portfolios/{pa}/holdings", json={"symbol": sym, "quantity": 1})
        assert r.status_code == 201

    rows = _import(b, pb, [_row(name="Palantir"), _row(name="Apple"), _row(name="Appel")])
    palantir, apple, appel = rows
    assert palantir["symbol"] is None and "unmatched" in palantir["flags"]
    assert apple["symbol"] == "AAPL"  # the seeded, verified Apple and nothing else
    seen = {c["symbol"] for r in rows for c in r["candidates"]}
    assert not seen & {"PALANTIR", "APPLE"}, seen
    assert appel["symbol"] is None


def test_a_users_own_symbols_still_match_for_that_user(signup: SignupFn) -> None:
    a = signup("a@mail.com")
    pa = make_portfolio(a)
    r = a.post(f"/api/portfolios/{pa}/holdings", json={"symbol": "PALANTIR", "quantity": 1})
    assert r.status_code == 201
    row = _import(a, pa, [_row(name="Palantir")])[0]
    assert row["symbol"] == "PALANTIR"


def test_ambiguous_hebrew_name_returns_candidates_never_auto_accepts(signup: SignupFn) -> None:
    """ "אלפבית" is Alphabet: Class A (GOOGL) or Class C (GOOG). The user decides."""
    c = signup()
    pid = make_portfolio(c)
    row = _import(c, pid, [_row(name="אלפבית")])[0]
    assert row["symbol"] is None
    assert "low_confidence_match" in row["flags"]
    assert {x["symbol"] for x in row["candidates"]} >= {"GOOG", "GOOGL"}
    # an unambiguous full name still auto-matches
    full = _import(c, pid, [_row(name="Alphabet Class C")])[0]
    assert full["symbol"] == "GOOG"
