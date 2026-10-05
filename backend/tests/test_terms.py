from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.config import get_settings
from app.models import TermsAcceptance, User
from tests.conftest import SignupFn

PASSWORD = "correct horse battery"


def _version() -> str:
    return get_settings().terms_version


def test_blocked_before_acceptance_allowed_after(signup: SignupFn) -> None:
    c = signup(accept_terms=False)
    r = c.get("/api/launch-gate")
    assert r.status_code == 403 and r.json() == {"detail": "terms_not_accepted"}
    assert c.get("/api/terms").json()["accepted"] is False
    # exempt routes keep working
    assert c.get("/api/auth/me").json()["terms_accepted"] is False
    assert c.get("/api/settings").status_code == 200
    r = c.post("/api/terms/accept", json={"version": _version()})
    assert r.status_code == 200 and r.json()["accepted"] is True and r.json()["accepted_at"]
    assert c.get("/api/launch-gate").status_code == 200
    assert c.get("/api/auth/me").json()["terms_accepted"] is True


def test_post_routes_blocked_too(signup: SignupFn) -> None:
    c = signup(accept_terms=False)
    r = c.post("/api/portfolios", json={"name": "x"})
    assert r.status_code == 403 and r.json()["detail"] == "terms_not_accepted"


def test_wrong_version_409(signup: SignupFn) -> None:
    c = signup(accept_terms=False)
    assert c.post("/api/terms/accept", json={"version": "1999-01-01"}).status_code == 409
    assert c.get("/api/terms").json()["accepted"] is False


def test_requires_csrf_and_login(signup: SignupFn, client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/terms").status_code == 401
    c = signup(accept_terms=False)
    del c.headers["X-CSRF-Token"]
    assert c.post("/api/terms/accept", json={"version": _version()}).status_code == 403


def test_reblocked_when_version_bumps(signup: SignupFn, monkeypatch: pytest.MonkeyPatch) -> None:
    c = signup()
    assert c.get("/api/launch-gate").status_code == 200
    monkeypatch.setenv("TERMS_VERSION", "2099-01-01")
    get_settings.cache_clear()
    assert c.get("/api/launch-gate").status_code == 403
    t = c.get("/api/terms").json()
    assert t == {"version": "2099-01-01", "accepted": False, "accepted_at": None}
    assert c.post("/api/terms/accept", json={"version": "2099-01-01"}).status_code == 200
    assert c.get("/api/launch-gate").status_code == 200


def test_admin_is_gated_too(signup: SignupFn, db: Session) -> None:
    c = signup(accept_terms=False)
    user = db.exec(select(User)).one()
    user.is_admin = True
    db.add(user)
    db.commit()
    assert c.get("/api/admin/users").status_code == 403
    c.post("/api/terms/accept", json={"version": _version()})
    assert c.get("/api/admin/users").status_code != 403


def test_no_cross_user_leakage(signup: SignupFn) -> None:
    a = signup("a@mail.com", accept_terms=True)
    b = signup("b@mail.com", accept_terms=False)
    assert a.get("/api/terms").json()["accepted"] is True
    assert b.get("/api/terms").json()["accepted"] is False
    assert b.get("/api/launch-gate").status_code == 403


def test_accept_is_idempotent_and_history_kept(signup: SignupFn, db: Session) -> None:
    c = signup(accept_terms=False)
    for _ in range(2):
        assert c.post("/api/terms/accept", json={"version": _version()}).status_code == 200
    assert len(db.exec(select(TermsAcceptance)).all()) == 1


def test_cascade_on_user_delete(signup: SignupFn, db: Session) -> None:
    c = signup()
    assert db.exec(select(TermsAcceptance)).all()
    r = c.request("DELETE", "/api/me", json={"password": PASSWORD})
    assert r.status_code == 204
    db.expire_all()
    assert db.exec(select(TermsAcceptance)).all() == []


def test_delete_and_export_allowed_without_acceptance(signup: SignupFn) -> None:
    c = signup(accept_terms=False)
    assert c.post("/api/me/export", json={"password": PASSWORD}).status_code == 200
    assert c.request("DELETE", "/api/me", json={"password": PASSWORD}).status_code == 204
