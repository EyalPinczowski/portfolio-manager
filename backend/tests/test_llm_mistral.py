"""Mistral adapter through a fake HTTP transport: request body, strict schema, usage, 429, order."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.llm import LLMRateLimitedError, LLMRequest, LLMUnavailableError, MistralProvider
from app.llm.providers import build_providers
from app.llm.scrub import PersonalDataScrubber
from app.model_probe import NetworkModelLister, Provider

S = Settings(_env_file=None, gemini_api_key="g", mistral_api_key="mis-key-1", groq_api_key="q")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
REQ = LLMRequest(role="t", system="be brief", prompt="AAPL", json_schema=SCHEMA)
OK = {
    "choices": [{"message": {"content": '{"ok": true}'}}],
    "usage": {"prompt_tokens": 40, "completion_tokens": 9, "total_tokens": 49},
}


def make(handler: Any) -> MistralProvider:
    scrub = PersonalDataScrubber(S, names=lambda uid: [])
    return MistralProvider(S, scrub, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_request_strict_schema_and_usage() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=OK)

    out = make(handler).complete(REQ)
    assert (out.tokens, out.tokens_in, out.tokens_out, out.strict) == (49, 40, 9, True)
    assert (out.provider, out.model) == ("mistral", "mistral-small-latest")
    req = seen[0]
    assert str(req.url) == "https://api.mistral.ai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer mis-key-1"
    body = json.loads(req.content)
    assert body["model"] == "mistral-small-latest"
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert body["messages"][0] == {"role": "system", "content": "be brief"}


def test_schema_refused_falls_back_to_json_object() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(422 if len(bodies) == 1 else 200, json=OK)

    out = make(handler).complete(REQ)
    assert out.strict is False and len(bodies) == 2
    assert bodies[1]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in bodies[1]["messages"][0]["content"]


def test_429_is_rate_limited() -> None:
    with pytest.raises(LLMRateLimitedError):
        make(lambda r: httpx.Response(429, json={})).complete(REQ)


def test_missing_key_is_unavailable_and_sends_nothing() -> None:
    calls: list[httpx.Request] = []
    s = Settings(_env_file=None, mistral_api_key=None)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    )
    with pytest.raises(LLMUnavailableError):
        MistralProvider(s, client=client).complete(REQ)
    assert calls == []


def test_order_and_privacy() -> None:
    assert Settings(_env_file=None).llm_provider_order == ["gemini", "mistral", "groq"]
    providers = build_providers(S)
    assert [p.name for p in providers] == ["gemini", "mistral", "groq"]
    no_key = S.model_copy(update={"mistral_api_key": None})
    assert [p.name for p in build_providers(no_key)] == ["gemini", "groq"]
    # Ask my portfolio: no free provider may see portfolio data
    assert all(getattr(p, "privacy", None) != "no_training" for p in providers)


def test_model_probe_lists_mistral_models() -> None:
    urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        urls.append(f"{req.url} {req.headers['authorization']}")
        return httpx.Response(200, json={"data": [{"id": "mistral-small-latest"}]})

    p: Provider = "mistral"
    found = NetworkModelLister(httpx.MockTransport(handler)).list_models(p, S)
    assert found == {"mistral-small-latest"}
    assert urls == ["https://api.mistral.ai/v1/models Bearer mis-key-1"]
