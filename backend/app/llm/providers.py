"""Gemini and Groq adapters over plain HTTPS. The `httpx.Client` is injectable (tests use a
`MockTransport`), model ids come from settings (`gemini_model`, `groq_model`, or the startup probe's
fallback), keys come from settings and are never logged. Errors carry the HTTP status only."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, ClassVar

import httpx

from app.config import Settings, get_settings
from app.llm.base import (
    BaseLLMProvider,
    LLMError,
    LLMProvider,
    LLMRateLimitedError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)
from app.llm.scrub import PersonalDataScrubber
from app.model_probe import active_model
from app.rag.tokens import estimate_tokens

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
TEMPERATURE = 0.2


class _HttpProvider(BaseLLMProvider):
    def __init__(
        self,
        settings: Settings | None = None,
        scrubber: PersonalDataScrubber | None = None,
        model: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(settings, scrubber, model)
        self._client = client

    @property
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.settings.llm_timeout_seconds)
        return self._client

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        return self._post_full(url, headers, body)[0]

    def _post_full(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[dict[str, Any], httpx.Headers]:
        try:
            resp = self._http.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: transport error ({type(exc).__name__})") from None
        if resp.status_code == 429:
            self._on_rate_limited(resp.headers)
            raise LLMRateLimitedError(f"{self.name}: rate limited (HTTP 429)")
        if resp.status_code >= 400:
            raise LLMError(f"{self.name}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise LLMError(f"{self.name}: response is not JSON") from None
        if not isinstance(data, dict):
            raise LLMError(f"{self.name}: unexpected response shape")
        return data, resp.headers

    def _on_rate_limited(self, headers: httpx.Headers) -> None:
        """Hook: a 429 arrived (Groq remembers its limit headers)."""

    def _max_tokens(self, request: LLMRequest) -> int:
        return request.max_output_tokens or self.settings.llm_max_output_tokens


class GeminiProvider(_HttpProvider):
    name: ClassVar[str] = "gemini"

    def _default_model(self) -> str:
        return active_model("gemini", self.settings)

    def _send(self, request: LLMRequest) -> LLMResponse:
        key = self.settings.gemini_api_key
        if not key:
            raise LLMUnavailableError("gemini: no API key configured")
        config: dict[str, Any] = {
            "maxOutputTokens": self._max_tokens(request),
            "temperature": TEMPERATURE,
        }
        if self.settings.gemini_thinking_budget is not None:
            # thinking tokens count against maxOutputTokens: an explicit budget keeps the JSON whole
            config["thinkingConfig"] = {"thinkingBudget": self.settings.gemini_thinking_budget}
        if request.json_schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = request.json_schema
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": config,
        }
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        model = request.model or self.model
        data = self._post(
            f"{GEMINI_BASE_URL}/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": key, "content-type": "application/json"},
            body,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(str(p.get("text", "")) for p in parts)
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("gemini: empty or blocked response") from None
        usage = data.get("usageMetadata")
        usage = usage if isinstance(usage, dict) else {}
        out = _int(usage.get("candidatesTokenCount")) + _int(usage.get("thoughtsTokenCount"))
        return LLMResponse(
            text=text, provider=self.name, model=model,
            tokens=_int(usage.get("totalTokenCount")),
            tokens_in=_int(usage.get("promptTokenCount")), tokens_out=out,
            tokens_cached=_int(usage.get("cachedContentTokenCount")),
        )  # fmt: skip


_STRICT_KEEP = {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf"}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A pydantic JSON schema in the form Groq's strict mode accepts: `$ref`s inlined, every field
    required, `additionalProperties: false`, and only the keywords strict mode is known to support.
    Length and range limits are dropped here; the answer is still validated against the full
    model afterwards."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[str(node["$ref"]).rsplit("/", 1)[-1]])
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k not in _STRICT_KEEP:
                continue
            if k == "properties":
                out[k] = {name: walk(sub) for name, sub in v.items()}
            elif k == "anyOf":
                out[k] = [walk(sub) for sub in v]
            elif k == "items":
                out[k] = walk(v)
            else:
                out[k] = v
        if out.get("type") == "object":
            out["required"] = list(out.get("properties", {}))
            out["additionalProperties"] = False
        return out

    return walk(schema)  # type: ignore[no-any-return]


# What Groq last said about the tokens left in this minute, per model: (remaining, valid until).
# In memory and per process: a hint to skip a call that would only earn a 429, never a guarantee.
_groq_limits: dict[str, tuple[int, float]] = {}
_groq_lock = threading.Lock()
_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def _int(v: object) -> int:
    return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0


def _seconds(text: str) -> float | None:
    """Groq reset header ("7.66s", "1m2.5s", "120ms") -> seconds."""
    m = _DURATION.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    mins, secs, millis = (float(g) if g else 0.0 for g in m.groups())
    return mins * 60 + secs + millis / 1000


def reset_groq_limits() -> None:
    with _groq_lock:
        _groq_limits.clear()


def _prefixed(model: str, table: dict[str, str]) -> str | None:
    for prefix, value in table.items():
        if model.startswith(prefix):
            return value
    return None


class GroqProvider(_HttpProvider):
    name: ClassVar[str] = "groq"
    _last_model: str | None = None

    def _default_model(self) -> str:
        return active_model("groq", self.settings)

    def uses_strict(self, model: str) -> bool:
        return any(model.startswith(p) for p in self.settings.groq_strict_json_prefixes)

    def estimate_request_tokens(self, request: LLMRequest, model: str) -> int:
        """Tokens this call counts against the per-minute limit: prompt (and the schema text when it
        is pasted into the system prompt) plus the output cap."""
        s = self.settings
        text = f"{request.system}\n{request.prompt}"
        if request.json_schema is not None and not self.uses_strict(model):
            text += json.dumps(request.json_schema)
        return estimate_tokens(text, s) + self._max_tokens(request)

    def precheck(self, request: LLMRequest, model: str) -> str | None:
        """Why this call would be refused by Groq's per-minute token limit, from the headers of the
        last response (None when there is no fresh reading or the call fits)."""
        with _groq_lock:
            known = _groq_limits.get(model)
        if known is None:
            return None
        remaining, valid_until = known
        if time.monotonic() >= valid_until:
            return None
        need = self.estimate_request_tokens(request, model)
        if need > remaining:
            return f"groq: about {need} tokens needed, {remaining} left this minute for {model}"
        return None

    def _remember_limits(self, model: str, headers: httpx.Headers) -> None:
        raw = headers.get("x-ratelimit-remaining-tokens")
        if raw is None:
            return
        try:
            remaining = int(float(raw))
        except ValueError:
            return
        reset = _seconds(headers.get("x-ratelimit-reset-tokens", "")) or 60.0
        with _groq_lock:
            _groq_limits[model] = (remaining, time.monotonic() + min(reset, 60.0))

    def _on_rate_limited(self, headers: httpx.Headers) -> None:
        self._remember_limits(self._last_model or self.model, headers)

    def _send(self, request: LLMRequest) -> LLMResponse:
        key = self.settings.groq_api_key
        if not key:
            raise LLMUnavailableError("groq: no API key configured")
        model = request.model or self.model
        self._last_model = model
        strict = request.json_schema is not None and self.uses_strict(model)
        system = request.system
        if request.json_schema is not None and not strict:
            # JSON mode has no schema field: put it in the prompt
            system = (
                f"{system}\n\nReply with one JSON object that follows this JSON schema, "
                f"and nothing else:\n{json.dumps(request.json_schema)}"
            ).strip()
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                *([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": request.prompt},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": self._max_tokens(request),
        }
        effort = _prefixed(model, self.settings.groq_reasoning_effort)
        if effort:
            body["reasoning_effort"] = effort
        if request.json_schema is not None:
            if strict:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "answer",
                        "strict": True,
                        "schema": strict_schema(request.json_schema),
                    },
                }
            else:
                body["response_format"] = {"type": "json_object"}
        data, headers = self._post_full(
            f"{GROQ_BASE_URL}/chat/completions",
            {"authorization": f"Bearer {key}", "content-type": "application/json"},
            body,
        )
        self._remember_limits(model, headers)
        try:
            text = str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise LLMError("groq: empty response") from None
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, dict) else {}
        return LLMResponse(
            text=text, provider=self.name, model=model,
            tokens=_int(usage.get("total_tokens")),
            tokens_in=_int(usage.get("prompt_tokens")),
            tokens_out=_int(usage.get("completion_tokens")),
            tokens_cached=_int(details.get("cached_tokens")),
            strict=strict,
        )  # fmt: skip


def build_providers(
    settings: Settings | None = None, client: httpx.Client | None = None
) -> list[LLMProvider]:
    """The providers that can be used now, in `llm_provider_order`: those with an API key."""
    s = settings or get_settings()
    if not s.llm_enabled:
        return []
    out: list[LLMProvider] = []
    for name in s.llm_provider_order:
        if name == "gemini" and s.gemini_api_key:
            out.append(GeminiProvider(s, client=client))
        elif name == "groq" and s.groq_api_key:
            out.append(GroqProvider(s, client=client))
    return out
