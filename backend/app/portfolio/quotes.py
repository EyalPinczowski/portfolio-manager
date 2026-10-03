"""Persist quotes into the PriceQuote cache (shared by the API and the scheduler)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlmodel import Session

from app.models import PriceQuote, Security
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
                )
            )
        else:
            row.price, row.currency, row.change_pct, row.as_of = (
                q.price,
                q.currency,
                q.change_pct,
                q.as_of,
            )
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
    if not symbols:
        return 0
    try:
        quotes = provider.get_quotes(symbols)
    except Exception as exc:
        log.warning("quote refresh failed for %s: %s", symbols, exc)
        return 0
    return store_quotes(db, quotes.values())
