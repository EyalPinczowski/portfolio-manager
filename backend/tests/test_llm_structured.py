"""structured_call: the template path first (the default), then validate -> retry once, the cache,
the shared token bucket and the fallbacks counter. No network: providers are scripted fakes."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import BaseModel, Field

from app.config import Settings
from app.llm import LLMError, LLMUnavailableError, TokenBucket, structured_call, usage_for_day
from app.llm.cache import get_cached
from app.llm.fakes import FakeLLMProvider
from app.model_probe import reset_probe_results
from app.timeutil import utcnow

NO_KEYS = {"gemini_api_key": None, "groq_api_key": None}


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **{**NO_KEYS, **kw})  # type: ignore[arg-type]


class Bear(BaseModel):
    risk: str
    severity: int = Field(ge=1, le=5)


GOOD = '{"risk": "valuation", "severity": 3}'


def template() -> Bear:
    return Bear(risk="rule-based: P/E above sector median", severity=2)


def call(providers: list[FakeLLMProvider] | None, s: Settings | None = None, **kw: object):  # type: ignore[no-untyped-def]
    return structured_call(
        role="bear",
        model_cls=Bear,
        system="argue against buying",
        prompt="AAPL report",
        template=template,
        providers=providers,
        settings=s or settings(),
        **kw,  # type: ignore[arg-type]
    )


def totals() -> dict[str, int]:
    rows = usage_for_day()
    return {
        "requests": sum(r.requests for r in rows),
        "tokens": sum(r.tokens for r in rows),
        "fallbacks": sum(r.fallbacks for r in rows),
    }


# ---------------------------------------------------------------- the template path (default)
def test_with_no_key_the_template_answers_and_counts_a_fallback(env: None) -> None:
    res = call(None)  # providers come from settings: no keys, so none
    assert res.source == "template" and res.provider == "template"
    assert res.value == template()
    assert any("no LLM provider" in n for n in res.notes)
    rows = usage_for_day()
    assert [(r.provider, r.requests, r.fallbacks) for r in rows] == [("gemini", 0, 1)]
    assert rows[0].model == settings().gemini_model  # the configured id, not a hard-coded one


def test_each_template_call_increments_the_counter(env: None) -> None:
    for _ in range(3):
        call(None)
    assert totals() == {"requests": 0, "tokens": 0, "fallbacks": 3}


def test_llm_disabled_is_the_template_even_with_keys(env: None) -> None:
    s = settings(llm_enabled=False, gemini_api_key="k")
    res = call(None, s)
    assert res.source == "template" and totals()["fallbacks"] == 1


def test_template_results_are_never_cached(env: None) -> None:
    res = call(None)
    assert get_cached(res.input_hash, 24) is None
    fake = FakeLLMProvider([GOOD])
    assert call([fake]).source == "llm"  # a later success is not shadowed by the template


def test_template_result_carries_hashes_for_paper_calls(env: None) -> None:
    res = call(None)
    assert len(res.prompt_hash) == 16 and len(res.model_hash) == 16 and len(res.input_hash) == 64
    assert res.model_hash != call([FakeLLMProvider([GOOD])], use_cache=False).model_hash


# ---------------------------------------------------------------- the LLM path
def test_valid_answer_is_used_recorded_and_cached(env: None) -> None:
    fake = FakeLLMProvider([GOOD], tokens=42)
    res = call([fake])
    assert res.source == "llm" and res.value == Bear(risk="valuation", severity=3)
    assert (res.provider, res.model, res.attempts) == ("fake", "fake-1", 1)
    assert totals() == {"requests": 1, "tokens": 42, "fallbacks": 0}
    assert (
        fake.seen[0].json_schema is not None
        and "severity" in fake.seen[0].json_schema["properties"]
    )

    again = call([FakeLLMProvider([])])  # nothing scripted: it must not be asked
    assert again.source == "cache" and again.value == res.value
    assert totals()["requests"] == 1


def test_cache_expires_after_the_ttl(env: None) -> None:
    s = settings(llm_cache_ttl_hours=1.0)
    call([FakeLLMProvider([GOOD])], s)
    later = utcnow() + timedelta(hours=2)
    res = call([FakeLLMProvider([GOOD])], s, now=later)
    assert res.source == "llm"


def test_a_different_input_is_a_different_cache_key(env: None) -> None:
    a = call([FakeLLMProvider([GOOD])])
    b = structured_call(
        role="bear",
        model_cls=Bear,
        system="argue against buying",
        prompt="MSFT report",
        template=template,
        providers=[FakeLLMProvider([GOOD])],
        settings=settings(),
    )
    assert a.input_hash != b.input_hash and b.source == "llm"


def test_invalid_then_valid_retries_once_with_the_error_attached(env: None) -> None:
    fake = FakeLLMProvider(['{"risk": "x", "severity": 9}', GOOD])
    res = call([fake])
    assert res.source == "llm" and res.attempts == 2
    assert "rejected" in fake.seen[1].prompt and "severity" in fake.seen[1].prompt
    assert fake.seen[1].prompt.startswith("AAPL report")
    assert totals() == {"requests": 2, "tokens": 50, "fallbacks": 0}


def test_invalid_twice_falls_back_to_the_template(env: None) -> None:
    fake = FakeLLMProvider(["not json", '{"risk": "x"}', GOOD])
    res = call([fake])
    assert res.source == "template" and res.attempts == 2
    assert len(fake.responses) == 1  # exactly one retry, no third request
    assert totals() == {"requests": 2, "tokens": 50, "fallbacks": 1}


@pytest.mark.parametrize(
    "bad", ['{"risk": "x", "severity": NaN}', '{"risk": "x", "severity": Infinity}']
)
def test_non_finite_json_is_invalid(env: None, bad: str) -> None:
    res = call([FakeLLMProvider([bad, bad])])
    assert res.source == "template"


def test_code_fences_around_json_are_accepted(env: None) -> None:
    assert call([FakeLLMProvider([f"```json\n{GOOD}\n```"])]).source == "llm"


def test_provider_error_moves_on_to_the_next_provider(env: None) -> None:
    broken = FakeLLMProvider([LLMError("gemini: HTTP 500")], name="gemini")
    backup = FakeLLMProvider([GOOD], name="groq", model="g-1")
    res = call([broken, backup])
    assert res.source == "llm" and res.provider == "groq"
    rows = {r.provider: r for r in usage_for_day()}
    assert rows["gemini"].requests == 1 and rows["groq"].requests == 1  # a failed call used quota


def test_an_unavailable_provider_sends_and_counts_nothing(env: None) -> None:
    nokey = FakeLLMProvider([LLMUnavailableError("gemini: no API key configured")], name="gemini")
    res = call([nokey])
    assert res.source == "template" and res.attempts == 0
    assert totals() == {"requests": 0, "tokens": 0, "fallbacks": 1}


# ---------------------------------------------------------------- the shared token bucket
def test_an_empty_bucket_yields_the_template_without_asking_the_provider(env: None) -> None:
    s = settings(llm_requests_per_minute=2)
    now = utcnow()
    bucket = TokenBucket(s)
    fake = FakeLLMProvider([GOOD, GOOD, GOOD], name="gemini")
    results = [
        structured_call(
            role="bear",
            model_cls=Bear,
            system="s",
            prompt=f"p{i}",
            template=template,
            providers=[fake],
            settings=s,
            bucket=bucket,
            now=now,
        )
        for i in range(3)
    ]
    assert [r.source for r in results] == ["llm", "llm", "template"]
    assert len(fake.seen) == 2  # the third was never sent
    assert any("bucket empty" in n for n in results[2].notes)
    assert totals() == {"requests": 2, "tokens": 50, "fallbacks": 1}


def test_an_empty_bucket_moves_to_the_next_provider(env: None) -> None:
    s = settings(llm_requests_per_minute=1)
    now = utcnow()
    a = FakeLLMProvider([GOOD, GOOD], name="gemini")
    b = FakeLLMProvider([GOOD, GOOD], name="groq")
    bucket = TokenBucket(s)
    out = [
        structured_call(
            role="r",
            model_cls=Bear,
            system="s",
            prompt=f"q{i}",
            template=template,
            providers=[a, b],
            settings=s,
            bucket=bucket,
            now=now,
        ).provider
        for i in range(3)
    ]
    assert out == ["gemini", "groq", "template"]
    assert len(a.seen) == 1 and len(b.seen) == 1  # one token each, per provider


def test_the_retry_needs_its_own_token(env: None) -> None:
    s = settings(llm_requests_per_minute=1)
    fake = FakeLLMProvider(["nope", GOOD])
    res = call([fake], s, bucket=TokenBucket(s), now=utcnow())
    assert res.source == "template" and len(fake.seen) == 1
    assert totals()["fallbacks"] == 1


def test_model_ids_come_from_settings(env: None) -> None:
    from app.llm import GeminiProvider, GroqProvider

    reset_probe_results()
    s = settings(gemini_model="gem-test-1", groq_model="groq-test-1")
    assert GeminiProvider(s).model == "gem-test-1"
    assert GroqProvider(s).model == "groq-test-1"
