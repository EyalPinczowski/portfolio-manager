"""Health try-lock, Caddy client address behind a tunnel, and `currency_changed` on a PATCH
(2.0-F item 10)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app import health
from app.health import HealthProbe
from tests.test_api_portfolio import make_portfolio

ROOT = Path(__file__).resolve().parents[2]
SignupFn = Callable[..., TestClient]


# ---------------------------------------------------------------- /api/health never queues
def test_a_hanging_database_cannot_queue_health_requests(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: the probe held its lock during the read, so a hanging DB queued every health call on
    a thread-pool worker. Now only one request reads; the others answer at once."""
    release = threading.Event()
    entered = threading.Event()

    class Hang:
        def __enter__(self) -> Any:
            entered.set()
            release.wait(5)
            raise RuntimeError("db hung")

        def __exit__(self, *a: object) -> None: ...

    monkeypatch.setattr(health, "new_session", lambda: Hang())
    probe = HealthProbe(ttl_seconds=10.0)
    reader = threading.Thread(target=probe.read)
    reader.start()
    assert entered.wait(2)
    started = time.monotonic()
    answers = [probe.read() for _ in range(50)]  # the lock is held by the hanging read
    assert time.monotonic() - started < 0.5
    assert answers == [(None, None, None)] * 50  # the last known value
    release.set()
    reader.join(5)
    assert not reader.is_alive()
    assert probe.read() == (None, None, None)  # and the probe works again afterwards


def test_health_keeps_serving_the_last_known_values_while_a_read_is_slow(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    probe = HealthProbe(ttl_seconds=10.0, clock=lambda: now[0])
    probe._value = ("q", "s")  # type: ignore[assignment]
    probe._at = 0.0
    assert probe._lock.acquire(blocking=False)  # another request is reading right now
    try:
        now[0] = 100.0  # the cache is stale, but nobody queues behind the lock
        assert probe.read() == ("q", "s")
    finally:
        probe._lock.release()


# ---------------------------------------------------------------- Caddy behind a tunnel
def caddyfile() -> str:
    return (ROOT / "frontend" / "Caddyfile").read_text()


def code_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_caddy_forwards_client_ip_not_remote_host() -> None:
    lines = code_lines(caddyfile())
    assert "header_up CF-Connecting-IP {client_ip}" in lines
    assert not any("{remote_host}" in ln for ln in lines)  # the tunnel daemon, not the visitor


def test_caddy_trusts_nobody_by_default_and_reads_the_tunnel_header_when_told_to() -> None:
    lines = code_lines(caddyfile())
    (trusted,) = [ln for ln in lines if ln.startswith("trusted_proxies")]
    assert (
        trusted == "trusted_proxies static {$TRUSTED_PROXIES:192.0.2.1/32}"
    )  # TEST-NET-1: no peer
    (headers,) = [ln for ln in lines if ln.startswith("client_ip_headers")]
    assert "CF-Connecting-IP" in headers and "X-Forwarded-For" in headers
    assert not any("private_ranges" in ln for ln in lines)  # a LAN visitor must never be trusted


def test_caddy_still_strips_the_other_forwarding_headers() -> None:
    lines = code_lines(caddyfile())
    for header in ("-X-Forwarded-For", "-X-Real-IP", "-True-Client-IP"):
        assert f"header_up {header}" in lines


def test_the_servers_options_live_in_the_global_options_block() -> None:
    text = caddyfile()
    assert text.index("servers {") < text.index(":{$PORT:8080}")  # before the site block
    assert text.index("admin off") < text.index("servers {")


def test_compose_passes_the_trusted_proxies_with_a_safe_default() -> None:
    compose = yaml.safe_load((ROOT / "compose.prod.yml").read_text())
    env = {k: str(v) for k, v in compose["services"]["web"]["environment"].items()}
    assert env["TRUSTED_PROXIES"] == "${TRUSTED_PROXIES:-192.0.2.1/32}"
    assert env["CLIENT_IP_HEADERS"].startswith("${CLIENT_IP_HEADERS:-CF-Connecting-IP")
    assert "ports" not in compose["services"]["api"]  # the API itself is still unreachable


# ---------------------------------------------------------------- PATCH rows and currency_changed
ROW: dict[str, Any] = {
    "name": "Apple", "symbol": "AAPL", "quantity": 10, "price": 200.0, "value": 2000.0,
    "currency": "USD", "unit": "USD",
}  # fmt: skip


def _draft(c: TestClient) -> tuple[str, dict[str, Any]]:
    pid = make_portfolio(c)
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert "currency_changed" not in d["rows"][0]["flags"]
    return f"/api/imports/{d['id']}", d


def test_patching_the_currency_of_a_known_symbol_raises_the_flag(signup: SignupFn) -> None:
    """Review: a PATCH with a known symbol skipped matching, so the edit was a silent confirmation."""
    c = signup()
    url, _ = _draft(c)
    shekels = {**ROW, "currency": "ILS", "unit": "ILS", "price": 700.0, "value": 7000.0}
    r = c.patch(url, json={"rows": [shekels]})
    assert r.status_code == 200, r.text
    assert "currency_changed" in r.json()["rows"][0]["flags"]
    assert c.post(url + "/confirm").status_code == 422  # and it blocks confirming


def test_an_untouched_row_keeps_the_confirmation_the_user_gave(signup: SignupFn) -> None:
    c = signup()
    url, _ = _draft(c)
    shekels = {**ROW, "currency": "ILS", "unit": "ILS", "price": 700.0, "value": 7000.0}
    flagged = c.patch(url, json={"rows": [shekels]}).json()["rows"][0]
    assert "currency_changed" in flagged["flags"]
    confirmed = {**shekels, "flags": [f for f in flagged["flags"] if f != "currency_changed"]}
    again = c.patch(url, json={"rows": [confirmed]}).json()["rows"][0]
    assert "currency_changed" not in again["flags"]  # same currency, flag removed: confirmed
    edited = {**confirmed, "quantity": 11}
    assert (
        "currency_changed" not in c.patch(url, json={"rows": [edited]}).json()["rows"][0]["flags"]
    )


def test_changing_the_currency_back_clears_the_flag_and_a_new_change_raises_it_again(
    signup: SignupFn,
) -> None:
    c = signup()
    url, _ = _draft(c)
    shekels = {**ROW, "currency": "ILS", "unit": "ILS", "price": 700.0, "value": 7000.0}
    assert "currency_changed" in c.patch(url, json={"rows": [shekels]}).json()["rows"][0]["flags"]
    back = c.patch(url, json={"rows": [ROW]}).json()["rows"][0]
    assert "currency_changed" not in back["flags"]
    assert "currency_changed" in c.patch(url, json={"rows": [shekels]}).json()["rows"][0]["flags"]


def test_patching_the_unit_to_agorot_on_a_dollar_stock_raises_unit_mismatch(
    signup: SignupFn,
) -> None:
    c = signup()
    url, _ = _draft(c)
    agorot = {**ROW, "currency": "ILS", "unit": "agorot", "price": 70000.0, "value": 7000.0}
    r = c.patch(url, json={"rows": [agorot]})
    assert r.status_code == 200, r.text
    assert "unit_mismatch" in r.json()["rows"][0]["flags"]
