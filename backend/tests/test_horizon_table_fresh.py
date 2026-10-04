"""The README horizon table lives in config; `price_is_fresh` guards exit levels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.config import HORIZONS, HorizonSpec, Settings, default_horizon_table
from app.models import Holding, Security
from app.portfolio.freshness import price_is_fresh
from app.portfolio.valuation import ValuedHolding

S = Settings(_env_file=None)


# ---------------------------------------------------------------- horizon table
def test_default_table_has_every_horizon_and_matches_the_readme() -> None:
    t = S.horizon_table
    assert list(t) == list(HORIZONS) == ["1w", "1m", "3m", "6m", "1y"]
    assert all(isinstance(v, HorizonSpec) for v in t.values())
    # "1 week | Daily / 4h, ATR(14) daily | ~1-1.5x ATR, nearest minor support | resistance, 1.5R-2R"
    w = t["1w"]
    assert (w.chart_timeframes, w.atr_timeframe, w.atr_period) == (["1d", "4h"], "1d", 14)
    assert (w.atr_multiple_min, w.atr_multiple_max) == (1.0, 1.5)
    assert (w.r_multiple_min, w.r_multiple_max) == (1.5, 2.0) and w.stop_structure == [
        "minor_support"
    ]
    # "1 month | Daily, ATR(14) | ~2x ATR, swing low / SMA 20 | resistance, 2R, upper Bollinger"
    m = t["1m"]
    assert (m.atr_multiple_min, m.stop_ma_period, m.r_multiple_min) == (2.0, 20, 2.0)
    assert m.take_profit_sources == ["resistance", "r_multiple", "upper_bollinger"]
    # "3 months | Daily + weekly | ~2.5-3x ATR, SMA 50 / major support | weekly resistance, 2R-3R, analyst mean"
    q = t["3m"]
    assert q.chart_timeframes == ["1d", "1wk"] and (q.atr_multiple_min, q.atr_multiple_max) == (
        2.5,
        3.0,
    )
    assert q.stop_ma_period == 50 and (q.r_multiple_min, q.r_multiple_max) == (2.0, 3.0)
    assert "analyst_mean_target" in q.take_profit_sources
    # "6 months | Weekly, weekly ATR | ~2x weekly ATR, SMA 100 / weekly swing low | analyst mean/high, 3R, Fibonacci"
    h = t["6m"]
    assert (h.atr_timeframe, h.atr_multiple_min, h.stop_ma_period) == ("1wk", 2.0, 100)
    assert set(h.take_profit_sources) == {
        "analyst_mean_target", "analyst_high_target", "r_multiple", "fibonacci_extension",
    }  # fmt: skip
    # "1 year+ | Weekly / monthly | below SMA 200 / major multi-month support | analyst high, long-term resistance, trailing"
    y = t["1y"]
    assert y.chart_timeframes == ["1wk", "1mo"] and y.stop_ma_period == 200
    assert y.atr_multiple_min is None and y.r_multiple_min is None
    assert y.take_profit_sources == ["analyst_high_target", "long_term_resistance", "trailing_only"]


def test_each_default_is_a_fresh_copy() -> None:
    assert Settings(_env_file=None).horizon_table is not Settings(_env_file=None).horizon_table
    assert default_horizon_table() == default_horizon_table()


def test_specs_are_immutable_and_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        S.horizon_table["1m"].atr_period = 5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        HorizonSpec.model_validate({**S.horizon_table["1m"].model_dump(), "surprise": 1})


def table_with(horizon: str, **changes: object) -> dict[str, dict[str, object]]:
    raw = {k: v.model_dump() for k, v in default_horizon_table().items()}
    raw[horizon].update(changes)
    return raw


@pytest.mark.parametrize(
    "changes",
    [
        {"atr_multiple_min": 3.0, "atr_multiple_max": 2.0},
        {"atr_multiple_min": None},
        {"atr_period": 0},
        {"r_multiple_min": None, "r_multiple_max": None},  # source without a range
        {"take_profit_sources": ["magic"]},
        {"chart_timeframes": []},
        {"chart_timeframes": ["5m"]},
        {"stop_ma_period": 0},
    ],
)
def test_a_bad_spec_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, horizon_table=table_with("1m", **changes))  # type: ignore[arg-type]


def test_a_structureless_stop_is_rejected() -> None:
    raw = table_with("1y", atr_multiple_min=None, stop_ma_period=None, stop_structure=[])
    with pytest.raises(ValidationError, match="stop needs"):
        Settings(_env_file=None, horizon_table=raw)  # type: ignore[arg-type]


def test_the_table_needs_exactly_the_five_horizons() -> None:
    raw = {k: v.model_dump() for k, v in default_horizon_table().items()}
    missing = {k: v for k, v in raw.items() if k != "3m"}
    extra = {**raw, "2y": raw["1y"]}
    for bad in (missing, extra, {}):
        with pytest.raises(ValidationError, match="horizon_table"):
            Settings(_env_file=None, horizon_table=bad)  # type: ignore[arg-type]


def test_the_table_can_be_overridden_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    raw = table_with("1m", atr_multiple_min=2.2, atr_multiple_max=2.2)
    monkeypatch.setenv("HORIZON_TABLE", json.dumps(raw))
    assert Settings(_env_file=None).horizon_table["1m"].atr_multiple_min == 2.2


def test_freshness_windows_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, price_fresh_window_minutes={"US": 0})


# ---------------------------------------------------------------- price_is_fresh
WED_US_OPEN = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)  # 11:00 New York; 18:00 Tel Aviv (closed)
SAT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def valued(
    market: str = "US",
    source: str = "quote",
    as_of: datetime | None = None,
    stale: bool = False,
) -> ValuedHolding:
    sym = "AAPL" if market == "US" else "TEVA.TA" if market == "TASE" else "BTC-USD"
    return ValuedHolding(
        holding=Holding(portfolio_id=1, symbol=sym, quantity=1.0),
        security=Security(symbol=sym, name_en=sym, market=market),
        price=10.0, currency="USD", stale=stale, day_change_pct=0.0, value_native=10.0,
        value_ils=36.0, value_usd=10.0, day_pnl_ils=0.0, pnl_ils=None, pnl_usd=None,
        pnl_pct=None, as_of=None if as_of is None else as_of.replace(tzinfo=None),
        price_source=source,
    )  # fmt: skip


def test_a_cost_price_is_never_fresh() -> None:
    assert not price_is_fresh(valued(source="cost", as_of=None, stale=True), WED_US_OPEN, S)
    assert not price_is_fresh(valued(source="cost", as_of=WED_US_OPEN), WED_US_OPEN, S)


def test_a_screenshot_price_is_never_fresh() -> None:
    assert not price_is_fresh(valued(source="screenshot", as_of=None, stale=True), WED_US_OPEN, S)
    assert not price_is_fresh(valued(source="screenshot", as_of=WED_US_OPEN), WED_US_OPEN, S)


def test_a_quote_without_a_timestamp_or_flagged_stale_is_not_fresh() -> None:
    assert not price_is_fresh(valued(as_of=None), WED_US_OPEN, S)
    assert not price_is_fresh(valued(as_of=WED_US_OPEN, stale=True), WED_US_OPEN, S)


def test_a_recent_quote_in_an_open_market_is_fresh() -> None:
    assert price_is_fresh(valued(as_of=WED_US_OPEN - timedelta(minutes=5)), WED_US_OPEN, S)
    assert price_is_fresh(valued(as_of=WED_US_OPEN - timedelta(minutes=60)), WED_US_OPEN, S)


def test_an_old_quote_in_an_open_market_is_not_fresh() -> None:
    assert not price_is_fresh(valued(as_of=WED_US_OPEN - timedelta(minutes=61)), WED_US_OPEN, S)
    assert not price_is_fresh(valued(as_of=WED_US_OPEN - timedelta(hours=5)), WED_US_OPEN, S)


def test_the_window_is_config_per_market() -> None:
    tight = Settings(
        _env_file=None, price_fresh_window_minutes={"US": 10, "TASE": 60, "CRYPTO": 30}
    )
    quote = valued(as_of=WED_US_OPEN - timedelta(minutes=20))
    assert price_is_fresh(quote, WED_US_OPEN, S) and not price_is_fresh(quote, WED_US_OPEN, tight)


def test_the_closing_quote_stays_fresh_while_the_market_is_closed() -> None:
    # TASE closed at 17:25 Israel time (14:25 UTC) on Wed; it is 18:00 there now.
    close = datetime(2026, 10, 7, 14, 25, tzinfo=UTC)
    assert price_is_fresh(valued("TASE", as_of=close - timedelta(minutes=5)), WED_US_OPEN, S)
    # A quote from before the close by more than the window is not.
    assert not price_is_fresh(valued("TASE", as_of=close - timedelta(hours=3)), WED_US_OPEN, S)
    # Yesterday's close is not today's close.
    assert not price_is_fresh(valued("TASE", as_of=close - timedelta(days=1)), WED_US_OPEN, S)


def test_over_the_weekend_the_friday_close_is_fresh_and_thursday_is_not() -> None:
    us_close = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # Fri 16:00 New York
    assert price_is_fresh(valued("US", as_of=us_close - timedelta(minutes=2)), SAT, S)
    assert not price_is_fresh(valued("US", as_of=us_close - timedelta(hours=5)), SAT, S)
    assert not price_is_fresh(valued("US", as_of=us_close - timedelta(days=1)), SAT, S)
    tase_close = datetime(2026, 10, 9, 10, 50, tzinfo=UTC)  # Fri 13:50 Israel (IDT)
    assert price_is_fresh(valued("TASE", as_of=tase_close - timedelta(minutes=1)), SAT, S)


def test_crypto_has_no_closing_quote() -> None:
    assert price_is_fresh(valued("CRYPTO", as_of=SAT - timedelta(minutes=10)), SAT, S)
    assert not price_is_fresh(valued("CRYPTO", as_of=SAT - timedelta(hours=2)), SAT, S)


def test_a_quote_from_the_future_is_not_fresh() -> None:
    assert not price_is_fresh(valued(as_of=WED_US_OPEN + timedelta(hours=3)), WED_US_OPEN, S)


def test_naive_now_is_read_as_utc() -> None:
    naive = WED_US_OPEN.replace(tzinfo=None)
    assert price_is_fresh(valued(as_of=WED_US_OPEN - timedelta(minutes=5)), naive, S)
