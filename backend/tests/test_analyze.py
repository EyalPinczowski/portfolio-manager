"""Analyze a stock: GET /api/analyze/{symbol} and POST /api/analyze/{symbol}/ask (fixtures only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analyze.public_facts import PublicFacts, build_prompt, public_facts, summarize
from app.config import DISCLAIMER, get_settings
from app.db import new_session
from app.launchgate import get_launch_gate
from app.llm.base import LLMError, LLMRequest, LLMResponse
from app.models import SignalCache
from app.portfolio.quotes import refresh_symbols
from app.timeutil import utcnow
from app.verdict_words import verdict_words_in_text
from tests.conftest import FakeHistory, FakeQuotes
from tests.test_exit_levels import frame
from tests.test_launch_gate import gate as open_gate
from tests.test_screener import _keys, _strings
from tests.verdict_contract import verdict_token

SignupFn = Callable[..., TestClient]
FIT = {"amount": 20000, "currency": "ILS", "horizon": "1m"}


class World:
    def __init__(self, c: TestClient, pid: int, quotes: FakeQuotes, hist: FakeHistory) -> None:
        self.c, self.pid, self.quotes, self.hist = c, pid, quotes, hist

    def get(self, symbol: str = "AAPL", **params: Any) -> Any:
        return self.c.get(f"/api/analyze/{symbol}", params=params)

    def hold(self, symbol: str, quantity: float) -> None:
        with new_session() as db:
            refresh_symbols(db, [symbol], self.quotes)  # type: ignore[arg-type]
        r = self.c.post(
            f"/api/portfolios/{self.pid}/holdings", json={"symbol": symbol, "quantity": quantity}
        )
        assert r.status_code == 201, r.text


@pytest.fixture
def world(signup: SignupFn, quotes: FakeQuotes, history: FakeHistory) -> World:
    df = frame()
    price = float(df["Close"].iloc[-1])
    for sym in ("AAPL", "MSFT", "NVDA", "XOM", "JPM", "TEVA.TA"):
        history.frames[sym] = df
        quotes.set(sym, price, "ILS" if sym.endswith(".TA") else "USD")
    c = signup("a@mail.com")
    pid = c.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    return World(c, pid, quotes, history)


# ---------------------------------------------------------------- Scout + Chartist
def test_any_ticker_gets_scout_and_chart_reports_without_a_portfolio(world: World) -> None:
    r = world.get("AAPL")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["scout"]["symbol"] == "AAPL" and out["scout"]["price"]["is_fresh"] is True
    assert out["scout"]["bars"] == out["chart"]["bars"] == 320
    chart = out["chart"]
    assert chart["available"] and -100 <= chart["score"] <= 100 and chart["confidence"] > 0
    by_name = {line["name"]: line for line in chart["breakdown"]}
    assert by_name["technical"]["available"] and by_name["patterns"]["available"]
    for name in ("fundamentals", "analysts", "geo_news", "sentiment"):
        assert by_name[name]["available"] is False
        assert by_name[name]["confidence"] == 0 and by_name[name]["weight"] == 0
    assert {"sma50", "sma200", "rsi14", "atr14"} <= set(chart["indicators"])
    kinds = {lv["kind"] for lv in chart["levels"]}
    assert kinds == {"support", "resistance"}
    assert all(lv["distance_pct"] < 0 for lv in chart["levels"] if lv["kind"] == "support")
    assert (
        chart["data_as_of"] and chart["explanation"]["summary"] and chart["explanation"]["sources"]
    )
    assert {m["name"] for m in out["scout"]["not_available"]} >= {"fundamentals", "analysts"}
    assert out["scout"]["explanation"]["summary"]
    # no portfolio: the fit section asks for one instead of guessing
    assert out["needs_input"] == ["portfolio_id"]
    assert out["portfolio_fit"]["status"] == "incomplete"
    assert out["summary"] and out["llm_used"] is False and out["disclaimer"] == DISCLAIMER


def test_chart_has_the_last_n_ohlc_candles_from_the_same_history(world: World) -> None:
    chart = world.get("AAPL").json()["chart"]
    candles = chart["candles"]
    assert len(candles) == get_settings().analyze_chart_bars == 120
    df = frame()
    last = candles[-1]
    assert last["time"] == df.index[-1].strftime("%Y-%m-%d")
    assert last["close"] == pytest.approx(float(df["Close"].iloc[-1]), abs=1e-3)
    assert all(c["low"] <= min(c["open"], c["close"]) for c in candles)
    assert [c["time"] for c in candles] == sorted(c["time"] for c in candles)
    # annotations carry the date they were detected on
    assert all(a["as_of"] for a in chart["annotations"])


def test_tase_uses_ils_and_catches_agorot_history(world: World) -> None:
    ok = world.get("TEVA.TA", portfolio_id=world.pid, **FIT).json()
    assert ok["scout"]["market"] == "TASE" and ok["scout"]["price"]["currency"] == "ILS"
    fit = ok["portfolio_fit"]
    assert fit["status"] in ("fits", "fits_smaller"), fit["summary"]
    size, price = fit["suggested_size"], ok["scout"]["price"]["price"]
    assert size["currency"] == "ILS" and size["cost_ils"] == pytest.approx(
        size["quantity"] * price, abs=0.01
    )
    # history left in agorot (x100) next to an ILS quote: no levels, the reason says why
    world.hist.frames["TEVA.TA"] = frame() * 100
    with new_session() as db:
        db.delete(db.get(SignalCache, "analyze:TEVA.TA"))
        db.commit()
    bad = world.get("TEVA.TA", portfolio_id=world.pid, **FIT).json()["portfolio_fit"]
    assert bad["status"] == "incomplete" and bad["suggested_size"] is None
    assert bad["levels"]["reason_code"] == "history_price_mismatch"
    assert "No levels" in bad["levels_unavailable_reason"]


def test_crypto_has_fractional_size_and_no_analyst_data(world: World) -> None:
    df = frame(start=40000.0, drift=15.0, amp=500.0)
    world.hist.frames["BTC-USD"] = df
    world.quotes.set("BTC-USD", float(df["Close"].iloc[-1]), "USD")
    out = world.get("BTC-USD", portfolio_id=world.pid, **FIT).json()
    assert out["scout"]["asset_type"] == "crypto"
    assert "analyst_and_insider" in {m["name"] for m in out["scout"]["not_available"]}
    size = out["portfolio_fit"]["suggested_size"]
    assert size is not None and 0 < size["quantity"] < 1


def test_an_unknown_symbol_with_provider_data_works_and_one_without_is_a_404(
    world: World,
) -> None:
    world.hist.frames["NEWCO"] = frame()
    world.quotes.set("NEWCO", float(frame()["Close"].iloc[-1]), "USD")
    out = world.get("newco").json()
    assert out["symbol"] == "NEWCO" and out["scout"]["sector"] == "Unknown"
    with new_session() as db:  # nothing was added to the security table
        from app.models import Security

        assert db.get(Security, "NEWCO") is None
    r = world.get("ZZZZ")
    assert r.status_code == 404 and r.json()["code"] == "symbol_not_found"


@pytest.mark.parametrize("symbol", ["^GSPC", "ILS=X", "A%20B", "WAYTOOLONGTICKERSYMBOLX1"])
def test_bad_symbols_are_rejected(world: World, symbol: str) -> None:
    assert world.get(symbol).status_code == 422


@pytest.mark.parametrize(
    "over", [{"horizon": "2y"}, {"risk": "yolo"}, {"amount": 0}, {"currency": "EUR"}]
)
def test_bad_inputs_are_rejected(world: World, over: dict[str, Any]) -> None:
    assert world.get("AAPL", portfolio_id=world.pid, **over).status_code == 422


def test_no_history_degrades_to_confidence_zero(world: World) -> None:
    world.hist.frames.pop("MSFT")
    out = world.get("MSFT").json()
    assert out["chart"]["available"] is False and out["chart"]["score"] == 0
    assert out["chart"]["confidence"] == 0 and out["chart"]["levels"] == []
    assert out["candidate_info"]["score_available"] is False
    assert all(not line["available"] for line in out["chart"]["breakdown"])


# ---------------------------------------------------------------- the portfolio fit
def test_missing_inputs_are_listed_not_guessed(world: World) -> None:
    fit = world.get("AAPL", portfolio_id=world.pid).json()["portfolio_fit"]
    assert fit["needs_input"] == ["amount", "horizon"] and fit["status"] == "incomplete"
    assert fit["suggested_size"] is None and fit["amount"] is None and fit["horizon"] is None
    assert fit["levels"]["status"] == "needs_horizon"
    only_amount = world.get("AAPL", portfolio_id=world.pid, amount=1000, horizon="1m").json()
    assert only_amount["needs_input"] == ["currency"]
    assert world.get("AAPL", portfolio_id=world.pid, horizon="1m").json()["needs_input"] == [
        "amount"
    ]


def test_a_new_position_has_size_levels_and_a_why(world: World) -> None:
    out = world.get("AAPL", portfolio_id=world.pid, **FIT).json()
    fit = out["portfolio_fit"]
    assert fit["needs_input"] == [] and fit["mode"] == "new_position" and fit["held"] is None
    assert fit["status"] in ("fits", "fits_smaller")
    size = fit["suggested_size"]
    assert size["quantity"] >= 1 and size["cost_ils"] <= fit["amount_ils"] + 1e-6
    levels = fit["levels"]
    assert levels["status"] == "levels" and levels["stop"]["price"] < fit["entry"]
    assert levels["take_profits"] and levels["stop"]["reason"]
    assert fit["horizon"] == "1m" and fit["horizon_source"] == "request"
    assert fit["risk_preset"] == "balanced_aggressive" and fit["risk_source"] == "portfolio"
    e = fit["explanation"]
    assert e["summary"] and e["risk_rules_applied"] and e["sources"] and e["invalidation_risks"]
    assert fit["rules_not_checked"]
    names = {r["rule"] for r in fit["rules"]}
    assert {"max_position_pct", "max_sector_pct", "max_country_pct", "min_rr"} <= names
    assert out["candidate_info"]["fit_passes"] is True


def test_an_empty_portfolio_has_no_caps_to_measure(world: World) -> None:
    fit = world.get("AAPL", portfolio_id=world.pid, **FIT).json()["portfolio_fit"]
    assert fit["portfolio_value_ils"] == 0
    assert all(e["applies"] is False and e["breaks"] is False for e in fit["exposures"])
    assert fit["max_position_size"]["max_additional_ils"] is None


def test_exposure_before_and_after_and_the_cap_that_breaks(world: World) -> None:
    world.hold("AAPL", 100)
    world.hold("XOM", 100)  # technology 50%, energy 50%
    fit = world.get(
        "JPM",
        portfolio_id=world.pid,
        amount=500000,
        currency="ILS",
        horizon="1m",
        risk="very_aggressive",
    ).json()["portfolio_fit"]
    pos = next(e for e in fit["exposures"] if e["dimension"] == "position")
    assert pos["before_pct"] == 0 and pos["after_pct"] > 20 and pos["breaks"] is True
    assert pos["rule"] == "max_position_pct" and pos["limit_pct"] == 20
    assert "max_position_pct" in fit["caps_broken_at_requested_amount"]
    assert fit["risk_source"] == "request"
    # shrunk to fit, with the cap named; the stop was not moved
    assert fit["status"] == "fits_smaller"
    size = fit["suggested_size"]
    assert size["position_pct_after"] <= 20 + 1e-6 and size["cost_ils"] < fit["amount_ils"]
    assert any("limit" in x or "max_position_pct" in x for x in size["limited_by"])
    assert fit["max_position_size"]["binding_rule"] == "max_position_pct"
    assert fit["max_position_size"]["max_additional_ils"] > 0
    assert fit["max_position_size"]["reason_text"]["code"] == "max_size_binding"
    assert pos["reason_text"]["code"] == "exposure_breaks"
    assert {"before_pct", "after_pct", "limit_pct", "dimension"} <= set(
        pos["reason_text"]["params"]
    )
    sector = next(e for e in fit["exposures"] if e["dimension"] == "sector")
    assert sector["before_pct"] == 0 and sector["name"] == "Financials"


def test_a_full_sector_does_not_fit_and_says_which_rule(world: World) -> None:
    world.hold("NVDA", 100)  # all of the portfolio in Technology
    out = world.get("AAPL", portfolio_id=world.pid, **FIT).json()
    fit = out["portfolio_fit"]
    sector = next(e for e in fit["exposures"] if e["dimension"] == "sector")
    assert sector["before_pct"] == 100 and sector["breaks"] is True
    assert "max_sector_pct" in fit["caps_broken_at_requested_amount"]
    assert fit["status"] == "does_not_fit" and fit["suggested_size"] is None
    assert fit["max_position_size"]["max_additional_ils"] == 0
    assert sector["reason_text"]["code"] == "exposure_breaks"
    assert any(r["status"] == "fail" for r in fit["rules"])
    assert out["candidate_info"]["fit_passes"] is False


def test_an_owned_stock_is_an_increase_and_its_own_limit_wins(world: World) -> None:
    world.hold("AAPL", 10)
    hid = world.c.get(f"/api/portfolios/{world.pid}/holdings").json()[0]["id"]
    r = world.c.patch(
        f"/api/portfolios/{world.pid}/holdings/{hid}",
        json={"risk_override": {"max_position_pct": 5}, "horizon": "3m"},
    )
    assert r.status_code == 200, r.text
    fit = world.get("AAPL", portfolio_id=world.pid, amount=5000, currency="ILS").json()[
        "portfolio_fit"
    ]
    assert fit["mode"] == "increase_existing" and fit["held"]["quantity"] == 10
    assert fit["horizon"] == "3m" and fit["horizon_source"] == "holding"  # the holding's own
    assert fit["risk_source"] == "holding_override"
    pos = fit["max_position_size"]
    assert pos["position_limit_pct"] == 5 and pos["limit_source"] == "holding_override"
    pe = next(e for e in fit["exposures"] if e["dimension"] == "position")
    assert (
        pe["before_pct"] == 100
        and pe["breaks"] is True
        and pe["limit_source"] == "holding_override"
    )
    assert fit["status"] == "does_not_fit"  # already far over its own 5% limit


def test_the_other_listing_of_a_dual_listed_company_counts_as_held(world: World) -> None:
    world.hold("TEVA.TA", 100)
    # TEVA's US line is not seeded in the fixture set here: use the group mapping via the security table
    from sqlmodel import select

    from app.models import Security

    with new_session() as db:
        group = db.get(Security, "TEVA.TA").dual_listing_group  # type: ignore[union-attr]
        sibling = db.exec(
            select(Security).where(
                Security.dual_listing_group == group, Security.symbol != "TEVA.TA"
            )
        ).first()
    if sibling is None:
        pytest.skip("no seeded sibling listing")
    world.hist.frames[sibling.symbol] = frame()
    world.quotes.set(sibling.symbol, float(frame()["Close"].iloc[-1]), "USD")
    fit = world.get(sibling.symbol, portfolio_id=world.pid, **FIT).json()["portfolio_fit"]
    assert fit["mode"] == "increase_existing" and fit["held"]["via_dual_listing"] is True


def test_a_stale_price_gives_no_levels_with_a_reason(world: World) -> None:
    world.quotes.quotes["AAPL"].as_of = utcnow() - timedelta(days=10)
    out = world.get("AAPL", portfolio_id=world.pid, **FIT).json()
    fit = out["portfolio_fit"]
    assert out["scout"]["price"]["is_fresh"] is False
    assert fit["status"] == "incomplete" and fit["suggested_size"] is None
    assert fit["levels"]["reason_code"] == "stale_price"
    assert fit["levels_unavailable_reason"].startswith("No levels")
    assert fit["max_position_size"] is not None  # the limits themselves do not need a price
    assert out["candidate_info"]["fit_passes"] is None


def test_no_price_at_all_gives_no_levels(world: World) -> None:
    del world.quotes.quotes["MSFT"]
    fit = world.get("MSFT", portfolio_id=world.pid, **FIT).json()["portfolio_fit"]
    assert fit["status"] == "incomplete" and "no market price" in fit["levels_unavailable_reason"]


def test_the_stop_is_not_tightened_the_size_shrinks(world: World) -> None:
    world.hold("XOM", 100)
    wide = world.get("JPM", portfolio_id=world.pid, **FIT).json()["portfolio_fit"]
    tight = world.get("JPM", portfolio_id=world.pid, **{**FIT, "risk": "very_conservative"}).json()[
        "portfolio_fit"
    ]
    if wide["levels"]["status"] == "levels" and tight["levels"]["status"] == "levels":
        sg = tight["levels"]["size_guidance"]
        if sg["needed"] and tight["suggested_size"]:
            assert tight["suggested_size"]["quantity"] <= wide["suggested_size"]["quantity"]
            assert tight["suggested_size"]["risk_pct_of_portfolio"] <= 0.25 + 1e-6


# ---------------------------------------------------------------- cache
def test_the_cache_serves_a_second_call_without_a_provider_call(world: World) -> None:
    first = world.get("AAPL").json()
    assert first["cached"] is False
    calls = len(world.quotes.calls)

    def boom(symbol: str, days: int) -> pd.DataFrame:
        raise AssertionError("must not fetch while the cache row is fresh")

    world.hist.get_history = boom  # type: ignore[method-assign]
    second = world.get("AAPL").json()
    assert second["cached"] is True and len(world.quotes.calls) == calls
    assert second["chart"]["score"] == first["chart"]["score"]
    # past the TTL the providers are asked again
    with new_session() as db:
        row = db.get(SignalCache, "analyze:AAPL")
        assert row is not None
        row.computed_at = row.computed_at - timedelta(
            minutes=get_settings().analyze_cache_ttl_minutes + 1
        )
        db.add(row)
        db.commit()
    world.hist.get_history = lambda s, d: frame()  # type: ignore[method-assign]
    assert world.get("AAPL").json()["cached"] is False
    assert len(world.quotes.calls) == calls + 1


# ---------------------------------------------------------------- scoping
def test_someone_elses_portfolio_is_a_404_before_anything_is_fetched(
    world: World, signup: SignupFn
) -> None:
    b = signup("b@mail.com")
    calls = len(world.quotes.calls)
    r = b.get("/api/analyze/AAPL", params={"portfolio_id": world.pid, **FIT})
    assert r.status_code == 404 and len(world.quotes.calls) == calls
    assert b.get("/api/analyze/AAPL").status_code == 200  # the symbol part needs no portfolio
    assert TestClient(world.c.app).get("/api/analyze/AAPL").status_code == 401
    assert (
        TestClient(world.c.app).post("/api/analyze/AAPL/ask", json={"question": "x"}).status_code
        == 401
    )


# ---------------------------------------------------------------- launch gate, verdict words
def test_no_verdict_field_or_word_anywhere_and_the_gate_state_is_shown(world: World) -> None:
    world.hold("XOM", 100)
    for params in ({}, {"portfolio_id": world.pid, **FIT}, {"portfolio_id": world.pid}):
        out = world.get("AAPL", **params).json()
        assert [k for k in _keys(out) if verdict_token(k)] == []
        found = {w for t in _strings(out) for w in verdict_words_in_text(t, ignore=(DISCLAIMER,))}
        assert not found, [
            (t, verdict_words_in_text(t, ignore=(DISCLAIMER,)))
            for t in _strings(out)
            if verdict_words_in_text(t, ignore=(DISCLAIMER,))
        ]
        info = out["candidate_info"]
        assert info["launch_gate_open"] is False and info["launch_gate_reasons"]
        assert "not yet validated" in info["notice"]
        assert set(info) == {
            "score", "confidence", "score_available", "fit_passes", "launch_gate_open",
            "launch_gate_reasons", "notice",
        }  # fmt: skip


def test_an_open_gate_still_adds_no_verdict_to_this_route(world: World) -> None:
    world.c.app.dependency_overrides[get_launch_gate] = lambda: open_gate()  # type: ignore[attr-defined]
    out = world.get("AAPL", portfolio_id=world.pid, **FIT).json()
    assert out["candidate_info"]["launch_gate_open"] is True
    assert out["candidate_info"]["launch_gate_reasons"] == []
    assert [k for k in _keys(out) if verdict_token(k)] == []
    assert not {w for t in _strings(out) for w in verdict_words_in_text(t, ignore=(DISCLAIMER,))}


# ---------------------------------------------------------------- ask
def test_ask_answers_from_the_computed_numbers_with_a_template(world: World) -> None:
    out = world.get("AAPL").json()
    rsi = out["chart"]["indicators"]["rsi14"]
    r = world.c.post(
        "/api/analyze/AAPL/ask", json={"question": "What is the RSI?", "notes": "my note"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert f"{rsi:.1f}" in body["answer"] and body["grounded_in"] == ["rsi14"]
    assert body["llm_used"] is False and body["notes_stored"] is False
    assert body["question_declined"] is False and "never sent" in body["privacy_note"]
    assert "my note" not in body["answer"]
    assert not verdict_words_in_text(body["answer"], ignore=(DISCLAIMER,))


def test_ask_declines_a_request_for_an_instruction_to_trade(world: World) -> None:
    body = world.c.post("/api/analyze/AAPL/ask", json={"question": "Should I buy it now?"}).json()
    assert body["question_declined"] is True and body["answer"]
    assert not verdict_words_in_text(body["answer"], ignore=(DISCLAIMER,))
    assert [k for k in _keys(body) if verdict_token(k)] == []


@pytest.mark.parametrize(
    "body", [{}, {"question": ""}, {"question": "x" * 1001}, {"question": "x", "extra": 1}]
)
def test_ask_validates_its_input(world: World, body: dict[str, Any]) -> None:
    assert world.c.post("/api/analyze/AAPL/ask", json=body).status_code == 422


# ---------------------------------------------------------------- PublicFacts (what an AI may see)
class _Provider:
    name, model = "fake", "m"

    def __init__(self, text: str | None) -> None:
        self.text, self.seen = text, []  # type: ignore[var-annotated]

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request)
        if self.text is None:
            raise LLMError("down")
        return LLMResponse(text=self.text, provider="fake", model="m")


def _facts(world: World) -> PublicFacts:
    from app.analyze.chartist import build_chart_report
    from app.analyze.scout import build_scout_report
    from app.securities import infer_security

    chart = build_chart_report("AAPL", frame())
    scout = build_scout_report(
        infer_security("AAPL"),
        verified=True,
        price=None,
        price_reason="x",
        chart=chart,
        history_as_of=None,
    )
    return public_facts(scout, chart)


def test_public_facts_carry_no_personal_or_portfolio_field(world: World) -> None:
    fields = set(PublicFacts.model_fields)
    forbidden = (
        "portfolio",
        "holding",
        "amount",
        "quantity",
        "note",
        "question",
        "user",
        "email",
        "value_ils",
        "cost",
    )
    assert not [f for f in fields if any(x in f for x in forbidden)]
    with pytest.raises(ValueError):
        PublicFacts(
            symbol="A", name="A", market="US", asset_type="stock", sector="x", country="y", amount=5
        )  # type: ignore[call-arg]
    assert "amount" not in build_prompt(_facts(world))


def test_summary_is_a_template_by_default_and_falls_back_on_any_failure(world: World) -> None:
    facts = _facts(world)
    text, used = summarize(facts)
    assert used is False and facts.symbol in text
    assert summarize(facts, _Provider(None)) == (text, False)  # provider down
    assert summarize(facts, _Provider("You should buy this now."))[1] is False  # verdict words
    ok = _Provider("A calm, rising chart.")
    assert summarize(facts, ok) == ("A calm, rising chart.", True)
    assert ok.seen[0].untrusted == [] and "amount" not in ok.seen[0].prompt
