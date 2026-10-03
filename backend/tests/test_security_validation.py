"""Typed, bounded request bodies (no mass assignment) and verified-only search."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import new_session
from app.models import Holding, Portfolio, Security
from app.portfolio.quotes import store_quotes
from app.providers.base import Quote
from app.scoring.risk import resolve_risk_filter
from app.timeutil import utcnow
from tests.conftest import FakeQuotes

SignupFn = Callable[..., TestClient]


def setup_holding(c: TestClient, symbol: str = "AAPL") -> tuple[int, int]:
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    r = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": symbol, "quantity": 3, "avg_cost": 100}
    )
    assert r.status_code == 201, r.text
    return pid, r.json()["id"]


# ---------------------------------------------------------------- per-holding override
@pytest.mark.parametrize(
    "bad",
    [
        {"max_position_pct": "abc"},
        {"max_position_pct": -50},
        {"max_position_pct": 0},
        {"max_position_pct": 101},
        {"max_position_pct": 1e308},
        {"max_position_pct": True},
        {"max_position_pct": None, "unknown_key": 1},
        {"stop_type": "yolo"},
        {"preset": "ultra"},
        {"min_rr": [1]},
    ],
)
def test_bad_risk_override_is_422_and_nothing_is_stored(
    signup: SignupFn, bad: dict[str, Any]
) -> None:
    c = signup()
    pid, hid = setup_holding(c)
    r = c.patch(f"/api/portfolios/{pid}/holdings/{hid}", json={"risk_override": bad})
    assert r.status_code == 422, r.text
    with new_session() as db:
        assert db.get(Holding, hid).risk_override is None  # type: ignore[union-attr]
    assert c.get(f"/api/portfolios/{pid}/xray").status_code == 200  # the review's 500


def test_valid_risk_override_is_stored_typed_and_used_by_the_xray(signup: SignupFn) -> None:
    c = signup()
    pid, hid = setup_holding(c)
    r = c.patch(
        f"/api/portfolios/{pid}/holdings/{hid}",
        json={"risk_override": {"max_position_pct": 5, "stop_type": "fixed"}},
    )
    assert r.status_code == 200, r.text
    with new_session() as db:
        assert db.get(Holding, hid).risk_override == {"max_position_pct": 5.0, "stop_type": "fixed"}  # type: ignore[union-attr]
    x = c.get(f"/api/portfolios/{pid}/xray")
    assert x.status_code == 200
    c.patch(f"/api/portfolios/{pid}/holdings/{hid}", json={"risk_override": None})
    with new_session() as db:
        assert db.get(Holding, hid).risk_override is None  # type: ignore[union-attr]


def test_xray_never_500s_on_legacy_unvalidated_stored_json(signup: SignupFn) -> None:
    c = signup()
    pid, hid = setup_holding(c)
    with new_session() as db:
        h = db.get(Holding, hid)
        assert h is not None
        h.risk_override = {"max_position_pct": "abc"}
        p = db.get(Portfolio, pid)
        assert p is not None
        p.risk_filter = {"preset": "nonsense", "max_position_pct": "abc", "max_sector_pct": -5,
                         "stop_type": 7, "min_rr": "inf"}  # fmt: skip
        db.add(h)
        db.add(p)
        db.commit()
    assert c.get(f"/api/portfolios/{pid}/xray").status_code == 200
    assert c.get(f"/api/portfolios/{pid}/holdings").status_code == 200
    out = c.get(f"/api/portfolios/{pid}")
    assert out.status_code == 200 and out.json()["risk_filter"]["max_position_pct"] > 0
    with new_session() as db:
        f = resolve_risk_filter({"max_position_pct": "abc", "max_sector_pct": -5, "stop_type": 7})
        assert 0 < f.max_position_pct <= 100 and f.stop_type in ("fixed", "trailing", "both")
        h = db.get(Holding, hid)
        assert h is not None
        h.risk_override = ["not", "a", "dict"]  # type: ignore[assignment]
        db.add(h)
        db.commit()
    assert c.get(f"/api/portfolios/{pid}/xray").status_code == 200


# ---------------------------------------------------------------- portfolio risk filter
@pytest.mark.parametrize(
    "bad",
    [
        {"max_position_pct": "abc"},
        {"max_position_pct": -50},
        {"max_sector_pct": 500},
        {"stop_type": "nope"},
        {"preset": "unknown"},
        {"admin": True},
    ],
)
def test_bad_portfolio_risk_filter_is_422(signup: SignupFn, bad: dict[str, Any]) -> None:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    before = c.get(f"/api/portfolios/{pid}").json()["risk_filter"]
    assert c.patch(f"/api/portfolios/{pid}", json={"risk_filter": bad}).status_code == 422
    assert c.get(f"/api/portfolios/{pid}").json()["risk_filter"] == before


def test_portfolio_risk_filter_accepts_the_full_object_the_ui_sends(signup: SignupFn) -> None:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    current = c.get(f"/api/portfolios/{pid}").json()["risk_filter"]
    r = c.patch(f"/api/portfolios/{pid}", json={"risk_filter": {**current, "max_position_pct": 7}})
    assert r.status_code == 200 and r.json()["risk_filter"]["max_position_pct"] == 7
    r = c.patch(f"/api/portfolios/{pid}", json={"risk_filter": {"preset": "conservative"}})
    assert r.json()["risk_filter"]["preset"] == "conservative"


# ---------------------------------------------------------------- unknown fields everywhere
def test_unknown_fields_are_rejected_on_every_body(signup: SignupFn, client: TestClient) -> None:
    c = signup()
    pid, hid = setup_holding(c)
    draft = c.post(
        f"/api/portfolios/{pid}/imports/rows",
        json={
            "rows": [
                {"name": "טבע", "quantity": 1000, "price": 6500, "value": 65000, "unit": "agorot"}
            ]
        },
    ).json()
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("POST", "/api/portfolios", {"name": "x", "is_admin": True}),
        ("PATCH", f"/api/portfolios/{pid}", {"owner_id": 2}),
        (
            "POST",
            f"/api/portfolios/{pid}/holdings",
            {"symbol": "MSFT", "quantity": 1, "portfolio_id": 9},
        ),
        ("PATCH", f"/api/portfolios/{pid}/holdings/{hid}", {"symbol": "MSFT"}),
        ("POST", "/api/alerts", {"symbol": "AAPL", "op": "above", "price": 1, "user_id": 2}),
        ("PATCH", f"/api/imports/{draft['id']}", {"status": "confirmed"}),
        ("POST", f"/api/portfolios/{pid}/imports/rows", {"rows": [], "extra": 1}),
        ("POST", "/api/me/export", {"password": "x", "also": 1}),
    ]
    for method, path, body in cases:
        assert c.request(method, path, json=body).status_code == 422, (method, path)
    anon = TestClient(client.app)
    base = {"email": "a@mail.com", "password": "x"}
    assert anon.post("/api/auth/login", json={**base, "admin": True}).status_code == 422
    sign = {"invite_code": "c", "accept_disclaimer": True, **base, "is_admin": True}
    assert anon.post("/api/auth/signup", json=sign).status_code == 422


def test_row_edits_reject_extra_row_fields_and_negative_amounts(signup: SignupFn) -> None:
    c = signup()
    pid, _ = setup_holding(c)
    row = {"name": "טבע", "quantity": 1000, "price": 6500, "value": 65000, "unit": "agorot"}
    d = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [row]}).json()
    url = f"/api/imports/{d['id']}"
    assert c.patch(url, json={"rows": [{**d["rows"][0], "ocr_text": "raw"}]}).status_code == 422
    change = {
        "row_index": 0,
        "symbol": "TEVA.TA",
        "type": "buy",
        "quantity": 5,
        "amount": 100.0,
        "currency": "ILS",
    }
    assert c.patch(url, json={"proposed_changes": [{**change, "amount": -1}]}).status_code == 422
    assert c.patch(url, json={"proposed_changes": [{**change, "quantity": -1}]}).status_code == 422
    assert c.patch(url, json={"proposed_changes": [{**change, "type": "gift"}]}).status_code == 422
    assert c.patch(url, json={"proposed_changes": [{**change, "amount": 0}]}).status_code == 200


# ---------------------------------------------------------------- symbols and numbers
@pytest.mark.parametrize(
    "symbol",
    ["", "AAPL; DROP TABLE", "<script>", "A" * 21, "AA PL", "אפל", "../etc", "AAPL,MSFT"],
)
def test_bad_symbols_are_422(signup: SignupFn, symbol: str) -> None:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    assert (
        c.post(
            f"/api/portfolios/{pid}/holdings", json={"symbol": symbol, "quantity": 1}
        ).status_code
        == 422
    )
    assert (
        c.post("/api/alerts", json={"symbol": symbol, "op": "above", "price": 1}).status_code == 422
    )
    with new_session() as db:
        assert db.exec(select(Security).where(Security.symbol == symbol)).first() is None


def test_symbols_are_trimmed_and_upper_cased(signup: SignupFn) -> None:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    r = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": " msft ", "quantity": 1})
    assert r.status_code == 201 and r.json()["symbol"] == "MSFT"
    a = c.post("/api/alerts", json={"symbol": "teva.ta", "op": "below", "price": 5})
    assert a.status_code == 201 and a.json()["symbol"] == "TEVA.TA"
    assert (
        c.post("/api/alerts", json={"symbol": "^GSPC", "op": "below", "price": 5}).status_code
        == 201
    )
    assert (
        c.post("/api/alerts", json={"symbol": "ILS=X", "op": "below", "price": 5}).status_code
        == 201
    )
    assert (
        c.post("/api/alerts", json={"symbol": "BRK-B", "op": "below", "price": 5}).status_code
        == 201
    )


@pytest.mark.parametrize(
    "body",
    [
        {"quantity": 0},
        {"quantity": -1},
        {"quantity": 1e13},
        {"quantity": 1, "avg_cost": -1},
        {"quantity": "x"},
    ],
)
def test_holding_numbers_are_bounded(signup: SignupFn, body: dict[str, Any]) -> None:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    assert (
        c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", **body}).status_code
        == 422
    )


def test_alert_price_and_op_are_validated(signup: SignupFn) -> None:
    c = signup()
    for bad in ({"price": 0}, {"price": -3}, {"price": 1e15}, {"op": "sideways"}):
        body = {"symbol": "AAPL", "op": "above", "price": 1, **bad}
        assert c.post("/api/alerts", json=body).status_code == 422, bad


# ---------------------------------------------------------------- alert cap
def test_alerts_are_capped_per_user(signup: SignupFn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_ALERTS_PER_USER", "3")
    get_settings.cache_clear()
    a, b = signup("a@mail.com"), signup("b@mail.com")
    body = {"symbol": "AAPL", "op": "above", "price": 300}
    assert [a.post("/api/alerts", json=body).status_code for _ in range(4)] == [201, 201, 201, 409]
    assert b.post("/api/alerts", json=body).status_code == 201  # per user, not global
    one = a.get("/api/alerts").json()[0]["id"]
    a.delete(f"/api/alerts/{one}")
    assert a.post("/api/alerts", json=body).status_code == 201


def test_default_alert_cap_is_50() -> None:
    assert get_settings().max_alerts_per_user == 50


# ---------------------------------------------------------------- search
def hits(c: TestClient, q: str) -> list[str]:
    return [h["symbol"] for h in c.get("/api/securities/search", params={"q": q}).json()]


def test_search_hides_other_users_unverified_tickers(signup: SignupFn, quotes: FakeQuotes) -> None:
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pid = a.post("/api/portfolios", json={"name": "p", "base_currency": "USD"}).json()["id"]
    assert (
        a.post(
            f"/api/portfolios/{pid}/holdings", json={"symbol": "ZZQX", "quantity": 1}
        ).status_code
        == 201
    )
    a.post("/api/alerts", json={"symbol": "QQWW", "op": "above", "price": 5})
    for who in (a, b):
        assert hits(who, "ZZQX") == [] and hits(who, "QQWW") == []  # nobody finds unverified rows
    assert "AAPL" in hits(b, "AAPL")  # seeded ones are always there
    with new_session() as db:  # the provider returns a price: now it is a real, verified ticker
        store_quotes(db, [Quote(symbol="ZZQX", price=5.0, currency="USD", as_of=utcnow())])
        sec = db.get(Security, "ZZQX")
        assert sec is not None and sec.verified is True
    assert hits(b, "ZZQX") == ["ZZQX"]


def test_seeded_securities_are_verified_and_inferred_ones_are_not(db: Any) -> None:
    from app.securities import infer_security

    assert all(s.verified for s in db.exec(select(Security)).all())
    assert infer_security("NEWT").verified is False and infer_security("X.TA").verified is False


def test_search_query_length_is_bounded(signup: SignupFn) -> None:
    c = signup()
    assert c.get("/api/securities/search", params={"q": "a" * 65}).status_code == 422
    assert c.get("/api/securities/search").status_code == 422
