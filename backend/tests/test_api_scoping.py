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
        f"/api/portfolios/{pid}/imports", files={"file": ("s.png", png_bytes(), "image/png")}
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
        ("POST", f"/api/portfolios/{pid}/holdings", {"symbol": "MSFT", "quantity": 1}),
    ]:
        assert status_of(method, path, body) == 404, (method, path)

    # holding level, through A's portfolio and through B's own portfolio
    for pth in (pid, bpid):
        assert status_of("PATCH", f"/api/portfolios/{pth}/holdings/{hid}", {"quantity": 99}) == 404
        assert status_of("DELETE", f"/api/portfolios/{pth}/holdings/{hid}") == 404
    assert status_of("GET", f"/api/holdings/{hid}/scorecard") == 404

    # imports
    r = b.post(
        f"/api/portfolios/{pid}/imports", files={"file": ("s.png", png_bytes(), "image/png")}
    )
    assert r.status_code == 404
    assert status_of("GET", f"/api/imports/{did}") == 404
    assert status_of("PATCH", f"/api/imports/{did}", {"rows": []}) == 404
    assert status_of("POST", f"/api/imports/{did}/confirm") == 404

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


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    for path in [
        "/api/portfolios",
        "/api/alerts",
        "/api/notifications",
        "/api/risk/presets",
        "/api/me/export",
    ]:
        assert client.get(path).status_code == 401, path
