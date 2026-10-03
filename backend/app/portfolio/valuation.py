"""Value a portfolio from cached quotes; daily snapshots; tracking start."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import Holding, Portfolio, PortfolioSnapshot, PriceQuote, Security, Transaction
from app.providers.fx_provider import get_usd_ils, to_ils
from app.timeutil import local_today

FLOW_SIGN = {"buy": 1.0, "deposit": 1.0, "sell": -1.0, "withdrawal": -1.0}


@dataclass
class ValuedHolding:
    holding: Holding
    security: Security
    price: float
    currency: str
    stale: bool
    day_change_pct: float
    value_native: float
    value_ils: float
    value_usd: float
    day_pnl_ils: float
    pnl_ils: float | None
    pnl_usd: float | None
    pnl_pct: float | None
    as_of: datetime | None


@dataclass
class PortfolioValuation:
    holdings: list[ValuedHolding] = field(default_factory=list)
    usd_ils: float = 1.0
    total_ils: float = 0.0
    total_usd: float = 0.0
    day_pnl_ils: float = 0.0
    as_of: datetime | None = None


def value_portfolio(
    db: Session, portfolio: Portfolio, settings: Settings | None = None
) -> PortfolioValuation:
    s = settings or get_settings()
    usd_ils = get_usd_ils(db, s)
    holdings = db.exec(select(Holding).where(Holding.portfolio_id == portfolio.id)).all()
    out = PortfolioValuation(usd_ils=usd_ils)
    for h in holdings:
        sec = db.get(Security, h.symbol)
        if sec is None:
            continue
        quote = db.get(PriceQuote, h.symbol)
        if quote is not None:
            price, cur, stale, chg, as_of = (
                quote.price,
                quote.currency or sec.currency,
                False,
                quote.change_pct or 0.0,
                quote.as_of,
            )
        else:
            price, cur, stale, chg, as_of = h.avg_cost or 0.0, h.cost_currency, True, 0.0, None
        native = price * h.quantity
        v_ils = to_ils(native, cur, usd_ils)
        day_pnl = v_ils - v_ils / (1 + chg / 100.0) if chg > -100 else 0.0
        pnl_ils = pnl_usd = pnl_pct = None
        if h.avg_cost is not None and not stale:
            cost_ils = to_ils(h.avg_cost * h.quantity, h.cost_currency, usd_ils)
            pnl_ils = v_ils - cost_ils
            pnl_usd = pnl_ils / usd_ils
            pnl_pct = (pnl_ils / cost_ils * 100.0) if cost_ils else 0.0
        elif h.avg_cost is not None:
            pnl_ils, pnl_usd, pnl_pct = 0.0, 0.0, 0.0
        out.holdings.append(
            ValuedHolding(
                h,
                sec,
                price,
                cur,
                stale,
                chg,
                native,
                v_ils,
                v_ils / usd_ils,
                day_pnl,
                pnl_ils,
                pnl_usd,
                pnl_pct,
                as_of,
            )
        )
        if as_of is not None and (out.as_of is None or as_of > out.as_of):
            out.as_of = as_of
    out.total_ils = sum(v.value_ils for v in out.holdings)
    out.total_usd = out.total_ils / usd_ils if usd_ils else 0.0
    out.day_pnl_ils = sum(v.day_pnl_ils for v in out.holdings)
    return out


def flow_ils(tx: Transaction) -> float:
    """Signed external flow in ILS (buy/deposit in, sell/withdrawal out) using the stored FX."""
    return FLOW_SIGN.get(tx.type, 0.0) * tx.amount * tx.fx_to_ils


def net_flow_for_date(db: Session, portfolio_id: int, d: date) -> float:
    txs = db.exec(
        select(Transaction).where(Transaction.portfolio_id == portfolio_id, Transaction.date == d)
    ).all()
    return sum(flow_ils(t) for t in txs)


def take_snapshot(
    db: Session, portfolio: Portfolio, d: date | None = None, settings: Settings | None = None
) -> PortfolioSnapshot | None:
    """Upsert the end-of-day snapshot. Only for portfolios whose tracking has started."""
    day = d or local_today()
    if portfolio.tracking_started_at is None or day < portfolio.tracking_started_at:
        return None
    assert portfolio.id is not None
    val = value_portfolio(db, portfolio, settings)
    flow = net_flow_for_date(db, portfolio.id, day)
    snap = db.exec(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id == portfolio.id, PortfolioSnapshot.date == day
        )
    ).first()
    if snap is None:
        snap = PortfolioSnapshot(
            portfolio_id=portfolio.id,
            date=day,
            value_ils=val.total_ils,
            value_usd=val.total_usd,
            net_flow_ils=flow,
        )
    else:
        snap.value_ils, snap.value_usd, snap.net_flow_ils = val.total_ils, val.total_usd, flow
    db.add(snap)
    db.commit()
    return snap


def ensure_tracking_started(db: Session, portfolio: Portfolio, today: date | None = None) -> bool:
    """Set `tracking_started_at` on the first confirmed import / manual add and take the baseline.

    Returns True if tracking was started by this call. The baseline is the portfolio value on that
    day (never the broker's cost basis), so earlier gains are not counted.
    """
    if portfolio.tracking_started_at is not None:
        return False
    day = today or local_today()
    portfolio.tracking_started_at = day
    db.add(portfolio)
    db.commit()
    take_snapshot(db, portfolio, day)
    return True


def all_user_snapshots(db: Session, portfolio_id: int) -> list[PortfolioSnapshot]:
    return list(
        db.exec(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.portfolio_id == portfolio_id)
            .order_by(col(PortfolioSnapshot.date))
        ).all()
    )


__all__ = [
    "PortfolioValuation",
    "ValuedHolding",
    "all_user_snapshots",
    "ensure_tracking_started",
    "flow_ils",
    "net_flow_for_date",
    "take_snapshot",
    "value_portfolio",
]
