"""Auth hardening: argon2 cost, limiter order, proxy trust, backoff, signup, sessions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi import Request
from fastapi.testclient import TestClient
from sqlmodel import select

from app.auth import passwords
from app.auth.ratelimit import BackoffLimiter, CountLimiter, client_ip
from app.config import Settings, get_settings
from app.db import new_session
from app.main import create_app
from app.models import AuthSession, Invite, User
from tests.conftest import make_invite

SignupFn = Callable[..., TestClient]
PW = "correct horse battery"
GOOD = {"password": PW, "accept_disclaimer": True, "locale": "en"}


def raw_signup(c: TestClient, code: str, email: str, **over: Any) -> Any:
    return c.post("/api/auth/signup", json={"invite_code": code, "email": email, **GOOD, **over})


SECRET = "proxy-secret-0123456789"


def via_proxy(ip: str) -> dict[str, str]:
    """Headers the Pages Function sends: the visitor's IP plus the shared secret."""
    return {"X-Client-IP": ip, "X-Proxy-Auth": SECRET}


def trust_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_HEADER", "X-Client-IP")
    monkeypatch.setenv("PROXY_SHARED_SECRET", SECRET)
    get_settings.cache_clear()


def fake_request(peer: str, headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


# ---------------------------------------------------------------- argon2
def test_argon2_uses_owasp_parameters(client: TestClient) -> None:
    h = passwords.hash_password("whatever-password")
    assert h.startswith("$argon2id$") and "m=19456,t=2,p=1" in h


def test_old_hashes_are_rehashed_on_login(signup: Callable[..., TestClient]) -> None:
    c = signup("old@mail.com")
    old = PasswordHasher().hash(PW)  # argon2-cffi defaults: m=65536
    assert "m=65536" in old
    with new_session() as db:
        u = db.exec(select(User)).one()
        u.password_hash = old
        db.add(u)
        db.commit()
    fresh = TestClient(create_app())
    assert (
        fresh.post("/api/auth/login", json={"email": "old@mail.com", "password": PW}).status_code
        == 200
    )
    with new_session() as db:
        assert "m=19456,t=2,p=1" in db.exec(select(User)).one().password_hash
    del c


def test_hashing_concurrency_is_capped(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    passwords.reset_hasher_cache()
    active = 0
    peak = 0
    lock = threading.Lock()

    class Slow:
        def hash(self, password: str) -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return "h"

    monkeypatch.setattr(passwords, "_hasher", lambda: Slow())
    threads = [threading.Thread(target=passwords.hash_password, args=("x",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak <= 2


# ---------------------------------------------------------------- limiter before hashing
def test_login_limiter_runs_before_any_hashing(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    signup("rl@mail.com").post("/api/auth/logout")
    c = TestClient(create_app())
    for _ in range(3):
        assert (
            c.post("/api/auth/login", json={"email": "rl@mail.com", "password": "x"}).status_code
            == 401
        )
    calls: list[str] = []
    import app.api.auth as api_auth

    monkeypatch.setattr(api_auth, "verify_password", lambda *a: calls.append("verify") or True)
    monkeypatch.setattr(api_auth, "burn_verify", lambda *a: calls.append("burn"))
    r = c.post("/api/auth/login", json={"email": "rl@mail.com", "password": PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert calls == [], "a blocked login must not cost a hash"


# ---------------------------------------------------------------- backoff, not a hard lock
def test_backoff_grows_then_clears_without_a_hard_lock() -> None:
    now = [0.0]
    lim = BackoffLimiter(clock=lambda: now[0])
    free, base, window = 3, 10.0, 300.0
    for _ in range(3):
        assert lim.retry_after("k", free, base, window) == 0
        lim.record_failure("k", window)
    first = lim.retry_after("k", free, base, window)
    assert 10 <= first <= 11  # base seconds after the free attempts are used
    now[0] += first
    assert lim.retry_after("k", free, base, window) == 0  # allowed again, no lock
    lim.record_failure("k", window)
    assert 20 <= lim.retry_after("k", free, base, window) <= 21  # doubled
    now[0] += 1000  # well past the window: forgotten entirely
    assert lim.retry_after("k", free, base, window) == 0
    lim.record_failure("k", window)
    assert lim.retry_after("k", free, base, window) == 0


def test_backoff_is_capped_at_the_window() -> None:
    now = [0.0]
    lim = BackoffLimiter(clock=lambda: now[0])
    for _ in range(40):
        lim.record_failure("k", 300.0)
    assert lim.retry_after("k", 3, 10.0, 300.0) <= 301


def test_backoff_is_per_email_and_ip_pair(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    signup("victim@mail.com").post("/api/auth/logout")
    attacker = TestClient(create_app())
    for _ in range(3):  # 3 free failures per (email, IP): this pair is now in backoff
        attacker.post(
            "/api/auth/login",
            json={"email": "victim@mail.com", "password": "x"},
            headers=via_proxy("198.51.100.1"),
        )
    blocked = attacker.post(
        "/api/auth/login",
        json={"email": "victim@mail.com", "password": PW},
        headers=via_proxy("198.51.100.1"),
    )
    assert blocked.status_code == 429
    # the same email from another IP (the owner, or another attacker IP) is NOT held back
    owner = TestClient(create_app()).post(
        "/api/auth/login",
        json={"email": "victim@mail.com", "password": PW},
        headers=via_proxy("198.51.100.2"),
    )
    assert owner.status_code == 200
    other = attacker.post(
        "/api/auth/login",
        json={"email": "other@mail.com", "password": "x"},
        headers=via_proxy("198.51.100.1"),
    )
    assert other.status_code == 401  # a different email from the same IP is still allowed


def test_one_ip_is_blocked_after_many_distinct_emails(
    env: None, providers: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    c = TestClient(create_app())
    hdr = via_proxy("198.51.100.7")
    codes = [
        c.post(
            "/api/auth/login", json={"email": f"u{i}@mail.com", "password": "x"}, headers=hdr
        ).status_code
        for i in range(14)  # 3 attempts x 4 (ip multiplier) = 12 free failures
    ]
    assert codes[:12] == [401] * 12 and codes[12] == 429
    clean = c.post(
        "/api/auth/login",
        json={"email": "z@mail.com", "password": "x"},
        headers=via_proxy("198.51.100.8"),
    )
    assert clean.status_code == 401  # other IPs are not affected: no global lock-out


# ---------------------------------------------------------------- proxy trust
def test_proxy_header_is_ignored_by_default() -> None:
    req = fake_request("10.0.0.5", via_proxy("203.0.113.50"))
    assert client_ip(req, Settings()) == "10.0.0.5"


def test_proxy_header_is_trusted_only_with_the_shared_secret() -> None:
    s = Settings(trusted_proxy_header="X-Client-IP", proxy_shared_secret=SECRET)
    assert client_ip(fake_request("10.0.0.5", via_proxy("203.0.113.50")), s) == "203.0.113.50"
    assert client_ip(fake_request("10.0.0.5", via_proxy("not-an-ip")), s) == "10.0.0.5"
    assert client_ip(fake_request("10.0.0.5", {}), s) == "10.0.0.5"


def test_spoofed_client_ip_without_the_secret_is_ignored() -> None:
    s = Settings(trusted_proxy_header="X-Client-IP", proxy_shared_secret=SECRET)
    spoof = {"X-Client-IP": "6.6.6.6"}
    assert client_ip(fake_request("10.0.0.5", spoof), s) == "10.0.0.5"
    wrong = {**spoof, "X-Proxy-Auth": "wrong-secret-0123456789"}
    assert client_ip(fake_request("10.0.0.5", wrong), s) == "10.0.0.5"
    empty = {**spoof, "X-Proxy-Auth": ""}
    assert client_ip(fake_request("10.0.0.5", empty), s) == "10.0.0.5"


def test_untrusted_request_falls_back_to_the_host_header_then_the_peer() -> None:
    s = Settings(
        trusted_proxy_header="X-Client-IP",
        proxy_shared_secret=SECRET,
        fallback_ip_header="CF-Connecting-IP",
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )
    req = fake_request("10.0.0.5", {"X-Client-IP": "6.6.6.6", "CF-Connecting-IP": "2a06:98c0::103"})
    # Render's own header, never the spoofed one; IPv6 is bucketed to its /64 (2.0-F item 4)
    assert client_ip(req, s) == "2a06:98c0::"
    assert client_ip(fake_request("10.0.0.5", {"X-Client-IP": "6.6.6.6"}), s) == "10.0.0.5"


def test_secret_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.auth.ratelimit as rl

    calls: list[tuple[bytes, bytes]] = []
    real = rl.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(rl.hmac, "compare_digest", spy)
    s = Settings(trusted_proxy_header="X-Client-IP", proxy_shared_secret=SECRET)
    client_ip(fake_request("10.0.0.5", via_proxy("203.0.113.50")), s)
    assert calls == [(SECRET.encode(), SECRET.encode())]


def test_proxy_header_is_trusted_only_from_the_configured_proxy_networks() -> None:
    s = Settings(
        trusted_proxy_header="X-Client-IP",
        proxy_shared_secret=SECRET,
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )
    spoof = via_proxy("203.0.113.50")
    assert client_ip(fake_request("10.1.2.3", spoof), s) == "203.0.113.50"
    assert client_ip(fake_request("192.0.2.9", spoof), s) == "192.0.2.9"  # not from the proxy


def test_app_refuses_to_start_when_the_proxy_header_has_no_secret(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_HEADER", "X-Client-IP")
    monkeypatch.delenv("PROXY_SHARED_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="PROXY_SHARED_SECRET"), TestClient(create_app()):
        pass
    monkeypatch.setenv("PROXY_SHARED_SECRET", "short")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="at least"), TestClient(create_app()):
        pass
    monkeypatch.setenv("PROXY_SHARED_SECRET", SECRET)
    get_settings.cache_clear()
    with TestClient(create_app()) as ok:
        assert ok.get("/api/health").status_code == 200


# ---------------------------------------------------------------- signup / upload limits, LRU
def test_signup_is_rate_limited_per_ip(
    env: None, providers: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGNUP_RATE_LIMIT_PER_HOUR", "3")
    get_settings.cache_clear()
    c = TestClient(create_app())
    for i in range(3):
        assert raw_signup(c, "nope", f"e{i}@mail.com").status_code == 400
    r = raw_signup(c, make_invite(), "late@mail.com")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "late@mail.com" not in str(r.json())


def test_upload_is_rate_limited_per_user(signup: SignupFn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPLOAD_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    c = signup("up@mail.com")
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"]
    c.post("/api/auth/consent/ocr")
    row = {"name": "טבע", "quantity": 1000, "price": 6500, "value": 65000, "unit": "agorot"}
    codes = [
        c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [row]}).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]
    last = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [row]})
    assert last.status_code == 429 and "retry-after" in last.headers


def test_limiter_storage_is_bounded() -> None:
    cl = CountLimiter(max_keys=100)
    for i in range(1000):
        cl.hit(f"ip:{i}", 5, 60.0)
    assert len(cl) == 100
    bl = BackoffLimiter(max_keys=100)
    for i in range(1000):
        bl.record_failure(f"email:{i}", 60.0)
    assert len(bl) == 100


def test_count_limiter_window_slides() -> None:
    now = [0.0]
    cl = CountLimiter(clock=lambda: now[0])
    assert [cl.hit("k", 2, 60.0) for _ in range(2)] == [0, 0]
    wait = cl.hit("k", 2, 60.0)
    assert 58 <= wait <= 61
    now[0] = 61.0
    assert cl.hit("k", 2, 60.0) == 0


# ---------------------------------------------------------------- signup answers and invite race
def test_existing_email_gets_the_same_answer_and_keeps_the_invite(client: TestClient) -> None:
    assert raw_signup(client, make_invite(), "taken@mail.com").status_code == 201
    spare = make_invite()
    dup = raw_signup(TestClient(create_app()), spare, "Taken@Mail.com")
    bad_invite = raw_signup(TestClient(create_app()), "no-such-code", "new@mail.com")
    assert dup.status_code == bad_invite.status_code == 400
    assert dup.json() == bad_invite.json() and "set-cookie" not in dup.headers
    with new_session() as db:
        inv = db.get(Invite, spare)
        assert inv is not None and inv.used_by is None
        assert len(db.exec(select(User)).all()) == 1  # no half-created user either


def test_invite_use_is_atomic_under_a_race(env: None, providers: object) -> None:
    code = make_invite()
    n = 6
    barrier = threading.Barrier(n)
    results: list[int] = []

    def attempt(i: int) -> None:
        c = TestClient(create_app())
        barrier.wait()
        results.append(raw_signup(c, code, f"race{i}@mail.com").status_code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201] + [400] * (n - 1)
    with new_session() as db:
        assert len(db.exec(select(User)).all()) == 1
        inv = db.get(Invite, code)
        assert inv is not None and inv.used_by is not None


def test_claim_invite_is_a_conditional_update(env: None) -> None:
    from app.api.auth import claim_invite

    code = make_invite()
    with new_session() as db:
        db.add(User(email="a@x.com", password_hash="h"))
        db.add(User(email="b@x.com", password_hash="h"))
        db.commit()
        assert claim_invite(db, code, 1) is True
        assert claim_invite(db, code, 2) is False
        assert claim_invite(db, "missing", 2) is False


# ---------------------------------------------------------------- password for export / delete
def test_export_and_delete_need_the_password(signup: SignupFn) -> None:
    c = signup("pw@mail.com")
    assert c.request("DELETE", "/api/me").status_code == 422  # no body at all
    assert c.post("/api/me/export", json={}).status_code == 422
    assert c.request("DELETE", "/api/me", json={"password": "nope"}).status_code == 403
    assert c.post("/api/me/export", json={"password": "nope"}).status_code == 403
    ok = c.post("/api/me/export", json={"password": PW})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "pw@mail.com"
    with new_session() as db:
        assert db.exec(select(User)).first() is not None  # the wrong-password delete did nothing


def test_password_guessing_through_export_is_rate_limited(signup: SignupFn) -> None:
    c = signup("guess@mail.com")
    codes = [c.post("/api/me/export", json={"password": f"g{i}"}).status_code for i in range(4)]
    assert codes[:3] == [403, 403, 403] and codes[3] == 429
    assert "retry-after" in c.post("/api/me/export", json={"password": PW}).headers


# ---------------------------------------------------------------- sessions
def login(email: str) -> TestClient:
    c = TestClient(create_app())
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def test_sessions_list_marks_the_current_one(signup: SignupFn) -> None:
    a = signup("s@mail.com")
    b = login("s@mail.com")
    rows = a.get("/api/auth/sessions").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    assert set(rows[0]) == {"id", "created_at", "last_seen_at", "current"}
    assert isinstance(rows[0]["id"], int)
    mine = next(r for r in a.get("/api/auth/sessions").json() if r["current"])
    theirs = next(r for r in b.get("/api/auth/sessions").json() if r["current"])
    assert mine["id"] != theirs["id"]


def test_revoke_one_session_logs_that_device_out(signup: SignupFn) -> None:
    a = signup("s2@mail.com")
    b = login("s2@mail.com")
    other = next(r for r in a.get("/api/auth/sessions").json() if not r["current"])
    assert a.delete(f"/api/auth/sessions/{other['id']}").status_code == 204
    assert b.get("/api/auth/me").status_code == 401
    assert a.get("/api/auth/me").status_code == 200
    assert a.delete(f"/api/auth/sessions/{other['id']}").status_code == 404


def test_revoke_all_keeps_the_current_session(signup: SignupFn) -> None:
    a = signup("s3@mail.com")
    b, c = login("s3@mail.com"), login("s3@mail.com")
    assert a.post("/api/auth/sessions/revoke-all").status_code == 204
    assert b.get("/api/auth/me").status_code == 401 and c.get("/api/auth/me").status_code == 401
    assert a.get("/api/auth/me").status_code == 200
    assert len(a.get("/api/auth/sessions").json()) == 1


def test_sessions_are_private_to_their_owner(signup: SignupFn) -> None:
    a = signup("sa@mail.com")
    b = signup("sb@mail.com")
    a_id = a.get("/api/auth/sessions").json()[0]["id"]
    assert b.delete(f"/api/auth/sessions/{a_id}").status_code == 404
    assert all(r["id"] != a_id for r in b.get("/api/auth/sessions").json())
    b.post("/api/auth/sessions/revoke-all")
    assert a.get("/api/auth/me").status_code == 200
    with new_session() as db:
        assert db.exec(select(AuthSession).where(AuthSession.user_id == 1)).first() is not None


def test_last_seen_is_refreshed_at_most_once_per_interval(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    a = signup("ls@mail.com")
    with new_session() as db:
        row = db.exec(select(AuthSession)).one()
        row.last_seen_at = row.last_seen_at - timedelta(hours=1)
        old = row.last_seen_at
        db.add(row)
        db.commit()
    a.get("/api/auth/me")
    with new_session() as db:
        assert db.exec(select(AuthSession)).one().last_seen_at > old
