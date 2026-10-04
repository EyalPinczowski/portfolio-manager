"""Screener: POST /api/portfolios/{id}/buy-ideas and the universe refresh job (fixtures only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import DISCLAIMER, Settings, get_settings
from app.db import new_session
from app.launchgate import get_launch_gate
from app.models import PriceQuote, SignalCache
from app.portfolio.quotes import refresh_symbols
from app.scheduler.jobs import run_universe_score_refresh
from app.scoring.universe import bars_frame, bars_payload, load_universe
from app.verdict_words import verdict_words_in_text
from tests.conftest import FakeHistory, FakeQuotes
from tests.test_exit_levels import frame
from tests.test_launch_gate import gate as open_gate
from tests.verdict_contract import verdict_token

SignupFn = Callable[..., TestClient]
OPEN_US = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)  # a Tuesday, US market open
UNIVERSE = ["AAPL", "MSFT", "XOM", "JPM"]
BODY: dict[str, Any] = {
    "amount": 20000,
    "currency": "ILS",
    "horizon": "1m",
    "risk": "balanced_aggressive",
    "markets": ["US"],
    "asset_types": ["stock"],
}


class World:
    def __init__(self, client: TestClient, pid: int, quotes: FakeQuotes, hist: FakeHistory) -> None:
        self.client, self.pid, self.quotes, self.hist = client, pid, quotes, hist

    def refresh(self) -> int:
        with new_session() as db:
            return run_universe_score_refresh(db, self.hist, self.quotes, get_settings(), OPEN_US)  # type: ignore[arg-type]

    def post(self, **over: Any) -> Any:
        body = {**BODY, **over}
        return self.client.post(f"/api/portfolios/{self.pid}/buy-ideas", json=body)

    def skipped(self, r: Any) -> dict[str, str]:
        return {s["symbol"]: s["code"] for s in r.json()["skipped"]}


@pytest.fixture
def world(
    signup: SignupFn,
    quotes: FakeQuotes,
    history: FakeHistory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    uni = tmp_path / "universe.txt"
    uni.write_text("# test universe\n" + "\n".join(UNIVERSE) + "\nAAPL\nNOPE\n", encoding="utf-8")
    monkeypatch.setenv("UNIVERSE_FILE", str(uni))
    monkeypatch.setenv("UNIVERSE_PAUSE_SECONDS", "0")
    get_settings.cache_clear()
    df = frame()
    price = float(df["Close"].iloc[-1])
    for sym in [*UNIVERSE, "NVDA"]:
        history.frames[sym] = df
        quotes.set(sym, price, "USD")
    c = signup("a@mail.com")
    pid = c.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    w = World(c, pid, quotes, history)
    return w


def _add_holding(w: World, symbol: str, quantity: float) -> None:
    with new_session() as db:
        refresh_symbols(db, [symbol], w.quotes)  # type: ignore[arg-type]
    r = w.client.post(
        f"/api/portfolios/{w.pid}/holdings", json={"symbol": symbol, "quantity": quantity}
    )
    assert r.status_code == 201, r.text


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for v in node.values() for t in _strings(v)]
    if isinstance(node, list):
        return [t for v in node for t in _strings(v)]
    return []


def _keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [*node, *[k for v in node.values() for k in _keys(v)]]
    if isinstance(node, list):
        return [k for v in node for k in _keys(v)]
    return []


# ---------------------------------------------------------------- inputs
@pytest.mark.parametrize(
    "missing", ["amount", "currency", "horizon", "risk", "markets", "asset_types"]
)
def test_every_input_is_required(world: World, missing: str) -> None:
    world.refresh()
    body = {k: v for k, v in BODY.items() if k != missing}
    r = world.client.post(f"/api/portfolios/{world.pid}/buy-ideas", json=body)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "over",
    [
        {"amount": 0},
        {"amount": -5},
        {"amount": "lots"},
        {"currency": "EUR"},
        {"horizon": "2y"},
        {"risk": "yolo"},
        {"markets": []},
        {"markets": ["US", "US"]},
        {"markets": ["NYSE"]},
        {"asset_types": []},
        {"asset_types": ["bond"]},
        {"surprise": 1},
    ],
)
def test_bad_inputs_are_rejected(world: World, over: dict[str, Any]) -> None:
    assert world.post(**over).status_code == 422


def test_an_empty_body_is_rejected(world: World) -> None:
    r = world.client.post(f"/api/portfolios/{world.pid}/buy-ideas", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------- the happy path
def test_ranked_candidates_carry_size_levels_and_a_why(world: World) -> None:
    assert world.refresh() == len(UNIVERSE)  # NOPE is not in the security table, AAPL once
    r = world.post()
    assert r.status_code == 200, r.text
    out = r.json()
    cands = out["candidates"]
    assert [c["symbol"] for c in cands] and len(cands) <= get_settings().screener_top_n
    assert [c["rank"] for c in cands] == list(range(1, len(cands) + 1))
    assert out["universe_size"] == len(UNIVERSE)
    for c in cands:
        assert c["stop"]["price"] < c["entry"] and c["take_profits"]
        assert c["best_rr"] >= 1.5  # balanced_aggressive min R:R
        assert c["stop"]["reason"] and c["stop"]["explanation"]["summary"]
        e = c["explanation"]
        assert e["summary"] and e["contributions"] and e["risk_rules_applied"] and e["sources"]
        assert e["invalidation_risks"] and e["as_of"]
        size = c["size"]
        assert size["quantity"] >= 1 and size["quantity"] == int(size["quantity"])
        assert size["cost_ils"] <= out["amount_ils"] + 1e-6
        assert size["risk_pct_of_portfolio"] <= 1.0 + 1e-6  # per-trade limit of the preset
        assert c["rank_score"] == pytest.approx(
            c["score"] * c["confidence"] + c["diversification_bonus"], abs=0.01
        )
    assert [c["rank_score"] for c in cands] == sorted(
        (c["rank_score"] for c in cands), reverse=True
    )


def test_nothing_is_fetched_while_the_request_runs(world: World) -> None:
    world.refresh()
    calls = len(world.quotes.calls)

    def boom(symbol: str, days: int) -> pd.DataFrame:
        raise AssertionError("the request must not fetch history")

    world.hist.get_history = boom  # type: ignore[method-assign]
    assert world.post().status_code == 200
    assert len(world.quotes.calls) == calls


def test_no_verdict_field_or_word_in_the_answer(world: World) -> None:
    world.refresh()
    out = world.post().json()
    assert out["candidates"]
    assert [k for k in _keys(out) if verdict_token(k)] == []
    found = {w for t in _strings(out) for w in verdict_words_in_text(t, ignore=(DISCLAIMER,))}
    assert not found, found


def test_exclude_symbols_skips_them_with_a_reason(world: World) -> None:
    world.refresh()
    r = world.post(exclude_symbols=["aapl"])
    assert "AAPL" not in [c["symbol"] for c in r.json()["candidates"]]
    assert world.skipped(r)["AAPL"] == "excluded_by_user"


def test_markets_and_asset_types_narrow_the_universe(world: World) -> None:
    world.refresh()
    assert world.post(markets=["TASE"]).json()["candidates"] == []
    assert world.post(asset_types=["etf"]).json()["universe_size"] == 0


# ---------------------------------------------------------------- diversification
def test_a_full_sector_is_excluded_and_a_light_one_ranks_higher(world: World) -> None:
    world.refresh()
    _add_holding(world, "NVDA", 100)  # all of the portfolio in Technology
    r = world.post(risk="very_aggressive")  # no country cap: only the sector cap matters
    codes = world.skipped(r)
    assert codes["AAPL"] == "sector_cap" and codes["MSFT"] == "sector_cap"
    syms = [c["symbol"] for c in r.json()["candidates"]]
    assert syms and set(syms) <= {"XOM", "JPM"}
    assert all(c["diversification_bonus"] > 0 for c in r.json()["candidates"])
    for c in r.json()["candidates"]:
        assert c["size"]["sector_pct_after"] <= 50 + 1e-6  # very_aggressive sector cap


def test_a_country_over_its_cap_excludes_more_of_it(world: World) -> None:
    world.refresh()
    _add_holding(world, "NVDA", 100)  # 100% United States, cap 80% for balanced_aggressive
    r = world.post()
    codes = world.skipped(r)
    assert codes["XOM"] == "country_cap" and codes["JPM"] == "country_cap"
    assert r.json()["candidates"] == []
    assert "country limit" in next(s["reason"] for s in r.json()["skipped"] if s["symbol"] == "XOM")


def test_the_size_shrinks_to_keep_a_sector_under_its_cap(world: World) -> None:
    world.refresh()
    _add_holding(world, "AAPL", 100)
    _add_holding(world, "XOM", 100)  # tech 50%, energy 50%
    r = world.post(
        amount=500000, risk="very_aggressive"
    )  # a large amount: the position cap binds, not the amount
    jpm = next(c for c in r.json()["candidates"] if c["symbol"] == "JPM")
    assert jpm["size"]["position_pct_after"] <= 20 + 1e-6
    assert any("limit" in x for x in jpm["size"]["limited_by"])
    assert jpm["size"]["cost_ils"] < r.json()["amount_ils"]


def test_an_amount_below_one_unit_is_skipped(world: World) -> None:
    world.refresh()
    r = world.post(amount=5)
    assert r.json()["candidates"] == []
    assert set(world.skipped(r).values()) == {"size_too_small"}


# ---------------------------------------------------------------- price and levels
def test_a_stale_price_drops_the_candidate_with_a_reason(world: World) -> None:
    world.refresh()
    with new_session() as db:
        q = db.get(PriceQuote, "XOM")
        assert q is not None
        q.as_of = q.as_of - timedelta(days=10)
        db.add(q)
        db.commit()
    r = world.post()
    assert world.skipped(r)["XOM"] == "stale_price"
    assert "XOM" not in [c["symbol"] for c in r.json()["candidates"]]
    reason = next(s["reason"] for s in r.json()["skipped"] if s["symbol"] == "XOM")
    assert "No levels" in reason


def test_a_missing_quote_or_score_is_skipped_with_a_code(world: World) -> None:
    world.refresh()
    with new_session() as db:
        db.delete(db.get(PriceQuote, "JPM"))
        db.delete(db.get(SignalCache, "MSFT"))
        db.commit()
    codes = world.skipped(world.post())
    assert codes["JPM"] == "no_quote" and codes["MSFT"] == "no_score"


def test_a_volatile_symbol_is_over_the_cap(world: World) -> None:
    wild = frame()
    wild["Close"] = [c * (1.12 if i % 2 else 0.89) for i, c in enumerate(wild["Close"])]
    wild["High"], wild["Low"] = wild["Close"] + 1, wild["Close"] - 1
    world.hist.frames["JPM"] = wild
    world.quotes.set("JPM", float(wild["Close"].iloc[-1]), "USD")
    world.refresh()
    r = world.post(risk="very_conservative")
    assert world.skipped(r).get("JPM") in {"volatility_cap", "low_score", "low_confidence"}
    assert "JPM" not in [c["symbol"] for c in r.json()["candidates"]]


def test_a_stale_score_is_skipped(world: World) -> None:
    world.refresh()
    with new_session() as db:
        row = db.get(SignalCache, "AAPL")
        assert row is not None
        row.computed_at = row.computed_at - timedelta(days=30)
        db.add(row)
        db.commit()
    assert world.skipped(world.post())["AAPL"] == "stale_score"


# ---------------------------------------------------------------- the launch gate
def test_the_gate_is_closed_by_default_and_candidates_stay_neutral(world: World) -> None:
    world.refresh()
    out = world.post().json()
    assert out["launch_gate_open"] is False and out["launch_gate_reasons"]
    assert out["candidates"]
    assert "not yet validated" in out["notice"]


def test_an_open_gate_adds_no_verdict_to_this_route(world: World) -> None:
    world.refresh()
    world.client.app.dependency_overrides[get_launch_gate] = lambda: open_gate()  # type: ignore[attr-defined]
    out = world.post().json()
    assert out["launch_gate_open"] is True and out["launch_gate_reasons"] == []
    assert [k for k in _keys(out) if verdict_token(k)] == []
    assert not {w for t in _strings(out) for w in verdict_words_in_text(t, ignore=(DISCLAIMER,))}


# ---------------------------------------------------------------- the universe job
def test_universe_file_loader_skips_comments_and_duplicates(world: World) -> None:
    assert load_universe(get_settings()) == [*UNIVERSE, "NOPE"]
    assert load_universe(Settings(_env_file=None, universe_file="/nonexistent")) == []
    bundled = load_universe(Settings(_env_file=None, universe_file=None))
    assert {"AAPL", "SPY", "TEVA.TA", "BTC-USD", "ETH-USD"} <= set(bundled)


def test_the_job_is_batched_and_skips_fresh_scores(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNIVERSE_REFRESH_BATCH_SIZE", "2")
    get_settings.cache_clear()
    assert world.refresh() == 2
    assert world.refresh() == 2  # the other two
    assert world.refresh() == 0  # all fresh now
    with new_session() as db:
        assert all(db.get(SignalCache, s) is not None for s in UNIVERSE)
        assert all(db.get(SignalCache, "bars:" + s) is not None for s in UNIVERSE)


def test_a_failing_history_call_never_stops_the_batch(world: World) -> None:
    real = world.hist.get_history

    def flaky(symbol: str, days: int) -> pd.DataFrame | None:
        if symbol == "AAPL":
            raise RuntimeError("rate limited")
        return real(symbol, days)

    world.hist.get_history = flaky  # type: ignore[method-assign]
    assert world.refresh() == len(UNIVERSE)
    world.refresh()
    codes = world.skipped(world.post())
    assert codes["AAPL"] == "no_score"


def test_bars_round_trip_for_the_exit_levels_engine() -> None:
    df = frame()
    back = bars_frame(dict(bars_payload(df)))
    assert back is not None and len(back) == len(df)
    assert float(back["Close"].iloc[-1]) == pytest.approx(float(df["Close"].iloc[-1]), abs=1e-5)
    assert bars_frame({"bars": []}) is None and bars_frame(None) is None
    assert bars_frame({"bars": [["bad"]]}) is None


# ---------------------------------------------------------------- scoping
def test_another_users_portfolio_is_a_404(world: World, signup: SignupFn) -> None:
    world.refresh()
    b = signup("b@mail.com")
    r = b.post(f"/api/portfolios/{world.pid}/buy-ideas", json=BODY)
    assert r.status_code == 404
    assert world.post().status_code == 200


def test_unauthenticated_is_rejected(client: TestClient) -> None:
    assert client.post("/api/portfolios/1/buy-ideas", json=BODY).status_code == 401
