"""`compose.prod.yml` (Option B) has no development defaults and no spoofable proxy header."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / "compose.prod.yml").read_text())
    assert isinstance(data, dict)
    return data


def env_of(compose: dict[str, Any], service: str) -> dict[str, str]:
    return {k: str(v) for k, v in compose["services"][service]["environment"].items()}


def test_it_is_production_with_secure_cookies_and_a_required_secret(
    compose: dict[str, Any],
) -> None:
    for svc in ("api", "scheduler", "migrate"):
        env = env_of(compose, svc)
        assert env["ENV"] == "production" and env["COOKIE_SECURE"] == "true"
        assert env["SECRET_KEY"].startswith(
            "${SECRET_KEY:?"
        )  # no default: compose refuses without it
        assert "dev" not in env["SECRET_KEY"].lower().replace("developer", "")
        assert env["AUTO_MIGRATE"] == "false"


def test_no_dev_defaults_anywhere() -> None:
    text = (ROOT / "compose.prod.yml").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for banned in ("ENV: dev", "dev-only", "change-me", 'COOKIE_SECURE: "false"', "/api/docs"):
        assert banned not in code


def test_the_api_is_not_published_and_no_spoofable_proxy_header(compose: dict[str, Any]) -> None:
    api = compose["services"]["api"]
    assert "ports" not in api and "8000" in api["expose"][0]
    for svc in ("api", "scheduler"):
        env = env_of(compose, svc)
        assert "TRUSTED_PROXY_HEADER" not in env  # that mode needs a shared secret (Option A)
        assert env["FALLBACK_IP_HEADER"] == "CF-Connecting-IP"  # safe: only Caddy can reach the API
    assert not any(
        ":8000" in str(p) for s in compose["services"].values() for p in s.get("ports", [])
    )


def test_the_web_port_binds_to_localhost_by_default(compose: dict[str, Any]) -> None:
    (port,) = compose["services"]["web"]["ports"]
    assert port.startswith("${WEB_BIND:-127.0.0.1}:")


def test_migrations_run_first_and_the_services_wait_for_them(compose: dict[str, Any]) -> None:
    for svc in ("api", "scheduler"):
        assert (
            compose["services"][svc]["depends_on"]["migrate"]["condition"]
            == "service_completed_successfully"
        )
    assert compose["services"]["migrate"]["command"][-1] == "migrate"


def test_containers_are_hardened(compose: dict[str, Any]) -> None:
    for svc in ("api", "scheduler", "web"):
        s = compose["services"][svc]
        assert s["read_only"] is True and s["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in s["security_opt"]
