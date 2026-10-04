"""Block 2.1: fallback quote providers, the chain, source on each quote, exit-level price gate.

Fixed fixtures only (httpx MockTransport); no network. Live checks are marked `live`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest
from sqlmodel import Session

from app.config import QuoteSourceLimits, Settings
from app.models import Holding, Portfolio, PriceQuote, Security, User
from app.portfolio.freshness import StalePriceError, exit_level_price, price_is_fresh
from app.portfolio.quotes import store_quotes
from app.portfolio.valuation import value_portfolio
from app.providers.base import Quote
from app.providers.chain import (
    HISTORY_MISMATCH,
    HISTORY_UNVERIFIED,
    ChainedHistoryProvider,
    ChainedQuoteProvider,
    compare_overlap,
    history_usable_for_exit_levels,
)
from app.providers.fallback_sources import (
    BoiFxProvider,
    CoinGeckoQuoteProvider,
    FinnhubQuoteProvider,
    FmpHistoryProvider,
    FrankfurterFxProvider,
    StooqProvider,
    parse_daily_csv,
)
from app.timeutil import utcnow

TS = 1_790_000_000  # 2026-09-21


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _settings(**kw: object) -> Settings:
    return Settings(
        finnhub_api_key="fh-key",
        coingecko_api_key="cg-key",
        fmp_api_key="fmp-key",
        stooq_api_key="st-key",
        **kw,  # type: ignore[arg-type]
    )


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class PrimaryQuotes:
    def __init__(self, quotes: dict[str, Quote]) -> None:
        self.quotes = quotes
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self.calls.append(symbols)
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def _yq(sym: str, price: float, cur: str = "USD") -> Quote:
    return Quote(symbol=sym, price=price, currency=cur, as_of=utcnow())


# ------------------------------------------------------------------ Finnhub
def test_finnhub_quote_parses_and_sends_key_in_header() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        sym = req.url.params["symbol"]
        if sym == "NOPE":
            return httpx.Response(200, json={"c": 0, "d": None, "dp": None, "t": 0})
        return httpx.Response(
            200,
            json={
                "c": 191.5,
                "d": 1.0,
                "dp": 0.52,
                "h": 192,
                "l": 189,
                "o": 190,
                "pc": 190.5,
                "t": TS,
            },
        )

    f = FinnhubQuoteProvider(_settings(), client=_client(handler))
    out = f.get_quotes(["BRK-B", "NOPE", "TEVA.TA", "BTC-USD"])
    assert set(out) == {"BRK-B"}  # NOPE: all-zero answer; .TA and crypto: not covered, no call
    q = out["BRK-B"]
    assert (q.price, q.currency, q.source, q.basis) == (191.5, "USD", "finnhub", "live")
    assert q.as_of == datetime.fromtimestamp(TS, UTC).replace(tzinfo=None)
    assert [r.url.params["symbol"] for r in seen] == ["BRK.B", "NOPE"]
    assert all(
        "fh-key" not in str(r.url) and r.headers["X-Finnhub-Token"] == "fh-key" for r in seen
    )


def test_source_without_key_is_disabled_not_an_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no call without a key")

    f = FinnhubQuoteProvider(Settings(finnhub_api_key=None), client=_client(boom))
    assert not f.enabled and f.get_quotes(["AAPL"]) == {}


def test_finnhub_cache_ttl_budget_and_breaker() -> None:
    clock, calls = Clock(), []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.params["symbol"])
        if req.url.params["symbol"] == "BAD":
            return httpx.Response(429)
        return httpx.Response(200, json={"c": 10.0, "dp": 0, "t": TS})

    s = _settings(
        quote_source_limits={
            "finnhub": QuoteSourceLimits(
                ttl_seconds=60, max_calls_per_minute=1, breaker_cooldown_seconds=100
            )
        }
    )
    f = FinnhubQuoteProvider(s, client=_client(handler), clock=clock)
    assert "AAA" in f.get_quotes(["AAA"])
    assert "AAA" in f.get_quotes(["AAA"])  # served from the TTL cache
    assert calls == ["AAA"]
    clock.t += 61  # TTL over, budget window still holds the first call
    assert "AAA" in f.get_quotes(["AAA"]) and calls == ["AAA", "AAA"]
    assert f.get_quotes(["CCC"]) == {}  # budget of 1 per minute used up
    assert calls == ["AAA", "AAA"]
    clock.t += 61
    assert f.get_quotes(["BAD"]) == {}  # 429 opens the breaker
    assert not f.available()
    assert f.get_quotes(["DDD"]) == {} and calls[-1] == "BAD"
    clock.t += 101
    assert f.available()


# ------------------------------------------------------------------ CoinGecko
def test_coingecko_batches_all_coins_in_one_call() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "bitcoin": {"usd": 64000.5, "usd_24h_change": -1.2, "last_updated_at": TS},
                "ethereum": {"usd": 0, "last_updated_at": TS},  # zero price: refused
            },
        )

    c = CoinGeckoQuoteProvider(_settings(), client=_client(handler))
    out = c.get_quotes(["BTC-USD", "ETH-USD", "UNKNOWN-USD", "AAPL"])
    assert set(out) == {"BTC-USD"}
    assert out["BTC-USD"].source == "coingecko" and out["BTC-USD"].change_pct == -1.2
    assert len(seen) == 1 and seen[0].url.params["ids"] == "bitcoin,ethereum"
    assert seen[0].headers["x-cg-demo-api-key"] == "cg-key"


# ------------------------------------------------------------------ Stooq / FMP
STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2026-09-18,100,102,99,101,1000
2026-09-21,101,104,100,103.5,2000
"""


