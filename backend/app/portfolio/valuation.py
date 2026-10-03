"""Value a portfolio from cached quotes; daily snapshots; tracking start."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import (
    Holding,
    HoldingsSnapshot,
    Portfolio,
    PortfolioSnapshot,
    PriceQuote,
    Security,
    Transaction,
)
from app.providers.fx_provider import SUPPORTED_CURRENCIES, get_fx_rate, to_ils
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
    price_source: str = "quote"  # quote | screenshot | cost (screenshot/cost are always stale)


@dataclass
class PortfolioValuation:
    holdings: list[ValuedHolding] = field(default_factory=list)
    usd_ils: float = 1.0
    fx_stale: bool = False
    total_ils: float = 0.0
    total_usd: float = 0.0
    day_pnl_ils: float = 0.0
    as_of: datetime | None = None


def screenshot_prices(
    db: Session, portfolio_id: int, limit: int = 10
) -> dict[str, tuple[float, str]]:
    """Newest per-portfolio broker price (native price, currency) for each symbol.

    Screenshot prices belong to the user who imported them. They are never written to the shared
    `PriceQuote` table, where they would look live and leak into other users' valuations.
    """
    out: dict[str, tuple[float, str]] = {}
    snaps = db.exec(
        select(HoldingsSnapshot)
        .where(HoldingsSnapshot.portfolio_id == portfolio_id)
        .order_by(col(HoldingsSnapshot.id).desc())
        .limit(limit)
    ).all()
    for snap in snaps:
        for row in snap.rows:
            sym, px, cur = row.get("symbol"), row.get("price_native"), row.get("currency")
            if (
                sym
                and sym not in out
                and px is not None
                and float(px) > 0
                and cur in ("ILS", "USD")
            ):
                out[str(sym)] = (float(px), str(cur))
    return out


def value_portfolio(
    db: Session, portfolio: Portfolio, settings: Settings | None = None
) -> PortfolioValuation:
    s = settings or get_settings()
    fx = get_fx_rate(db, s)
    usd_ils = fx.usd_ils
    holdings = db.exec(select(Holding).where(Holding.portfolio_id == portfolio.id)).all()
    out = PortfolioValuation(usd_ils=usd_ils, fx_stale=fx.stale)
    broker_prices = screenshot_prices(db, portfolio.id) if portfolio.id is not None else {}
    for h in holdings:
        sec = db.get(Security, h.symbol)
        if sec is None:
            continue
        quote = db.get(PriceQuote, h.symbol)
        if quote is not None and quote.currency.strip().upper() not in SUPPORTED_CURRENCIES:
            # Unknown currency (e.g. a legacy row stored without one): never guess, show stale.
            quote = None
        source = "quote"
        if quote is not None:
            price, cur, stale, chg, as_of = (
                quote.price,
                quote.currency,
                False,
                quote.change_pct or 0.0,
                quote.as_of,
            )
        elif h.symbol in broker_prices:
            (price, cur), stale, chg, as_of = broker_prices[h.symbol], True, 0.0, None
            source = "screenshot"
        else:
            price, cur, stale, chg, as_of = h.avg_cost or 0.0, h.cost_currency, True, 0.0, None
            source = "cost"
        native = price * h.quantity
        v_ils = to_ils(native, cur, usd_ils)
        day_pnl = v_ils - v_ils / (1 + chg / 100.0) if chg > -100 else 0.0
        pnl_ils = pnl_usd = pnl_pct = None
        if h.avg_cost is not None and source != "cost":
            cost_ils = to_ils(h.avg_cost * h.quantity, h.cost_currency, usd_ils)
            pnl_ils = v_ils - cost_ils
            pnl_usd = pnl_ils / usd_ils
            pnl_pct = (pnl_ils / cost_ils * 100.0) if cost_ils else 0.0
        elif h.avg_cost is not None:
            pnl_ils, pnl_usd, pnl_pct = 0.0, 0.0, 0.0  # valued at cost: no information
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
                source,
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


def net_flow_between(db: Session, portfolio_id: int, after: date, upto: date) -> float:
    """Signed ILS flow of the transactions dated in the half-open interval (after, upto]."""
    txs = db.exec(
        select(Transaction).where(
            Transaction.portfolio_id == portfolio_id,
            Transaction.date > after,
            Transaction.date <= upto,
        )
    ).all()
    return sum(flow_ils(t) for t in txs)


def net_flow_for_date(db: Session, portfolio_id: int, d: date) -> float:
    return net_flow_between(db, portfolio_id, d - timedelta(days=1), d)


def flows_since_previous_point(txs: list[Transaction], dates: list[date]) -> dict[date, float]:
    """For sorted `dates`, the flow of the transactions in (previous date, date].

    Days without a snapshot (a missed 23:59 job) must not lose their flows: they roll into the
    next point, otherwise a deposit would show up as profit.
    """
    out: dict[date, float] = {}
    for i, d in enumerate(dates):
        lo = dates[i - 1] if i > 0 else d - timedelta(days=1)
        out[d] = sum(flow_ils(t) for t in txs if lo < t.date <= d)
    return out


def take_snapshot(
    db: Session, portfolio: Portfolio, d: date | None = None, settings: Settings | None = None
) -> PortfolioSnapshot | None:
    """Upsert the end-of-day snapshot. Only for portfolios whose tracking has started.

    The stored flow is that of the transactions in (previous snapshot date, `d`], so a day without
    a snapshot does not lose its flows.
    """
    day = d or local_today()
    if portfolio.tracking_started_at is None or day < portfolio.tracking_started_at:
        return None
    assert portfolio.id is not None
    val = value_portfolio(db, portfolio, settings)
    prev = db.exec(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio.id, PortfolioSnapshot.date < day)
        .order_by(col(PortfolioSnapshot.date).desc())
    ).first()
    after = prev.date if prev is not None else day - timedelta(days=1)
    flow = net_flow_between(db, portfolio.id, after, day)
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


def take_catchup_snapshots(
    db: Session, today: date | None = None, settings: Settings | None = None
) -> int:
    """Snapshot yesterday for portfolios whose last snapshot is older (a missed 23:59 job).

    Called when the scheduler starts. One snapshot is taken (with the latest known quotes); the
    flows of all the skipped days are summed into it by `take_snapshot`.
    """
    day = (today or local_today()) - timedelta(days=1)
    n = 0
    for p in db.exec(select(Portfolio)).all():
        if p.id is None or p.tracking_started_at is None or day < p.tracking_started_at:
            continue
        exists = db.exec(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.portfolio_id == p.id, PortfolioSnapshot.date >= day
            )
        ).first()
        if exists is None and take_snapshot(db, p, day, settings) is not None:
            n += 1
    return n


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
    "flows_since_previous_point",
    "net_flow_between",
    "net_flow_for_date",
    "take_catchup_snapshots",
    "take_snapshot",
    "value_portfolio",
]
