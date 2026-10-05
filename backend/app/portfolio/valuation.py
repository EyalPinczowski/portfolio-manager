"""Value a portfolio from cached quotes; daily snapshots; tracking start."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.funds import is_fund_symbol, manual_as_of
from app.models import (
    FundHolding,
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

# A marker (not a flow: `FLOW_SIGN` has no entry for it) for a holding that was added, or grew,
# while it had no real price (no quote and no screenshot price). One marker per holding (unique
# partial index on `transaction.holding_id`). See `sync_pending_flows`.
#
# Rule: only a real quote or screenshot price puts a holding into the performance value (the one
# the daily snapshots and the time-weighted return use). A holding valued at its cost, or at 0, is
# out of the TWR: its cost is not a market price, so editing `avg_cost` or `cost_currency` can never
# move the P&L, and its quantity waits in the marker until the first real price arrives, when it
# becomes one ordinary `buy` flow at that price (a deposit, never profit). The displayed value of
# the portfolio still shows the cost-valued holding (stale), exactly as before.
PENDING_BUY = "pending_buy"


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
    # In the time-weighted return: a real (quote/screenshot) price and no outstanding marker.
    performance_priced: bool = False
    usd_ils: float = 1.0  # the FX rate this valuation used
    quote_source: str | None = None  # which provider produced the price (quote rows only)
    price_basis: str = "live"  # live | last_close (a daily/EOD price, shown with its date)
    quote_flag: str | None = None  # e.g. price_disagreement


@dataclass
class PortfolioValuation:
    holdings: list[ValuedHolding] = field(default_factory=list)
    usd_ils: float = 1.0
    fx_stale: bool = False
    fx_source: str | None = None  # provider of the USD/ILS quote (yfinance, frankfurter, boi)
    fx_basis: str = "live"  # "last_close": a daily reference rate
    fx_as_of: datetime | None = None
    total_ils: float = 0.0
    total_usd: float = 0.0
    performance_total_ils: float = 0.0  # holdings with a real price only: what snapshots store
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
    out = PortfolioValuation(
        usd_ils=usd_ils,
        fx_stale=fx.stale,
        fx_source=fx.quote_source,
        fx_basis=fx.basis,
        fx_as_of=fx.as_of,
    )
    broker_prices = screenshot_prices(db, portfolio.id) if portfolio.id is not None else {}
    waiting = set(pending_markers(db, portfolio.id)) if portfolio.id is not None else set()
    for h in holdings:
        sec = db.get(Security, h.symbol)
        if sec is None:
            continue
        fund = db.get(FundHolding, h.id) if sec.asset_type == "fund" and h.id is not None else None
        quote = None if sec.asset_type == "fund" else db.get(PriceQuote, h.symbol)
        if quote is not None and quote.currency.strip().upper() not in SUPPORTED_CURRENCIES:
            # Unknown currency (e.g. a legacy row stored without one): never guess, show stale.
            quote = None
        source = "quote"
        if fund is not None and fund.manual_value_ils and fund.manual_value_ils > 0:
            # GemelNet has monthly returns, no unit price: the value is the user's own entry.
            price, cur, stale, chg = fund.manual_value_ils / h.quantity, "ILS", True, 0.0
            as_of = manual_as_of(fund.manual_value_as_of) if fund.manual_value_as_of else None
            source = "manual"
        elif quote is not None:
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
                performance_priced=source not in ("cost", "manual")
                and price > 0
                and h.id not in waiting,
                usd_ils=usd_ils,
                quote_source=quote.source if source == "quote" and quote is not None else None,
                price_basis=quote.basis
                if source == "quote" and quote is not None
                else "last_close",
                quote_flag=quote.flag if source == "quote" and quote is not None else None,
            )
        )
        if as_of is not None and (out.as_of is None or as_of > out.as_of):
            out.as_of = as_of
    out.total_ils = sum(v.value_ils for v in out.holdings)
    out.total_usd = out.total_ils / usd_ils if usd_ils else 0.0
    out.performance_total_ils = sum(v.value_ils for v in out.holdings if v.performance_priced)
    out.day_pnl_ils = sum(v.day_pnl_ils for v in out.holdings)
    return out


def pending_markers(db: Session, portfolio_id: int) -> dict[int, Transaction]:
    """Outstanding `pending_buy` markers by holding id (a legacy marker without one is matched
    by symbol, so rows written before the `holding_id` column keep working)."""
    holdings = {
        h.symbol: h.id
        for h in db.exec(select(Holding).where(Holding.portfolio_id == portfolio_id)).all()
    }
    out: dict[int, Transaction] = {}
    for t in db.exec(
        select(Transaction)
        .where(Transaction.portfolio_id == portfolio_id, Transaction.type == PENDING_BUY)
        .order_by(col(Transaction.id))
    ).all():
        hid = t.holding_id if t.holding_id is not None else holdings.get(t.symbol or "")
        if hid is not None and hid not in out:
            out[hid] = t
    return out


def register_pending(
    db: Session, portfolio: Portfolio, holding: Holding, quantity: float, today: date | None = None
) -> Transaction | None:
    """Create the holding's one marker, or add `quantity` to the existing one.

    Called only when an unpriced holding is added or its quantity changes (never from the daily
    snapshot). A marker whose quantity falls to 0 or below is removed. The caller commits.
    """
    if portfolio.id is None or holding.id is None or is_fund_symbol(holding.symbol):
        return None  # a fund never gets a market price: a marker would wait forever
    marker = pending_markers(db, portfolio.id).get(holding.id)
    if marker is not None:
        marker.holding_id = holding.id
        marker.quantity = (marker.quantity or 0.0) + quantity
        if marker.quantity <= 1e-12:
            db.delete(marker)
            db.flush()
            return None
        db.add(marker)
        db.flush()
        return marker
    if quantity <= 1e-12:
        return None
    marker = Transaction(
        portfolio_id=portfolio.id,
        symbol=holding.symbol,
        holding_id=holding.id,
        type=PENDING_BUY,
        quantity=quantity,
        amount=0.0,
        currency="ILS",  # never "USD" with fx 1.0: the FX fallback reads those
        date=today or local_today(),
        inferred=True,
    )
    try:
        with db.begin_nested():  # a concurrent writer may have just created it: the index says no
            db.add(marker)
            db.flush()
    except IntegrityError:
        existing = pending_markers(db, portfolio.id).get(holding.id)
        if existing is None:
            raise
        existing.quantity = (existing.quantity or 0.0) + quantity
        db.add(existing)
        db.flush()
        return existing
    return marker


def register_unpriced(
    db: Session, portfolio: Portfolio, valuation: PortfolioValuation, today: date | None = None
) -> int:
    """Marker for every holding that is not in the performance value (tracking start, importer)."""
    n = 0
    waiting = pending_markers(db, portfolio.id) if portfolio.id is not None else {}
    for v in valuation.holdings:
        if v.performance_priced or v.holding.id in waiting:
            continue
        if register_pending(db, portfolio, v.holding, v.holding.quantity, today) is not None:
            n += 1
    return n


def record_quantity_change(
    db: Session,
    portfolio: Portfolio,
    holding: Holding,
    delta: float,
    valued: ValuedHolding | None,
    today: date | None = None,
) -> None:
    """A quantity change after tracking started: a flow at the real price, or the marker moves.

    `valued` is the holding's valuation *before* the change. With a real price, buying adds a `buy`
    and selling a `sell` flow in the price's own currency (never `Security.currency`); without one
    the quantity goes into the holding's single marker and settles at the first real price.
    """
    if portfolio.id is None or portfolio.tracking_started_at is None or abs(delta) < 1e-12:
        return
    if valued is not None and valued.performance_priced:
        db.add(
            Transaction(
                portfolio_id=portfolio.id,
                symbol=holding.symbol,
                type="buy" if delta > 0 else "sell",
                quantity=abs(delta),
                price=valued.price,
                amount=abs(delta) * valued.price,
                currency=valued.currency,
                fx_to_ils=valued.usd_ils if valued.currency.upper() == "USD" else 1.0,
                date=today or local_today(),
                inferred=False,
            )
        )
        return
    register_pending(db, portfolio, holding, delta, today)


def sync_pending_flows(
    db: Session,
    portfolio: Portfolio,
    valuation: PortfolioValuation | None = None,
    settings: Settings | None = None,
    today: date | None = None,
) -> int:
    """Settle markers: a holding's first real price turns its marker into a buy flow.

    Never creates a marker (see `register_pending`; the daily snapshot must not, or a priced
    holding that is briefly unpriced would later be booked as a deposit). A marker settles only on a
    real quote or screenshot price, never on the cost fallback: the flow is the marker's quantity
    x that price in the *valuation's* currency, dated that day, so the value that appears is a
    deposit and never profit. A marker whose holding is gone is deleted. Returns the number of
    markers settled. The caller commits.
    """
    if portfolio.id is None:
        return 0
    val = valuation or value_portfolio(db, portfolio, settings)
    day = today or local_today()
    markers = pending_markers(db, portfolio.id)
    live = {v.holding.id: v for v in val.holdings}
    # A marker of a deleted holding (or a legacy duplicate) carries no flow: just drop it.
    kept = {id(t) for t in markers.values()}
    for t in db.exec(
        select(Transaction).where(
            Transaction.portfolio_id == portfolio.id, Transaction.type == PENDING_BUY
        )
    ).all():
        if id(t) not in kept:
            db.delete(t)
    for hid in [h for h in markers if h not in live]:
        db.delete(markers.pop(hid))
    n = 0
    for hid, marker in markers.items():
        v = live.get(hid)
        if v is None or v.price_source in ("cost", "manual") or v.price <= 0:
            continue
        marker.type = "buy"
        marker.holding_id = (
            None  # a settled marker is an ordinary buy; the holding may get a new one
        )
        marker.quantity = marker.quantity if marker.quantity else v.holding.quantity
        marker.price = v.price
        marker.amount = (marker.quantity or 0.0) * v.price
        marker.currency = v.currency
        marker.fx_to_ils = val.usd_ils if v.currency.upper() == "USD" else 1.0
        marker.date = day
        marker.inferred = True
        db.add(marker)
        n += 1
    db.flush()
    return n


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
    db: Session,
    portfolio: Portfolio,
    d: date | None = None,
    settings: Settings | None = None,
    commit: bool = True,
) -> PortfolioSnapshot | None:
    """Upsert the end-of-day snapshot. Only for portfolios whose tracking has started.

    `commit=False` only flushes: the caller owns the transaction (the screenshot confirm is one).

    The stored flow is that of the transactions in (previous snapshot date, `d`], so a day without
    a snapshot does not lose its flows.
    """
    day = d or local_today()
    if portfolio.tracking_started_at is None or day < portfolio.tracking_started_at:
        return None
    assert portfolio.id is not None
    val = value_portfolio(db, portfolio, settings)
    if sync_pending_flows(db, portfolio, val, settings, day):  # settle only, never create
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
            value_ils=val.performance_total_ils,
            value_usd=val.performance_total_ils / val.usd_ils if val.usd_ils else 0.0,
            net_flow_ils=flow,
        )
    else:
        snap.value_ils = val.performance_total_ils
        snap.value_usd = val.performance_total_ils / val.usd_ils if val.usd_ils else 0.0
        snap.net_flow_ils = flow
    db.add(snap)
    if commit:
        db.commit()
    else:
        db.flush()
    return snap


def _snapshot_flow_stale(db: Session, portfolio_id: int, snap: PortfolioSnapshot) -> bool:
    prev = db.exec(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio_id, PortfolioSnapshot.date < snap.date)
        .order_by(col(PortfolioSnapshot.date).desc())
    ).first()
    after = prev.date if prev is not None else snap.date - timedelta(days=1)
    return abs(net_flow_between(db, portfolio_id, after, snap.date) - snap.net_flow_ils) > 1e-9


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
        newest = db.exec(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.portfolio_id == p.id)
            .order_by(col(PortfolioSnapshot.date).desc())
        ).first()
        # A baseline row dated `day` (the tracking start day) is also redone when flows were booked
        # on that day after it was taken and the 23:59 job was missed.
        redo = newest is not None and newest.date == day and _snapshot_flow_stale(db, p.id, newest)
        if (newest is None or newest.date < day or redo) and (
            take_snapshot(db, p, day, settings) is not None
        ):
            n += 1
    return n


def ensure_tracking_started(
    db: Session, portfolio: Portfolio, today: date | None = None, commit: bool = True
) -> bool:
    """Set `tracking_started_at` on the first confirmed import / manual add and take the baseline.

    Returns True if tracking was started by this call. The baseline is the portfolio value on that
    day (never the broker's cost basis), so earlier gains are not counted.
    """
    if portfolio.tracking_started_at is not None:
        return False
    day = today or local_today()
    portfolio.tracking_started_at = day
    db.add(portfolio)
    db.flush()
    # Holdings without a real price are out of the baseline; each waits in its one marker.
    register_unpriced(db, portfolio, value_portfolio(db, portfolio), day)
    if commit:
        db.commit()
    else:
        db.flush()
    take_snapshot(db, portfolio, day, commit=commit)
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
    "pending_markers",
    "record_quantity_change",
    "register_pending",
    "register_unpriced",
    "sync_pending_flows",
    "take_catchup_snapshots",
    "take_snapshot",
    "value_portfolio",
]