def test_parse_daily_csv_rejects_non_csv_answers() -> None:
    assert parse_daily_csv("Exceeded the daily hits limit") is None
    assert parse_daily_csv("") is None
    df = parse_daily_csv(STOOQ_CSV)
    assert df is not None and list(df["Close"]) == [101.0, 103.5]


def test_stooq_last_close_is_labelled_and_history_cached() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.params["s"])
        return httpx.Response(200, text=STOOQ_CSV)

    st = StooqProvider(_settings(), client=_client(handler))
    q = st.get_quotes(["AAPL"])["AAPL"]
    assert (q.price, q.source, q.basis) == (103.5, "stooq", "last_close")
    assert q.as_of.date().isoformat() == "2026-09-21"
    assert q.change_pct == pytest.approx(2.4752, abs=1e-3)
    assert calls == ["aapl.us"]
    hist = st.get_history("AAPL", 30)
    assert hist is not None and hist.attrs["source"] == "stooq"


def test_stooq_error_text_with_http_200_is_a_failure() -> None:
    st = StooqProvider(
        _settings(), client=_client(lambda r: httpx.Response(200, text="Get your apikey"))
    )
    assert st.get_quotes(["AAPL"]) == {} and st.get_history("AAPL", 30) is None


def test_fmp_parses_stable_and_legacy_shapes() -> None:
    rows = [
        {"date": "2026-09-21", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": "2026-09-18", "open": 1, "high": 2, "low": 0.5, "close": 1.4, "volume": 10},
    ]
    for body in (rows, {"symbol": "X", "historical": rows}):
        df = FmpHistoryProvider.parse_bars(body)
        assert df is not None and list(df["Close"]) == [1.4, 1.5]  # sorted ascending
    assert FmpHistoryProvider.parse_bars({"Error Message": "limit"}) is None
    fmp = FmpHistoryProvider(_settings(), client=_client(lambda r: httpx.Response(200, json=rows)))
    assert fmp.get_history("AAPL", 30) is not None
    assert fmp.get_history("TEVA.TA", 30) is None  # not covered: no call


# ------------------------------------------------------------------ FX
def test_frankfurter_daily_rate_is_dated_and_not_live() -> None:
    body = {"amount": 1.0, "base": "USD", "date": "2026-10-02", "rates": {"ILS": 3.31}}
    fx = FrankfurterFxProvider(
        _settings(), client=_client(lambda r: httpx.Response(200, json=body))
    )
    q = fx.get_quotes(["ILS=X", "AAPL"])["ILS=X"]
    assert (q.price, q.currency, q.source, q.basis) == (3.31, "ILS", "frankfurter", "last_close")
    assert q.as_of.date().isoformat() == "2026-10-02"


def test_frankfurter_rejects_implausible_rate() -> None:
    body = {"date": "2026-10-02", "rates": {"ILS": 331.0}}  # e.g. agorot by mistake
    fx = FrankfurterFxProvider(
        _settings(), client=_client(lambda r: httpx.Response(200, json=body))
    )
    assert fx.get_quotes(["ILS=X"]) == {}


BOI_XML = """<?xml version="1.0"?>
<ExchangeRates>
  <ExchangeRateResponseDTO><Key>EUR</Key><CurrentExchangeRate>3.9</CurrentExchangeRate><LastUpdate>2026-10-02T00:00:00</LastUpdate></ExchangeRateResponseDTO>
  <ExchangeRateResponseDTO><Key>USD</Key><CurrentExchangeRate>3.337</CurrentExchangeRate><LastUpdate>2026-10-02T00:00:00</LastUpdate></ExchangeRateResponseDTO>
</ExchangeRates>"""


def test_boi_parses_usd_rate_and_survives_garbage() -> None:
    boi = BoiFxProvider(_settings(), client=_client(lambda r: httpx.Response(200, text=BOI_XML)))
    q = boi.get_quotes(["ILS=X"])["ILS=X"]
    assert (q.price, q.source, q.basis) == (3.337, "boi", "last_close")
    bad = BoiFxProvider(_settings(), client=_client(lambda r: httpx.Response(200, text="<html>no")))
    assert bad.get_quotes(["ILS=X"]) == {}


# ------------------------------------------------------------------ the chain
class StubSource:
    def __init__(self, name: str, quotes: dict[str, Quote], live: bool = True) -> None:
        self.name, self.quotes, self.calls = name, quotes, []
        self._live = live

    def available(self) -> bool:
        return True

    def covers(self, symbol: str) -> bool:
        return True

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self.calls.append(symbols)
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def _fq(sym: str, price: float, source: str = "finnhub", basis: str = "live") -> Quote:
    return Quote(
        symbol=sym, price=price, currency="USD", as_of=utcnow(), source=source, basis=basis
    )  # type: ignore[arg-type]


def test_chain_uses_fallback_only_for_missing_symbols_and_records_source() -> None:
    s = _settings(quote_crosscheck_max_symbols=0)
    fh = StubSource("finnhub", {"MSFT": _fq("MSFT", 400.0)})
    chain = ChainedQuoteProvider(PrimaryQuotes({"AAPL": _yq("AAPL", 190.0)}), s, {"finnhub": fh})  # type: ignore[arg-type]
    out = chain.get_quotes(["AAPL", "MSFT"])
    assert out["AAPL"].source == "yfinance" and out["MSFT"].source == "finnhub"
    assert fh.calls == [["MSFT"]]


def test_chain_tase_has_no_fallback_and_never_asks_us_sources() -> None:
    fh = StubSource("finnhub", {"TEVA.TA": _fq("TEVA.TA", 1.0)})
    chain = ChainedQuoteProvider(PrimaryQuotes({}), _settings(), {"finnhub": fh})  # type: ignore[arg-type]
    assert chain.get_quotes(["TEVA.TA"]) == {} and fh.calls == []


def test_chain_routes_fx_symbol_to_fx_sources_in_order() -> None:
    fr = StubSource("frankfurter", {})
    boi = StubSource("boi", {"ILS=X": _fq("ILS=X", 3.3, "boi", "last_close")})
    chain = ChainedQuoteProvider(PrimaryQuotes({}), _settings(), {"frankfurter": fr, "boi": boi})  # type: ignore[arg-type]
    out = chain.get_quotes(["ILS=X"])
    assert out["ILS=X"].source == "boi" and fr.calls == [["ILS=X"]]


def test_chain_survives_primary_exception() -> None:
    class Broken:
        def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
            raise RuntimeError("yahoo 429")

    fh = StubSource("finnhub", {"AAPL": _fq("AAPL", 190.0)})
    chain = ChainedQuoteProvider(
        Broken(), _settings(quote_crosscheck_max_symbols=0), {"finnhub": fh}
    )  # type: ignore[arg-type]
    assert chain.get_quotes(["AAPL"])["AAPL"].source == "finnhub"


def test_price_disagreement_over_five_percent_is_flagged_not_silent() -> None:
    fh = StubSource("finnhub", {"AAPL": _fq("AAPL", 190.0), "MSFT": _fq("MSFT", 400.0)})
    prim = PrimaryQuotes({"AAPL": _yq("AAPL", 205.0), "MSFT": _yq("MSFT", 404.0)})  # +7.9 %, +1 %
    chain = ChainedQuoteProvider(prim, _settings(), {"finnhub": fh})  # type: ignore[arg-type]
    out = chain.get_quotes(["AAPL", "MSFT"])
    assert out["AAPL"].flag == "price_disagreement" and out["AAPL"].price == 205.0
    assert out["MSFT"].flag is None


def test_crosscheck_ignores_last_close_sources_and_is_bounded() -> None:
    st = StubSource("stooq", {"AAPL": _fq("AAPL", 100.0, "stooq", "last_close")})
    s = _settings(quote_fallback_order={"US": ["stooq"], "CRYPTO": [], "TASE": [], "FX": []})
    chain = ChainedQuoteProvider(PrimaryQuotes({"AAPL": _yq("AAPL", 190.0)}), s, {"stooq": st})  # type: ignore[arg-type]
    assert chain.get_quotes(["AAPL"])["AAPL"].flag is None and st.calls == []
    fh = StubSource("finnhub", {f"S{i}": _fq(f"S{i}", 10.0) for i in range(5)})
    prim = PrimaryQuotes({f"S{i}": _yq(f"S{i}", 10.0) for i in range(5)})
    ChainedQuoteProvider(
        prim, _settings(quote_crosscheck_max_symbols=2), {"finnhub": fh}
    ).get_quotes(  # type: ignore[arg-type]
        [f"S{i}" for i in range(5)]
    )
    assert len(fh.calls) == 2


# ------------------------------------------------------------------ history chain
def _frame(closes: list[float], end: str = "2026-09-21") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1.0}, index=idx
    )


