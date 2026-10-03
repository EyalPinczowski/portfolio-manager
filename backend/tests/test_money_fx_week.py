"""FX staleness (no silent 3.6) and the Sunday-based week in Asia/Jerusalem."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import Settings, get_settings
from app.db import new_session
from app.models import Holding, Portfolio, PortfolioSnapshot, PriceQuote, Transaction, User
from app.portfolio.performance import DayPoint, grouped_bars, period_result, week_start
from app.portfolio.summary import build_summary
from app.providers.fx_provider import get_fx_rate
from app.timeutil import utcnow

SignupFn = Callable[..., TestClient]


# ---------------------------------------------------------------- FX
def test_fx_without_any_data_is_flagged_stale_not_silent(db: Session) -> None:
    rate = get_fx_rate(db)
    assert rate.stale is True and rate.source == "config"
    assert rate.usd_ils == get_settings().fx_fallback_usd_ils


def test_fx_prefers_last_known_transaction_rate_over_the_constant(db: Session) -> None:
    user = User(email="f@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p")
    db.add(p)
    db.commit()
    db.add(
        Transaction(
            portfolio_id=p.id or 0,
            type="buy",
            amount=10,
            currency="USD",
            fx_to_ils=3.71,
            date=date(2026, 3, 2),
        )
    )
    db.commit()
    rate = get_fx_rate(db)
    assert rate.usd_ils == 3.71 and rate.stale and rate.source == "last_transaction"


def test_fx_quote_freshness(db: Session) -> None:
    now = utcnow()
    db.add(PriceQuote(symbol="ILS=X", price=3.4, currency="ILS", as_of=now - timedelta(hours=1)))
    db.commit()
    fresh = get_fx_rate(db, now=now)
    assert (fresh.usd_ils, fresh.stale, fresh.source) == (3.4, False, "quote")
    old = get_fx_rate(db, now=now + timedelta(hours=100))
    assert old.usd_ils == 3.4 and old.stale and old.source == "stale_quote"


def _summary_client(signup: SignupFn) -> tuple[TestClient, int]:
    c = signup()
    r = c.post("/api/portfolios", json={"name": "main", "base_currency": "ILS"})
    return c, int(r.json()["id"])


def test_summary_reports_fx_stale(signup: SignupFn) -> None:
    c, pid = _summary_client(signup)
    assert c.get(f"/api/portfolios/{pid}/summary").json()["fx_stale"] is True  # no ILS=X yet
    with new_session() as db:
        db.merge(PriceQuote(symbol="ILS=X", price=3.5, currency="ILS", as_of=utcnow()))
        db.commit()
    assert c.get(f"/api/portfolios/{pid}/summary").json()["fx_stale"] is False
    with new_session() as db:
        db.merge(
            PriceQuote(
                symbol="ILS=X", price=3.5, currency="ILS", as_of=utcnow() - timedelta(days=10)
            )
        )
        db.commit()
    assert c.get(f"/api/portfolios/{pid}/summary").json()["fx_stale"] is True


# ---------------------------------------------------------------- week start
def test_week_starts_on_sunday_by_default() -> None:
    assert week_start(date(2026, 3, 8)) == date(2026, 3, 8)  # Sunday
    assert week_start(date(2026, 3, 14)) == date(2026, 3, 8)  # Saturday
    assert week_start(date(2026, 3, 9)) == date(2026, 3, 8)  # Monday
    assert week_start(date(2026, 3, 15)) == date(2026, 3, 15)


def test_week_start_day_is_configurable() -> None:
    assert week_start(date(2026, 3, 8), "monday") == date(2026, 3, 2)
    assert week_start(date(2026, 3, 14), "saturday") == date(2026, 3, 14)
    assert Settings().week_start_day == "sunday"


def test_weekly_bars_bucket_on_sundays() -> None:
    d = date
    pts = [
        DayPoint(d(2026, 3, 1), 1000),  # Sunday
        DayPoint(d(2026, 3, 5), 1100),  # Thursday
        DayPoint(d(2026, 3, 8), 1210),  # next Sunday: belongs to the new week
        DayPoint(d(2026, 3, 12), 1210),
    ]
    bars = grouped_bars(pts, week_start, 12)
    assert [k for k, _ in bars] == [d(2026, 3, 1), d(2026, 3, 8)]
    assert all(k.weekday() == 6 for k, _ in bars)
    assert bars[0][1].pnl_ils == pytest.approx(100)
    assert bars[1][1].pnl_ils == pytest.approx(110)


JLM_OFFSET = timedelta(hours=2)  # Israel standard time (UTC+2) in early March


def _jerusalem(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return (datetime(y, m, d, hh, mm) - JLM_OFFSET).replace(tzinfo=UTC)


def _tracked_portfolio(db: Session) -> Portfolio:
    user = User(email="w@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p", tracking_started_at=date(2026, 2, 20))
    db.add(p)
    db.commit()
    db.add(Holding(portfolio_id=p.id or 0, symbol="TEVA.TA", quantity=10, avg_cost=100))
    db.add(PriceQuote(symbol="TEVA.TA", price=110.0, currency="ILS", as_of=utcnow()))
    db.add(PriceQuote(symbol="ILS=X", price=3.5, currency="ILS", as_of=utcnow()))
    for day, val in (
        (date(2026, 3, 5), 1000.0),
        (date(2026, 3, 6), 1000.0),
        (date(2026, 3, 7), 1100.0),
    ):
        db.add(PortfolioSnapshot(portfolio_id=p.id or 0, date=day, value_ils=val, value_usd=1))
    db.commit()
    return p


def test_summary_week_start_uses_jerusalem_boundary(db: Session) -> None:
    p = _tracked_portfolio(db)
    # Saturday 2026-03-07 23:30 in Jerusalem (21:30 UTC): still the week of Sunday 03-01
    sat = build_summary(db, [p], None, now=_jerusalem(2026, 3, 7, 23, 30))
    assert sat["week_start"] == date(2026, 3, 1)
    # Sunday 00:30 in Jerusalem is still Saturday 22:30 UTC: the new week has begun
    sun = build_summary(db, [p], None, now=_jerusalem(2026, 3, 8, 0, 30))
    assert sun["week_start"] == date(2026, 3, 8)
    # Late Saturday UTC but already Sunday in Jerusalem must not stay on the old week
    assert sun["week_start"].weekday() == 6


def test_week_pnl_resets_on_sunday(db: Session) -> None:
    p = _tracked_portfolio(db)  # live value 1100; snapshots 1000 Thu/Fri, 1100 Sat
    sat = build_summary(db, [p], None, now=_jerusalem(2026, 3, 7, 12, 0))
    assert sat["week_pnl"]["ils"] == pytest.approx(100.0)
    sun = build_summary(db, [p], None, now=_jerusalem(2026, 3, 8, 12, 0))
    assert sun["week_pnl"]["ils"] == pytest.approx(0.0)  # Sunday is the new week's baseline
    assert [b["week_start"] for b in sun["weekly_bars"]][-2:] == [
        date(2026, 3, 1),
        date(2026, 3, 8),
    ]


def test_summary_api_exposes_week_start_and_fx_stale(signup: SignupFn) -> None:
    c, pid = _summary_client(signup)
    body = c.get(f"/api/portfolios/{pid}/summary").json()
    ws = date.fromisoformat(body["week_start"])
    assert ws.weekday() == 6 and 0 <= (date.today() - ws).days <= 7
    assert isinstance(body["fx_stale"], bool)


def test_period_result_unaffected_by_helper_signature() -> None:
    assert period_result([]).pct == 0.0
