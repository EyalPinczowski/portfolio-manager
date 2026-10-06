"""Symbol search beyond the seed (Update 8): providers on fixtures, the endpoint, manual import rows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import Settings
from app.db import new_session
from app.models import Security
from app.providers.base import SymbolHit, SymbolSearchProvider
from app.providers.registry import Providers
from app.providers.symbol_search import (
    FinnhubSymbolSearch,
    SymbolSearchChain,
    YahooSymbolSearch,
)
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]

YAHOO_FIXTURE: list[dict[str, Any]] = [
    {"symbol": "RDDT", "shortname": "Reddit, Inc.", "quoteType": "EQUITY", "exchange": "NYQ"},
    {"symbol": "RDDT", "shortname": "dup", "quoteType": "EQUITY", "exchange": "NYQ"},
    {"symbol": "NICE.TA", "longname": "NICE Ltd", "quoteType": "EQUITY", "exchange": "TLV"},
    {"symbol": "RDDT.MX", "shortname": "Reddit MX", "quoteType": "EQUITY", "exchange": "MEX"},
    {"symbol": "RDDTWX", "shortname": "warrant", "quoteType": "EQUITY", "exchange": "NMS"},
    {"symbol": "BTC-USD", "shortname": "Bitcoin", "quoteType": "CRYPTOCURRENCY", "exchange": "CCC"},
    {"symbol": "SPY", "shortname": "SPDR S&P 500", "quoteType": "ETF", "exchange": "PCX"},
    {"symbol": "OTCX", "shortname": "otc", "quoteType": "EQUITY", "exchange": "PNK"},
]


class FakeSearch:
    name = "fake"

    def __init__(self, hits: list[SymbolHit]) -> None:
        self.hits = hits
        self.calls: list[str] = []

    def search(self, query: str, limit: int = 8) -> list[SymbolHit]:
        self.calls.append(query)
        return self.hits[:limit]


def _hit(symbol: str, name: str, market: str = "US") -> SymbolHit:
    return SymbolHit(
        symbol=symbol, name=name, exchange="NYSE" if market == "US" else "TASE",
        market=market, currency="USD" if market == "US" else "ILS", source="fake",
    )  # type: ignore[arg-type]  # fmt: skip


def test_yahoo_keeps_only_us_and_tase_listings_with_currency() -> None:
    hits = YahooSymbolSearch(Settings()).parse(YAHOO_FIXTURE, 8)
    assert [(h.symbol, h.exchange, h.currency) for h in hits] == [
        ("RDDT", "NYSE", "USD"),
        ("NICE.TA", "TASE", "ILS"),
        ("SPY", "NYSE", "USD"),
    ]


def test_yahoo_search_is_cached_and_rate_limited_and_never_raises() -> None:
    calls: list[str] = []

    class P(YahooSymbolSearch):
        def _fetch(self, query: str, limit: int) -> list[dict[str, Any]]:
            calls.append(query)
            return YAHOO_FIXTURE

    s = Settings(symbol_search_per_minute=2)
    p = P(s, clock=lambda: 0.0)
    assert p.search("reddit")[0].symbol == "RDDT"
    p.search("Reddit")  # cached (case-insensitive)
    assert calls == ["reddit"]
    p.search("a b")
    assert p.search("c d") == []  # over the per-minute budget
    assert len(calls) == 2

    class Boom(YahooSymbolSearch):
        def _fetch(self, query: str, limit: int) -> list[dict[str, Any]]:
            raise RuntimeError("down")

    assert Boom(s).search("x y") == []


FINNHUB_FIXTURE = {
    "count": 4,
    "result": [
        {"description": "REDDIT INC-CL A", "displaySymbol": "RDDT", "symbol": "RDDT",
         "type": "Common Stock"},
        {"description": "REDDIT", "displaySymbol": "RDDT.MX", "symbol": "RDDT.MX",
         "type": "Common Stock"},
        {"description": "Reddit warrant", "displaySymbol": "RDDTW", "symbol": "RDDTW",
         "type": "Warrant"},
    ],
}  # fmt: skip


def _finnhub(key: str | None, handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    s = Settings(finnhub_api_key=key)
    return FinnhubSymbolSearch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_finnhub_runs_only_with_a_key_and_filters() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=FINNHUB_FIXTURE)

    assert _finnhub(None, handler).search("reddit") == []
    assert seen == []
    hits = _finnhub("k", handler).search("reddit")
    assert [(h.symbol, h.currency) for h in hits] == [("RDDT", "USD")]
    assert seen[0].headers["X-Finnhub-Token"] == "k"
    blocked = _finnhub("k", lambda r: httpx.Response(429))
    assert blocked.search("reddit") == [] and blocked.search("again") == []  # breaker open


def test_chain_merges_without_duplicates() -> None:
    a = FakeSearch([_hit("RDDT", "Reddit")])
    b = FakeSearch([_hit("RDDT", "Reddit again"), _hit("NICE.TA", "NICE", "TASE")])
    out = SymbolSearchChain([a, b]).search("re")
    assert [h.symbol for h in out] == ["RDDT", "NICE.TA"]


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def fake_search(providers: Providers) -> FakeSearch:
    f = FakeSearch([_hit("AAPL", "Apple Inc"), _hit("RDDT", "Reddit, Inc."),
                    _hit("XYZ.TA", "Xyz Ltd", "TASE")])  # fmt: skip
    providers.symbol_search = f  # type: ignore[assignment]
    return f


def test_search_without_remote_never_calls_the_provider(
    signup: SignupFn, fake_search: FakeSearch
) -> None:
    c = signup()
    r = c.get("/api/securities/search", params={"q": "apple"})
    assert r.status_code == 200
    assert {h["source"] for h in r.json()} == {"known"}
    assert fake_search.calls == []


def test_remote_search_adds_new_hits_after_known_ones(
    signup: SignupFn, fake_search: FakeSearch
) -> None:
    c = signup()
    rows = c.get("/api/securities/search", params={"q": "aap", "remote": 1}).json()
    syms = [(h["symbol"], h["source"]) for h in rows]
    assert syms[0] == ("AAPL", "known")  # known first, not repeated as new
    assert ("AAPL", "new") not in syms
    assert ("RDDT", "new") in syms
    new_tase = next(h for h in rows if h["symbol"] == "XYZ.TA")
    assert new_tase["market"] == "TASE" and new_tase["currency"] == "ILS"
    assert new_tase["exchange"] == "TASE"
    # one character is too short to call out
    fake_search.calls.clear()
    c.get("/api/securities/search", params={"q": "a", "remote": 1})
    assert fake_search.calls == []


def test_remote_search_degrades_to_known_when_the_provider_is_empty(
    signup: SignupFn, providers: Providers
) -> None:
    providers.symbol_search = FakeSearch([])  # type: ignore[assignment]
    c = signup()
    r = c.get("/api/securities/search", params={"q": "apple", "remote": 1})
    assert r.status_code == 200 and all(h["source"] == "known" for h in r.json())


# ---------------------------------------------------------------- importer
def _row(**kw: Any) -> dict[str, Any]:
    return {"name": "x", "quantity": 5, "price": 10.0, "value": 50.0,
            "currency": "USD", "unit": "USD", **kw}  # fmt: skip


def _draft(c: TestClient, rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    pid = make_portfolio(c)
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows})
    assert r.status_code == 201, r.text
    d = r.json()
    return f"/api/imports/{d['id']}", d


def test_a_manual_row_with_a_new_us_ticker_validates_and_confirms(
    signup: SignupFn, providers: Providers
) -> None:
    c = signup()
    url, d = _draft(c, [_row(index=0, symbol="AAPL", name="Apple")])
    manual = _row(index=1, name="", symbol="ZZQX")  # a brand-new index, as "+ Add a stock"
    r = c.patch(url, json={"rows": [*d["rows"], manual]})
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert [x["index"] for x in rows] == [0, 1]
    assert rows[1]["symbol"] == "ZZQX" and "unmatched" not in rows[1]["flags"]
    q = providers.quotes
    assert isinstance(q, FakeQuotes)
    q.set("ZZQX", 10.0, "USD")
    assert c.post(f"{url}/confirm").status_code == 200
    with new_session() as db:
        sec = db.exec(select(Security).where(Security.symbol == "ZZQX")).one()
        assert sec.market == "US" and sec.currency == "USD"
        assert sec.verified is True  # the first quote verified it


def test_a_typed_dot_ta_symbol_on_a_shekel_row_is_evidence_for_a_new_security(
    signup: SignupFn, providers: Providers
) -> None:
    c = signup()
    url, d = _draft(c, [_row(index=0, symbol="AAPL", name="Apple")])
    manual = _row(index=1, name="", symbol="zzqy.ta", currency="ILS", unit="ILS", price=20.0,
                  value=100.0)  # fmt: skip
    r = c.patch(url, json={"rows": [*d["rows"], manual]})
    assert r.status_code == 200, r.text
    row = r.json()["rows"][1]
    assert row["symbol"] == "ZZQY.TA" and "unmatched" not in row["flags"]
    q = providers.quotes
    assert isinstance(q, FakeQuotes)
    q.set("ZZQY.TA", 20.0, "ILS")
    assert c.post(f"{url}/confirm").status_code == 200
    with new_session() as db:
        sec = db.exec(select(Security).where(Security.symbol == "ZZQY.TA")).one()
        assert sec.market == "TASE" and sec.currency == "ILS" and sec.asset_type == "stock"
        assert sec.verified is True


def test_dot_ta_on_a_dollar_row_and_a_us_ticker_on_a_shekel_row_stay_unmatched(
    signup: SignupFn,
) -> None:
    c = signup()
    url, d = _draft(c, [_row(index=0, symbol="AAPL", name="Apple")])
    bad_usd = _row(index=1, name="", symbol="ZZQY.TA")
    bad_ils = _row(index=2, name="", symbol="ZZQX", currency="ILS", unit="ILS")
    rows = c.patch(url, json={"rows": [*d["rows"], bad_usd, bad_ils]}).json()["rows"]
    assert "unmatched" in rows[1]["flags"] and rows[1]["symbol"] is None
    assert "unmatched" in rows[2]["flags"] and rows[2]["symbol"] is None
    assert c.post(f"{url}/confirm").status_code == 422


def test_a_manual_row_without_quantity_blocks_confirm(signup: SignupFn) -> None:
    c = signup()
    url, d = _draft(c, [_row(index=0, symbol="AAPL", name="Apple")])
    manual = _row(index=1, name="", symbol="ZZQX", quantity=None, price=None, value=None)
    c.patch(url, json={"rows": [*d["rows"], manual]})
    assert c.post(f"{url}/confirm").status_code == 422


def test_search_provider_protocol_is_satisfied() -> None:
    assert isinstance(FakeSearch([]), SymbolSearchProvider)
    assert isinstance(YahooSymbolSearch(Settings()), SymbolSearchProvider)


def test_a_row_echoed_without_its_manual_evidence_keeps_its_symbol(signup: SignupFn) -> None:
    c = signup()
    url, d = _draft(c, [_row(index=0, symbol="AAPL", name="Apple")])
    manual = _row(index=1, name="", symbol="ZZQX")
    first = c.patch(url, json={"rows": [*d["rows"], manual]}).json()["rows"]
    assert first[1]["exchange"] == "MANUAL"
    stripped = {k: v for k, v in first[1].items() if k != "exchange"}
    again = c.patch(url, json={"rows": [first[0], stripped]}).json()["rows"]
    assert again[1]["symbol"] == "ZZQX" and again[1]["exchange"] == "MANUAL"
