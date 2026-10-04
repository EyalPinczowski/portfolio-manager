"""TWR must not lose flows on days without a snapshot (missed 23:59 job)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Holding, Portfolio, PortfolioSnapshot, PriceQuote, Transaction, User
from app.portfolio.performance import period_result
from app.portfolio.summary import portfolio_points
from app.portfolio.valuation import (
    take_catchup_snapshots,
    take_snapshot,
    value_portfolio,
)
from app.scheduler.__main__ import build_scheduler
from app.timeutil import utcnow

D1 = date(2026, 3, 2)
D2 = D1 + timedelta(days=1)
D3 = D1 + timedelta(days=2)


@pytest.fixture
def pf(db: Session) -> Portfolio:
    user = User(email="t@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p", tracking_started_at=D1)
    db.add(p)
    db.commit()
    db.add(Holding(portfolio_id=p.id or 0, symbol="TEVA.TA", quantity=10, avg_cost=100))
    db.add(PriceQuote(symbol="TEVA.TA", price=100.0, currency="ILS", as_of=utcnow()))
    db.add(PriceQuote(symbol=get_settings().fx_symbol, price=3.5, currency="ILS"))
    db.commit()
    return p


def _deposit(db: Session, p: Portfolio, d: date, amount: float) -> None:
    db.add(
        Transaction(portfolio_id=p.id or 0, type="deposit", amount=amount, currency="ILS", date=d)
    )
    db.commit()


def _set_qty(db: Session, qty: float) -> None:
    h = db.exec(select(Holding)).one()
    h.quantity = qty
    db.add(h)
    db.commit()


def test_snapshot_after_a_gap_day_carries_the_gap_days_deposit(db: Session, pf: Portfolio) -> None:
    take_snapshot(db, pf, D1)  # value 1000
    _deposit(db, pf, D2, 1000.0)  # the D2 23:59 snapshot is missed
    _set_qty(db, 20)
    snap = take_snapshot(db, pf, D3)
    assert snap is not None and snap.net_flow_ils == pytest.approx(1000.0)
    pts = portfolio_points(db, pf, value_portfolio(db, pf), D3)
    assert [p.date for p in pts] == [D1, D3]
    res = period_result(pts)
    assert res.pnl_ils == pytest.approx(0.0)  # the deposit is not profit
    assert res.pct == pytest.approx(0.0)


def test_points_recompute_flows_from_transactions_for_old_bad_snapshots(
    db: Session, pf: Portfolio
) -> None:
    # A snapshot written by the old logic: the D2 deposit was dropped (net_flow 0 on D3).
    db.add(PortfolioSnapshot(portfolio_id=pf.id or 0, date=D1, value_ils=1000, value_usd=285))
    db.add(PortfolioSnapshot(portfolio_id=pf.id or 0, date=D3, value_ils=2000, value_usd=571))
    db.commit()
    _deposit(db, pf, D2, 1000.0)
    _set_qty(db, 20)
    pts = portfolio_points(db, pf, value_portfolio(db, pf), D3)
    assert period_result(pts).pnl_ils == pytest.approx(0.0)


def test_live_point_today_includes_flows_since_last_snapshot(db: Session, pf: Portfolio) -> None:
    take_snapshot(db, pf, D1)
    _deposit(db, pf, D2, 500.0)
    _deposit(db, pf, D3, 500.0)
    _set_qty(db, 20)
    pts = portfolio_points(db, pf, value_portfolio(db, pf), D3)  # today = D3, no D2/D3 snapshot
    assert pts[-1].net_flow_ils == pytest.approx(1000.0)
    assert period_result(pts).pnl_ils == pytest.approx(0.0)


def test_catchup_snapshot_fills_yesterday_once(db: Session, pf: Portfolio) -> None:
    take_snapshot(db, pf, D1)
    _deposit(db, pf, D2, 1000.0)
    _set_qty(db, 20)
    assert take_catchup_snapshots(db, today=D3 + timedelta(days=1)) == 1  # yesterday = D3
    assert take_catchup_snapshots(db, today=D3 + timedelta(days=1)) == 0  # idempotent
    snaps = {s.date: s for s in db.exec(select(PortfolioSnapshot)).all()}
    assert set(snaps) == {D1, D3}
    assert snaps[D3].net_flow_ils == pytest.approx(1000.0)


def test_catchup_skips_untracked_and_up_to_date_portfolios(db: Session, pf: Portfolio) -> None:
    db.add(Portfolio(owner_id=pf.owner_id, name="untracked"))
    db.commit()
    take_snapshot(db, pf, D2)
    assert take_catchup_snapshots(db, today=D3) == 0  # yesterday (D2) already exists


def test_scheduler_jobs_have_misfire_grace_time() -> None:
    sched = build_scheduler()
    for job_id in ("quotes", "daily_snapshot", "scores"):
        job = sched.get_job(job_id)
        assert job is not None and job.misfire_grace_time and job.misfire_grace_time >= 60
    snap = sched.get_job("daily_snapshot")
    assert snap is not None and snap.misfire_grace_time >= 3600
