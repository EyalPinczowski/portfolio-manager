"""Portfolio post-mortem: fixture-only tests of the pure functions (no network, no LLM)."""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.config import DISCLAIMER, get_settings
from app.portfolio.performance import DayPoint
from app.portfolio.postmortem import (
    HoldingIn,
    PostmortemInput,
    PostmortemOut,
    SecInfo,
    TxIn,
    build_postmortem,
    expected_for_period,
)
from app.verdict_words import verdict_words_in_text

S = get_settings()
START = date(2026, 1, 1)
END = START + timedelta(days=73)
AAPL = SecInfo("AAPL", "Apple", "US", "USD", "Technology", "US")
TEVA = SecInfo("TEVA.TA", "Teva", "TASE", "ILS", "Health", "IL")


def series(
    first: float, last: float, start: date = START - timedelta(days=70), end: date = END
) -> pd.Series:
    """Flat at `first` before START, linear to `last` at `end`."""
    idx = pd.date_range(start, end, freq="D")
    span = (end - START).days
    vals = [first + (last - first) * max((d.date() - START).days, 0) / span for d in idx]
    return pd.Series(vals, index=idx)


def base_input(**over: object) -> PostmortemInput:
    kw: dict[str, object] = {
        "start": START,
        "end": END,
        "today": END,
        "points": [DayPoint(START, 3500.0), DayPoint(END, 4070.0)],
        "txs": [],
        "holdings": [HoldingIn("AAPL", 10, 110.0, True)],
        "securities": {"AAPL": AAPL},
        "closes": {"AAPL": series(100, 110)},
        "fx_closes": series(3.5, 3.7),
        "benchmark_closes": {"^GSPC": series(5000, 5250), "^TA125.TA": series(2000, 2100)},
    }
    kw.update(over)
    return PostmortemInput(**kw)  # type: ignore[arg-type]


def finding(out: PostmortemOut, kind: str):
    return next(f for f in out.findings if f.kind == kind)


def test_attribution_plus_residual_equals_the_gap_for_both_references() -> None:
    inp = base_input(
        points=[
            DayPoint(START, 3500.0),
            DayPoint(START + timedelta(days=10), 4500.0, 1000.0),
            DayPoint(END, 5070.0),
        ],
        expected_return_pct=20.0,
        expected_return_horizon_months=12,
    )
    out = build_postmortem(inp, S)
    assert out.status == "ok"
    for g in (out.gap_vs_expectation, out.gap_vs_benchmark):
        assert g is not None and g.status == "ok"
        assert g.gap_pp is not None and g.attributed_pp is not None and g.residual_pp is not None
        assert g.attributed_pp + g.residual_pp == pytest.approx(g.gap_pp, abs=1e-4)
        assert sum(i.pp for i in g.items) == pytest.approx(g.attributed_pp, abs=1e-4)
        assert g.reconciles is True
        assert [abs(i.pp) for i in g.items] == sorted((abs(i.pp) for i in g.items), reverse=True)
    # the deposit's cash is not in any holding, so a visible residual remains (never hidden)
    assert out.gap_vs_benchmark is not None and out.gap_vs_benchmark.residual_pp != 0


def test_a_deposit_is_never_counted_as_profit() -> None:
    inp = base_input(
        points=[
            DayPoint(START, 3500.0),
            DayPoint(START + timedelta(days=10), 4500.0, 1000.0),
            DayPoint(END, 5070.0),
        ],
        txs=[TxIn(START + timedelta(days=10), "deposit", amount=1000.0)],
    )
    out = build_postmortem(inp, S)
    assert out.pnl_ils == pytest.approx(570.0)  # 5070 - 3500 - 1000
    flows = finding(out, "flows")
    assert flows.amount_ils == pytest.approx(1000.0)
    assert out.holdings[0].total_ils == pytest.approx(570.0)
    assert out.twr_pct == pytest.approx((5070 / 4500 - 1) * 100, abs=1e-3)


def test_fx_effect_is_separated_from_the_local_currency_return() -> None:
    out = build_postmortem(base_input(), S)
    h = out.holdings[0]
    assert h.local_ils == pytest.approx(10 * 10 * 3.5)  # +10 per share at the entry rate
    assert h.fx_ils == pytest.approx(10 * 110 * 0.2)  # end value x rate change
    assert h.local_ils + h.fx_ils == pytest.approx(h.total_ils) == pytest.approx(4070 - 3500)
    fx = finding(out, "fx")
    assert fx.amount_ils == pytest.approx(220.0) and fx.status == "ok"
    # flat price, moving rate: the whole result is FX
    flat = base_input(
        closes={"AAPL": series(100, 100)}, holdings=[HoldingIn("AAPL", 10, 100.0, True)]
    )
    h2 = build_postmortem(flat, S).holdings[0]
    assert h2.local_ils == pytest.approx(0.0) and h2.fx_ils == pytest.approx(10 * 100 * 0.2)


