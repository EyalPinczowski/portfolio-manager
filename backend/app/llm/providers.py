"""Gemini and Groq adapters over plain HTTPS. The `httpx.Client` is injectable (tests use a
`MockTransport`), model ids come from settings (`gemini_model`, `groq_model`, or the startup probe's
fallback), keys come from settings and are never logged. Errors carry the HTTP status only."""

from __future__ import annotations

import json
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
        try:
            resp = self._http.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: transport error ({type(exc).__name__})") from None
        if resp.status_code == 429:
            raise LLMRateLimitedError(f"{self.name}: rate limited (HTTP 429)")
        if resp.status_code >= 400:
            raise LLMError(f"{self.name}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise LLMError(f"{self.name}: response is not JSON") from None
        if not isinstance(data, dict):
            raise LLMError(f"{self.name}: unexpected response shape")
        return data

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
        model = self.model
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
        tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
        return LLMResponse(text=text, provider=self.name, model=model, tokens=int(tokens or 0))


class GroqProvider(_HttpProvider):
    name: ClassVar[str] = "groq"

    def _default_model(self) -> str:
        return active_model("groq", self.settings)

    def _send(self, request: LLMRequest) -> LLMResponse:
        key = self.settings.groq_api_key
        if not key:
            raise LLMUnavailableError("groq: no API key configured")
        system = request.system
        if request.json_schema is not None:  # JSON mode has no schema field: put it in the prompt
            system = (
                f"{system}\n\nReply with one JSON object that follows this JSON schema, "
                f"and nothing else:\n{json.dumps(request.json_schema)}"
            ).strip()
        model = self.model
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                *([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": request.prompt},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": self._max_tokens(request),
        }
        if request.json_schema is not None:
            body["response_format"] = {"type": "json_object"}
        data = self._post(
            f"{GROQ_BASE_URL}/chat/completions",
            {"authorization": f"Bearer {key}", "content-type": "application/json"},
            body,
        )
        try:
            text = str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise LLMError("groq: empty response") from None
        tokens = data.get("usage", {}).get("total_tokens", 0)
        return LLMResponse(text=text, provider=self.name, model=model, tokens=int(tokens or 0))


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
