"""IPv6 buckets, Turnstile on the email key, Turnstile hostname/action and device-cookie revocation
(2.0-F item 4; the reviewer's /64 rotation probe is replayed)."""

from __future__ import annotations

import ipaddress

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import ratelimit
from app.auth.ratelimit import client_ip
from app.auth.turnstile import CloudflareVerifier, set_turnstile_verifier
from app.config import Settings, get_settings
from app.main import create_app
from tests.conftest import SignupFn
from tests.test_login_backoff_turnstile import FakeVerifier, login
from tests.test_security_auth import PW, fake_request, trust_proxy

VICTIM = "victim@mail.com"
SECRET = "s" * 32


# ---------------------------------------------------------------- client_ip normalisation
@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("203.0.113.5", "203.0.113.5"),
        ("::ffff:203.0.113.5", "203.0.113.5"),  # IPv4-mapped is the IPv4 address
        ("::FFFF:203.0.113.5", "203.0.113.5"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::"),  # /64 bucket
        ("2001:db8:1:2:1111:2222:3333:4444", "2001:db8:1:2::"),
        ("fe80::1%eth0", "fe80::"),  # zone id removed
        ("fe80::1%25eth0".replace("%25", "%"), "fe80::"),
        ("[2001:db8:1:2::9]", "2001:db8:1:2::"),
    ],
)
def test_client_ip_is_normalised(sent: str, expected: str) -> None:
    s = Settings(_env_file=None, trusted_proxy_header="X-Client-IP", proxy_shared_secret=SECRET)
    req = fake_request("10.0.0.1", {"X-Client-IP": sent, "X-Proxy-Auth": SECRET})
    assert client_ip(req, s) == expected


def test_the_peer_address_is_normalised_too_and_different_64s_stay_different() -> None:
    s = Settings(_env_file=None)
    assert client_ip(fake_request("::ffff:198.51.100.7", {}), s) == "198.51.100.7"
    assert client_ip(fake_request("2001:db8:5:6:7:8:9:a", {}), s) == "2001:db8:5:6::"
    assert client_ip(fake_request("2001:db8:5:7::1", {}), s) != client_ip(
        fake_request("2001:db8:5:6::1", {}), s
    )


# ---------------------------------------------------------------- the /64 rotation probe
def _rotating(i: int) -> str:
    return str(ipaddress.IPv6Address(int(ipaddress.IPv6Address("2001:db8:aa:bb::")) + 1000 + i))


