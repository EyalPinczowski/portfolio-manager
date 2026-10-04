"""Phase 1.5 money-correctness regressions: quote frames, unknown currency, GBp, circuit breaker."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session

from app.config import Settings
from app.models import Holding, Portfolio, PriceQuote, User
from app.portfolio.quotes import store_quotes
from app.portfolio.valuation import value_portfolio
from app.providers.base import Quote, normalize_history, normalize_price
from app.providers.fx_provider import UnknownCurrencyError, to_ils
from app.providers.yfinance_provider import YFinanceProvider
from app.timeutil import utcnow
from tests.fixtures.series import make_ohlcv


def _single_multiindex(sym: str, closes: list[float], ticker_first: bool) -> pd.DataFrame:
    fields = ["Open", "High", "Low", "Close", "Volume"]
    data = {f: closes if f != "Volume" else [1000.0] * len(closes) for f in fields}
    frame = pd.DataFrame(data)
    if ticker_first:
        frame.columns = pd.MultiIndex.from_product([[sym], fields], names=["Ticker", "Price"])
    else:
        frame.columns = pd.MultiIndex.from_product([fields, [sym]], names=["Price", "Ticker"])
    return frame


def _provider(
    monkeypatch: pytest.MonkeyPatch,
    downloads: list[pd.DataFrame],
    currencies: dict[str, str | None],
    calls: list[str] | None = None,
) -> YFinanceProvider:
    import yfinance as yf

    queue = list(downloads)

    def fake_download(**kw: object) -> pd.DataFrame:
        if calls is not None:
            calls.append(str(kw["tickers"]))
        return queue.pop(0) if queue else pd.DataFrame()

    monkeypatch.setattr(yf, "download", fake_download)
    prov = YFinanceProvider(Settings(provider_backoff_base_seconds=0.0))
    monkeypatch.setattr(prov, "raw_currency", lambda s: currencies.get(s))
    return prov


@pytest.mark.parametrize("ticker_first", [True, False])
def test_single_ticker_multiindex_frame_returns_a_quote(
    monkeypatch: pytest.MonkeyPatch, ticker_first: bool
) -> None:
    frame = _single_multiindex("AAPL", [190.0, 195.0], ticker_first)
    prov = _provider(monkeypatch, [frame], {"AAPL": "USD"})
    quotes = prov.get_quotes(["AAPL"])
    assert set(quotes) == {"AAPL"}
    assert quotes["AAPL"].price == 195.0 and quotes["AAPL"].currency == "USD"


def test_multi_ticker_frame_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    a = _single_multiindex("AAPL", [190.0, 195.0], True)
    b = _single_multiindex("TEVA.TA", [6400.0, 6500.0], True)
    prov = _provider(monkeypatch, [pd.concat([a, b], axis=1)], {"AAPL": "USD", "TEVA.TA": "ILA"})
    quotes = prov.get_quotes(["AAPL", "TEVA.TA"])
    assert quotes["TEVA.TA"].price == 65.0 and quotes["AAPL"].price == 195.0


def test_ila_stock_with_failed_currency_lookup_is_never_stored_in_agorot(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    frame = _single_multiindex("TEVA.TA", [6400.0, 6500.0], True)
    prov = _provider(monkeypatch, [frame, frame, frame], {"TEVA.TA": None})
    quotes = prov.get_quotes(["TEVA.TA"])
    assert quotes == {}  # ambiguous agorot/ILS: no quote rather than a x100 value
    assert store_quotes(db, quotes.values()) == 0


def test_failed_lookup_uses_stale_cached_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    import yfinance as yf

    frame = _single_multiindex("TEVA.TA", [6400.0, 6500.0], True)
    monkeypatch.setattr(yf, "download", lambda **kw: frame)
    prov = YFinanceProvider(Settings(provider_backoff_base_seconds=0.0))
    prov._currency.set("TEVA.TA", "ILA")
    prov._currency.ttl = -1.0  # expired
    monkeypatch.setattr(
        yf, "Ticker", lambda s: (_ for _ in ()).throw(RuntimeError("429 Too Many Requests"))
    )
    q = prov.get_quotes(["TEVA.TA"])
    assert q["TEVA.TA"].price == 65.0 and q["TEVA.TA"].currency == "ILS"


def test_unknown_currency_falls_back_to_security_currency_for_non_agorot_markets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _single_multiindex("AAPL", [190.0, 195.0], True)
    prov = _provider(monkeypatch, [frame], {"AAPL": None})
    prov.currency_hint = lambda s: "USD" if s == "AAPL" else None
    assert prov.get_quotes(["AAPL"])["AAPL"].currency == "USD"


def test_index_in_points_is_stored_without_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _single_multiindex("^TA125.TA", [2400.0, 2450.0], True)
    prov = _provider(monkeypatch, [frame], {"^TA125.TA": None})
    q = prov.get_quotes(["^TA125.TA"])["^TA125.TA"]
    assert q.price == 2450.0 and q.currency == ""


def test_store_quotes_rejects_unknown_currency_but_allows_indices(db: Session) -> None:
    bad = Quote(symbol="TEVA.TA", price=6500.0, currency="", as_of=utcnow())
    idx = Quote(symbol="^GSPC", price=5800.0, currency="", as_of=utcnow())
    assert store_quotes(db, [bad, idx]) == 1
    assert db.get(PriceQuote, "TEVA.TA") is None and db.get(PriceQuote, "^GSPC") is not None


def test_gbp_pence_normalisation() -> None:
    assert normalize_price(1234.0, "GBp") == (12.34, "GBP")
    assert normalize_price(1234.0, "GBX") == (12.34, "GBP")
    assert normalize_price(12.34, "GBP") == (12.34, "GBP")  # pounds are untouched
    df = make_ohlcv([1000.0, 1100.0])
    assert normalize_history(df, "GBp")["Close"].tolist() == pytest.approx([10.0, 11.0])


def test_to_ils_raises_on_unknown_currency() -> None:
    assert to_ils(10, "ILS", 3.5) == 10
    assert to_ils(10, "usd", 3.5) == 35
    for bad in ("", "GBP", "XXX"):
        with pytest.raises(UnknownCurrencyError):
            to_ils(10, bad, 3.5)


def test_valuation_ignores_legacy_quote_with_empty_currency(db: Session) -> None:
    user = User(email="a@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p")
    db.add(p)
    db.commit()
    db.add(
        Holding(
            portfolio_id=p.id or 0, symbol="TEVA.TA", quantity=100, avg_cost=60, cost_currency="ILS"
        )
    )
    db.add(PriceQuote(symbol="TEVA.TA", price=6500.0, currency=""))
    db.commit()
    v = value_portfolio(db, p).holdings[0]
    assert v.stale is True
    assert v.value_ils < 10_000  # not 6500 x 100 (x360 when treated as USD)


def test_history_is_not_cached_when_currency_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    import yfinance as yf

    df = make_ohlcv([6400.0] * 40)

    class FakeTicker:
        def __init__(self, s: str) -> None: ...
        def history(self, **kw: object) -> pd.DataFrame:
            return df

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    prov = YFinanceProvider(Settings(provider_backoff_base_seconds=0.0))
    cur: dict[str, str | None] = {"TEVA.TA": None}
    monkeypatch.setattr(prov, "raw_currency", lambda s: cur[s])
    assert prov.get_history("TEVA.TA", 30) is None  # ambiguous, nothing un-normalised returned
    cur["TEVA.TA"] = "ILA"
    out = prov.get_history("TEVA.TA", 30)
    assert out is not None and out["Close"].iloc[-1] == pytest.approx(64.0)


def test_cached_history_is_revalidated_when_currency_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yfinance as yf

    df = make_ohlcv([6400.0] * 40)

    class FakeTicker:
        def __init__(self, s: str) -> None: ...
        def history(self, **kw: object) -> pd.DataFrame:
            return df

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    prov = YFinanceProvider(Settings(provider_backoff_base_seconds=0.0))
    cur: dict[str, str | None] = {"X.TA": "ILS"}
    monkeypatch.setattr(prov, "raw_currency", lambda s: cur[s])
    first = prov.get_history("X.TA", 30)
    assert first is not None and first["Close"].iloc[-1] == 6400.0
    cur["X.TA"] = "ILA"  # Yahoo now reports agorot: the cached frame must not be reused
    second = prov.get_history("X.TA", 30)
    assert second is not None and second["Close"].iloc[-1] == pytest.approx(64.0)


def test_empty_tickers_are_retried_singly_then_trip_the_circuit_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yfinance as yf

    calls: list[str] = []
    monkeypatch.setattr(yf, "download", lambda **kw: calls.append(str(kw["tickers"])) or None)
    now = [0.0]
    prov = YFinanceProvider(
        Settings(
            provider_backoff_base_seconds=0.0,
            provider_breaker_threshold=2,
            provider_breaker_cooldown_seconds=300.0,
            provider_single_retry_max=2,
        ),
        clock=lambda: now[0],
        sleep=lambda _s: None,
    )
    monkeypatch.setattr(prov, "raw_currency", lambda s: "USD")
    assert prov.get_quotes(["AAA", "BBB", "CCC"]) == {}
    assert calls == ["AAA BBB CCC", "AAA", "BBB"]  # single retries are capped
    assert not prov.breaker_open()
    assert prov.get_quotes(["AAA", "BBB", "CCC"]) == {}
    assert prov.breaker_open()
    n = len(calls)
    assert prov.get_quotes(["AAA"]) == {} and len(calls) == n  # no network while open
    now[0] = 301.0
    assert not prov.breaker_open()
    prov.get_quotes(["AAA"])
    assert len(calls) > n


def test_partial_batch_recovers_missing_ticker_with_single_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _single_multiindex("AAA", [10.0, 11.0], True)
    b = _single_multiindex("BBB", [20.0, 22.0], True)
    batch = a  # BBB came back empty (rate-limited)
    calls: list[str] = []
    prov = _provider(monkeypatch, [batch, b], {"AAA": "USD", "BBB": "USD"}, calls)
    quotes = prov.get_quotes(["AAA", "BBB"])
    assert set(quotes) == {"AAA", "BBB"} and calls == ["AAA BBB", "BBB"]
    assert not prov.breaker_open()
