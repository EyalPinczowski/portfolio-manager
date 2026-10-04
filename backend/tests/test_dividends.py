"""Dividend calendar: provider (agorot, gaps, cache), estimate maths and the owner-scoped API.

Fixtures only (`tests/fixtures/dividends/yahoo_actions.json`, hand-built in the shape yfinance
returns, dates as offsets from today). No network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.providers.base import DividendProvider
from app.providers.dividends import YFinanceDividendProvider
from app.providers.registry import Providers, set_providers
from app.timeutil import local_today
from tests.conftest import FakeQuotes

FIX = json.loads(
    (Path(__file__).parent / "fixtures" / "dividends" / "yahoo_actions.json").read_text()
)
SignupFn = Callable[..., TestClient]


class FixtureYahoo(YFinanceDividendProvider):
    """The real provider with only the three network methods replaced by the fixture."""

    def __init__(self, today: date, fail: dict[str, Exception] | None = None) -> None:
        super().__init__(Settings(), currency_lookup=None)
        self.today = today
        self.fail = fail or {}
        self.series_calls: list[str] = []

    def _fetch_series(self, symbol: str) -> pd.Series:
        self.series_calls.append(symbol)
        if symbol in self.fail:
            raise self.fail[symbol]
        row = FIX.get(symbol)
        if row is None:
            return pd.Series(dtype=float)
        idx = [
            pd.Timestamp(self.today + timedelta(days=d), tz="America/New_York")
            for d, _ in row["paid"]
        ]
        return pd.Series([a for _, a in row["paid"]], index=idx, dtype=float)

    def _fetch_calendar(self, symbol: str) -> dict[str, Any]:
        cal = (FIX.get(symbol) or {}).get("calendar")
        if not cal:
            return {}
        return {
            "Ex-Dividend Date": self.today + timedelta(days=cal["ex_days"]),
            "Dividend Date": self.today + timedelta(days=cal["pay_days"]),
        }

    def _currency(self, symbol: str) -> str | None:
        return (FIX.get(symbol) or {}).get("currency")


TODAY = local_today()


# ---------------------------------------------------------------- provider
def test_us_dividends_with_the_next_ex_date() -> None:
    p = FixtureYahoo(TODAY)
    f = p.get_dividends("AAPL")
    assert f.value is not None and f.source == "yfinance" and f.as_of is not None
    ev = f.value.events
    assert [e.amount for e in ev[:4]] == [0.25, 0.25, 0.26, 0.26] and {e.currency for e in ev} == {
        "USD"
    }
    nxt = ev[-1]
    assert nxt.announced and nxt.ex_date == TODAY + timedelta(days=55)
    assert nxt.pay_date == TODAY + timedelta(days=62)
    assert nxt.amount == 0.26 and nxt.amount_is_estimate  # the source gives a date, not an amount
    assert not any(e.announced for e in ev[:-1])


def test_tase_dividends_are_agorot_divided_by_100() -> None:
    f = FixtureYahoo(TODAY).get_dividends("TEVA.TA")
    assert f.value is not None
    assert [e.amount for e in f.value.events] == [0.55, 0.60]
    assert {e.currency for e in f.value.events} == {"ILS"}  # never "ILA"
    assert all(e.pay_date is None for e in f.value.events)  # unknown stays unknown


def test_tase_decided_by_currency_not_by_suffix() -> None:
    p = FixtureYahoo(TODAY)
    p._currency = lambda s: "ILS"  # type: ignore[method-assign]  # a .TA symbol already in shekels
    f = p.get_dividends("TEVA.TA")
    assert f.value is not None and [e.amount for e in f.value.events] == [55.0, 60.0]


def test_missing_data_is_a_gap_never_zero() -> None:
    p = FixtureYahoo(TODAY)
    assert p.get_dividends("NODIV").missing_reason == "not_found"
    assert p.get_dividends("UNKNOWN").missing_reason == "not_found"
    assert (
        p.get_dividends("BTC-USD").missing_reason == "coverage"
        and p.series_calls.count("BTC-USD") == 0
    )
    # no currency: an agorot amount could be 100x too large, so nothing is used
    assert p.get_dividends("NOCUR").missing_reason == "unavailable"


def test_failures_are_labelled_and_not_cached() -> None:
    p = FixtureYahoo(
        TODAY, fail={"AAPL": RuntimeError("boom"), "TEVA.TA": RuntimeError("429 Too Many Requests")}
    )
    assert p.get_dividends("AAPL").missing_reason == "unavailable"
    assert p.get_dividends("TEVA.TA").missing_reason == "rate_limited"
    p.fail.clear()
    assert p.get_dividends("AAPL").value is not None  # the failure was not cached


def test_results_are_cached() -> None:
    p = FixtureYahoo(TODAY)
    p.get_dividends("AAPL")
    p.get_dividends("AAPL")
    p.get_dividends("NODIV")
    p.get_dividends("NODIV")
    assert p.series_calls == ["AAPL", "NODIV"]


def test_provider_satisfies_the_protocol() -> None:
    assert isinstance(FixtureYahoo(TODAY), DividendProvider)


# ---------------------------------------------------------------- API
@pytest.fixture
def setup(
    signup: SignupFn, providers: Providers, quotes: FakeQuotes
) -> tuple[SignupFn, FixtureYahoo, FakeQuotes]:
    p = FixtureYahoo(TODAY)
    set_providers(Providers(quotes=providers.quotes, history=providers.history, dividends=p))  # type: ignore[arg-type]
    quotes.set("AAPL", 200.0, "USD")
    quotes.set("TEVA.TA", 60.0, "ILS")
    quotes.set("OLDCO", 10.0, "USD")
    quotes.set("NODIV", 10.0, "USD")
    return signup, p, quotes


def _pf(c: TestClient, holdings: dict[str, float]) -> int:
    pid = int(c.post("/api/portfolios", json={"name": "P", "base_currency": "ILS"}).json()["id"])
    for sym, qty in holdings.items():
        r = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": sym, "quantity": qty})
        assert r.status_code == 201, r.text
    return pid


def test_calendar_and_estimate(setup: tuple[SignupFn, FixtureYahoo, FakeQuotes]) -> None:
    c = setup[0]("div@mail.com")
    pid = _pf(c, {"AAPL": 10, "TEVA.TA": 100})
    r = c.get(f"/api/portfolios/{pid}/dividends")
    assert r.status_code == 200, r.text
    j = r.json()
    assert (
        j["source"] == "yfinance"
        and "Yahoo" in j["credit"]
        and "Not financial advice" in j["disclaimer"]
    )
    up = j["upcoming"]
    assert [u["symbol"] for u in up] == ["AAPL"]  # TASE: the source knows no next date
    a = up[0]
    assert a["ex_date"] == (TODAY + timedelta(days=55)).isoformat()
    assert a["pay_date"] == (TODAY + timedelta(days=62)).isoformat()
    assert (
        a["amount_per_share"] == 0.26
        and a["expected_amount"] == 2.6
        and a["amount_is_estimate"] is True
    )
    assert a["expected_amount_ils"] > 2.6  # converted at the USD/ILS rate
    est = j["income_estimate"]
    assert est["is_estimate"] is True and est["months"] == 12 and "estimate" in est["note"].lower()
    lines = {ln["symbol"]: ln for ln in est["lines"]}
    # AAPL: all four payments (-300 .. -30 days) are inside the last 12 months, x 10 shares
    assert lines["AAPL"]["per_share"] == pytest.approx(0.25 + 0.25 + 0.26 + 0.26)
    assert lines["AAPL"]["amount"] == pytest.approx(10.2)
    # TASE: 55 and 60 agorot -> 0.55 + 0.60 ILS per share, x 100 shares = 115 ILS, never 11,500
    assert lines["TEVA.TA"]["per_share"] == pytest.approx(1.15)
    assert lines["TEVA.TA"]["currency"] == "ILS" and lines["TEVA.TA"][
        "amount_ils"
    ] == pytest.approx(115.0)
    assert est["total_ils"] == pytest.approx(lines["AAPL"]["amount_ils"] + 115.0)
    assert est["symbols_without_data"] == []
    st = {s["symbol"]: s for s in j["symbols"]}
    assert st["AAPL"]["data_status"] == "ok" and st["AAPL"]["payments_in_period"] == 4
    assert st["TEVA.TA"]["last_amount_per_share"] == 0.6


def test_unknown_stopped_and_not_applicable_are_honest(
    setup: tuple[SignupFn, FixtureYahoo, FakeQuotes],
) -> None:
    c = setup[0]("gaps@mail.com")
    pid = _pf(c, {"OLDCO": 5, "NODIV": 5, "BTC-USD": 1, "GEMEL-77": 1})
    j = c.get(f"/api/portfolios/{pid}/dividends").json()
    st = {s["symbol"]: s["data_status"] for s in j["symbols"]}
    assert st == {
        "OLDCO": "stopped",
        "NODIV": "no_data",
        "BTC-USD": "not_applicable",
        "GEMEL-77": "not_applicable",
    }
    est = j["income_estimate"]
    assert est["lines"] == [] and est["total_ils"] is None  # nothing to estimate: not a 0
    assert sorted(est["symbols_without_data"]) == ["NODIV", "OLDCO"]
    assert j["upcoming"] == []


def test_provider_outage_is_reported_per_symbol(
    setup: tuple[SignupFn, FixtureYahoo, FakeQuotes],
) -> None:
    _, prov, _ = setup
    prov.fail["AAPL"] = RuntimeError("down")
    c = setup[0]("out@mail.com")
    pid = _pf(c, {"AAPL": 1, "TEVA.TA": 1})
    j = c.get(f"/api/portfolios/{pid}/dividends").json()
    st = {s["symbol"]: s["data_status"] for s in j["symbols"]}
    assert st == {"AAPL": "unavailable", "TEVA.TA": "ok"}
    assert j["income_estimate"]["symbols_without_data"] == ["AAPL"]


def test_per_request_cap_marks_the_rest_not_checked(
    setup: tuple[SignupFn, FixtureYahoo, FakeQuotes], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DIVIDEND_MAX_SYMBOLS_PER_REQUEST", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    c = setup[0]("cap@mail.com")
    pid = _pf(c, {"AAPL": 1, "TEVA.TA": 1})
    st = {
        s["symbol"]: s["data_status"]
        for s in c.get(f"/api/portfolios/{pid}/dividends").json()["symbols"]
    }
    assert st == {"AAPL": "ok", "TEVA.TA": "not_checked"}


def test_empty_portfolio_and_scoping(setup: tuple[SignupFn, FixtureYahoo, FakeQuotes]) -> None:
    a, b = setup[0]("a@mail.com"), setup[0]("b@mail.com")
    pid = _pf(a, {})
    j = a.get(f"/api/portfolios/{pid}/dividends").json()
    assert j["upcoming"] == [] and j["symbols"] == [] and j["income_estimate"]["total_ils"] is None
    assert b.get(f"/api/portfolios/{pid}/dividends").status_code == 404
    assert b.get("/api/portfolios/99999/dividends").status_code == 404
    assert a.get("/api/portfolios/abc/dividends").status_code == 422


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/portfolios/1/dividends").status_code == 401
