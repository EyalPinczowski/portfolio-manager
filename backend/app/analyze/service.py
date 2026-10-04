"""Load the symbol-level data once and cache it (market data, not user data).

One `SignalCache` row per symbol (`analyze:<SYMBOL>`) holds the Chartist report, the daily bars the
exit-levels engine needs and the last quote. A fresh row (config TTL) means no provider call at all;
otherwise the existing provider chain is asked once for the quote and once for the history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
from sqlmodel import Session

from app.analyze.chartist import build_chart_report, usable
from app.analyze.schemas import ChartReport
from app.config import Settings
from app.models import PriceQuote, Security, SignalCache
from app.portfolio.quotes import store_quotes
from app.providers.base import Quote
from app.providers.registry import Providers
from app.scoring.universe import bars_frame, bars_payload
from app.timeutil import as_utc, utcnow

log = logging.getLogger(__name__)
KEY_PREFIX = "analyze:"


@dataclass
class MarketData:
    chart: ChartReport
    df: pd.DataFrame | None  # bars for the exit-levels engine
    quote: Quote | None
    history_as_of: datetime | None
    cached: bool
    has_any_data: bool  # a provider (or the cache) knew this symbol


def _quote_row(q: Quote) -> PriceQuote:
    """An in-memory row (never added to the session) so the levels engine can use it."""
    return PriceQuote(
        symbol=q.symbol,
        price=q.price,
        currency=q.currency,
        change_pct=q.change_pct,
        as_of=q.as_of,
        source=q.source,
        basis=q.basis,
        flag=q.flag,
    )


def quote_row(q: Quote | None) -> PriceQuote | None:
    return _quote_row(q) if q is not None else None


def load_market(
    db: Session,
    symbol: str,
    sec_in_db: Security | None,
    providers: Providers,
    settings: Settings,
    now: datetime | None = None,
) -> MarketData:
    at = as_utc(now or utcnow())
    key = KEY_PREFIX + symbol
    row = db.get(SignalCache, key)
    ttl = timedelta(minutes=settings.analyze_cache_ttl_minutes)
    payload = row.payload if row is not None else None
    cached_quote: Quote | None = None
    if row is not None and payload and at - as_utc(row.computed_at) < ttl:
        try:
            chart = ChartReport.model_validate(payload["chart"])
            q_raw = payload.get("quote")
            cached_quote = Quote.model_validate(q_raw) if q_raw else None
            return MarketData(
                chart=chart,
                df=bars_frame(payload.get("bars")),
                quote=_freshest(db, symbol, sec_in_db, cached_quote),
                history_as_of=chart.data_as_of,
                cached=True,
                has_any_data=bool(payload.get("has_any_data", True)),
            )
        except Exception:  # an old or damaged row is simply recomputed
            log.info("analyze cache row for %s unreadable, recomputing", symbol)

    quote: Quote | None = None
    df: pd.DataFrame | None = None
    try:
        quote = providers.quotes.get_quotes([symbol]).get(symbol)
    except Exception as exc:
        log.warning("analyze quote fetch failed for %s: %s", symbol, type(exc).__name__)
    try:
        df = providers.history.get_history(symbol, settings.history_days)
    except Exception as exc:
        log.warning("analyze history fetch failed for %s: %s", symbol, type(exc).__name__)
    frame = usable(df)
    chart = build_chart_report(symbol, frame, settings)
    has_any = quote is not None or (df is not None and not df.empty)
    if has_any:
        stored: dict[str, object] = {
            "chart": chart.model_dump(mode="json"),
            "bars": dict(bars_payload(df)),
            "quote": quote.model_dump(mode="json") if quote else None,
            "has_any_data": True,
        }
        if row is None:
            db.add(SignalCache(symbol=key, computed_at=at, payload=stored))
        else:
            row.payload, row.computed_at = stored, at
            db.add(row)
        db.commit()
        if quote is not None and sec_in_db is not None:
            store_quotes(db, [quote])  # a known security: keep the shared quote cache current
    return MarketData(
        chart=chart,
        df=bars_frame(dict[str, object](bars_payload(df))),
        quote=_freshest(db, symbol, sec_in_db, quote),
        history_as_of=chart.data_as_of,
        cached=False,
        has_any_data=has_any,
    )


def _freshest(db: Session, symbol: str, sec: Security | None, quote: Quote | None) -> Quote | None:
    """The newer of the analyze quote and the shared quote cache the scheduler keeps current."""
    if sec is None:
        return quote
    row = db.get(PriceQuote, symbol)
    if row is None:
        return quote
    if quote is not None and as_utc(quote.as_of) >= as_utc(row.as_of):
        return quote
    return Quote(
        symbol=row.symbol,
        price=row.price,
        currency=row.currency,
        change_pct=row.change_pct,
        as_of=row.as_of,
        source=row.source,
        basis=row.basis,  # type: ignore[arg-type]
        flag=row.flag,
    )
