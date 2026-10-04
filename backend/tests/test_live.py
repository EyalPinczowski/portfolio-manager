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
    """Needs GEMELNET_RESOURCE_IDS (JSON). Verifies the UNVERIFIED column names against data.gov.il."""
    from app.config import get_settings
    from app.providers.gemelnet import GemelNetProvider

    s = get_settings()
    if not s.gemelnet_resource_ids.get("monthly_returns"):
        pytest.skip("set GEMELNET_RESOURCE_IDS={'monthly_returns': '<resource id>'}")
    p = GemelNetProvider(s)
    records = p._records({"limit": 5})
    assert records and set(s.gemelnet_fields.values()) <= set(records[0]), records[0].keys()
    found = p.search_funds("קרן")
    assert found.value, found.missing_reason


@pytest.mark.live
def test_live_dividends_tase_is_ils_not_agorot() -> None:
    from app.providers.dividends import YFinanceDividendProvider

    f = YFinanceDividendProvider().get_dividends("TEVA.TA")
    if f.value is not None:
        assert {e.currency for e in f.value.events} == {"ILS"}
        assert all(e.amount < 50 for e in f.value.events)  # shekels per share, not agorot
