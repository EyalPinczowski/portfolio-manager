"""Pre-deploy review fixes: Gemini model + probe (B1/M6), token budget (H2), committee deadline (H3),
proxy-auth lock (H4), scheduler flag (H1), replayed Telegram /start (M8). No network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.analyze.public_facts import PublicFacts
from app.committee.roles import run_committee
from app.config import Settings, gemini_thinking_config, get_settings, validate_proxy
from app.llm import providers as llm_providers
from app.llm.base import LLMRequest
from app.llm.fakes import FakeLLMProvider
from app.llm.ledger import record_usage
from app.llm.providers import GeminiProvider
from app.model_probe import NetworkModelLister, probe_models

SignupFn = Callable[..., TestClient]
NO_KEYS: dict[str, Any] = {"gemini_api_key": None, "groq_api_key": None}


def cfg(**kw: Any) -> Settings:
    return Settings(_env_file=None, **{**NO_KEYS, **kw})


# ------------------------------------------------------------------ B1: thinking config per model
def test_default_model_and_fallback_order() -> None:
    s = cfg()
    assert s.gemini_model == "gemini-3.5-flash-lite"
    assert s.gemini_model_fallbacks == ["gemini-3.6-flash", "gemini-2.5-flash-lite"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gemini-2.5-flash-lite", {"thinkingBudget": 0}),
        ("gemini-3.5-flash-lite", {"thinkingLevel": "minimal"}),
        ("gemini-3.6-flash", {"thinkingLevel": "minimal"}),
        ("gemini-3.8-flash", {"thinkingLevel": "low"}),  # 3.8 rejects "minimal"
    ],
)
def test_thinking_config_is_per_model_and_never_both(model: str, expected: dict[str, Any]) -> None:
    assert gemini_thinking_config(model, cfg()) == expected


def test_gemini_request_sends_the_matching_thinking_field() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prov = GeminiProvider(cfg(gemini_api_key="k"), client=client)
    prov.complete(LLMRequest(role="r", prompt="p", model="gemini-2.5-flash-lite"))
    prov.complete(LLMRequest(role="r", prompt="p", model="gemini-3.8-flash"))
    assert seen[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert seen[1]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert llm_providers.GEMINI_BASE_URL.startswith("https://")


# ------------------------------------------------------------------ B1/M6: the probe, plain httpx
def _gemini_api(listed: list[str], refused: set[str], calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "secret-key"
        assert "secret-key" not in str(request.url)  # the key goes in a header, not the URL
        if request.method == "GET":
            calls.append("list")
            return httpx.Response(200, json={"models": [{"name": f"models/{m}"} for m in listed]})
        model = request.url.path.split("/models/")[1].split(":")[0]
        calls.append(model)
        body = json.loads(request.content)
        assert body["generationConfig"]["maxOutputTokens"] <= 16
        return httpx.Response(403 if model in refused else 200, json={})

    return httpx.MockTransport(handler)


def test_probe_skips_a_model_that_is_listed_but_refused() -> None:
    calls: list[str] = []
    transport = _gemini_api(
        ["gemini-3.5-flash-lite", "gemini-3.6-flash"], {"gemini-3.5-flash-lite"}, calls
    )
    s = cfg(gemini_api_key="secret-key")
    res = probe_models(s, NetworkModelLister(transport))
    assert res["gemini"].status == "fallback" and res["gemini"].model == "gemini-3.6-flash"
    assert calls == ["list", "gemini-3.5-flash-lite", "gemini-3.6-flash"]


def test_probe_keeps_a_working_model_with_one_call() -> None:
    calls: list[str] = []
    transport = _gemini_api(["gemini-3.5-flash-lite"], set(), calls)
    res = probe_models(cfg(gemini_api_key="secret-key"), NetworkModelLister(transport))
    assert res["gemini"].status == "ok" and calls == ["list", "gemini-3.5-flash-lite"]


def test_probe_does_not_import_the_google_sdk() -> None:
    import app.model_probe as mp

    src = Path(mp.__file__).read_text()
    assert "google" not in src.replace("googleapis", "")


# ------------------------------------------------------------------ H2: tokens per day
class Out(BaseModel):
    ok: bool


def test_a_provider_over_its_token_budget_is_skipped(env: None) -> None:
    from app.llm.structured import structured_call

    s = cfg(llm_daily_token_budget={"gemini": 1000}, llm_requests_per_minute=100)
    record_usage("gemini", "m", requests=1, tokens_in=600, tokens_out=500)
    gem = FakeLLMProvider(name="gemini", responses=['{"ok": true}'])
    res = structured_call(
        role="r", model_cls=Out, system="s", prompt="p", template=lambda: Out(ok=False),
        cache_scope="global", providers=[gem], settings=s, use_cache=False,
    )  # fmt: skip
    assert res.source == "template"
    assert any("daily token budget reached" in n for n in res.notes)
    # 0 = no limit known
    s2 = cfg(llm_daily_token_budget={"gemini": 0}, llm_requests_per_minute=100)
    res2 = structured_call(
        role="r", model_cls=Out, system="s", prompt="p2", template=lambda: Out(ok=False),
        cache_scope="global", providers=[gem], settings=s2, use_cache=False,
    )  # fmt: skip
    assert res2.source == "llm"


def test_default_token_budgets() -> None:
    assert cfg().llm_daily_token_budget == {"gemini": 0, "mistral": 0, "groq": 180000}


# ------------------------------------------------------------------ H3: committee deadline
def test_after_the_deadline_the_remaining_roles_use_templates(env: None) -> None:
    from app.db import new_session
    from tests.evals.conftest import seed
    from tests.evals.mock_llm import MockLLM

    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])  # profile in time, then late
    s = cfg(committee_deadline_seconds=75, llm_requests_per_minute=1000, llm_role_rpm=1000,
            llm_user_rpm=1000, llm_daily_budget=10000)  # fmt: skip
    llm = MockLLM()
    with new_session() as db:
        seed(db, "AAPL", 4, "news")
        seed(db, "AAPL", 3, "filing")
        facts = PublicFacts(
            symbol="AAPL",
            name="Apple",
            market="US",
            asset_type="stock",
            sector="Tech",
            country="US",
        )
        rep = run_committee(db, facts, providers=[llm], settings=s, user_id=1,
                            clock=lambda: next(ticks, 100.0))  # fmt: skip
    roles = {r.role for r in llm.seen}
    assert roles == {"company_profile"}  # no model call after the deadline
    for r in (rep.news, rep.bear, rep.cio):
        assert r.source == "template" and any("time limit" in n for n in r.notes)
    assert not any("time limit" in n for n in rep.profile.notes)


# ------------------------------------------------------------------ H4: proxy auth
@pytest.fixture
def locked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("REQUIRE_PROXY_AUTH", "true")
    monkeypatch.setenv("PROXY_SHARED_SECRET", "s" * 20)
    get_settings.cache_clear()
    return client


def test_proxy_auth_blocks_direct_api_calls(locked: TestClient) -> None:
    assert locked.get("/api/portfolios").status_code == 404
    assert locked.get("/api/portfolios", headers={"X-Proxy-Auth": "wrong"}).status_code == 404
    assert locked.get("/api/portfolios", headers={"X-Proxy-Auth": "s" * 20}).status_code != 404
    assert locked.get("/api/health").status_code == 200


def test_proxy_auth_leaves_the_telegram_webhook_to_its_own_secret(locked: TestClient) -> None:
    r = locked.post("/api/telegram/webhook", json={})
    assert r.status_code != 404  # 503/403 from the webhook's own check, not the origin lock


def test_proxy_auth_is_off_by_default(client: TestClient) -> None:
    assert get_settings().require_proxy_auth is False
    assert client.get("/api/portfolios").status_code != 404


def test_the_app_refuses_to_start_without_the_secret() -> None:
    with pytest.raises(RuntimeError, match="PROXY_SHARED_SECRET"):
        validate_proxy(cfg(require_proxy_auth=True))
    validate_proxy(cfg(require_proxy_auth=True, proxy_shared_secret="s" * 20))


# ------------------------------------------------------------------ H1: scheduler flag
def test_universe_job_can_be_turned_off() -> None:
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.scheduler.setup import register_jobs

    for flag, present in ((True, True), (False, False)):
        sched = BackgroundScheduler()
        register_jobs(sched, cfg(scheduler_universe_enabled=flag))
        ids = {j.id for j in sched.get_jobs()}
        assert ("universe_scores" in ids) is present and "quotes" in ids


def test_the_slim_image_turns_the_universe_job_off() -> None:
    docker = (Path(__file__).parent.parent / "Dockerfile.slim").read_text()
    assert "SCHEDULER_UNIVERSE_ENABLED=false" in docker


def test_database_url_must_be_a_database_and_the_error_never_shows_it() -> None:
    import pytest as _pytest

    from app.config import Settings

    for ok in (
        "sqlite:///./x.db",
        "postgresql://u:p@h:5432/db",
        "postgres://u:p@h/db",
        "postgresql+psycopg://u:p@h/db",
    ):
        assert Settings(database_url=ok).database_url == ok
    with _pytest.raises(ValueError) as err:
        Settings(database_url="https://abc.supabase.co/secretpass")
    assert "DATABASE_URL must start with postgresql://" in str(err.value)
    assert "secretpass" not in str(err.value)
