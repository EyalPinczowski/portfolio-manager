"""Phase 2.0-B item 5: CORS, expired sessions, history cache cap, model ids and their probe."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app import model_probe
from app.cli import check_config
from app.config import Settings, validate_production
from app.model_probe import ModelChoice, probe_models, resolve_model, run_probe
from app.models import AuthSession, User
from app.providers.cache import TTLCache
from app.scheduler import jobs
from app.timeutil import utcnow

GOOD = {"secret_key": "x" * 40, "cookie_secure": True}


# ---------------------------------------------------------------- CORS
def test_production_refuses_a_wildcard_cors_origin() -> None:
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        validate_production(Settings(env="production", cors_origins=["*"], _env_file=None, **GOOD))
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        validate_production(
            Settings(
                env="production", cors_origins=["https://a.com", " * "], _env_file=None, **GOOD
            )
        )
    validate_production(
        Settings(env="production", cors_origins=["https://a.com"], _env_file=None, **GOOD)
    )
    validate_production(Settings(env="dev", cors_origins=["*"], _env_file=None))  # dev may


# ---------------------------------------------------------------- expired sessions
def test_expired_sessions_are_purged_and_live_ones_kept(db: Session) -> None:
    user = User(email="s@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    assert user.id is not None
    now = utcnow()
    for i, delta in enumerate((-timedelta(days=1), -timedelta(seconds=1), timedelta(hours=1))):
        db.add(
            AuthSession(token_hash=f"h{i}", user_id=user.id, csrf_token="c", expires_at=now + delta)
        )
    db.commit()
    assert jobs.run_session_purge(db) == 2
    left = db.exec(select(AuthSession)).all()
    assert [s.token_hash for s in left] == ["h2"]
    assert jobs.run_session_purge(db) == 0


def test_the_session_purge_is_a_scheduled_job() -> None:
    from app.scheduler.setup import JOB_IDS, build_scheduler

    assert "purge_sessions" in JOB_IDS
    assert "purge_sessions" in {j.id for j in build_scheduler().get_jobs()}


# ---------------------------------------------------------------- history cache cap
def test_ttl_cache_evicts_the_oldest_entry_past_its_cap() -> None:
    c: TTLCache[int] = TTLCache(3600, max_entries=3)
    for i in range(10):
        c.set(f"k{i}", i)
    assert len(c) == 3 and c.get("k9") == 9 and c.get("k6") is None
    c.set("k7", 70)  # refreshing a key makes it the newest
    c.set("k10", 10)
    assert c.get("k7") == 70 and c.get("k8") is None


def test_the_yahoo_history_cache_is_capped_by_config() -> None:
    from app.providers.yfinance_provider import YFinanceProvider

    p = YFinanceProvider(Settings(_env_file=None, history_cache_max_entries=5))
    for i in range(50):
        p._history.set(f"S{i}", (None, "USD"))  # type: ignore[arg-type]
    assert len(p._history) == 5


# ---------------------------------------------------------------- model ids and the probe
def test_model_ids_are_config() -> None:
    s = Settings(_env_file=None)
    assert s.gemini_model and s.groq_model and s.gemini_model_fallbacks and s.groq_model_fallbacks
    custom = Settings(_env_file=None, gemini_model="g-x", groq_model="q-y")
    assert (custom.gemini_model, custom.groq_model) == ("g-x", "q-y")


def test_resolve_model_prefers_the_configured_id_then_a_listed_fallback() -> None:
    assert resolve_model("gemini", "a", ["b"], {"a", "b"}).status == "ok"
    fb = resolve_model("gemini", "a", ["x", "b"], {"b"})
    assert (fb.model, fb.status) == ("b", "fallback")
    gone = resolve_model("gemini", "a", ["b"], {"c"})
    assert (gone.model, gone.status) == ("a", "none_available")
    assert resolve_model("groq", "a", ["b"], None).status == "unverified"


class FakeLister:
    def __init__(self, models: dict[str, set[str] | None]) -> None:
        self.models = models
        self.asked: list[str] = []

    def list_models(self, provider: str, settings: Settings) -> set[str] | None:
        self.asked.append(provider)
        return self.models[provider]


def test_probe_only_asks_providers_that_have_a_key() -> None:
    lister = FakeLister({"gemini": {"gemini-3.5-flash-lite"}, "groq": set()})
    s = Settings(_env_file=None, gemini_api_key="k")
    res = probe_models(s, lister)
    assert lister.asked == ["gemini"]
    assert res["gemini"].status == "ok" and res["groq"].status == "no_key"


def test_probe_switches_to_a_fallback_when_the_model_is_retired() -> None:
    s = Settings(_env_file=None, gemini_api_key="k", groq_api_key="q")
    lister = FakeLister({"gemini": {"gemini-3.6-flash"}, "groq": {"openai/gpt-oss-20b"}})
    model_probe.reset_probe_results()
    run_probe(s, lister)
    assert model_probe.active_model("gemini", s) == "gemini-3.6-flash"
    assert model_probe.active_model("groq", s) == "openai/gpt-oss-20b"
    assert model_probe.model_status()["gemini"].status == "fallback"
    model_probe.reset_probe_results()
    assert (
        model_probe.active_model("gemini", s) == "gemini-3.5-flash-lite"
    )  # nothing probed: config


def test_a_changed_config_ignores_a_stale_probe_result() -> None:
    model_probe.reset_probe_results()
    run_probe(
        Settings(_env_file=None, gemini_api_key="k"), FakeLister({"gemini": {"x"}, "groq": set()})
    )
    other = Settings(_env_file=None, gemini_api_key="k", gemini_model="brand-new")
    assert model_probe.active_model("gemini", other) == "brand-new"
    model_probe.reset_probe_results()


def test_probe_errors_and_unreachable_providers_never_raise() -> None:
    class Boom:
        def list_models(self, provider: str, settings: Settings) -> set[str] | None:
            raise RuntimeError("network down")

    res = probe_models(Settings(_env_file=None, gemini_api_key="k"), Boom())
    assert res["gemini"].status == "unverified" and res["gemini"].model == "gemini-3.5-flash-lite"
    run_probe(Settings(_env_file=None, gemini_api_key="k"), Boom())  # swallowed
    model_probe.reset_probe_results()


def test_probe_can_be_disabled() -> None:
    res = probe_models(Settings(_env_file=None, model_probe_enabled=False, gemini_api_key="k"))
    assert {c.status for c in res.values()} == {"disabled"}


def test_the_probe_never_blocks_startup(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    class Slow:
        def list_models(self, provider: str, settings: Settings) -> set[str] | None:
            time.sleep(3)
            return set()

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(model_probe, "NetworkModelLister", Slow)
    t0 = time.monotonic()
    with TestClient(create_app()) as c:
        assert c.get("/api/health").status_code == 200
        assert time.monotonic() - t0 < 2.0
    model_probe.reset_probe_results()


def test_check_config_reports_the_state_without_secrets() -> None:
    s = Settings(
        env="production", _env_file=None, trusted_proxy_header="X-Client-IP",
        turnstile_enabled=True, **GOOD,
    )  # fmt: skip
    report = check_config(s)
    assert report["proxy"].startswith("PROBLEM") and "PROXY_SHARED_SECRET" in report["proxy"]
    assert report["turnstile"].startswith("misconfigured")
    assert "x" * 40 not in " ".join(report.values())
    ok = check_config(Settings(env="production", _env_file=None, **GOOD))
    assert ok["turnstile"].startswith("off") and "no challenge" in ok["turnstile"]


def test_gemini_ocr_uses_the_resolved_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.ocr import gemini

    seen: list[str] = []

    class FakeModels:
        def generate_content(self, model: str, **kw: object) -> object:
            seen.append(model)
            raise RuntimeError("stop")

    class FakeClient:
        def __init__(self, **kw: object) -> None:
            self.models = FakeModels()

    import google.genai as genai

    monkeypatch.setattr(genai, "Client", FakeClient)
    s = Settings(_env_file=None, gemini_api_key="k", gemini_model="old-model")
    model_probe.reset_probe_results()
    model_probe._results["gemini"] = ModelChoice("gemini", "old-model", "new-model", "fallback")
    with pytest.raises(Exception, match="not available"):
        gemini.GeminiProvider(s).extract(b"x")
    model_probe.reset_probe_results()
    assert seen == ["new-model"]
