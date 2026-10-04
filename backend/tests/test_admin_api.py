"""Admin endpoints: strictly admin-only, no user data beyond email/created/last seen, audited."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import new_session
from app.models import AuditLog, Invite, User
from tests.conftest import make_invite

SignupFn = Callable[..., TestClient]
PW = "correct horse battery"

ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/api/admin/invites", None),
    ("POST", "/api/admin/invites", {}),
    ("POST", "/api/admin/invites/revoke", {"code": "x"}),
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users/1/disable", None),
    ("POST", "/api/admin/users/1/enable", None),
]


def make_admin(email: str) -> None:
    with new_session() as db:
        u = db.exec(select(User).where(User.email == email)).one()
        u.is_admin = True
        db.add(u)
        db.commit()


def login(c: TestClient, email: str) -> None:
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]


def test_every_admin_route_is_403_for_a_member_and_401_without_a_session(
    signup: SignupFn, client: TestClient
) -> None:
    member = signup("member@mail.com")
    before = len(make_invite_rows())
    for method, path, body in ROUTES:
        assert client.request(method, path, json=body).status_code == 401, (method, path)
        r = member.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.text)
    assert len(make_invite_rows()) == before  # nothing was created by the refused calls
    with new_session() as db:
        assert db.exec(select(AuditLog)).all() == []
        assert all(u.disabled_at is None for u in db.exec(select(User)).all())


def make_invite_rows() -> list[Invite]:
    with new_session() as db:
        return list(db.exec(select(Invite)).all())


def test_a_member_cannot_make_themselves_admin(signup: SignupFn) -> None:
    m = signup("member@mail.com")
    # no field of any body can set it (strict bodies); the settings route does not know it either
    assert m.patch("/api/settings", json={"is_admin": True}).status_code == 422
    assert m.get("/api/admin/users").status_code == 403


def test_users_list_has_no_portfolio_data(signup: SignupFn) -> None:
    admin = signup("admin@mail.com")
    member = signup("member@mail.com")
    make_admin("admin@mail.com")
    member.post("/api/portfolios", json={"name": "SECRET-NAME", "base_currency": "ILS"})
    r = admin.get("/api/admin/users")
    assert r.status_code == 200
    rows = r.json()
    assert [u["email"] for u in rows] == ["admin@mail.com", "member@mail.com"]
    assert set(rows[0]) == {
        "id",
        "email",
        "created_at",
        "last_seen_at",
        "is_admin",
        "active",
        "telegram_linked",
    }
    assert rows[0]["is_admin"] is True and rows[1]["is_admin"] is False
    assert rows[1]["last_seen_at"] is not None and rows[1]["active"] is True
    assert "SECRET-NAME" not in r.text and "portfolio" not in r.text.lower()


def test_invites_create_list_revoke(signup: SignupFn) -> None:
    admin = signup("admin@mail.com")
    make_admin("admin@mail.com")
    spare = make_invite()  # created by the CLI (no creator)
    r = admin.post("/api/admin/invites", json={"days": 3})
    assert r.status_code == 201, r.text
    mine = r.json()
    assert mine["status"] == "unused" and mine["created_by_me"] is True and mine["code"]
    listed = admin.get("/api/admin/invites").json()
    by_code = {i["code"]: i for i in listed if i["code"]}
    assert by_code[mine["code"]]["created_by_me"] is True
    assert by_code[spare]["created_by_me"] is False and by_code[spare]["status"] == "unused"
    used = [i for i in listed if i["status"] == "used"]
    assert len(used) == 1 and used[0]["code"] == ""  # the signup invite; its code is not shown
    # revoke: the code stops working
    assert admin.post("/api/admin/invites/revoke", json={"code": mine["code"]}).status_code == 204
    assert admin.post("/api/admin/invites/revoke", json={"code": mine["code"]}).status_code == 404
    new = TestClient(admin.app)
    body = {
        "invite_code": mine["code"],
        "email": "x@mail.com",
        "password": PW,
        "accept_disclaimer": True,
        "locale": "en",
    }
    assert new.post("/api/auth/signup", json=body).status_code == 400
    # a used invite cannot be revoked
    with new_session() as db:
        used_code = db.exec(select(Invite).where(Invite.used_by.is_not(None))).first()  # type: ignore[union-attr]
        assert used_code is not None
        code = used_code.code
    r = admin.post("/api/admin/invites/revoke", json={"code": code})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "body", [{"days": 0}, {"days": 1000}, {"days": "7"}, {"x": 1}, {"days": -1}]
)
def test_invite_bodies_are_strict(signup: SignupFn, body: dict[str, Any]) -> None:
    admin = signup("admin@mail.com")
    make_admin("admin@mail.com")
    assert admin.post("/api/admin/invites", json=body).status_code == 422


def test_invite_creation_is_rate_limited(signup: SignupFn, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ADMIN_INVITES_PER_HOUR", "2")
    get_settings.cache_clear()
    admin = signup("admin@mail.com")
    make_admin("admin@mail.com")
    assert admin.post("/api/admin/invites", json={}).status_code == 201
    assert admin.post("/api/admin/invites", json={}).status_code == 201
    assert admin.post("/api/admin/invites", json={}).status_code == 429


def test_disable_blocks_login_and_kills_sessions_and_enable_restores(signup: SignupFn) -> None:
    admin = signup("admin@mail.com")
    member = signup("member@mail.com")
    make_admin("admin@mail.com")
    mid = next(
        u["id"] for u in admin.get("/api/admin/users").json() if u["email"] == "member@mail.com"
    )
    r = admin.post(f"/api/admin/users/{mid}/disable")
    assert r.status_code == 200 and r.json()["active"] is False
    assert member.get("/api/portfolios").status_code == 401  # the session is gone
    fresh = TestClient(admin.app)
    r = fresh.post("/api/auth/login", json={"email": "member@mail.com", "password": PW})
    assert r.status_code == 403 and r.json()["code"] == "account_disabled"
    # a wrong password is still the generic 401: disabled accounts are not enumerable
    r = fresh.post(
        "/api/auth/login", json={"email": "member@mail.com", "password": "nope-nope-nope"}
    )
    assert r.status_code == 401
    # idempotent
    assert admin.post(f"/api/admin/users/{mid}/disable").status_code == 200
    r = admin.post(f"/api/admin/users/{mid}/enable")
    assert r.status_code == 200 and r.json()["active"] is True
    login(fresh, "member@mail.com")
    # the admin's own data is untouched
    assert admin.get("/api/portfolios").status_code == 200


def test_unknown_user_and_self_disable(signup: SignupFn) -> None:
    admin = signup("admin@mail.com")
    make_admin("admin@mail.com")
    assert admin.post("/api/admin/users/9999/disable").status_code == 404
    assert admin.post("/api/admin/users/9999/enable").status_code == 404
    me = admin.get("/api/auth/me").json()["id"]
    r = admin.post(f"/api/admin/users/{me}/disable")
    assert r.status_code == 409 and r.json()["code"] == "cannot_disable_self"
    assert admin.get("/api/portfolios").status_code == 200


def test_audit_rows_exist_and_hold_no_personal_data(
    signup: SignupFn, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="audit")
    admin = signup("admin@mail.com")
    signup("member@mail.com")
    make_admin("admin@mail.com")
    mid = next(
        u["id"] for u in admin.get("/api/admin/users").json() if u["email"] == "member@mail.com"
    )
    code = admin.post("/api/admin/invites", json={}).json()["code"]
    admin.post("/api/admin/invites/revoke", json={"code": code})
    admin.post(f"/api/admin/users/{mid}/disable")
    admin.post(f"/api/admin/users/{mid}/enable")
    with new_session() as db:
        rows = db.exec(select(AuditLog).order_by(AuditLog.id)).all()  # type: ignore[arg-type]
        assert [r.action for r in rows] == [
            "invite.create",
            "invite.revoke",
            "user.disable",
            "user.enable",
        ]
        assert rows[2].target_user_id == mid and rows[0].target_user_id is None
        dumped = " ".join(str(r.model_dump()) for r in rows)
    for personal in ("admin@mail.com", "member@mail.com", code):
        assert personal not in dumped and personal not in caplog.text
    assert "action=user.disable" in caplog.text
    # deleting the admin account keeps the rows but not who did it
    assert admin.request("DELETE", "/api/me", json={"password": PW}).status_code == 204
    with new_session() as db:
        rows = db.exec(select(AuditLog)).all()
        assert len(rows) == 4 and all(r.actor_user_id is None for r in rows)
