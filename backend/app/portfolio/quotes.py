"""Persist quotes into the PriceQuote cache (shared by the API and the scheduler)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlmodel import Session, col, select

from app.funds import is_fund_symbol
from app.models import Portfolio, PriceQuote, Security, Transaction
from app.providers.base import Quote, QuoteProvider

log = logging.getLogger(__name__)


def has_valid_currency(q: Quote) -> bool:
    """Only indices (points) may have an empty currency; every other quote must name one."""
    return bool(q.currency) or q.symbol.startswith("^")


def store_quotes(db: Session, quotes: Iterable[Quote]) -> int:
    n = 0
    for q in quotes:
        if not has_valid_currency(q):
            log.warning("not storing quote for %s: unknown currency", q.symbol)
            continue
        row = db.get(PriceQuote, q.symbol)
        if row is None:
            db.add(
                PriceQuote(
                    symbol=q.symbol,
                    price=q.price,
                    currency=q.currency,
                    change_pct=q.change_pct,
                    as_of=q.as_of,
                    source=q.source,
                    basis=q.basis,
                    flag=q.flag,
                )
            )
        else:
            if q.as_of < row.as_of:
                continue  # an older price (a daily fallback) never overwrites a newer one
            row.price, row.currency, row.change_pct, row.as_of = (
                q.price,
                q.currency,
                q.change_pct,
                q.as_of,
            )
            row.source, row.basis, row.flag = q.source, q.basis, q.flag
            db.add(row)
        n += 1
        sec = db.get(Security, q.symbol)
        if sec is not None and not sec.verified:
            sec.verified = True  # the provider knows this ticker: it may now appear in search
            db.add(sec)
    db.commit()
    return n


def refresh_symbols(db: Session, symbols: list[str], provider: QuoteProvider) -> int:
    """Fetch and store quotes; failures leave the (stale) cache untouched."""
    symbols = [s for s in symbols if not is_fund_symbol(s)]  # a fund has no market quote
    if not symbols:
        return 0
    try:
        quotes = provider.get_quotes(symbols)
    except Exception as exc:
        log.warning("quote refresh failed for %s: %s", symbols, exc)
        return 0
    stored = store_quotes(db, quotes.values())
    settle_pending_flows_for(db, list(quotes))
    return stored


def settle_pending_flows_for(db: Session, symbols: list[str]) -> int:
    """A first real price turns a holding's pending marker into a flow (see `sync_pending_flows`)."""
    from app.portfolio.valuation import PENDING_BUY, sync_pending_flows

    if not symbols:
        return 0
    pids = set(
        db.exec(
            select(Transaction.portfolio_id).where(
                Transaction.type == PENDING_BUY, col(Transaction.symbol).in_(symbols)
            )
        ).all()
    )
    n = 0
    try:
        for pid in pids:
            p = db.get(Portfolio, pid)
            if p is not None:
                n += sync_pending_flows(db, p)
        db.commit()
    except Exception:  # pragma: no cover - never let bookkeeping break a quote cycle
        db.rollback()
        log.exception("settling pending flows failed")
    return n
