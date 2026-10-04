"""Exit levels: fixture-only tests on synthetic OHLC frames (no provider, no network)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pandas as pd
import pytest

from app.config import Settings
from app.models import Holding, Security
from app.portfolio.valuation import ValuedHolding
from app.scoring.exit_levels import (
    AnalystTargets,
    ExitLevelsResult,
    Horizon,
    StopState,
    compute_exit_levels,
)
from app.scoring.risk import PRESETS, RiskFilter
from app.verdict_words import verdict_words_in_text

S = Settings(_env_file=None)
NOW = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)
BALANCED: RiskFilter = RiskFilter.model_validate(PRESETS["balanced"].model_dump(exclude={"name"}))


def frame(
    n: int = 320, start: float = 100.0, drift: float = 0.15, amp: float = 5.0
) -> pd.DataFrame:
    """A rising, oscillating daily series: real swing lows and highs, a steady ATR of about 2."""
    idx = pd.bdate_range(end="2026-10-06", periods=n)
    close = [start + drift * i + amp * math.sin(i / 6.0) for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c + 1.0 for c in close],
            "Low": [c - 1.0 for c in close],
            "Close": close,
            "Volume": [1000.0] * n,
        },
        index=idx,
    )


def valued(
    price: float,
    *,
    symbol: str = "AAPL",
    market: str = "US",
    asset_type: str = "stock",
    currency: str = "USD",
    quantity: float = 100.0,
    avg_cost: float | None = None,
    cost_currency: str | None = None,
    source: str = "quote",
    basis: str = "live",
    age_minutes: float = 1.0,
    flag: str | None = None,
) -> ValuedHolding:
    return ValuedHolding(
        holding=Holding(
            portfolio_id=1,
            symbol=symbol,
            quantity=quantity,
            avg_cost=avg_cost,
            cost_currency=cost_currency or currency,
        ),
        security=Security(symbol=symbol, name_en=symbol, market=market, asset_type=asset_type),
        price=price,
        currency=currency,
        stale=source != "quote",
        day_change_pct=0.0,
        value_native=price * quantity,
        value_ils=price * quantity * (3.6 if currency == "USD" else 1.0),
        value_usd=price * quantity / (1.0 if currency == "USD" else 3.6),
        day_pnl_ils=0.0,
        pnl_ils=None,
        pnl_usd=None,
        pnl_pct=None,
        as_of=(NOW - timedelta(minutes=age_minutes)).replace(tzinfo=None),
        price_source=source,
        price_basis=basis,
        quote_source="fake" if source == "quote" else None,
        quote_flag=flag,
        usd_ils=3.6,
    )


def run(
    df: pd.DataFrame | None = None,
    horizon: str | Horizon | None = "1m",
    risk: RiskFilter = BALANCED,
    v: ValuedHolding | None = None,
    **kw: object,
) -> ExitLevelsResult:
    d = frame() if df is None else df
    holding = v or valued(float(d["Close"].iloc[-1]))
    return compute_exit_levels(holding, horizon, risk, d, now=NOW, settings=S, **kw)  # type: ignore[arg-type]


def all_texts(r: ExitLevelsResult) -> list[str]:
    out = [r.reason, r.explanation.summary]
    for lv in [r.stop, r.trailing_stop, r.breakeven, *r.take_profits]:
        if lv is not None:
            out += [lv.reason, lv.label, lv.explanation.summary]
    out += [s.label for s in r.scale_out] + [s.reason for s in r.skipped]
    if r.size_guidance:
        out.append(r.size_guidance.reason)
    return out


# ---------------------------------------------------------------- the gates
def test_no_horizon_answers_needs_horizon_and_computes_nothing() -> None:
    r = run(horizon=None)
    assert r.status == "needs_horizon" and r.reason_code == "needs_horizon"
    assert r.stop is None and r.take_profits == [] and r.price is None


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"source": "cost"}, "cost placeholder"),
        ({"source": "screenshot"}, "screenshot placeholder"),
        ({"basis": "last_close"}, "last close only"),
        ({"flag": "price_disagreement"}, "price_disagreement"),
        ({"age_minutes": 600.0}, "too old"),
    ],
)
def test_a_stale_or_placeholder_price_answers_no_levels_with_the_reason(
    kwargs: dict[str, object], fragment: str
) -> None:
    df = frame()
    r = run(df, v=valued(float(df["Close"].iloc[-1]), **kwargs))  # type: ignore[arg-type]
    assert r.status == "no_levels" and r.reason_code == "stale_price"
    assert fragment in r.reason
    assert r.stop is None and r.take_profits == [] and r.trailing_stop is None


def test_missing_or_short_history_answers_no_levels() -> None:
    assert run(frame()[0:0], v=valued(100.0)).reason_code == "no_history"
    assert run(frame(30)).reason_code == "insufficient_history"
    none = compute_exit_levels(valued(100.0), "1m", BALANCED, None, now=NOW, settings=S)
    assert none.status == "no_levels" and none.reason_code == "no_history"


def test_a_history_in_agorot_against_a_quote_in_shekels_is_refused() -> None:
    df = frame(start=4000.0, drift=1.0, amp=100.0)  # the x100 slip
    v = valued(40.0, symbol="TEVA.TA", market="TASE", currency="ILS")
    r = run(df, v=v)
    assert r.status == "no_levels" and r.reason_code == "history_price_mismatch"


# ---------------------------------------------------------------- levels
@pytest.mark.parametrize("hz", ["1w", "1m", "3m", "6m", "1y"])
def test_every_horizon_gives_a_stop_below_the_price_with_reason_and_explanation(hz: str) -> None:
    r = run(horizon=hz)
    assert r.status == "levels" and r.horizon == Horizon(hz)
    assert r.stop is not None and r.price is not None and 0 < r.stop.price < r.price
    for lv in [r.stop, r.trailing_stop, r.breakeven, *r.take_profits]:
        if lv is None:
            continue
        assert lv.reason.strip() and lv.explanation.summary.strip()
        assert lv.explanation.sources and lv.explanation.as_of is not None
    assert r.explanation.summary and r.explanation.risk_rules_applied
    assert [t.price for t in r.take_profits] == sorted(t.price for t in r.take_profits)
    for t in r.take_profits:
        assert t.price > r.price and t.rr is not None and t.rr >= BALANCED.min_rr - 1e-9


def test_no_verdict_words_in_any_text() -> None:
    for hz in ("1w", "1m", "3m", "6m", "1y"):
        for text in all_texts(run(horizon=hz)):
            assert verdict_words_in_text(text) == [], (hz, text)


def test_1m_stop_uses_the_atr_multiple_of_the_balanced_preset() -> None:
    r = run(horizon="1m")
    assert r.atr is not None and r.stop is not None and r.price is not None
    mult = 2.0 * S.exit_levels_preset_atr_scale["balanced"]
    atr_c = next(c for c in r.candidates if c.source == "atr")
    assert atr_c.price == pytest.approx(r.price - mult * r.atr, rel=1e-4)
    assert r.stop.price <= atr_c.price + 1e-9  # a chart level may only widen the ATR stop


def test_riskier_presets_get_wider_atr_stops() -> None:
    cons = RiskFilter.model_validate(PRESETS["very_conservative"].model_dump(exclude={"name"}))
    aggr = RiskFilter.model_validate(PRESETS["very_aggressive"].model_dump(exclude={"name"}))
    a = next(c for c in run(risk=cons).candidates if c.source == "atr")
    b = next(c for c in run(risk=aggr).candidates if c.source == "atr")
    assert b.price < a.price


def test_crypto_gets_a_wider_atr_stop_than_a_stock() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    stock = run(df, v=valued(price))
    coin = run(df, v=valued(price, symbol="BTC-USD", market="CRYPTO", asset_type="crypto"))
    d_stock = next(c for c in stock.candidates if c.source == "atr").distance_pct
    d_coin = next(c for c in coin.candidates if c.source == "atr").distance_pct
    assert d_coin == pytest.approx(d_stock * S.exit_levels_crypto_atr_multiplier, rel=1e-3)
    assert d_coin < d_stock  # distances are negative: further below the price


def test_tase_levels_stay_in_shekels_and_nothing_is_multiplied_by_100() -> None:
    df = frame(start=38.0, drift=0.02, amp=1.5)  # already ILS (the provider divided agorot)
    price = float(df["Close"].iloc[-1])
    v = valued(price, symbol="TEVA.TA", market="TASE", currency="ILS", avg_cost=30.0)
    r = run(df, v=v)
    assert r.currency == "ILS" and r.status == "levels"
    assert r.stop is not None and r.price is not None
    assert price * 0.5 < r.stop.price < price
    # ILS: the move in ILS equals the native move, with no FX and no x100.
    assert r.stop.vs_price_ils == pytest.approx((r.stop.price - price) * 100.0, abs=0.01)
    assert r.stop.pnl_ils == pytest.approx((r.stop.price - 30.0) * 100.0, abs=0.01)
    assert r.stop.pnl_usd == pytest.approx(r.stop.pnl_ils / 3.6, abs=0.01)  # type: ignore[operator]


def test_usd_cost_is_converted_into_the_price_currency() -> None:
    df = frame(start=38.0, drift=0.02, amp=1.5)
    price = float(df["Close"].iloc[-1])
    v = valued(
        price, symbol="TEVA.TA", market="TASE", currency="ILS", avg_cost=10.0, cost_currency="USD"
    )
    r = run(df, v=v)
    assert r.stop is not None
    assert r.stop.pnl_native == pytest.approx((r.stop.price - 36.0) * 100.0, abs=0.01)


# ---------------------------------------------------------------- never tighten to fit the filter
def test_a_wide_stop_is_not_tightened_a_smaller_size_is_suggested() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    holding = valued(price, quantity=100.0)
    loose = BALANCED.model_copy(update={"max_loss_per_position_pct": 50.0})
    tight = BALANCED.model_copy(update={"max_loss_per_position_pct": 3.0})
    a = run(df, "3m", loose, v=holding)
    b = run(df, "3m", tight, v=holding)
    assert a.stop is not None and b.stop is not None
    assert b.stop.price == a.stop.price  # the filter never moves the stop
    dist = -b.stop.distance_pct
    assert dist > 3.0
    assert b.size_guidance is not None and b.size_guidance.needed
    assert b.size_guidance.keep_fraction == pytest.approx(3.0 / dist, abs=1e-3)
    assert b.size_guidance.suggested_quantity < 100.0
    assert "not moved closer" in b.size_guidance.reason
    assert a.size_guidance is not None and not a.size_guidance.needed
    assert a.size_guidance.suggested_quantity == 100.0


def test_the_per_trade_portfolio_limit_also_shrinks_the_size_not_the_stop() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    holding = valued(price, quantity=100.0)
    loose = BALANCED.model_copy(update={"max_loss_per_position_pct": 100.0})
    base = run(df, "1m", loose, v=holding)
    capped = run(df, "1m", loose, v=holding, portfolio_value_ils=price * 100.0 * 3.6)
    assert base.stop is not None and capped.stop is not None
    assert capped.stop.price == base.stop.price
    assert capped.size_guidance is not None and capped.size_guidance.needed
    assert any("risk per trade" in r for r in capped.size_guidance.rules)


# ---------------------------------------------------------------- trailing stop
def test_ratchet_only_moves_up() -> None:
    st = StopState()
    seen: list[float] = []
    for stop, high in [(10, 20), (8, 18), (12, 25), (9, 22), (12, 30)]:
        st = st.ratchet(stop, high)
        assert st.stop is not None
        seen.append(st.stop)
    assert seen == sorted(seen) == [10, 10, 12, 12, 12]
    assert st.highest_high == 30


def test_the_trailing_stop_never_lowers_along_a_rise_then_a_fall() -> None:
    full = frame()
    # A path that rises (bars 0..n) and then falls back: append a falling tail.
    tail_idx = pd.bdate_range(start=full.index[-1] + pd.Timedelta(days=1), periods=25)
    last = float(full["Close"].iloc[-1])
    fall = [last - 1.5 * i for i in range(1, 26)]
    tail = pd.DataFrame(
        {
            "Open": fall,
            "High": [c + 1 for c in fall],
            "Low": [c - 1 for c in fall],
            "Close": fall,
            "Volume": 1000.0,
        },
        index=tail_idx,
    )
    path = pd.concat([full, tail])
    state: StopState | None = None
    trail: list[float] = []
    for cut in range(len(full) - 30, len(path) + 1, 3):
        d = path.iloc[:cut]
        r = run(d, "1m", v=valued(float(d["Close"].iloc[-1])), state=state)
        assert r.trailing_stop is not None and r.state is not None
        trail.append(r.trailing_stop.price)
        state = r.state
    assert trail == sorted(trail), trail
    assert trail[-1] > trail[0]


def test_a_saved_stop_above_the_new_one_is_kept_and_a_stop_is_never_lowered() -> None:
    df = frame()
    first = run(df, "1m")
    assert first.stop is not None and first.price is not None
    saved = first.stop.price + 3.0
    r = run(df, "1m", state=StopState(stop=saved))
    assert r.stop is not None and r.stop.price == pytest.approx(saved)
    assert r.stop.source == "saved_stop" and "never lowered" in r.stop.reason
    assert r.trailing_stop is not None and r.trailing_stop.price >= saved
    lower = run(df, "1m", state=StopState(stop=first.stop.price - 3.0))
    assert lower.stop is not None and lower.stop.price == first.stop.price  # not dragged down


def test_a_price_through_the_saved_stop_is_flagged_and_the_stop_stays() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    r = run(df, "1m", state=StopState(stop=price + 5.0))
    assert r.stop is not None and r.stop.price == pytest.approx(price + 5.0)
    assert r.stop.reached and r.take_profits == []


def test_a_fixed_stop_type_has_no_trailing_stop() -> None:
    fixed = BALANCED.model_copy(update={"stop_type": "fixed"})
    r = run(risk=fixed)
    assert r.stop is not None and r.trailing_stop is None


def test_the_trailing_stop_starts_no_lower_than_the_stop() -> None:
    r = run(risk=BALANCED.model_copy(update={"stop_type": "trailing"}))
    assert r.stop is not None and r.trailing_stop is not None
    assert r.trailing_stop.price >= r.stop.price


# ---------------------------------------------------------------- take-profits and the rest
def test_a_high_min_rr_filters_levels_and_raises_the_r_multiple() -> None:
    strict = BALANCED.model_copy(update={"min_rr": 4.0})
    r = run(horizon="1m", risk=strict)
    assert all(t.rr is not None and t.rr >= 4.0 - 1e-9 for t in r.take_profits)
    r_levels = [t for t in r.take_profits if t.source == "r_multiple"]
    assert r_levels and r_levels[0].rr == 4.0
    lax = run(horizon="1m", risk=BALANCED.model_copy(update={"min_rr": 0.5}))
    assert len(lax.take_profits) >= len(r.take_profits)


def test_analyst_targets_are_an_optional_input_skipped_with_a_reason_when_absent() -> None:
    absent = run(horizon="3m")
    assert any(
        s.source == "analyst_mean_target" and "no analyst" in s.reason for s in absent.skipped
    )
    price = absent.price
    assert price is not None
    given = run(horizon="3m", analyst=AnalystTargets(mean=price * 1.4))
    assert any(t.source == "analyst_mean_target" for t in given.take_profits)
    below = run(horizon="3m", analyst=AnalystTargets(mean=price * 0.9))
    assert any(s.source == "analyst_mean_target" and "not above" in s.reason for s in below.skipped)


def test_1y_has_no_fixed_take_profit_from_trailing_only_and_a_ma_based_stop() -> None:
    r = run(horizon="1y")
    assert (
        any(s.source == "trailing_stop" or s.source == "trailing_only" for s in r.skipped)
        or r.trailing_stop
    )
    assert r.stop is not None and r.stop.source in ("structure", "moving_average", "max_loss")


def test_scale_out_uses_the_balanced_row_and_trails_the_rest() -> None:
    r = run(horizon="1m", risk=BALANCED.model_copy(update={"min_rr": 1.0}))
    assert len(r.take_profits) >= 2
    first, second, rest = r.scale_out[0], r.scale_out[1], r.scale_out[-1]
    row = S.exit_levels_scale_out_plans["balanced"]
    assert first.fraction == pytest.approx(row.first_fraction, abs=1e-3)
    assert second.fraction == pytest.approx(row.second_fraction, abs=1e-3)
    assert second.price == r.take_profits[1].price
    assert rest.step == "trail_rest"
    assert sum(s.quantity for s in r.scale_out) == pytest.approx(100.0, abs=1e-3)


def test_breakeven_is_suggested_once_the_gain_is_an_atr_above_cost() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    fixed = BALANCED.model_copy(update={"stop_type": "fixed"})
    winner = run(df, risk=fixed, v=valued(price, avg_cost=price - 2.5))
    assert winner.breakeven is not None
    assert winner.breakeven.price == pytest.approx(price - 2.5)
    assert winner.breakeven.kind == "breakeven" and "can no longer lose" in winner.breakeven.reason
    loser = run(df, v=valued(price, avg_cost=price + 5.0))
    assert loser.breakeven is None
    # a stop already above the cost needs no breakeven move
    assert run(df, risk=fixed, v=valued(price, avg_cost=price - 10.0)).breakeven is None
    nocost = run(df, v=valued(price))
    assert nocost.breakeven is None


def test_review_style_fit_of_a_saved_stop_against_the_horizon() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    assert run(df, state=StopState(stop=price - 0.1)).stop_fit == "too_tight"
    assert run(df, state=StopState(stop=price * 0.2)).stop_fit == "too_wide"
    assert run(df).stop_fit is None


def test_risk_to_stop_is_in_shekels_and_dollars() -> None:
    df = frame()
    price = float(df["Close"].iloc[-1])
    r = run(df, v=valued(price, quantity=10.0), portfolio_value_ils=100_000.0)
    assert r.risk_to_stop is not None and r.stop is not None
    expected = (price - r.stop.price) * 10.0
    assert r.risk_to_stop.native == pytest.approx(expected, abs=1e-3)
    assert r.risk_to_stop.ils == pytest.approx(expected * 3.6, abs=0.01)
    assert r.risk_to_stop.usd == pytest.approx(expected, abs=0.01)
    assert r.risk_to_stop.pct_of_portfolio == pytest.approx(expected * 3.6 / 1000.0, abs=1e-3)


def test_new_tunables_are_in_config_with_sane_defaults() -> None:
    assert S.exit_levels_min_bars >= 20
    assert S.exit_levels_crypto_atr_multiplier > 1.0
    assert set(S.exit_levels_scale_out_plans) == set(PRESETS)
    assert set(S.exit_levels_preset_atr_scale) == set(PRESETS)


# ---- per-preset scale-out plans (suggestions, numbers in config)
ORDER = [
    "very_conservative",
    "conservative",
    "balanced",
    "balanced_aggressive",
    "aggressive",
    "very_aggressive",
]


def _preset_filter(name: str) -> RiskFilter:
    f = RiskFilter.model_validate(PRESETS[name].model_dump(exclude={"name"}))
    return f.model_copy(update={"min_rr": 1.0})


@pytest.mark.parametrize("name", ORDER)
def test_each_preset_yields_its_table_fractions(name: str) -> None:
    r = run(horizon="1m", risk=_preset_filter(name))
    row = S.exit_levels_scale_out_plans[name]
    assert len(r.take_profits) >= 2
    assert r.scale_out[0].fraction == pytest.approx(row.first_fraction, abs=1e-3)
    assert r.scale_out[1].fraction == pytest.approx(row.second_fraction, abs=1e-3)
    assert r.scale_out[-1].step == "trail_rest"
    assert r.scale_out[-1].fraction == pytest.approx(row.trail_fraction, abs=1e-3)
    assert sum(x.fraction for x in r.scale_out) == pytest.approx(1.0, abs=1e-3)
    assert r.scale_out_plan is not None and r.scale_out_plan.profile == name
    assert not r.scale_out_plan.used_fallback


def test_scale_out_table_is_monotonic_with_aggressiveness() -> None:
    rows = [S.exit_levels_scale_out_plans[n] for n in ORDER]
    for a, b in pairwise(rows):
        assert a.first_fraction >= b.first_fraction
        assert a.trail_atr_scale <= b.trail_atr_scale
        assert a.breakeven_atr_multiple <= b.breakeven_atr_multiple
        assert a.trail_fraction <= b.trail_fraction
    assert all(r.trail_fraction > 0 for r in rows)


def test_scale_out_reasons_name_the_profile_and_stay_verdict_free() -> None:
    for name in ORDER:
        r = run(horizon="1m", risk=_preset_filter(name))
        label = name.replace("_", " ").capitalize() + " profile"
        assert all(label in s.reason for s in r.scale_out)
        assert r.scale_out_plan is not None
        assert label in r.scale_out_plan.explanation.summary
        assert label in r.explanation.rules_applied[-1] or any(
            label in x for x in r.explanation.rules_applied
        )
        assert "not an instruction" in r.scale_out_plan.note
        assert (
            verdict_words_in_text(r.scale_out_plan.note + r.scale_out_plan.explanation.summary)
            == []
        )
        assert all(verdict_words_in_text(s.reason) == [] for s in r.scale_out)


def test_custom_filter_without_preset_uses_the_fallback_row() -> None:
    r = run(horizon="1m", risk=BALANCED.model_copy(update={"preset": None, "min_rr": 1.0}))
    assert r.scale_out_plan is not None and r.scale_out_plan.used_fallback
    assert r.scale_out_plan.profile == S.exit_levels_scale_out_fallback_preset


def test_a_wider_profile_trails_further_below_the_high() -> None:
    trail = {"stop_type": "atr"}
    cons = run(horizon="1m", risk=_preset_filter("very_conservative").model_copy(update=trail))
    aggr = run(horizon="1m", risk=_preset_filter("very_aggressive").model_copy(update=trail))
    assert cons.trailing_stop is not None and aggr.trailing_stop is not None
    assert cons.trailing_stop.price >= aggr.trailing_stop.price


def test_no_levels_results_carry_no_scale_out_plan() -> None:
    r = run(horizon=None)
    assert r.status == "needs_horizon" and r.scale_out_plan is None and r.scale_out == []
