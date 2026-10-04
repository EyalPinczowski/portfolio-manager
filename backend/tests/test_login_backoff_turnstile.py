"""Login backoff keyed on (email, IP), the known-device exemption and Cloudflare Turnstile."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import ratelimit
from app.auth.turnstile import CloudflareVerifier, set_turnstile_verifier
from app.config import Settings, get_settings, turnstile_state
from app.main import create_app
from tests.conftest import SignupFn
from tests.test_security_auth import PW, SECRET, trust_proxy, via_proxy

VICTIM = "victim@mail.com"


def login(c: TestClient, email: str, password: str, ip: str, **extra: str) -> object:
    return c.post(
        "/api/auth/login",
        json={"email": email, "password": password, **extra},
        headers=via_proxy(ip),
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(ratelimit.login_limiter, "_clock", lambda: now[0])
    return now


# ---------------------------------------------------------------- the verified lock-out scenario
def test_twenty_requests_an_hour_cannot_lock_the_owner_out(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    """Re-review: 20 requests an hour kept the victim in email backoff 99.4 % of the time."""
    trust_proxy(monkeypatch)
    owner_phone = TestClient(create_app())
    signup(VICTIM).post("/api/auth/logout")
    assert login(owner_phone, VICTIM, PW, "203.0.113.9").status_code == 200  # type: ignore[attr-defined]
    assert "pm_device" in owner_phone.cookies
    stranger_device = TestClient(create_app())  # the owner on a new laptop: no device cookie
    attacker = TestClient(create_app())
    blocked_owner_attempts = 0
    for i in range(20):
        clock[0] += 180.0  # 20 requests spread over the hour
        login(attacker, VICTIM, "wrong", "198.51.100.66")
        for dev in (owner_phone, stranger_device):
            r = login(dev, VICTIM, PW, f"203.0.113.{10 + i}")
            blocked_owner_attempts += r.status_code == 429  # type: ignore[attr-defined]
    assert blocked_owner_attempts == 0


def test_a_distributed_attack_blocks_unknown_devices_but_not_known_ones(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    phone = TestClient(create_app())
    assert login(phone, VICTIM, PW, "203.0.113.9").status_code == 200  # type: ignore[attr-defined]
    cookies = dict(phone.cookies)
    attackers = TestClient(create_app())
    free = 3 * get_settings().login_rate_limit_email_multiplier
    for i in range(free):  # one failure from each of many IPs
        login(attackers, VICTIM, "wrong", f"192.0.2.{i % 250}.".rstrip("."))
    unknown = login(TestClient(create_app()), VICTIM, PW, "203.0.113.77")
    assert unknown.status_code == 429  # type: ignore[attr-defined]
    known = TestClient(create_app())
    for k, v in cookies.items():
        known.cookies.set(k, v)
    assert login(known, VICTIM, PW, "203.0.113.78").status_code == 200  # type: ignore[attr-defined]


def test_device_cookie_is_signed_httponly_and_lasts_90_days(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    c = TestClient(create_app())
    r = login(c, VICTIM, PW, "203.0.113.9")
    header = next(h for h in r.headers.get_list("set-cookie") if h.startswith("pm_device="))  # type: ignore[attr-defined]
    low = header.lower()
    assert "httponly" in low and "max-age=7776000" in low and "samesite=lax" in low
    # a forged or tampered cookie grants no exemption
    forged = TestClient(create_app())
    forged.cookies.set("pm_device", "[1]")
    for _ in range(3 * get_settings().login_rate_limit_email_multiplier):
        ratelimit.login_limiter.record_failure(f"email:{VICTIM}", 300.0)
    assert login(forged, VICTIM, PW, "203.0.113.5").status_code == 429  # type: ignore[attr-defined]


def test_device_cookie_of_another_user_does_not_exempt(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    signup("mallory@mail.com").post("/api/auth/logout")
    m = TestClient(create_app())
    assert login(m, "mallory@mail.com", PW, "198.51.100.1").status_code == 200  # type: ignore[attr-defined]
    for _ in range(3 * get_settings().login_rate_limit_email_multiplier):
        ratelimit.login_limiter.record_failure(f"email:{VICTIM}", 300.0)
    assert login(m, VICTIM, PW, "198.51.100.1").status_code == 429  # type: ignore[attr-defined]


# ---------------------------------------------------------------- Turnstile
class FakeVerifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, str | None]] = []

    def verify(self, token: str, remote_ip: str | None) -> bool:
        self.calls.append((token, remote_ip))
        return self.ok and token == "good-token"


@pytest.fixture
def turnstile_env(env: None, monkeypatch: pytest.MonkeyPatch) -> FakeVerifier:
    trust_proxy(monkeypatch)
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-key")
    monkeypatch.setenv("TURNSTILE_AFTER_FAILURES", "2")
    get_settings.cache_clear()
    fake = FakeVerifier()
    set_turnstile_verifier(fake)
    yield fake  # type: ignore[misc]
    set_turnstile_verifier(None)


def test_turnstile_is_required_after_repeated_failures(
    signup: SignupFn, turnstile_env: FakeVerifier, clock: list[float]
) -> None:
    signup(VICTIM).post("/api/auth/logout")
    c = TestClient(create_app())
    for _ in range(2):
        assert login(c, VICTIM, "x", "198.51.100.1").status_code == 401  # type: ignore[attr-defined]
    r = login(c, VICTIM, PW, "198.51.100.1")
    assert r.status_code == 403  # type: ignore[attr-defined]
    body = r.json()  # type: ignore[attr-defined]
    assert body["code"] == "turnstile_required" and body["site_key"] == "site-key"
    assert isinstance(body["detail"], str)
    assert turnstile_env.calls == []  # no token sent: nothing to verify
    bad = login(c, VICTIM, PW, "198.51.100.1", turnstile_token="forged")
    assert bad.status_code == 403 and bad.json()["code"] == "turnstile_required"  # type: ignore[attr-defined]
    clock[0] += 60.0  # a forged token counts as a failed attempt (2.0-F), so wait out the backoff
    good = login(c, VICTIM, PW, "198.51.100.1", turnstile_token="good-token")
    assert good.status_code == 200  # type: ignore[attr-defined]
    assert turnstile_env.calls[-1] == ("good-token", "198.51.100.1")
    # success clears the (email, IP) count: no challenge again
    c.post("/api/auth/logout", headers={"X-CSRF-Token": good.json()["csrf_token"]})  # type: ignore[attr-defined]
    assert login(c, VICTIM, PW, "198.51.100.1").status_code == 200  # type: ignore[attr-defined]


def test_turnstile_is_per_email_and_ip_pair(signup: SignupFn, turnstile_env: FakeVerifier) -> None:
    signup(VICTIM).post("/api/auth/logout")
    c = TestClient(create_app())
    for _ in range(2):
        login(c, VICTIM, "x", "198.51.100.1")
    assert login(c, VICTIM, PW, "198.51.100.2").status_code == 200  # type: ignore[attr-defined]


def test_no_challenge_when_turnstile_is_off(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    signup(VICTIM).post("/api/auth/logout")
    c = TestClient(create_app())
    for _ in range(2):
        login(c, VICTIM, "x", "198.51.100.1")
    assert login(c, VICTIM, PW, "198.51.100.1").status_code == 200  # type: ignore[attr-defined]


def test_turnstile_defaults_and_config_state() -> None:
    assert turnstile_state(Settings(env="dev")) == "off"
    assert (
        Settings(env="dev", turnstile_site_key="a", turnstile_secret_key="b").turnstile_enabled
        is False
    )
    prod = Settings(env="production", turnstile_site_key="a", turnstile_secret_key="b")
    assert prod.turnstile_enabled is True and turnstile_state(prod) == "on"
    assert turnstile_state(Settings(env="production")) == "off"  # no keys: off, app still starts
    assert turnstile_state(Settings(env="dev", turnstile_enabled=True)) == "misconfigured"


def test_misconfigured_turnstile_does_not_lock_users_out(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_proxy(monkeypatch)
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")  # but no keys
    monkeypatch.setenv("TURNSTILE_AFTER_FAILURES", "1")
    get_settings.cache_clear()
    signup(VICTIM).post("/api/auth/logout")
    c = TestClient(create_app())
    login(c, VICTIM, "x", "198.51.100.1")
    assert login(c, VICTIM, PW, "198.51.100.1").status_code == 200  # type: ignore[attr-defined]


def test_cloudflare_verifier_posts_to_siteverify_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    sent: list[dict[str, object]] = []

    def fake_post(url: str, data: dict[str, str], timeout: float) -> httpx.Response:
        sent.append({"url": url, **data})
        return httpx.Response(
            200,
            json={"success": data["response"] == "ok", "action": "login"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    v = CloudflareVerifier(Settings(turnstile_secret_key="sek"))
    assert v.verify("ok", "203.0.113.1") is True
    assert v.verify("nope", None) is False
    assert sent[0]["url"].endswith("/turnstile/v0/siteverify") and sent[0]["secret"] == "sek"  # type: ignore[union-attr]
    assert sent[0]["remoteip"] == "203.0.113.1" and "remoteip" not in sent[1]

    def boom(*a: object, **k: object) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    assert v.verify("ok", None) is False
    assert CloudflareVerifier(Settings()).verify("ok", None) is False  # no secret key


def test_login_403_is_documented_in_openapi(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    resp = spec["paths"]["/api/auth/login"]["post"]["responses"]["403"]
    ref = resp["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
    assert schema["properties"]["code"]["const"] == "turnstile_required"
    assert "turnstile_token" in spec["components"]["schemas"]["LoginIn"]["properties"]


def test_secret_constant_is_long_enough() -> None:
    assert len(SECRET) >= 16
