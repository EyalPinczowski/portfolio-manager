"""Time-weighted return (TWR), daily-chained, with flows at the start of the day.

For consecutive points prev -> cur (cur carries the flow of its own day):

    profit_d = V_cur - V_prev - F_cur             (flows are not profit)
    r_d      = V_cur / (V_prev + F_cur) - 1       (flow is invested from the start of the day)

Period return = product(1 + r_d) - 1 over the days in the period. The baseline of a period is the
last point before it, and the series begins at the portfolio's first tracked day, so gains made
before the user started using the app are never counted.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

import pandas as pd


@dataclass(frozen=True)
class DayPoint:
    date: date
    value_ils: float
    net_flow_ils: float = 0.0


@dataclass(frozen=True)
class PeriodResult:
    pnl_ils: float
    pct: float


def day_profit_and_return(prev: DayPoint, cur: DayPoint) -> tuple[float, float]:
    profit = cur.value_ils - prev.value_ils - cur.net_flow_ils
    base = prev.value_ils + cur.net_flow_ils
    ret = (cur.value_ils / base - 1.0) if base > 0 else 0.0
    return profit, ret


def _sorted(points: Sequence[DayPoint]) -> list[DayPoint]:
    return sorted(points, key=lambda p: p.date)


def period_result(points: Sequence[DayPoint], start: date | None = None) -> PeriodResult:
    """Profit (ILS, flow-adjusted) and chained TWR % for days with date >= `start`."""
    pts = _sorted(points)
    profit_total = 0.0
    growth = 1.0
    for prev, cur in pairwise(pts):
        if start is not None and cur.date < start:
            continue
        profit, ret = day_profit_and_return(prev, cur)
        profit_total += profit
        growth *= 1.0 + ret
    return PeriodResult(round(profit_total, 6), round((growth - 1.0) * 100.0, 6))


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def grouped_bars(
    points: Sequence[DayPoint], key: Callable[[date], date], limit: int
) -> list[tuple[date, PeriodResult]]:
    pts = _sorted(points)
    buckets: dict[date, list[tuple[float, float]]] = {}
    for prev, cur in pairwise(pts):
        buckets.setdefault(key(cur.date), []).append(day_profit_and_return(prev, cur))
    out: list[tuple[date, PeriodResult]] = []
    for k in sorted(buckets):
        growth = 1.0
        profit = 0.0
        for p, r in buckets[k]:
            profit += p
            growth *= 1.0 + r
        out.append((k, PeriodResult(round(profit, 6), round((growth - 1.0) * 100.0, 6))))
    return out[-limit:]


def cumulative_series(points: Sequence[DayPoint]) -> list[tuple[date, float]]:
    """Cumulative TWR % per date, starting at 0.0 on the first (baseline) day."""
    pts = _sorted(points)
    if not pts:
        return []
    out = [(pts[0].date, 0.0)]
    growth = 1.0
    for prev, cur in pairwise(pts):
        _, ret = day_profit_and_return(prev, cur)
        growth *= 1.0 + ret
        out.append((cur.date, round((growth - 1.0) * 100.0, 6)))
    return out


def combine_points(per_portfolio: Sequence[Sequence[DayPoint]]) -> list[DayPoint]:
    """Aggregate several portfolios into one daily series.

    Values are forward-filled after each portfolio's own start. A portfolio that starts after the
    earliest one enters the combined series as a deposit on its first day (so it adds no profit).
    """
    series = [_sorted(p) for p in per_portfolio if p]
    if not series:
        return []
    earliest = min(s[0].date for s in series)
    all_dates = sorted({pt.date for s in series for pt in s})
    totals: dict[date, list[float]] = {d: [0.0, 0.0] for d in all_dates}
    for s in series:
        by_date = {pt.date: pt for pt in s}
        last_value = 0.0
        for d in all_dates:
            if d < s[0].date:
                continue
            pt = by_date.get(d)
            if pt is not None:
                last_value = pt.value_ils
                totals[d][1] += pt.net_flow_ils
                if d == s[0].date and d != earliest:
                    totals[d][1] += pt.value_ils  # joins as a deposit
            totals[d][0] += last_value
    return [DayPoint(d, totals[d][0], totals[d][1]) for d in all_dates]


def benchmark_pct(closes: pd.Series | None, start: date, d: date) -> float | None:
    """% change of a benchmark close series between `start` and `d` (at-or-before lookups)."""
    if closes is None or closes.empty:
        return None
    idx = pd.DatetimeIndex(closes.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(closes.to_numpy(dtype="float64"), index=idx.normalize()).sort_index()
    base_slice = s.loc[: pd.Timestamp(start)]
    cur_slice = s.loc[: pd.Timestamp(d)]
    if base_slice.empty or cur_slice.empty:
        return None
    base = float(base_slice.iloc[-1])
    if base == 0:
        return None
    return round((float(cur_slice.iloc[-1]) / base - 1.0) * 100.0, 6)
