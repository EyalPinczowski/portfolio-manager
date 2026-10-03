from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import select

from app.config import get_settings
from app.db import new_session
from app.main import create_app
from app.models import Invite, Portfolio, PriceAlert, User
from app.timeutil import utcnow
from tests.conftest import make_invite

SignupFn = Callable[..., TestClient]
GOOD = {"password": "correct horse battery", "accept_disclaimer": True, "locale": "en"}


def raw_signup(
    client: TestClient, code: str, email: str = "bob@mail.com", **over: object
) -> Response:
    body = {"invite_code": code, "email": email, **GOOD, **over}
    return client.post("/api/auth/signup", json=body)


def test_signup_requires_valid_invite(client: TestClient) -> None:
    assert raw_signup(client, "nope").status_code == 400
    with new_session() as db:
        db.add(Invite(code="old", expires_at=utcnow() - timedelta(days=1)))
        db.commit()
    assert raw_signup(client, "old").status_code == 400


def test_signup_requires_disclaimer_and_strong_password(client: TestClient) -> None:
    code = make_invite()
    assert raw_signup(client, code, accept_disclaimer=False).status_code == 422
    assert raw_signup(client, code, password="short").status_code == 422
    # the invite was not consumed by the failed attempts
    assert raw_signup(client, code).status_code == 201


def test_invite_is_single_use_and_email_unique(client: TestClient) -> None:
    code = make_invite()
    assert raw_signup(client, code).status_code == 201
    other = TestClient(create_app())
    assert raw_signup(other, code, "c@mail.com").status_code == 400
    assert raw_signup(other, make_invite(), "BOB@mail.com").status_code == 409


def test_signup_sets_disclaimer_and_argon2id_hash(client: TestClient) -> None:
    r = raw_signup(client, make_invite())
    body = r.json()
    assert body["disclaimer_accepted"] is True and body["ocr_consent"] is False
    assert body["csrf_token"] and body["email"] == "bob@mail.com" and body["locale"] == "en"
    with new_session() as db:
        user = db.exec(select(User)).one()
        assert user.password_hash.startswith("$argon2id$")
        assert user.disclaimer_accepted_at is not None


def test_cookie_is_httponly_samesite_lax_and_secure_follows_config(
    env: None, providers: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = TestClient(create_app())
    r = raw_signup(c, make_invite())
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    c2 = TestClient(create_app())
    r2 = raw_signup(c2, make_invite(), "z@mail.com")
    assert "secure" in r2.headers["set-cookie"].lower()


def test_me_requires_session_and_returns_contract_fields(
    signup: SignupFn, client: TestClient
) -> None:
    assert client.get("/api/auth/me").status_code == 401
    me = signup().get("/api/auth/me").json()
    assert set(me) == {"id", "email", "locale", "disclaimer_accepted", "ocr_consent", "csrf_token"}


def test_csrf_header_is_required_on_every_mutation(signup: SignupFn) -> None:
    c = signup()
    token = c.headers.pop("X-CSRF-Token")
    assert c.get("/api/portfolios").status_code == 200  # reads need no header
    for method, path, body in [
        ("POST", "/api/portfolios", {"name": "p", "base_currency": "ILS"}),
        ("POST", "/api/alerts", {"symbol": "AAPL", "op": "above", "price": 1}),
        ("POST", "/api/auth/consent/ocr", None),
        ("POST", "/api/auth/logout", None),
        ("DELETE", "/api/me", None),
    ]:
        r = c.request(method, path, json=body)
        assert r.status_code == 403, (method, path)
    c.headers["X-CSRF-Token"] = "wrong"
    assert c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).status_code == 201
    with new_session() as db:  # the rejected DELETE /me did not delete anything
        assert db.exec(select(User)).first() is not None


def test_login_logout_flow(signup: SignupFn) -> None:
    a = signup("login@mail.com")
    a.post("/api/auth/logout")
    assert a.get("/api/auth/me").status_code == 401
    fresh = TestClient(create_app())
    assert (
        fresh.post(
            "/api/auth/login", json={"email": "login@mail.com", "password": "bad"}
        ).status_code
        == 401
    )
    ok = fresh.post(
        "/api/auth/login", json={"email": "LOGIN@mail.com", "password": GOOD["password"]}
    )
    assert ok.status_code == 200
    assert fresh.get("/api/auth/me").json()["email"] == "login@mail.com"


def test_login_is_rate_limited(signup: SignupFn) -> None:
    signup("rl@mail.com").post("/api/auth/logout")
    c = TestClient(create_app())
    for _ in range(3):  # LOGIN_RATE_LIMIT_ATTEMPTS=3 in the test env
        assert (
            c.post(
                "/api/auth/login", json={"email": "rl@mail.com", "password": "wrong"}
            ).status_code
            == 401
        )
    blocked = c.post("/api/auth/login", json={"email": "rl@mail.com", "password": GOOD["password"]})
    assert blocked.status_code == 429 and "retry-after" in blocked.headers
    # unknown emails are limited too (no user enumeration through the limiter)
    for _ in range(3):
        c.post("/api/auth/login", json={"email": "ghost@mail.com", "password": "x"})
    assert (
        c.post("/api/auth/login", json={"email": "ghost@mail.com", "password": "x"}).status_code
        == 429
    )


def test_ocr_consent(signup: SignupFn) -> None:
    c = signup()
    assert c.post("/api/auth/consent/ocr").json()["ocr_consent"] is True
    assert c.get("/api/auth/me").json()["ocr_consent"] is True


def test_export_and_delete_account(signup: SignupFn) -> None:
    c = signup("gone@mail.com")
    pid = c.post("/api/portfolios", json={"name": "mine", "base_currency": "ILS"}).json()["id"]
    c.post("/api/alerts", json={"symbol": "AAPL", "op": "above", "price": 300})
    export = c.get("/api/me/export").json()
    assert export["user"]["email"] == "gone@mail.com" and "password_hash" not in export["user"]
    assert export["portfolios"][0]["id"] == pid and len(export["alerts"]) == 1
    assert c.delete("/api/me").status_code == 204
    assert c.get("/api/auth/me").status_code == 401
    with new_session() as db:
        assert db.exec(select(User)).first() is None
        assert db.exec(select(Portfolio)).first() is None
        assert db.exec(select(PriceAlert)).first() is None
    fresh = TestClient(create_app())
    assert (
        fresh.post(
            "/api/auth/login", json={"email": "gone@mail.com", "password": GOOD["password"]}
        ).status_code
        == 401
    )


def test_openapi_is_exposed_with_the_contract_paths(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    paths = set(spec["paths"])
    for p in [
        "/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/consent/ocr",
        "/api/me/export", "/api/me", "/api/portfolios", "/api/portfolios/{portfolio_id}",
        "/api/portfolios/{portfolio_id}/summary", "/api/portfolios/combined/summary",
        "/api/portfolios/{portfolio_id}/holdings", "/api/portfolios/{portfolio_id}/holdings/{holding_id}",
        "/api/portfolios/{portfolio_id}/xray", "/api/portfolios/{portfolio_id}/heatmap", "/api/risk/presets",
        "/api/portfolios/{portfolio_id}/imports", "/api/imports/{draft_id}", "/api/imports/{draft_id}/confirm",
        "/api/securities/search", "/api/holdings/{holding_id}/scorecard", "/api/alerts", "/api/alerts/{alert_id}",
        "/api/notifications", "/api/notifications/{notification_id}/read",
    ]:  # fmt: skip
        assert p in paths, p
