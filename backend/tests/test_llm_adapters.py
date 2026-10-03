"""Gemini and Groq adapters through a fake HTTP transport: no network, model ids from settings."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import (
    GeminiProvider,
    GroqProvider,
    LLMError,
    LLMRateLimitedError,
    LLMRequest,
    LLMUnavailableError,
    build_providers,
    structured_call,
    usage_for_day,
)
from app.llm.scrub import PersonalDataScrubber
from app.model_probe import reset_probe_results, run_probe

S = Settings(
    _env_file=None,
    gemini_api_key="gem-key-123",
    groq_api_key="groq-key-456",
    gemini_model="gem-test-1",
    groq_model="groq-test-1",
)
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
REQ = LLMRequest(
    role="t", system="be brief", prompt="owner a@b.co, 1234567, AAPL 1,234.56", json_schema=SCHEMA
)


class Recorder:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.calls: list[httpx.Request] = []
        self.status, self.body = status, body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, json=self.body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    @property
    def sent(self) -> dict[str, Any]:
        return json.loads(self.calls[-1].content)


GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": '{"ok": '}, {"text": "true}"}]}}],
    "usageMetadata": {"totalTokenCount": 77},
}
GROQ_OK = {
    "choices": [{"message": {"content": '{"ok": true}'}}],
    "usage": {"total_tokens": 55},
}


def no_names() -> PersonalDataScrubber:
    return PersonalDataScrubber(S, names=lambda uid: [])


def test_gemini_request_and_response() -> None:
    rec = Recorder(body=GEMINI_OK)
    p = GeminiProvider(S, no_names(), client=rec.client())
    out = p.complete(REQ)
    assert (out.text, out.tokens, out.provider, out.model) == (
        '{"ok": true}',
        77,
        "gemini",
        "gem-test-1",
    )
    req = rec.calls[0]
    assert req.url.path == "/v1beta/models/gem-test-1:generateContent"  # the id from settings
    assert req.headers["x-goog-api-key"] == "gem-key-123"
    body = rec.sent
    assert (
        body["contents"][0]["parts"][0]["text"]
        == "owner [email], 1234567, AAPL 1,234.56"  # facts stay; secrets go
    )  # scrubbed
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["responseJsonSchema"] == SCHEMA
    assert cfg["maxOutputTokens"] == S.llm_max_output_tokens


def test_groq_request_and_response() -> None:
    rec = Recorder(body=GROQ_OK)
    p = GroqProvider(S, no_names(), client=rec.client())
    out = p.complete(REQ)
    assert (out.text, out.tokens, out.provider, out.model) == (
        '{"ok": true}',
        55,
        "groq",
        "groq-test-1",
    )
    req = rec.calls[0]
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer groq-key-456"
    body = rec.sent
    assert body["model"] == "groq-test-1"
    assert body["response_format"] == {"type": "json_object"}
    assert (
        body["messages"][1]["content"] == "owner [email], 1234567, AAPL 1,234.56"
    )  # facts stay; secrets go
    assert (
        "JSON schema" in body["messages"][0]["content"] and '"ok"' in body["messages"][0]["content"]
    )


@pytest.mark.parametrize("cls", [GeminiProvider, GroqProvider])
def test_no_key_means_unavailable_and_nothing_is_sent(cls: type[GeminiProvider]) -> None:
    rec = Recorder(body={})
    p = cls(Settings(_env_file=None, gemini_api_key=None, groq_api_key=None), client=rec.client())
    with pytest.raises(LLMUnavailableError):
        p.complete(REQ)
    assert rec.calls == []


@pytest.mark.parametrize("cls", [GeminiProvider, GroqProvider])
def test_http_errors_map_to_llm_errors_without_leaking(cls: type[GeminiProvider]) -> None:
    secret_body = {"error": "echo of the prompt owner a@b.co and key gem-key-123"}
    with pytest.raises(LLMRateLimitedError):
        cls(S, no_names(), client=Recorder(429, secret_body).client()).complete(REQ)
    with pytest.raises(LLMError) as exc:
        cls(S, no_names(), client=Recorder(500, secret_body).client()).complete(REQ)
    assert (
        "500" in str(exc.value) and "a@b.co" not in str(exc.value) and "key" not in str(exc.value)
    )


@pytest.mark.parametrize("cls", [GeminiProvider, GroqProvider])
def test_empty_or_blocked_responses_are_errors(cls: type[GeminiProvider]) -> None:
    with pytest.raises(LLMError):
        cls(
            S, no_names(), client=Recorder(200, {"candidates": [], "choices": []}).client()
        ).complete(REQ)


def test_transport_failure_is_an_llm_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(LLMError, match="transport error"):
        GeminiProvider(S, no_names(), client=client).complete(REQ)


def test_the_probe_fallback_model_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    class Lister:
        def list_models(self, provider: str, settings: Settings) -> set[str]:
            return {"gem-fallback-2"} if provider == "gemini" else {"groq-test-1"}

    s = S.model_copy(update={"gemini_model_fallbacks": ["gem-fallback-2"]})
    reset_probe_results()
    try:
        run_probe(s, Lister())  # type: ignore[arg-type]
        rec = Recorder(body=GEMINI_OK)
        GeminiProvider(s, no_names(), client=rec.client()).complete(REQ)
        assert rec.calls[0].url.path == "/v1beta/models/gem-fallback-2:generateContent"
    finally:
        reset_probe_results()


def test_build_providers_follows_keys_and_order() -> None:
    assert [p.name for p in build_providers(S)] == ["gemini", "groq"]
    assert [p.name for p in build_providers(S.model_copy(update={"gemini_api_key": None}))] == [
        "groq"
    ]
    assert build_providers(S.model_copy(update={"llm_provider_order": ["groq"]}))[0].name == "groq"
    assert build_providers(Settings(_env_file=None, gemini_api_key=None, groq_api_key=None)) == []
    assert build_providers(S.model_copy(update={"llm_enabled": False})) == []


class Verdict(BaseModel):
    ok: bool


def test_structured_call_over_the_http_adapters_end_to_end(env: None) -> None:
    gem = Recorder(429, {})
    groq = Recorder(body=GROQ_OK)
    providers = [
        GeminiProvider(S, no_names(), client=gem.client()),
        GroqProvider(S, no_names(), client=groq.client()),
    ]
    res = structured_call(
        cache_scope="global",
        role="t",
        model_cls=Verdict,
        system="s",
        prompt="p a@b.co 1234567",
        template=lambda: Verdict(ok=False),
        providers=providers,
        settings=S,
    )
    assert res.source == "llm" and res.provider == "groq" and res.value.ok is True
    assert len(gem.calls) == 1 and len(groq.calls) == 1
    sent = groq.sent["messages"][1]["content"]
    assert "[email]" in sent and "1234567" in sent  # provider data: numbers are facts
    rows = {r.provider: (r.requests, r.tokens) for r in usage_for_day()}
    assert rows == {"gemini": (1, 0), "groq": (1, 55)}
