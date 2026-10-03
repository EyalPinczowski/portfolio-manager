"""Security headers, docs off in production, and refusing to start with unsafe production settings."""

from __future__ import annotations

import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.config import Settings, get_settings, validate_production
from app.db import alembic_config, get_engine
from app.main import create_app
from tests.asgi_helpers import asgi_request

GOOD_SECRET = "x" * 40


def test_api_responses_carry_the_security_headers(client: TestClient) -> None:
    for r in (
        client.get("/api/health"),
        client.get("/api/portfolios"),  # a 401
        client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}),  # a 401
        client.post("/api/auth/login", json={"nope": 1}),  # a 422
        client.get("/api/does-not-exist"),  # a 404
    ):
        h = r.headers
        assert h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert h["cache-control"] == "no-store"


def test_headers_are_also_on_413_responses(client: TestClient) -> None:
    res = asgi_request(create_app(), "POST", "/api/portfolios", [bytes(2 * 1024 * 1024)])
    assert res.status == 413
    assert (
        res.headers["cache-control"] == "no-store"
        and res.headers["x-content-type-options"] == "nosniff"
    )


def test_headers_are_on_authenticated_data_too(signup: object) -> None:
    c = signup()  # type: ignore[operator]
    r = c.get("/api/portfolios")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"


def test_hsts_only_when_secure(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "strict-transport-security" not in TestClient(create_app()).get("/api/health").headers
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    r = TestClient(create_app()).get("/api/health")
    assert "max-age=" in r.headers["strict-transport-security"]


def test_docs_are_on_in_dev_and_off_in_production(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = TestClient(create_app())
    assert dev.get("/api/docs").status_code == 200
    assert "content-security-policy" not in dev.get("/api/docs").headers  # Swagger needs its CDN
    assert dev.get("/api/openapi.json").status_code == 200
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SECRET_KEY", GOOD_SECRET)
    get_settings.cache_clear()
    prod = TestClient(create_app())
    assert prod.get("/api/docs").status_code == 404
    assert prod.get("/api/openapi.json").status_code == 404
    assert prod.get("/api/health").status_code == 200


def test_cookie_secure_defaults_to_true_and_env_to_production() -> None:
    s = Settings(_env_file=None)
    assert s.cookie_secure is True and s.env == "production"


def test_production_refuses_to_start_with_insecure_cookies(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("SECRET_KEY", GOOD_SECRET)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"), TestClient(create_app()):
        pass


def test_production_refuses_a_default_or_short_secret_key(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for secret in ("change-me-in-production", "short"):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("COOKIE_SECURE", "true")
        monkeypatch.setenv("SECRET_KEY", secret)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="SECRET_KEY"), TestClient(create_app()):
            pass


def test_production_starts_with_safe_settings(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SECRET_KEY", GOOD_SECRET)
    get_settings.cache_clear()
    # Production never calls create_all: the schema must already be migrated (here: stamped).
    with get_engine().begin() as conn:
        cfg = alembic_config()
        cfg.attributes["connection"] = conn
        command.stamp(cfg, "head")
    with TestClient(create_app()) as c:
        assert c.get("/api/health").status_code == 200


def test_dev_may_run_insecure() -> None:
    validate_production(Settings(env="dev", cookie_secure=False, _env_file=None))
    with pytest.raises(RuntimeError):
        validate_production(
            Settings(env="production", cookie_secure=False, secret_key=GOOD_SECRET, _env_file=None)
        )
