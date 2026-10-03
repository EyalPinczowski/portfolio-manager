"""Compose the /summary payload from valuations, snapshots and benchmark history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import Portfolio
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
from app.portfolio.valuation import (
    PortfolioValuation,
    all_user_snapshots,
    net_flow_for_date,
    value_portfolio,
)
from app.providers.base import HistoryProvider
from app.scheduler.calendars import markets_status
from app.timeutil import as_utc, local_today, utcnow


def portfolio_points(
    db: Session, portfolio: Portfolio, valuation: PortfolioValuation, today: date
) -> list[DayPoint]:
    """Snapshots plus a live point for today (flows of today included)."""
    if portfolio.tracking_started_at is None or portfolio.id is None:
        return []
    pts = {
        s.date: DayPoint(s.date, s.value_ils, s.net_flow_ils)
        for s in all_user_snapshots(db, portfolio.id)
    }
    if today >= portfolio.tracking_started_at:
        pts[today] = DayPoint(
            today, valuation.total_ils, net_flow_for_date(db, portfolio.id, today)
        )
    return sorted(pts.values(), key=lambda p: p.date)


def _pnl(res_pnl_ils: float, pct: float, usd_ils: float) -> dict[str, float]:
    return {
        "ils": round(res_pnl_ils, 2),
        "usd": round(res_pnl_ils / usd_ils, 2),
        "pct": round(pct, 4),
    }


def build_summary(
    db: Session,
    portfolios: Sequence[Portfolio],
    history: HistoryProvider | None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    now_dt = now or utcnow()
    today = local_today(now_dt)
    valuations = [value_portfolio(db, p, s) for p in portfolios]
    usd_ils = valuations[0].usd_ils if valuations else s.fx_fallback_usd_ils
    total_ils = sum(v.total_ils for v in valuations)
    day_ils = sum(v.day_pnl_ils for v in valuations)
    prev = total_ils - day_ils
    per_portfolio = [
        portfolio_points(db, p, v, today) for p, v in zip(portfolios, valuations, strict=True)
    ]
    points = (
        combine_points(per_portfolio)
        if len(portfolios) > 1
        else (per_portfolio[0] if per_portfolio else [])
    )
    week = period_result(points, week_start(today))
    month = period_result(points, month_start(today))
    since = period_result(points, None)
    starts = [p.tracking_started_at for p in portfolios if p.tracking_started_at is not None]
    since_date = min(starts) if starts else None
    series: list[dict[str, Any]] = []
    if points and since_date is not None:
        sp = _benchmark(history, s.benchmark_sp500, since_date, today, s)
        ta = _benchmark(history, s.benchmark_ta125, since_date, today, s)
        for d, pct in cumulative_series(points):
            series.append(
                {
                    "date": d.isoformat(),
                    "pct": round(pct, 4),
                    "sp500_pct": benchmark_pct(sp, since_date, d),
                    "ta125_pct": benchmark_pct(ta, since_date, d),
                }
            )
    as_of_candidates = [v.as_of for v in valuations if v.as_of is not None]
    as_of = max(as_of_candidates) if as_of_candidates else now_dt
    return {
        "value": {"ils": round(total_ils, 2), "usd": round(total_ils / usd_ils, 2)},
        "day_pnl": _pnl(day_ils, (day_ils / prev * 100.0) if prev > 0 else 0.0, usd_ils),
        "week_pnl": _pnl(week.pnl_ils, week.pct, usd_ils),
        "month_pnl": _pnl(month.pnl_ils, month.pct, usd_ils),
        "since_start_pnl": _pnl(since.pnl_ils, since.pct, usd_ils),
        "since_start_date": since_date.isoformat() if since_date else None,
        "weekly_bars": [
            {"week_start": k.isoformat(), "pnl_ils": round(r.pnl_ils, 2), "pct": round(r.pct, 4)}
            for k, r in grouped_bars(points, week_start, 12)
        ],
        "monthly_bars": [
            {"month": k.strftime("%Y-%m"), "pnl_ils": round(r.pnl_ils, 2), "pct": round(r.pct, 4)}
            for k, r in grouped_bars(points, month_start, 12)
        ],
        "since_start_series": series,
        "as_of": as_utc(as_of).astimezone(UTC).isoformat(),
        "markets": markets_status(now_dt, s),
    }


def _benchmark(
    history: HistoryProvider | None, symbol: str, since: date, today: date, s: Settings
) -> pd.Series | None:
    if history is None:
        return None
    days = max(s.history_days, (today - since).days + 10)
    try:
        df = history.get_history(symbol, days)
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    return df["Close"]