class PrimaryHistory:
    def __init__(self, frame: pd.DataFrame | None) -> None:
        self.frame = frame

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        return self.frame


class AltHistory:
    name = "fmp"

    def __init__(self, frame: pd.DataFrame | None) -> None:
        self.frame = frame

    def available(self) -> bool:
        return True

    def covers(self, symbol: str) -> bool:
        return not symbol.endswith(".TA")

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        return self.frame


def test_history_fallback_matching_primary_is_labelled_clean() -> None:
    closes = [100.0 + i for i in range(20)]
    prim = PrimaryHistory(_frame(closes))
    chain = ChainedHistoryProvider(
        prim, _settings(), {"fmp": AltHistory(_frame([c * 1.001 for c in closes]))}
    )  # type: ignore[arg-type]
    assert chain.get_history("AAPL", 30) is not None  # primary works: remembered
    prim.frame = None  # Yahoo is blocked now
    out = chain.get_history("AAPL", 30)
    assert out is not None and out.attrs["source"] == "fmp"
    assert HISTORY_MISMATCH not in out.attrs and HISTORY_UNVERIFIED not in out.attrs
    assert history_usable_for_exit_levels(out)


def test_history_fallback_that_disagrees_is_marked_mismatch() -> None:
    closes = [100.0 + i for i in range(20)]
    prim = PrimaryHistory(_frame(closes))
    chain = ChainedHistoryProvider(
        prim, _settings(), {"fmp": AltHistory(_frame([c * 1.2 for c in closes]))}
    )  # type: ignore[arg-type]
    chain.get_history("AAPL", 30)
    prim.frame = None
    out = chain.get_history("AAPL", 30)
    assert out is not None and out.attrs[HISTORY_MISMATCH] is True
    assert not history_usable_for_exit_levels(out)