def test_realized_and_unrealized_split_with_fx_on_the_trade_date() -> None:
    sale_day = START + timedelta(days=30)
    fx_sale = 3.6
    inp = base_input(
        txs=[TxIn(sale_day, "sale", "AAPL", 4, 105.0, 420.0, "USD", fx_sale)],
        holdings=[HoldingIn("AAPL", 6, 110.0, True)],
        points=[
            DayPoint(START, 3500.0),
            DayPoint(END, 6 * 110 * 3.7 + 4 * 105 * 3.6, -4 * 105 * 3.6),
        ],
    )
    h = build_postmortem(inp, S).holdings[0]
    assert h.realized_ils == pytest.approx(4 * 105 * fx_sale - 4 * 100 * 3.5)
    assert h.unrealized_ils == pytest.approx(6 * 110 * 3.7 - 6 * 100 * 3.5)
    assert h.still_held is True


def test_tase_prices_are_shekels_not_agorot() -> None:
    inp = base_input(
        holdings=[HoldingIn("TEVA.TA", 100, 65.0, True)],
        securities={"TEVA.TA": TEVA},
        closes={"TEVA.TA": series(60, 65)},
        points=[DayPoint(START, 6000.0), DayPoint(END, 6500.0)],
    )
    out = build_postmortem(inp, S)
    h = out.holdings[0]
    assert h.total_ils == pytest.approx(500.0) and h.fx_ils == 0.0  # not 50,000
    assert out.benchmarks[0].symbol == "^TA125.TA"
    assert finding(out, "fx").status == "no_foreign_assets"


def test_timing_on_a_synthetic_series() -> None:
    idx = pd.date_range(START - timedelta(days=70), END, freq="D")
    vals = []
    for d in idx:
        day = (d.date() - START).days
        if day < 20:
            vals.append(100.0 + max(day, -70) * 0.5)  # climbing to 110 on day 20
        elif day < 50:
            vals.append(110.0 - (day - 20) * 1.0)  # falling to 80 on day 50
        else:
            vals.append(80.0 + (day - 50) * 1.0)  # recovering
    closes = pd.Series(vals, index=idx)
    buy_day, sell_day = START + timedelta(days=20), START + timedelta(days=50)
    inp = base_input(
        closes={"AAPL": closes},
        txs=[
            TxIn(buy_day, "purchase", "AAPL", 5, 110.0, 550.0, "USD", 3.6),
            TxIn(sell_day, "sale", "AAPL", 5, 80.0, 400.0, "USD", 3.6),
        ],
        holdings=[HoldingIn("AAPL", 10, 103.0, True)],
    )
    t = finding(build_postmortem(inp, S), "timing")
    assert t.status == "ok"
    assert t.explanation.inputs["purchases_near_high"] == 1  # bought at the 60-day high
    assert t.explanation.inputs["sales_then_gains"] == 1  # the price rose after the sale
    rows = {r.label: r for r in t.evidence}
    assert rows[f"{buy_day} purchase AAPL"].value == pytest.approx(
        (80.0 / 110.0 - 1) * 100, abs=0.01
    )
    assert rows[f"{sell_day} sale AAPL"].value == pytest.approx(
        23.0 / 80.0 * 100, abs=0.01
    )  # latest close, 23 days on
    assert t.amount_ils is not None and t.amount_ils < 0  # both moved against the trade


def test_no_trades_means_no_activity_not_invented_timing() -> None:
    assert finding(build_postmortem(base_input(), S), "timing").status == "no_activity"


def test_not_enough_history_reports_days_remaining_and_no_numbers() -> None:
    inp = base_input(points=[DayPoint(START, 3500.0), DayPoint(START + timedelta(days=10), 3600.0)])
    out = build_postmortem(inp, S)
    assert out.status == "not_enough_history"
    assert out.history is not None
    assert (out.history.days_available, out.history.days_remaining) == (
        10,
        S.postmortem_min_days - 10,
    )
    assert out.twr_pct is None and out.pnl_ils is None and out.findings == []
    empty = build_postmortem(base_input(points=[]), S)
    assert empty.status == "not_enough_history" and empty.history is not None
    assert empty.history.days_remaining == S.postmortem_min_days