def test_rotating_inside_one_64_no_longer_buys_fresh_guesses(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe: 60 wrong guesses a window from one /64 (about 28k a day)."""
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    attacker = TestClient(create_app())
    codes = [login(attacker, VICTIM, "wrong", _rotating(i)).status_code for i in range(60)]  # type: ignore[attr-defined]
    assert codes.count(401) <= get_settings().login_rate_limit_attempts
    assert codes.count(429) >= 55


def test_the_challenge_triggers_for_a_rotating_ipv6_attacker(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "k")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sk")
    monkeypatch.setenv("TURNSTILE_AFTER_FAILURES", "2")
    get_settings.cache_clear()
    fake = FakeVerifier()
    set_turnstile_verifier(fake)
    try:
        signup(VICTIM).post("/api/auth/logout")
        attacker = TestClient(create_app())
        codes = [login(attacker, VICTIM, "wrong", _rotating(i)).status_code for i in range(60)]  # type: ignore[attr-defined]
    finally:
        set_turnstile_verifier(None)
    assert codes.count(401) == 2  # then: challenge (403) and backoff (429), never a password check
    assert 403 in codes
    assert fake.calls == []  # no token was ever sent: nothing for Cloudflare to verify


# ---------------------------------------------------------------- Turnstile on the email key
@pytest.fixture
def turnstile_email(env: None, monkeypatch: pytest.MonkeyPatch) -> FakeVerifier:
    trust_proxy(monkeypatch)
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-key")
    monkeypatch.setenv("TURNSTILE_AFTER_FAILURES", "50")  # the pair key alone never triggers
    monkeypatch.setenv("TURNSTILE_EMAIL_AFTER_FAILURES", "4")
    get_settings.cache_clear()
    fake = FakeVerifier()
    set_turnstile_verifier(fake)
    yield fake  # type: ignore[misc]
    set_turnstile_verifier(None)


def test_many_ips_against_one_email_trigger_the_challenge(
    signup: SignupFn, turnstile_email: FakeVerifier
) -> None:
    signup(VICTIM).post("/api/auth/logout")
    attacker = TestClient(create_app())
    for i in range(4):  # one guess from each of several addresses
        assert login(attacker, VICTIM, "wrong", f"198.51.100.{i + 1}").status_code == 401  # type: ignore[attr-defined]
    fresh = TestClient(create_app())
    r = login(fresh, VICTIM, PW, "203.0.113.200")
    assert r.status_code == 403 and r.json()["code"] == "turnstile_required"  # type: ignore[attr-defined]
    ok = login(fresh, VICTIM, PW, "203.0.113.200", turnstile_token="good-token")
    assert ok.status_code == 200  # type: ignore[attr-defined]  # the owner solves it and gets in


def test_a_known_device_is_not_challenged_or_locked_by_the_email_key(
    signup: SignupFn, turnstile_email: FakeVerifier
) -> None:
    signup(VICTIM).post("/api/auth/logout")
    phone = TestClient(create_app())
    assert login(phone, VICTIM, PW, "203.0.113.9").status_code == 200  # type: ignore[attr-defined]
    attacker = TestClient(create_app())
    for i in range(30):
        login(attacker, VICTIM, "wrong", f"198.51.100.{i + 1}")
    assert login(phone, VICTIM, PW, "203.0.113.10").status_code == 200  # type: ignore[attr-defined]
    assert turnstile_email.calls == []


# ---------------------------------------------------------------- Turnstile hostname / action
def _siteverify(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    def fake_post(url: str, data: dict[str, str], timeout: float) -> httpx.Response:
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"success": True, "hostname": "app.example.com", "action": "login"}, True),
        ({"success": True, "hostname": "evil.example.net", "action": "login"}, False),
        ({"success": True, "hostname": "app.example.com", "action": "signup"}, False),
        ({"success": True, "hostname": "app.example.com"}, False),  # no action echoed
        ({"success": True, "action": "login"}, False),  # no hostname echoed
        ({"success": False, "hostname": "app.example.com", "action": "login"}, False),
    ],
)
def test_siteverify_hostname_and_action_are_checked(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object], expected: bool
) -> None:
    _siteverify(monkeypatch, payload)
    v = CloudflareVerifier(
        Settings(
            _env_file=None,
            turnstile_secret_key="sek",
            turnstile_allowed_hostnames=["app.example.com"],
        )
    )
    assert v.verify("tok", "203.0.113.1") is expected


def test_hostnames_default_to_the_cors_origins_and_action_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _siteverify(monkeypatch, {"success": True, "hostname": "web.example.org"})
    cfg = Settings(
        _env_file=None,
        turnstile_secret_key="sek",
        cors_origins=["https://web.example.org"],
        turnstile_expected_action="",
    )
    assert CloudflareVerifier(cfg).verify("tok", None) is True
    _siteverify(monkeypatch, {"success": True, "hostname": "other.example.org"})
    assert CloudflareVerifier(cfg).verify("tok", None) is False


def test_a_failed_siteverify_counts_as_a_failure_and_ends_in_backoff(
    signup: SignupFn, turnstile_email: FakeVerifier
) -> None:
    signup(VICTIM).post("/api/auth/logout")
    attacker = TestClient(create_app())
    for i in range(4):
        login(attacker, VICTIM, "wrong", f"198.51.100.{i + 1}")
    codes = [
        login(attacker, VICTIM, PW, "198.51.100.77", turnstile_token="forged").status_code  # type: ignore[attr-defined]
        for _ in range(12)
    ]
    assert codes[0] == 403
    assert 429 in codes  # forged tokens are not free: they build the same backoff
    assert len(turnstile_email.calls) < 12  # and stop reaching Cloudflare once blocked


# ---------------------------------------------------------------- device cookie revocation
def _exempt(c: TestClient, cookie: dict[str, str]) -> bool:
    """True when the email-only backoff does not stop this device."""
    for k, v in cookie.items():
        c.cookies.set(k, v)
    return bool(login(c, VICTIM, PW, "203.0.113.50").status_code == 200)  # type: ignore[attr-defined]


def _lock_email() -> None:
    for _ in range(3 * get_settings().login_rate_limit_email_multiplier):
        ratelimit.login_limiter.record_failure(f"email:{VICTIM}", 300.0)


def test_logout_revokes_the_device_cookie(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    phone = TestClient(create_app())
    r = login(phone, VICTIM, PW, "203.0.113.9")
    assert r.status_code == 200  # type: ignore[attr-defined]
    stolen = dict(phone.cookies)
    phone.post("/api/auth/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]})  # type: ignore[attr-defined]
    _lock_email()
    assert _exempt(TestClient(create_app()), stolen) is False  # the old cookie exempts nothing


def test_a_reused_user_id_does_not_inherit_the_old_device(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite reuses the highest deleted id: an old browser must not become a known device."""
    trust_proxy(monkeypatch)
    old = signup("old@mail.com")
    phone = TestClient(create_app())
    assert login(phone, "old@mail.com", PW, "203.0.113.9").status_code == 200  # type: ignore[attr-defined]
    stolen = dict(phone.cookies)
    assert old.request("DELETE", "/api/me", json={"password": PW}).status_code == 204
    signup(VICTIM).post("/api/auth/logout")  # takes the freed id on SQLite
    _lock_email()
    assert _exempt(TestClient(create_app()), stolen) is False


def test_the_cookie_still_works_while_the_user_stays_logged_in_elsewhere(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    phone = TestClient(create_app())
    assert login(phone, VICTIM, PW, "203.0.113.9").status_code == 200  # type: ignore[attr-defined]
    _lock_email()
    assert _exempt(phone, dict(phone.cookies)) is True