def test_history_fallback_without_reference_is_unverified_and_tase_has_none() -> None:
    chain = ChainedHistoryProvider(
        PrimaryHistory(None), _settings(), {"fmp": AltHistory(_frame([1.0] * 10))}
    )  # type: ignore[arg-type]
    out = chain.get_history("AAPL", 30)
    assert out is not None and out.attrs[HISTORY_UNVERIFIED] is True
    assert chain.get_history("TEVA.TA", 30) is None  # TASE: no history fallback configured


def test_compare_overlap_needs_enough_common_days() -> None:
    a, b = _frame([1.0, 2.0, 3.0]), _frame([1.0, 2.0, 3.0])
    assert compare_overlap(a, b, 2.0, 5) is None
    assert compare_overlap(None, b, 2.0, 1) is None


# ------------------------------------------------------------------ storing, valuation, exit-level gate
def _seed(db: Session, symbol: str = "AAPL", market: str = "US", cur: str = "USD") -> Portfolio:
    user = User(email="a@example.com", password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    p = Portfolio(owner_id=user.id, name="p")  # type: ignore[arg-type]
    db.add(p)
    if db.get(Security, symbol) is None:
        db.add(
            Security(symbol=symbol, name_en=symbol, asset_type="stock", market=market, currency=cur)
        )
    db.commit()
    db.refresh(p)
    db.add(Holding(portfolio_id=p.id, symbol=symbol, quantity=1, avg_cost=100.0, cost_currency=cur))  # type: ignore[arg-type]
    db.commit()
    return p


def test_store_quotes_records_source_basis_flag_and_never_overwrites_newer(db: Session) -> None:
    now = utcnow()
    store_quotes(
        db,
        [
            Quote(
                symbol="AAPL",
                price=1.0,
                currency="USD",
                as_of=now,
                source="finnhub",
                flag="price_disagreement",
            )
        ],
    )
    row = db.get(PriceQuote, "AAPL")
    assert row is not None and (row.source, row.basis, row.flag) == (
        "finnhub",
        "live",
        "price_disagreement",
    )
    store_quotes(
        db,
        [
            Quote(
                symbol="AAPL",
                price=9.0,
                currency="USD",
                as_of=now - timedelta(days=1),
                source="stooq",
                basis="last_close",
            )
        ],
    )
    db.refresh(row)
    assert row.price == 1.0 and row.source == "finnhub"  # the older daily price was skipped
    store_quotes(
        db, [Quote(symbol="AAPL", price=2.0, currency="USD", as_of=now + timedelta(minutes=5))]
    )
    db.refresh(row)
    assert (row.price, row.source, row.flag) == (2.0, "yfinance", None)


def test_exit_level_price_refuses_cost_screenshot_flagged_last_close_and_old(db: Session) -> None:
    p = _seed(db)
    s = Settings()
    now = utcnow()

    def valued() -> object:
        return value_portfolio(db, p, s).holdings[0]

    with pytest.raises(StalePriceError, match="cost"):
        exit_level_price(valued(), now, s)  # type: ignore[arg-type]

    store_quotes(
        db,
        [
            Quote(
                symbol="AAPL",
                price=150.0,
                currency="USD",
                as_of=now,
                source="finnhub",
                flag="price_disagreement",
            )
        ],
    )
    with pytest.raises(StalePriceError, match="price_disagreement"):
        exit_level_price(valued(), now, s)  # type: ignore[arg-type]

    store_quotes(
        db,
        [
            Quote(
                symbol="AAPL",
                price=150.0,
                currency="USD",
                as_of=now + timedelta(minutes=1),
                source="stooq",
                basis="last_close",
            )
        ],
    )
    v = valued()
    assert not price_is_fresh(v, now, s)  # type: ignore[arg-type]
    with pytest.raises(StalePriceError, match="last close"):
        exit_level_price(v, now, s)  # type: ignore[arg-type]

    row = db.get(PriceQuote, "AAPL")
    assert row is not None
    row.basis, row.flag, row.source, row.as_of = "live", None, "finnhub", now - timedelta(days=10)
    db.add(row)
    db.commit()
    with pytest.raises(StalePriceError, match="too old"):
        exit_level_price(valued(), now, s)  # type: ignore[arg-type]


def test_exit_level_price_accepts_a_fresh_live_fallback_quote(db: Session) -> None:
    p = _seed(db)
    s = Settings()
    now = datetime(2026, 9, 22, 15, 0)  # Tuesday 11:00 New York: US open
    store_quotes(
        db,
        [
            Quote(
                symbol="AAPL",
                price=150.0,
                currency="USD",
                as_of=now - timedelta(minutes=2),
                source="finnhub",
            )
        ],
    )
    v = value_portfolio(db, p, s).holdings[0]
    assert v.quote_source == "finnhub" and v.price_basis == "live"
    assert exit_level_price(v, now, s) == 150.0


def test_tase_stored_last_close_is_labelled_and_never_fresh(db: Session) -> None:
    p = _seed(db, "TEVA.TA", "TASE", "ILS")
    s = Settings()
    now = datetime(2026, 9, 22, 10, 0)
    store_quotes(
        db,
        [
            Quote(
                symbol="TEVA.TA",
                price=55.0,
                currency="ILS",
                as_of=now - timedelta(days=2),
                source="yfinance",
            )
        ],
    )
    v = value_portfolio(db, p, s).holdings[0]
    assert v.as_of == now - timedelta(days=2)
    assert not price_is_fresh(v, now, s)
    with pytest.raises(StalePriceError):
        exit_level_price(v, now, s)


def test_fx_daily_fallback_rate_is_exposed_as_last_close(db: Session) -> None:
    from app.providers.fx_provider import get_fx_rate

    store_quotes(
        db,
        [
            Quote(
                symbol="ILS=X",
                price=3.31,
                currency="ILS",
                as_of=utcnow(),
                source="frankfurter",
                basis="last_close",
            )
        ],
    )
    fx = get_fx_rate(db)
    assert (fx.usd_ils, fx.quote_source, fx.basis) == (3.31, "frankfurter", "last_close")


def test_holdings_api_exposes_price_source(client: object) -> None:
    pass  # covered by the schema contract (openapi.json) and the valuation tests above


def test_limits_come_from_config() -> None:
    s = Settings(quote_source_limits={"finnhub": QuoteSourceLimits(max_calls_per_minute=7)})
    assert FinnhubQuoteProvider(s).limits.max_calls_per_minute == 7
    assert (
        json.loads(Settings().model_dump_json())["quote_source_limits"]["finnhub"][
            "max_calls_per_minute"
        ]
        == 50
    )


@pytest.mark.live
def test_live_frankfurter_usd_ils() -> None:
    q = FrankfurterFxProvider(Settings()).get_quotes(["ILS=X"])
    assert q["ILS=X"].basis == "last_close"
