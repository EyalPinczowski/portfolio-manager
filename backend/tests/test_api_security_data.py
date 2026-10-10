"""GET /api/securities/{symbol}/analysts and /candles (fixtures and fakes only, no live calls)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.providers.analyst_targets import AnalystTargets, normalise_targets
from app.providers.analyst_trends import RecommendationTrend
from app.providers.registry import Providers
from tests.conftest import FakeHistory
from tests.test_exit_levels import frame

SignupFn = Callable[..., TestClient]


class FakeTrends:
    def __init__(self, rows: dict[str, list[RecommendationTrend]] | None = None) -> None:
        self.rows = rows or {}
        self.boom = False

    def trends(self, symbol: str) -> list[RecommendationTrend] | None:
        if self.boom:
            raise RuntimeError("down")
        return self.rows.get(symbol)


class FakeTargets:
    def __init__(self, rows: dict[str, AnalystTargets] | None = None) -> None:
        self.rows = rows or {}

    def targets(self, symbol: str) -> AnalystTargets | None:
        return self.rows.get(symbol)


TREND = RecommendationTrend(
    period=date(2026, 10, 1), strong_buy=5, buy=10, hold=7, sell=2, strong_sell=1
)


@pytest.fixture
def fakes(providers: Providers) -> tuple[FakeTrends, FakeTargets]:
    t, g = FakeTrends(), FakeTargets()
    providers.analyst_trends = t
    providers.analyst_targets = g
    return t, g


def test_counts_and_targets(signup: SignupFn, fakes: tuple[FakeTrends, FakeTargets]) -> None:
    t, g = fakes
    t.rows["AAPL"] = [TREND]
    g.rows["AAPL"] = AnalystTargets(low=150, mean=200, high=250, currency="USD")
    body = signup("a@mail.com").get("/api/securities/aapl/analysts").json()
    assert body["status"] == "ok" and body["symbol"] == "AAPL"
    assert body["counts"] == {"strong_buy": 5, "buy": 10, "hold": 7, "sell": 2, "strong_sell": 1}
    assert body["analysts_total"] == 25 and body["as_of"] == "2026-10-01"
    assert body["targets"] == {"low": 150, "mean": 200, "high": 250, "currency": "USD"}


def test_targets_absent_is_null(signup: SignupFn, fakes: tuple[FakeTrends, FakeTargets]) -> None:
    fakes[0].rows["AAPL"] = [TREND]
    body = signup("a@mail.com").get("/api/securities/AAPL/analysts").json()
    assert body["status"] == "ok" and body["targets"] is None


def test_targets_only_still_ok(signup: SignupFn, fakes: tuple[FakeTrends, FakeTargets]) -> None:
    fakes[1].rows["AAPL"] = AnalystTargets(mean=10, currency="USD")
    body = signup("a@mail.com").get("/api/securities/AAPL/analysts").json()
    assert body["status"] == "ok" and body["analysts_total"] == 0 and body["as_of"] is None


def test_no_coverage_and_failure_never_raise(
    signup: SignupFn, fakes: tuple[FakeTrends, FakeTargets]
) -> None:
    c = signup("a@mail.com")
    assert c.get("/api/securities/ZZZZ/analysts").json()["status"] == "no_coverage"
    fakes[0].boom = True
    r = c.get("/api/securities/AAPL/analysts")
    assert r.status_code == 200 and r.json()["status"] == "no_coverage"
    assert r.json()["targets"] is None and r.json()["analysts_total"] == 0


def test_agorot_normalised_for_tase() -> None:
    out = normalise_targets({"low": 1000, "mean": 1500.0, "high": 2000, "current": 1400}, "TEVA.TA")
    assert out == AnalystTargets(low=10.0, mean=15.0, high=20.0, currency="ILS")
    us = normalise_targets({"low": 1.0, "mean": None, "high": float("nan")}, "AAPL")
    assert us == AnalystTargets(low=1.0, mean=None, high=None, currency="USD")
    assert normalise_targets({}, "AAPL") is None and normalise_targets(None, "AAPL") is None


def test_analysts_requires_login(client: TestClient) -> None:
    assert client.get("/api/securities/AAPL/analysts").status_code == 401
    assert client.get("/api/securities/AAPL/candles").status_code == 401


def test_analysts_rate_limited(
    signup: SignupFn, fakes: tuple[FakeTrends, FakeTargets], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SECURITY_DATA_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    c = signup("a@mail.com")
    assert c.get("/api/securities/AAPL/analysts").status_code == 200
    assert c.get("/api/securities/AAPL/analysts").status_code == 200
    r = c.get("/api/securities/AAPL/analysts")
    assert r.status_code == 429 and "retry-after" in r.headers


def test_candles_from_history(signup: SignupFn, history: FakeHistory) -> None:
    history.frames["AAPL"] = frame()
    c = signup("a@mail.com")
    body = c.get("/api/securities/aapl/candles", params={"days": 30}).json()
    assert body["symbol"] == "AAPL" and 0 < len(body["candles"]) <= 30
    first = body["candles"][0]
    assert set(first) == {"time", "open", "high", "low", "close"}
    assert c.get("/api/securities/NOPE/candles").json()["candles"] == []
    assert c.get("/api/securities/AAPL/candles", params={"days": 5}).status_code == 422
