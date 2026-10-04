"""Startup probe: does the configured LLM model id still exist for our key?

Model ids are config (`gemini_model`, `groq_model`) because providers retire them (the Gemini 2.5
series shuts down no earlier than 2026-10-16; Groq's Llama models may have left the free tier). At
startup a background thread asks each provider with a key for its model list. If the configured id
is missing, the first fallback that exists is used instead (`active_model`). The probe:

- never blocks startup (own daemon thread, timeouts, every error swallowed),
- is mockable (`ModelLister`),
- does nothing without a key, and nothing at all when `model_probe_enabled` is false,
- never raises: an unreachable provider leaves the configured id in place ("unverified").
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger("model_probe")

Provider = Literal["gemini", "groq"]
Status = Literal["ok", "fallback", "none_available", "unverified", "no_key", "disabled"]


@dataclass(frozen=True)
class ModelChoice:
    provider: Provider
    configured: str
    model: str  # the id to use: the configured one, or a fallback that exists
    status: Status


class ModelLister(Protocol):
    def list_models(self, provider: Provider, settings: Settings) -> set[str] | None:
        """Model ids the provider offers for our key, or None when it cannot be asked."""


class NetworkModelLister:
    def list_models(self, provider: Provider, settings: Settings) -> set[str] | None:
        timeout = settings.model_probe_timeout_seconds
        try:
            if provider == "gemini":
                if not settings.gemini_api_key:
                    return None
                from google import genai

                client = genai.Client(api_key=settings.gemini_api_key)
                return {str(m.name).removeprefix("models/") for m in client.models.list()}
            if not settings.groq_api_key:
                return None
            resp = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                timeout=timeout,
            )
            resp.raise_for_status()
            return {str(m["id"]) for m in resp.json().get("data", [])}
        except Exception as exc:  # never leak the key or a response body into the log
            log.warning("model probe for %s failed (%s)", provider, type(exc).__name__)
            return None


def resolve_model(
    provider: Provider, configured: str, fallbacks: list[str], available: set[str] | None
) -> ModelChoice:
    """Pure decision: keep the configured id if listed, else the first listed fallback."""
    if available is None:
        return ModelChoice(provider, configured, configured, "unverified")
    if configured in available:
        return ModelChoice(provider, configured, configured, "ok")
    for candidate in fallbacks:
        if candidate in available:
            return ModelChoice(provider, configured, candidate, "fallback")
    return ModelChoice(provider, configured, configured, "none_available")


def _plan(settings: Settings) -> list[tuple[Provider, str, list[str], str | None]]:
    return [
        ("gemini", settings.gemini_model, settings.gemini_model_fallbacks, settings.gemini_api_key),
        ("groq", settings.groq_model, settings.groq_model_fallbacks, settings.groq_api_key),
    ]


def probe_models(settings: Settings, lister: ModelLister | None = None) -> dict[str, ModelChoice]:
    out: dict[str, ModelChoice] = {}
    for provider, configured, fallbacks, key in _plan(settings):
        if not settings.model_probe_enabled:
            out[provider] = ModelChoice(provider, configured, configured, "disabled")
        elif not key:
            out[provider] = ModelChoice(provider, configured, configured, "no_key")
        else:
            try:
                available = (lister or NetworkModelLister()).list_models(provider, settings)
            except Exception as exc:
                log.warning("model probe for %s failed (%s)", provider, type(exc).__name__)
                available = None
            choice = resolve_model(provider, configured, fallbacks, available)
            out[provider] = choice
            if choice.status == "fallback":
                log.warning(
                    "%s model %r is not available: using %r", provider, configured, choice.model
                )
            elif choice.status == "none_available":
                log.error("%s model %r and all fallbacks are unavailable", provider, configured)
    return out


_lock = threading.Lock()
_results: dict[str, ModelChoice] = {}


def run_probe(settings: Settings | None = None, lister: ModelLister | None = None) -> None:
    """Probe and remember the result. Never raises."""
    try:
        results = probe_models(settings or get_settings(), lister)
    except Exception as exc:
        log.warning("model probe failed (%s)", type(exc).__name__)
        return
    with _lock:
        _results.update(results)


def start_probe_in_background(
    settings: Settings | None = None, lister: ModelLister | None = None
) -> threading.Thread:
    """Start the probe without waiting for it (startup must never block on a provider)."""
    t = threading.Thread(target=run_probe, args=(settings, lister), name="model-probe", daemon=True)
    t.start()
    return t


def model_status() -> dict[str, ModelChoice]:
    with _lock:
        return dict(_results)


def reset_probe_results() -> None:
    with _lock:
        _results.clear()


def active_model(provider: Provider, settings: Settings | None = None) -> str:
    """The model id to call: the probe's choice if it ran, else the configured id."""
    s = settings or get_settings()
    with _lock:
        choice = _results.get(provider)
    if choice is not None and choice.configured == (
        s.gemini_model if provider == "gemini" else s.groq_model
    ):
        return choice.model
    return s.gemini_model if provider == "gemini" else s.groq_model
