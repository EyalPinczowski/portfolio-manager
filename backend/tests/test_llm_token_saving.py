"""Token-saving changes: real token use recorded, Groq limits and strict JSON, per-role output caps,
cache reuse (same prompt, any provider), per-role model routing, fewer passages for the CIO."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.committee.schemas import BearCase, CIOAssessment, CompanyProfile, NewsReport
from app.config import Settings
from app.llm import GeminiProvider, GroqProvider, LLMRequest, structured_call, usage_for_day
from app.llm.fakes import FakeLLMProvider
from app.llm.ledger import TokenBucket, record_usage
from app.llm.providers import reset_groq_limits, strict_schema
from app.timeutil import utcnow

NO_KEYS = {"gemini_api_key": None, "groq_api_key": None}
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **{**NO_KEYS, **kw})  # type: ignore[arg-type]


class Out(BaseModel):
    ok: bool


GOOD = '{"ok": true}'


def client(
    body: Any, headers: dict[str, str] | None = None, status: int = 200
) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


@pytest.fixture(autouse=True)
def _clean_groq_limits() -> None:
    reset_groq_limits()


# ------------------------------------------------------------------ usage fields parsed
def test_gemini_usage_metadata_is_split_into_in_out_and_cached() -> None:
    body = {
        "candidates": [{"content": {"parts": [{"text": GOOD}]}}],
        "usageMetadata": {
            "promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5,
            "cachedContentTokenCount": 40, "totalTokenCount": 125,
        },
    }  # fmt: skip
    c, _ = client(body)
    p = GeminiProvider(settings(gemini_api_key="k", gemini_model="g1"), client=c)
    r = p.complete(LLMRequest(role="t", prompt="x", json_schema=SCHEMA))
    assert (r.tokens, r.tokens_in, r.tokens_out, r.tokens_cached) == (125, 100, 25, 40)


def test_groq_usage_is_split_and_missing_fields_are_zero() -> None:
    body = {
        "choices": [{"message": {"content": GOOD}}],
        "usage": {
            "prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 64},
        },
    }  # fmt: skip
    c, _ = client(body)
    p = GroqProvider(settings(groq_api_key="k", groq_model="llama-x"), client=c)
    r = p.complete(LLMRequest(role="t", prompt="x"))
    assert (r.tokens, r.tokens_in, r.tokens_out, r.tokens_cached) == (100, 90, 10, 64)
    c2, _ = client({"choices": [{"message": {"content": GOOD}}]})
    r2 = GroqProvider(settings(groq_api_key="k", groq_model="llama-x"), client=c2).complete(
        LLMRequest(role="t", prompt="x")
    )
    assert (r2.tokens, r2.tokens_in, r2.tokens_out, r2.tokens_cached) == (0, 0, 0, 0)


# ------------------------------------------------------------------ Groq strict JSON, effort, caps
GROQ_OK = {"choices": [{"message": {"content": GOOD}}], "usage": {"total_tokens": 5}}


def test_groq_strict_schema_reasoning_effort_and_output_cap_reach_the_request() -> None:
    c, seen = client(GROQ_OK)
    p = GroqProvider(settings(groq_api_key="k", groq_model="openai/gpt-oss-20b"), client=c)
    r = p.complete(
        LLMRequest(
            role="t",
            prompt="x",
            system="sys",
            json_schema=CIOAssessment.model_json_schema(),
            max_output_tokens=500,
        )
    )
    body = json.loads(seen[0].content)
    assert r.strict is True
    assert body["max_tokens"] == 500 and body["reasoning_effort"] == "low"
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    item = schema["properties"]["responses"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(
        item["properties"]
    )
    assert "JSON schema" not in body["messages"][0]["content"]  # not pasted into the prompt


def test_groq_model_without_strict_support_keeps_json_object_and_no_effort() -> None:
    c, seen = client(GROQ_OK)
    p = GroqProvider(settings(groq_api_key="k", groq_model="llama-x"), client=c)
    r = p.complete(LLMRequest(role="t", prompt="x", json_schema=SCHEMA))
    body = json.loads(seen[0].content)
    assert r.strict is False and body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body and "JSON schema" in body["messages"][0]["content"]


def test_qwen_gets_effort_none_and_request_model_overrides_the_default() -> None:
    c, seen = client(GROQ_OK)
    p = GroqProvider(settings(groq_api_key="k", groq_model="openai/gpt-oss-20b"), client=c)
    r = p.complete(LLMRequest(role="t", prompt="x", model="qwen/qwen3.8-27b"))
    assert json.loads(seen[0].content)["reasoning_effort"] == "none"
    assert r.model == "qwen/qwen3.8-27b"


def test_strict_schema_works_for_every_committee_model() -> None:
    for cls in (CompanyProfile, NewsReport, BearCase, CIOAssessment):
        out = strict_schema(cls.model_json_schema())

        def check(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node["additionalProperties"] is False
                    assert set(node["required"]) == set(node["properties"])
                for v in node.values():
                    check(v)
            elif isinstance(node, list):
                for v in node:
                    check(v)

        check(out)
        assert "$ref" not in json.dumps(out) and "default" not in json.dumps(out)


def test_gemini_keeps_thinking_off_and_uses_the_role_cap() -> None:
    c, seen = client({"candidates": [{"content": {"parts": [{"text": GOOD}]}}]})
    p = GeminiProvider(settings(gemini_api_key="k", gemini_model="g1"), client=c)
    p.complete(LLMRequest(role="news", prompt="x", json_schema=SCHEMA, max_output_tokens=500))
    cfg = json.loads(seen[0].content)["generationConfig"]
    assert cfg["maxOutputTokens"] == 500 and cfg["thinkingConfig"] == {"thinkingBudget": 0}


def test_each_role_cap_reaches_the_provider(env: None) -> None:
    s = settings()
    caps = s.llm_role_max_output_tokens
    assert caps == {"company_profile": 600, "news": 500, "bear": 500, "cio": 500}
    for role, cap in {**caps, "other": s.llm_max_output_tokens}.items():
        f = FakeLLMProvider([GOOD])
        structured_call(role=role, model_cls=Out, system="s", prompt=f"p-{role}",
                        template=lambda: Out(ok=False), cache_scope="global", providers=[f],
                        settings=s)  # fmt: skip
        assert f.seen[0].max_output_tokens == cap


# ------------------------------------------------------------------ Groq per-minute limits
def test_remaining_tokens_header_makes_a_too_big_call_skip_groq(env: None) -> None:
    s = settings(groq_api_key="k", groq_model="llama-x")
    headers = {"x-ratelimit-remaining-tokens": "300", "x-ratelimit-reset-tokens": "12s"}
    c, seen = client(GROQ_OK, headers)
    p = GroqProvider(s, client=c)
    kw: dict[str, Any] = {
        "role": "news",
        "model_cls": Out,
        "system": "s",
        "template": lambda: Out(ok=False),
        "cache_scope": "global",
        "providers": [p],
        "settings": s,
    }
    assert structured_call(prompt="short", **kw).source == "llm"  # records "300 left"
    big = structured_call(prompt="word " * 400, **kw)
    assert big.source == "template" and len(seen) == 1  # no second request was sent
    assert any("left this minute" in n for n in big.notes)


def test_tokens_per_minute_bucket_refuses_calls_that_do_not_fit(env: None) -> None:
    s = settings(groq_api_key="k", groq_model="llama-x", groq_tokens_per_minute=900)
    c, seen = client(GROQ_OK)
    p = GroqProvider(s, client=c)
    kw: dict[str, Any] = {
        "role": "news",
        "model_cls": Out,
        "system": "s",
        "template": lambda: Out(ok=False),
        "cache_scope": "global",
        "providers": [p],
        "settings": s,
    }
    r1 = structured_call(prompt="a " * 200, **kw)  # ~ 100 prompt + 500 output tokens
    r2 = structured_call(prompt="b " * 200, **kw)
    assert r1.source == "llm" and r2.source == "template" and len(seen) == 1
    assert any("token limit" in n for n in r2.notes)


def test_bucket_cost_is_taken_atomically_and_refills(env: None) -> None:
    b = TokenBucket(settings())
    now = utcnow()
    assert b.try_acquire("x|tpm", now, 1000, 600.0)
    assert not b.try_acquire("x|tpm", now, 1000, 600.0)
    assert b.try_acquire("x|tpm", now + timedelta(seconds=30), 1000, 600.0)  # +500 refilled
    assert not b.try_acquire("y|tpm", now, 1000, 1001.0)  # can never fit


# ------------------------------------------------------------------ ledger and cache hits
def test_record_usage_increments_the_new_columns(env: None) -> None:
    record_usage("gemini", "m", requests=1, tokens=10, tokens_in=6, tokens_out=4, tokens_cached=2)
    record_usage("gemini", "m", cache_hits=1)
    record_usage("gemini", "m", requests=1, tokens=5, tokens_in=3, tokens_out=2)
    (row,) = usage_for_day()
    assert (row.requests, row.tokens, row.tokens_in, row.tokens_out) == (2, 15, 9, 6)
    assert (row.tokens_cached, row.cache_hits) == (2, 1)


def test_structured_call_records_tokens_and_cache_hits(env: None) -> None:
    f = FakeLLMProvider([GOOD], name="gemini", model="m-1")
    kw: dict[str, Any] = {
        "role": "news",
        "model_cls": Out,
        "system": "s",
        "prompt": "p",
        "cache_scope": "global",
        "template": lambda: Out(ok=False),
        "providers": [f],
        "settings": settings(),
    }
    assert structured_call(**kw).source == "llm"
    assert structured_call(**kw).source == "cache"
    (row,) = usage_for_day()
    assert row.requests == 1 and row.cache_hits == 1


# ------------------------------------------------------------------ cache reuse
def test_identical_news_prompt_is_reused_after_six_hours(env: None) -> None:
    s = settings()
    f = FakeLLMProvider([GOOD, GOOD], name="gemini", model="m-1")
    kw: dict[str, Any] = {
        "role": "news",
        "model_cls": Out,
        "system": "s",
        "prompt": "same passages",
        "cache_scope": "global",
        "template": lambda: Out(ok=False),
        "providers": [f],
        "settings": s,
        "news_dependent": True,
    }
    structured_call(**kw)
    later = utcnow() + timedelta(hours=30)
    assert structured_call(now=later, **kw).source == "cache"
    kw["prompt"] = "different passages"  # real new news changes the prompt: it re-runs
    assert structured_call(now=later, **kw).source == "llm"


def test_an_answer_from_another_provider_serves_the_same_prompt_primary_preferred(
    env: None,
) -> None:
    s = settings()
    kw: dict[str, Any] = {
        "role": "bear",
        "model_cls": Out,
        "system": "s",
        "prompt": "p",
        "cache_scope": "global",
        "template": lambda: Out(ok=False),
        "settings": s,
    }
    groq = FakeLLMProvider(['{"ok": false}'], name="groq", model="g-1")
    gem = FakeLLMProvider(['{"ok": true}'], name="gemini", model="m-1")
    structured_call(providers=[groq], **kw)  # a failover day: groq answered
    again = structured_call(providers=[gem, groq], **kw)  # gemini is back
    assert again.source == "cache" and again.provider == "groq" and gem.seen == []
    structured_call(providers=[gem], **{**kw, "prompt": "q"})
    both = FakeLLMProvider(['{"ok": false}'], name="groq", model="g-1")
    structured_call(providers=[both], **{**kw, "prompt": "q"})  # cached under gemini
    hit = structured_call(providers=[groq, gem], **{**kw, "prompt": "q"})
    assert hit.source == "cache" and hit.provider in ("gemini", "groq")


def test_strict_answer_that_fails_validation_is_not_retried(env: None) -> None:
    s = settings(groq_api_key="k", groq_model="openai/gpt-oss-20b")
    c, seen = client({"choices": [{"message": {"content": '{"nope": 1}'}}]})
    p = GroqProvider(s, client=c)
    r = structured_call(role="news", model_cls=Out, system="s", prompt="p", cache_scope="global",
                        template=lambda: Out(ok=False), providers=[p], settings=s)  # fmt: skip
    assert r.source == "template" and len(seen) == 1


# ------------------------------------------------------------------ per-role model routing
def test_roles_use_their_configured_model_and_empty_setting_changes_nothing(env: None) -> None:
    routed = settings(llm_role_models={"news": {"gemini": "flash-lite"}})
    kw: dict[str, Any] = {
        "model_cls": Out,
        "system": "s",
        "prompt": "p",
        "cache_scope": "global",
        "template": lambda: Out(ok=False),
    }
    f = FakeLLMProvider([GOOD], name="gemini", model="flash")
    r = structured_call(role="news", providers=[f], settings=routed, **kw)
    assert f.seen[0].model == "flash-lite" and r.model == "flash-lite"
    assert [u.model for u in usage_for_day()] == ["flash-lite"]
    g = FakeLLMProvider([GOOD], name="gemini", model="flash")
    r2 = structured_call(role="bear", providers=[g], settings=routed, **kw)  # not routed
    assert g.seen[0].model is None and r2.model == "flash"
    assert settings().llm_role_models == {}
    h = FakeLLMProvider([GOOD], name="gemini", model="flash")
    structured_call(role="cio", providers=[h], settings=settings(), **kw)
    assert h.seen[0].model is None


def test_groq_fallbacks_no_longer_list_the_retired_llama() -> None:
    s = settings()
    assert "llama-3.3-70b-versatile" not in s.groq_model_fallbacks
    assert "qwen/qwen3.8-27b" in s.groq_model_fallbacks
