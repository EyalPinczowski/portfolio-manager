"""Cloudflare Turnstile verification behind a mockable provider (no network in tests)."""

from __future__ import annotations

import logging
from typing import Annotated, Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import Depends

from app.config import Settings, get_settings

log = logging.getLogger("auth.turnstile")


class TurnstileVerifier(Protocol):
    def verify(self, token: str, remote_ip: str | None) -> bool:
        """True only when Cloudflare accepted the token. Must never raise."""


class CloudflareVerifier:
    """POSTs the token to `siteverify`. Fails closed: any error counts as a failed challenge."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def verify(self, token: str, remote_ip: str | None) -> bool:
        s = self._settings
        if not s.turnstile_secret_key:
            return False
        data = {"secret": s.turnstile_secret_key, "response": token}
        if remote_ip and remote_ip != "unknown":
            data["remoteip"] = remote_ip
        try:
            resp = httpx.post(
                s.turnstile_verify_url, data=data, timeout=s.turnstile_timeout_seconds
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("success") is not True:
                return False
            return self._claims_match(body)
        except Exception as exc:  # network, HTTP or JSON error: treat as "not verified"
            log.warning("turnstile verification failed (%s)", type(exc).__name__)
            return False

    def _claims_match(self, body: dict[str, object]) -> bool:
        """The token was minted for this site (`hostname`) and this form (`action`)."""
        s = self._settings
        hosts = {h.lower() for h in s.turnstile_allowed_hostnames} or {
            (urlsplit(o).hostname or "").lower() for o in s.cors_origins
        }
        hosts.discard("")
        if hosts and str(body.get("hostname", "")).lower() not in hosts:
            log.warning("turnstile token for another hostname refused")
            return False
        if s.turnstile_expected_action and body.get("action") != s.turnstile_expected_action:
            log.warning("turnstile token for another action refused")
            return False
        return True


_override: TurnstileVerifier | None = None


def set_turnstile_verifier(verifier: TurnstileVerifier | None) -> None:
    """Tests (and the browser pass) plug a mock verifier in here; None restores the real one."""
    global _override
    _override = verifier


def get_turnstile_verifier() -> TurnstileVerifier:
    return _override or CloudflareVerifier(get_settings())


TurnstileDep = Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)]
