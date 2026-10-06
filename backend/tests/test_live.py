"""Live-network tests. Skipped by default; run with `pytest -m live`."""

from __future__ import annotations

import pytest

from app.providers.yfinance_provider import YFinanceProvider


@pytest.mark.live
def test_live_tase_quote_is_normalised_to_ils() -> None:
    quotes = YFinanceProvider().get_quotes(["TEVA.TA", "^TA125.TA", "SPY"])
    assert quotes["TEVA.TA"].currency == "ILS" and quotes["TEVA.TA"].price < 500  # agorot / 100
    assert quotes["^TA125.TA"].price > 500  # an index in points is never divided
    assert quotes["SPY"].currency == "USD"


@pytest.mark.live
def test_live_history_has_ohlcv() -> None:
    df = YFinanceProvider().get_history("AAPL", 300)
    assert df is not None and {"Open", "High", "Low", "Close", "Volume"} <= set(df.columns)


@pytest.mark.live
def test_live_gemelnet_columns_match_the_configured_names() -> None:
    """The configured resource still answers with the configured column names (data.gov.il may
    replace the file; then find the new id with `package_search?q=gemelnet`)."""
    from app.config import get_settings
    from app.providers.gemelnet import GemelNetProvider

    s = get_settings()
    if not s.gemelnet_resource_ids.get("monthly_returns"):
        pytest.skip("GEMELNET_RESOURCE_IDS is empty: the fund lookup is switched off")
    p = GemelNetProvider(s)
    records = p._records({"limit": 5})
    assert records and set(s.gemelnet_fields.values()) <= set(records[0]), records[0].keys()
    found = p.search_funds("מיטב")
    assert found.value, found.missing_reason
    fund = p.get_fund(found.value[0].fund_id)
    assert fund.value and fund.value.months, fund.missing_reason


@pytest.mark.live
def test_live_usd_ils_reference_rates() -> None:
    """Bank of Israel and Frankfurter (no key) still answer in the shape the parsers expect."""
    from app.config import get_settings
    from app.providers.fallback_sources import BoiFxProvider, FrankfurterFxProvider

    s = get_settings()
    for cls in (BoiFxProvider, FrankfurterFxProvider):
        q = cls(s).get_quotes([s.fx_symbol]).get(s.fx_symbol)
        assert q is not None and q.currency == "ILS", cls.name


@pytest.mark.live
def test_live_coingecko_ids_all_resolve() -> None:
    """Every configured coin id answers. Needs COINGECKO_API_KEY (a wrong key is a 401)."""
    from app.config import get_settings
    from app.providers.fallback_sources import CoinGeckoQuoteProvider

    s = get_settings()
    if not s.coingecko_api_key:
        pytest.skip("set COINGECKO_API_KEY (free demo key)")
    quotes = CoinGeckoQuoteProvider(s).get_quotes(list(s.coingecko_ids))
    assert set(quotes) == set(s.coingecko_ids)


@pytest.mark.live
def test_live_dividends_tase_is_ils_not_agorot() -> None:
    from app.providers.dividends import YFinanceDividendProvider

    f = YFinanceDividendProvider().get_dividends("TEVA.TA")
    if f.value is not None:
        assert {e.currency for e in f.value.events} == {"ILS"}
        assert all(e.amount < 50 for e in f.value.events)  # shekels per share, not agorot


@pytest.mark.live
def test_live_symbol_search_finds_reddit_and_a_tase_name() -> None:
    from app.providers.symbol_search import YahooSymbolSearch

    p = YahooSymbolSearch()
    assert "RDDT" in [h.symbol for h in p.search("reddit")]
    assert any(h.symbol.endswith(".TA") for h in p.search("nice systems teva leumi"))
