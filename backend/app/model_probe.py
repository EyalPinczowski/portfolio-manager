"""Startup probe: does the configured LLM model id still exist for our key?

Model ids are config (`gemini_model`, `groq_model`) because providers retire them (the Gemini 2.5
series shuts down no earlier than 2026-10-16; Groq's Llama models may have left the free tier). At
startup a background thread asks each provider with a key for its model list. If the configured id
is missing, the first fallback that exists is used instead (`active_model`). For Gemini a model that
is listed must also answer one tiny `generateContent` call (a key made late may list a model it is
refused on), otherwise it is skipped like a missing one. Plain `httpx` only: no vendor SDK is
imported into the API process. The probe:

- never blocks startup (own daemon thread, timeouts, every error swallowed),
- is mockable (`ModelLister`),
- does nothing without a key, and nothing at all when `model_probe_enabled` is false,
- never raises: an unreachable provider leaves the configured id in place ("unverified").
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from app.config import Settings, gemini_thinking_config, get_settings

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


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"


class NetworkModelLister:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # tests pass an `httpx.MockTransport`

    def _client(self, settings: Settings) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=settings.model_probe_timeout_seconds)

    def list_models(self, provider: Provider, settings: Settings) -> set[str] | None:
        try:
            with self._client(settings) as client:
                if provider == "gemini":
                    if not settings.gemini_api_key:
                        return None
                    return self._list_gemini(client, settings.gemini_api_key)
                if not settings.groq_api_key:
                    return None
                resp = client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                )
                resp.raise_for_status()
                return {str(m["id"]) for m in resp.json().get("data", [])}
        except Exception as exc:  # never leak the key or a response body into the log
            log.warning("model probe for %s failed (%s)", provider, type(exc).__name__)
            return None

    @staticmethod
    def _list_gemini(client: httpx.Client, key: str) -> set[str]:
        found: set[str] = set()
        token: str | None = None
        for _ in range(10):  # the list is paged
            params: dict[str, str | int] = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            resp = client.get(
                f"{GEMINI_BASE_URL}/v1beta/models", params=params, headers={"x-goog-api-key": key}
            )
            resp.raise_for_status()
            data = resp.json()
            found |= {str(m["name"]).removeprefix("models/") for m in data.get("models", [])}
            token = data.get("nextPageToken")
            if not token:
                break
        return found

    def generates(self, provider: Provider, model: str, settings: Settings) -> bool | None:
        """Does `model` answer one tiny request for our key? None: not checked (no key, Groq)."""
        if provider != "gemini" or not settings.gemini_api_key:
            return None
        config: dict[str, object] = {"maxOutputTokens": 16}
        thinking = gemini_thinking_config(model, settings)
        if thinking is not None:
            config["thinkingConfig"] = thinking
        body = {
            "contents": [{"role": "user", "parts": [{"text": "ok"}]}],
            "generationConfig": config,
        }
        try:
            with self._client(settings) as client:
                resp = client.post(
                    f"{GEMINI_BASE_URL}/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json=body,
                )
            return resp.status_code == 200
        except Exception as exc:
            log.warning("model probe call for %s failed (%s)", provider, type(exc).__name__)
            return None


def resolve_model(
    provider: Provider,
    configured: str,
    fallbacks: list[str],
    available: set[str] | None,
    usable: Callable[[str], bool | None] | None = None,
) -> ModelChoice:
    """Pure decision: keep the configured id if listed (and usable), else the first such fallback.
    `usable` answers False for a model that is listed but refuses calls; None means unchecked."""
    if available is None:
        return ModelChoice(provider, configured, configured, "unverified")

    def ok(model: str) -> bool:
        return model in available and (usable is None or usable(model) is not False)

    if ok(configured):
        return ModelChoice(provider, configured, configured, "ok")
    for candidate in fallbacks:
        if ok(candidate):
            return ModelChoice(provider, configured, candidate, "fallback")
    return ModelChoice(provider, configured, configured, "none_available")


def _plan(settings: Settings) -> list[tuple[Provider, str, list[str], str | None]]:
    return [
        ("gemini", settings.gemini_model, settings.gemini_model_fallbacks, settings.gemini_api_key),
        ("groq", settings.groq_model, settings.groq_model_fallbacks, settings.groq_api_key),
    ]


def probe_models(settings: Settings, lister: ModelLister | None = None) -> dict[str, ModelChoice]:
    out: dict[str, ModelChoice] = {}
    lister = lister or NetworkModelLister()
    for provider, configured, fallbacks, key in _plan(settings):
        if not settings.model_probe_enabled:
            out[provider] = ModelChoice(provider, configured, configured, "disabled")
        elif not key:
            out[provider] = ModelChoice(provider, configured, configured, "no_key")
        else:
            try:
                available = lister.list_models(provider, settings)
            except Exception as exc:
                log.warning("model probe for %s failed (%s)", provider, type(exc).__name__)
                available = None
            tester = getattr(lister, "generates", None)
            usable: Callable[[str], bool | None] | None = None
            if tester is not None:

                def usable(m: str, t: Any = tester, p: Provider = provider) -> bool | None:
                    return t(p, m, settings)  # type: ignore[no-any-return]

            choice = resolve_model(
                provider,
                configured,
                fallbacks,
                available,
                usable,
            )
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
