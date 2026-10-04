"""Owner scoping: user B gets 404 on every resource of user A."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.db import new_session
from app.models import Notification, PriceQuote
from app.timeutil import utcnow
from tests.conftest import FakeQuotes, png_bytes

SignupFn = Callable[..., TestClient]

OCR = "טבע 1,000 6,500 65,000\nלאומי 500 3,200 16,000\n"


def test_cross_user_access_returns_404(
    signup: SignupFn, quotes: FakeQuotes, ocr_text: dict[str, str]
) -> None:
    quotes.set("AAPL", 200.0, "USD")
    ocr_text["text"] = OCR
    a = signup("a@mail.com")
    b = signup("b@mail.com")

    # ---- user A creates one of everything
    pid = a.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    hold = a.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 3, "avg_cost": 100}
    )
    assert hold.status_code == 201, hold.text
    hid = hold.json()["id"]
    aid = a.post("/api/alerts", json={"symbol": "AAPL", "op": "above", "price": 300}).json()["id"]
    a.post("/api/auth/consent/ocr")
    draft = a.post(
        f"/api/portfolios/{pid}/imports", content=png_bytes(), headers={"Content-Type": "image/png"}
    )
    assert draft.status_code == 201, draft.text
    did = draft.json()["id"]
    with new_session() as db:
        n = Notification(user_id=1, kind="price_alert", title="t", body="b", created_at=utcnow())
        db.add(n)
        db.commit()
        nid = n.id
        assert db.get(PriceQuote, "AAPL") is not None

    # ---- user B owns a portfolio of his own (ids must not cross over either)
    bpid = b.post("/api/portfolios", json={"name": "B", "base_currency": "USD"}).json()["id"]
    b.post("/api/auth/consent/ocr")

    def status_of(method: str, path: str, body: Any = None) -> int:
        return b.request(method, path, json=body).status_code

    # portfolio level
    for method, path, body in [
        ("GET", f"/api/portfolios/{pid}", None),
        ("PATCH", f"/api/portfolios/{pid}", {"name": "hacked"}),
        ("DELETE", f"/api/portfolios/{pid}", None),
        ("GET", f"/api/portfolios/{pid}/summary", None),
        ("GET", f"/api/portfolios/{pid}/holdings", None),
        ("GET", f"/api/portfolios/{pid}/xray", None),
        ("GET", f"/api/portfolios/{pid}/heatmap", None),
        ("POST", f"/api/portfolios/{pid}/exit-review", {}),
        (
            "POST",
            f"/api/portfolios/{pid}/exit-review",
            {"horizon": "1m", "prior_stops": {"AAPL": 5}},
        ),
        (
            "POST",
            f"/api/portfolios/{pid}/buy-ideas",
            {
                "amount": 1000,
                "currency": "ILS",
                "horizon": "1m",
                "risk": "balanced",
                "markets": ["US"],
                "asset_types": ["stock"],
            },
        ),
        ("POST", f"/api/portfolios/{pid}/holdings", {"symbol": "MSFT", "quantity": 1}),
    ]:
        assert status_of(method, path, body) == 404, (method, path)

    # holding level, through A's portfolio and through B's own portfolio
    for pth in (pid, bpid):
        assert status_of("PATCH", f"/api/portfolios/{pth}/holdings/{hid}", {"quantity": 99}) == 404
        assert status_of("DELETE", f"/api/portfolios/{pth}/holdings/{hid}") == 404
    assert status_of("GET", f"/api/holdings/{hid}/scorecard") == 404
    assert status_of("GET", f"/api/holdings/{hid}/exit-levels") == 404
    assert status_of("GET", f"/api/holdings/{hid}/exit-levels?horizon=1m&risk=balanced") == 404

    # imports
    r = b.post(
        f"/api/portfolios/{pid}/imports", content=png_bytes(), headers={"Content-Type": "image/png"}
    )
    assert r.status_code == 404
    row = {"name": "טבע", "quantity": 1000, "price": 6500, "value": 65000, "unit": "agorot"}
    assert status_of("POST", f"/api/portfolios/{pid}/imports/rows", {"rows": [row]}) == 404
    assert status_of("GET", f"/api/imports/{did}") == 404
    assert status_of("PATCH", f"/api/imports/{did}", {"rows": []}) == 404
    assert status_of("POST", f"/api/imports/{did}/confirm") == 404

    # holding risk override goes through the same owner check as every other holding edit
    for pth in (pid, bpid):
        override = {"risk_override": {"max_position_pct": 5}}
        assert status_of("PATCH", f"/api/portfolios/{pth}/holdings/{hid}", override) == 404

    # sessions: B can neither see, revoke nor mass-revoke A's sessions
    a_session = a.get("/api/auth/sessions").json()[0]["id"]
    assert all(x["id"] != a_session for x in b.get("/api/auth/sessions").json())
    assert status_of("DELETE", f"/api/auth/sessions/{a_session}") == 404
    assert b.post("/api/auth/sessions/revoke-all").status_code == 204
    assert a.get("/api/auth/me").status_code == 200

    # export is the caller's own data only (and needs the caller's own password)
    mine = b.post("/api/me/export", json={"password": "correct horse battery"}).json()
    assert [p["id"] for p in mine["portfolios"]] == [bpid] and mine["alerts"] == []
    assert mine["user"]["email"] == "b@mail.com"

    # alerts and notifications
    assert status_of("DELETE", f"/api/alerts/{aid}") == 404
    assert b.get("/api/alerts").json() == []
    assert status_of("POST", f"/api/notifications/{nid}/read") == 404
    assert b.get("/api/notifications").json() == []

    # B's own listings and combined view never include A's data
    assert [p["id"] for p in b.get("/api/portfolios").json()] == [bpid]
    assert b.get("/api/portfolios/combined/summary").json()["value"]["ils"] == 0

    # ---- A is untouched
    assert a.get(f"/api/portfolios/{pid}").json()["name"] == "A"
    assert len(a.get(f"/api/portfolios/{pid}/holdings").json()) == 1
    assert a.get(f"/api/imports/{did}").json()["status"] == "draft"
    assert len(a.get("/api/alerts").json()) == 1
    assert a.get("/api/notifications").json()[0]["id"] == nid

    # deleting B's own account (B's password) leaves A's data untouched
    assert (
        b.request("DELETE", "/api/me", json={"password": "correct horse battery"}).status_code
        == 204
    )
    assert a.get(f"/api/portfolios/{pid}").json()["name"] == "A"
    assert len(a.get(f"/api/portfolios/{pid}/holdings").json()) == 1


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    for path in [
        "/api/portfolios",
        "/api/alerts",
        "/api/notifications",
        "/api/risk/presets",
        "/api/auth/sessions",
        "/api/launch-gate",
        "/api/portfolios/1/xray",
        "/api/imports/1",
    ]:
        assert client.get(path).status_code == 401, path
    for method, path, body in [
        ("POST", "/api/me/export", {"password": "x"}),
        ("DELETE", "/api/me", {"password": "x"}),
        ("DELETE", "/api/auth/sessions/1", None),
        ("POST", "/api/auth/sessions/revoke-all", None),
        ("POST", "/api/portfolios/1/imports/rows", {"rows": []}),
    ]:
        assert client.request(method, path, json=body).status_code == 401, (method, path)
    # the raw-body upload is turned away at the door, before its (small) body is read
    r = client.post(
        "/api/portfolios/1/imports", content=b"x", headers={"Content-Type": "image/png"}
    )
    assert r.status_code == 401