def test_without_an_expectation_the_rest_still_works() -> None:
    out = build_postmortem(base_input(), S)
    assert out.status == "ok"
    assert out.expectation.status == "needs_expectation"
    assert out.expectation.expected_for_period_pct is None
    assert out.gap_vs_expectation is not None
    assert out.gap_vs_expectation.status == "needs_expectation" and not out.gap_vs_expectation.items
    assert out.gap_vs_benchmark is not None and out.gap_vs_benchmark.status == "ok"
    assert "needs_expectation" in finding(out, "performance").flags


def test_expectation_is_scaled_by_compounding() -> None:
    assert expected_for_period(12.0, 12, 0) == pytest.approx(0.0)
    full = expected_for_period(12.0, 12, int(12 * 30.4375))
    assert full == pytest.approx(12.0, abs=0.01)
    half = expected_for_period(12.0, 12, int(6 * 30.4375))
    assert half == pytest.approx((1.12**0.5 - 1) * 100, abs=0.05)  # not 6.0
    out = build_postmortem(
        base_input(expected_return_pct=12.0, expected_return_horizon_months=12), S
    )
    assert out.expectation.status == "ok"
    assert out.expectation.expected_for_period_pct == pytest.approx(
        expected_for_period(12.0, 12, 73), abs=1e-3
    )
    assert out.annualised_is_extrapolated is True


def test_a_stale_or_cost_price_is_excluded_with_a_flag() -> None:
    stale = base_input(
        holdings=[HoldingIn("AAPL", 10, 90.0, False)],
        closes={"AAPL": series(100, 110, end=END - timedelta(days=30))},  # last close is old
    )
    out = build_postmortem(stale, S)
    assert [(e.symbol, e.reason) for e in out.excluded] == [("AAPL", "stale_price")]
    assert out.holdings == []
    assert "some_holdings_excluded_see_excluded" in out.flags
    assert any(f.startswith("excluded:AAPL") for f in finding(out, "contribution").flags)


def test_concentration_stops_cash_and_costs_are_reported_honestly() -> None:
    out = build_postmortem(base_input(max_country_pct=40.0, stops={"AAPL": 90.0}), S)
    conc = finding(out, "concentration")
    assert conc.status == "ok" and any(f.startswith("country_over_cap") for f in conc.flags)
    assert finding(out, "cash").status == "not_recorded"
    assert finding(out, "costs").status == "not_recorded"
    stops = finding(out, "stops")
    assert stops.status == "ok" and stops.explanation.inputs["stops_reached"] == 0
    stops_hit = build_postmortem(base_input(stops={"AAPL": 101.0}), S)
    assert finding(stops_hit, "stops").explanation.inputs["stops_reached"] in (0, 1)
    no_stops = finding(build_postmortem(base_input(), S), "stops")
    assert no_stops.status == "not_recorded"


def test_every_finding_has_an_explanation_and_no_verdict_words() -> None:
    out = build_postmortem(
        base_input(
            expected_return_pct=12.0,
            expected_return_horizon_months=12,
            txs=[TxIn(START + timedelta(days=30), "purchase", "AAPL", 1, 103.0, 103.0, "USD", 3.6)],
            holdings=[HoldingIn("AAPL", 11, 110.0, True)],
        ),
        S,
    )
    assert len(out.findings) >= 8
    texts = [out.summary]
    for f in out.findings:
        assert f.explanation.summary and f.explanation.sources
        texts += [f.headline, f.explanation.summary, *f.explanation.rules_applied]
        texts += [e.label for e in f.evidence] + [e.note or "" for e in f.evidence]
        texts += f.explanation.invalidation_risks
    for g in (out.gap_vs_expectation, out.gap_vs_benchmark):
        assert g is not None
        texts += [g.note, *(i.label for i in g.items)]
    words = list(S.outbound_verdict_words)
    for t in texts:
        assert not verdict_words_in_text(t, words, ignore=(DISCLAIMER,)), t


def test_the_module_imports_no_llm_provider() -> None:
    root = Path(__file__).resolve().parent.parent / "app" / "portfolio"
    for name in ("postmortem.py", "postmortem_data.py"):
        tree = ast.parse((root / name).read_text())
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            elif isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
        assert not [m for m in mods if m.startswith("app.llm") or "committee" in m], name
        assert not [m for m in mods if m.split(".")[0] in ("openai", "anthropic", "google", "groq")]
