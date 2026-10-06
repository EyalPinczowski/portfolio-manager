"""POST /api/analyze/{symbol}/committee: template fallback, fake LLM, gate, rate limit, scoping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.analyze import committee_providers
from app.config import get_settings
from app.db import new_session
from app.launchgate import get_launch_gate
from app.verdict_words import verdict_words_in_text
from tests.conftest import FakeHistory, FakeQuotes
from tests.evals.conftest import seed
from tests.evals.mock_llm import MockLLM
from tests.test_exit_levels import frame
from tests.test_launch_gate import gate as open_gate
from tests.test_screener import _strings

SignupFn = Callable[..., TestClient]


@pytest.fixture
def c(
    signup: SignupFn,
    quotes: FakeQuotes,
    history: FakeHistory,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    for k, v in {
        "LLM_REQUESTS_PER_MINUTE": "1000",
        "LLM_ROLE_RPM": "1000",
        "LLM_USER_RPM": "1000",
        "LLM_DAILY_BUDGET": "10000",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    df = frame()
    history.frames["AAPL"] = df
    quotes.set("AAPL", float(df["Close"].iloc[-1]), "USD")
    with new_session() as db:
        seed(db, "AAPL", 4, "news")
        seed(db, "AAPL", 3, "filing")
    return signup("a@mail.com")


def _use(c: TestClient, llm: MockLLM | None) -> None:
    c.app.dependency_overrides[committee_providers] = lambda: [llm] if llm else []  # type: ignore[attr-defined]


def test_without_a_provider_every_role_uses_its_template(c: TestClient) -> None:
    _use(c, None)
    r = c.post("/api/analyze/AAPL/committee")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "AAPL" and body["llm_used"] is False
    rep = body["report"]
    for role in ("profile", "news", "bear", "cio"):
        assert rep[role]["source"] == "template"
    assert rep["cio_score"]["adjusted_score"] is not None
    # codes, not English sentences; the template CIO never answers (so never "accepts") a risk
    assert rep["cio"]["value"]["adjustment_code"] == "no_adjustment"
    assert rep["cio"]["value"]["responses"] == [] and rep["cio_score"]["adjustment"] == 0
    assert not any(r["code"] == "low_data_completeness" for r in rep["bear"]["value"]["risks"])
    assert all(r["code"] or r["text"] for r in rep["bear"]["value"]["risks"])
    assert 0 <= body["data_completeness_pct"] <= 100
    assert isinstance(body["data_completeness_low"], bool)
    assert body["launch_gate_codes"] and all(g["code"] for g in body["launch_gate_codes"])
    assert body["disclaimer"] == "Not financial advice."


def test_fake_llm_answers_are_used_and_cited(c: TestClient) -> None:
    m = MockLLM()
    _use(c, m)
    body = c.post("/api/analyze/AAPL/committee").json()
    assert body["llm_used"] is True and m.seen
    rep = body["report"]
    assert rep["profile"]["source"] == "llm" and rep["profile"]["citations"]
    assert rep["bear"]["value"]["risks"] and rep["cio"]["source"] == "llm"
    cio = rep["cio_score"]
    assert abs(cio["adjustment"]) <= get_settings().committee_cio_max_adjustment
    # the model only ever saw public facts and passages, never a user field
    assert all("a@mail.com" not in r.prompt for r in m.seen)


def test_a_misbehaving_model_falls_back_to_templates(c: TestClient) -> None:
    for mode in ("garbage", "error", "verdict", "bad_cite"):
        _use(c, MockLLM(mode))
        r = c.post("/api/analyze/AAPL/committee")
        assert r.status_code == 200, (mode, r.text)
        rep = r.json()["report"]
        assert rep["bear"]["source"] == "template"


def test_no_verdict_even_when_the_gate_is_open(c: TestClient) -> None:
    _use(c, MockLLM())
    closed = c.post("/api/analyze/AAPL/committee").json()
    assert closed["launch_gate_open"] is False and closed["launch_gate_reasons"]
    c.app.dependency_overrides[get_launch_gate] = lambda: open_gate()  # type: ignore[attr-defined]
    opened = c.post("/api/analyze/AAPL/committee").json()
    assert opened["launch_gate_open"] is True

    def keys(o: Any) -> set[str]:
        if isinstance(o, dict):
            return set(o) | {k for v in o.values() for k in keys(v)}
        if isinstance(o, list):
            return {k for v in o for k in keys(v)}
        return set()

    assert not {k for k in keys(opened) if k in {"verdict", "recommendation", "action", "rating"}}
    texts = [
        t
        for t in _strings(opened["report"])
        if t not in {"rebutted", "accepted", "unresolved"} and " " in t
    ]
    assert not [t for t in texts if verdict_words_in_text(t)]


def test_bad_symbols_and_login(c: TestClient, client: TestClient) -> None:
    _use(c, None)
    assert c.post("/api/analyze/^GSPC/committee").status_code == 422
    assert c.post("/api/analyze/ZZZZNOPE/committee").status_code == 404
    assert client.post("/api/analyze/AAPL/committee").status_code in (401, 403)


def test_committee_is_rate_limited_per_user(
    c: TestClient, signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(c, None)
    monkeypatch.setenv("COMMITTEE_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    codes = [c.post("/api/analyze/AAPL/committee").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    other = signup("b@mail.com")
    other.app.dependency_overrides[committee_providers] = lambda: []  # type: ignore[attr-defined]
    assert other.post("/api/analyze/AAPL/committee").status_code == 200  # the limit is per user


def test_daily_cap_counts_every_tap_and_reports_runs_left(
    c: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(c, None)
    monkeypatch.setenv("COMMITTEE_RUNS_PER_USER_PER_DAY", "3")
    monkeypatch.setenv("COMMITTEE_RATE_LIMIT_PER_HOUR", "100")
    get_settings.cache_clear()
    left = []
    for _ in range(3):
        r = c.post("/api/analyze/AAPL/committee")  # the 2nd and 3rd are served from saved results
        assert r.status_code == 200, r.text
        assert r.json()["runs_per_day"] == 3
        left.append(r.json()["runs_left_today"])
    assert left == [2, 1, 0]
    r = c.post("/api/analyze/AAPL/committee")
    assert r.status_code == 429
    assert "Daily limit of 3" in r.json()["detail"] and int(r.headers["Retry-After"]) > 0


def test_daily_cap_lives_in_the_database_and_survives_a_restart(
    c: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.auth.ratelimit import clear_all_limiters

    _use(c, None)
    monkeypatch.setenv("COMMITTEE_RUNS_PER_USER_PER_DAY", "2")
    monkeypatch.setenv("COMMITTEE_RATE_LIMIT_PER_HOUR", "100")
    get_settings.cache_clear()
    assert [c.post("/api/analyze/AAPL/committee").status_code for _ in range(2)] == [200, 200]
    clear_all_limiters()  # a restart empties every in-memory limiter
    r = c.post("/api/analyze/AAPL/committee")
    assert r.status_code == 429 and "Daily limit of 2" in r.json()["detail"]


def test_a_bad_symbol_or_a_rejection_does_not_use_up_a_run(
    c: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm.ledger import quota_used

    _use(c, None)
    monkeypatch.setenv("COMMITTEE_RUNS_PER_USER_PER_DAY", "2")
    monkeypatch.setenv("COMMITTEE_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    assert c.post("/api/analyze/^GSPC/committee").status_code == 422
    assert c.post("/api/analyze/ZZZZNOPE/committee").status_code == 404
    assert quota_used("committee:user:1") == 0
    assert c.post("/api/analyze/AAPL/committee").json()["runs_left_today"] == 1
    assert c.post("/api/analyze/AAPL/committee").json()["runs_left_today"] == 0
    assert c.post("/api/analyze/AAPL/committee").status_code == 429  # daily cap: hourly not burnt
    assert quota_used("committee:user:1") == 2


def test_committee_calls_carry_the_user_id_for_the_per_user_budget(
    c: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm.ledger import quota_used

    llm = MockLLM()
    _use(c, llm)
    c.post("/api/analyze/AAPL/committee")
    assert llm.seen
    assert quota_used("user:1") > 0  # the per-user LLM ledger counted the calls


def test_default_caps_are_ten_a_day_and_five_an_hour() -> None:
    s = get_settings()
    assert s.committee_runs_per_user_per_day == 10 and s.committee_rate_limit_per_hour == 5
