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
