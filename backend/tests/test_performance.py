from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.portfolio.performance import (
    DayPoint,
    benchmark_pct,
    combine_points,
    cumulative_series,
    grouped_bars,
    month_start,
    period_result,
    week_start,
)


def D(day: int, month: int = 3) -> date:
    return date(2026, month, day)


def test_plain_growth_without_flows() -> None:
    pts = [DayPoint(D(2), 1000), DayPoint(D(3), 1100), DayPoint(D(4), 1210)]
    res = period_result(pts)
    assert res.pnl_ils == pytest.approx(210)
    assert res.pct == pytest.approx(21.0)  # 10% then 10% chained
    assert cumulative_series(pts) == [
        (D(2), 0.0),
        (D(3), pytest.approx(10.0)),
        (D(4), pytest.approx(21.0)),
    ]


def test_deposit_is_not_profit() -> None:
    # Day 3: user deposits 1000 and the market is flat. Value doubles, profit must be 0.
    pts = [DayPoint(D(2), 1000), DayPoint(D(3), 2000, net_flow_ils=1000)]
    res = period_result(pts)
    assert res.pnl_ils == pytest.approx(0.0)
    assert res.pct == pytest.approx(0.0)


def test_deposit_then_gain_uses_flow_at_start_of_day() -> None:
    # Deposit 1000 at the start of day 3, then +10% on 2000 -> value 2200, profit 200, TWR 10%
    pts = [DayPoint(D(2), 1000), DayPoint(D(3), 2200, net_flow_ils=1000)]
    res = period_result(pts)
    assert res.pnl_ils == pytest.approx(200)
    assert res.pct == pytest.approx(10.0)


def test_withdrawal_is_not_a_loss() -> None:
    pts = [DayPoint(D(2), 2000), DayPoint(D(3), 1000, net_flow_ils=-1000)]
    res = period_result(pts)
    assert res.pnl_ils == pytest.approx(0.0)
    assert res.pct == pytest.approx(0.0)


def test_twr_is_independent_of_flow_size() -> None:
    small = [
        DayPoint(D(2), 1000),
        DayPoint(D(3), 1100),
        DayPoint(D(4), (1100 + 10) * 1.05, 10),
        DayPoint(D(5), 1.02 * (1100 + 10) * 1.05),
    ]
    big = [
        DayPoint(D(2), 1000),
        DayPoint(D(3), 1100),
        DayPoint(D(4), (1100 + 5000) * 1.05, 5000),
        DayPoint(D(5), 1.02 * (1100 + 5000) * 1.05),
    ]
    assert period_result(small).pct == pytest.approx(period_result(big).pct)
    assert period_result(small).pct == pytest.approx((1.1 * 1.05 * 1.02 - 1) * 100)


def test_period_start_clamps_to_days_on_or_after_start() -> None:
    pts = [DayPoint(D(2), 1000), DayPoint(D(3), 1100), DayPoint(D(4), 1210), DayPoint(D(5), 1210)]
    res = period_result(pts, start=D(4))
    assert res.pnl_ils == pytest.approx(110)
    assert res.pct == pytest.approx(10.0)


def test_weekly_and_monthly_bars() -> None:
    # 2026-03-02 is a Monday. Week 1: 2..6 ; week 2 starts on the 9th.
    pts = [
        DayPoint(D(2), 1000),
        DayPoint(D(3), 1010),
        DayPoint(D(6), 1100),
        DayPoint(D(9), 1100 + 500, net_flow_ils=500),  # deposit on Monday of week 2
        DayPoint(D(10), 1650),
        DayPoint(D(1, 4), 1650),
    ]
    weeks = grouped_bars(pts, week_start, 12)
    assert [k for k, _ in weeks][:2] == [D(2), D(9)]
    assert weeks[0][1].pnl_ils == pytest.approx(100)
    assert weeks[0][1].pct == pytest.approx(10.0)
    assert weeks[1][1].pnl_ils == pytest.approx(50)  # the 500 deposit is not profit
    months = grouped_bars(pts, month_start, 12)
    assert [k for k, _ in months] == [date(2026, 3, 1), date(2026, 4, 1)]
    assert months[0][1].pnl_ils == pytest.approx(150)
    assert grouped_bars(pts, week_start, 1)[0][0] == weeks[-1][0]


def test_week_start_is_monday() -> None:
    assert week_start(date(2026, 3, 8)) == date(2026, 3, 2)  # Sunday
    assert week_start(date(2026, 3, 2)) == date(2026, 3, 2)


def test_empty_and_single_point() -> None:
    assert period_result([]).pct == 0.0
    assert period_result([DayPoint(D(2), 500)]).pnl_ils == 0.0
    assert cumulative_series([]) == []
    assert combine_points([]) == []


def test_combined_portfolio_joining_later_is_not_profit() -> None:
    a = [DayPoint(D(2), 1000), DayPoint(D(3), 1000), DayPoint(D(4), 1000)]
    b = [DayPoint(D(3), 500), DayPoint(D(4), 500)]  # starts tracking on the 3rd
    combined = combine_points([a, b])
    assert [(p.date, p.value_ils, p.net_flow_ils) for p in combined] == [
        (D(2), 1000, 0),
        (D(3), 1500, 500),
        (D(4), 1500, 0),
    ]
    assert period_result(combined).pnl_ils == pytest.approx(0.0)
    assert period_result(combined).pct == pytest.approx(0.0)


def test_combined_forward_fills_missing_days() -> None:
    a = [DayPoint(D(2), 100), DayPoint(D(3), 110), DayPoint(D(4), 121)]
    b = [DayPoint(D(2), 100), DayPoint(D(4), 100)]
    combined = combine_points([a, b])
    assert [p.value_ils for p in combined] == [200, 210, 221]


def test_benchmark_pct() -> None:
    idx = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04"])
    closes = pd.Series([100.0, 105.0, 110.0], index=idx)
    assert benchmark_pct(closes, D(2), D(4)) == pytest.approx(10.0)
    assert benchmark_pct(closes, D(2), D(2)) == pytest.approx(0.0)
    assert benchmark_pct(closes, D(2), D(10)) == pytest.approx(10.0)  # at-or-before lookup
    assert benchmark_pct(closes, date(2026, 1, 1), D(4)) is None  # no data before the start
    assert benchmark_pct(None, D(2), D(4)) is None
