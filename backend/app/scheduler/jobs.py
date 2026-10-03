"""Scheduler jobs: batched quotes, daily snapshots, price alerts, score refresh."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.alerts.price_alerts import check_price_alerts
from app.config import Settings, get_settings
from app.models import Holding, Portfolio, PriceAlert, Security
from app.portfolio.quotes import refresh_symbols
from app.portfolio.valuation import take_snapshot
from app.providers.base import HistoryProvider, QuoteProvider
from app.scheduler.calendars import is_market_open
from app.scoring.scorecard import is_fresh, refresh_scorecard
from app.timeutil import local_today

log = logging.getLogger(__name__)


def symbols_to_track(db: Session) -> set[str]:
    """Held symbols plus the watchlist (symbols with an active price alert)."""
    held = {h.symbol for h in db.exec(select(Holding)).all()}
    watch = {a.symbol for a in db.exec(select(PriceAlert).where(PriceAlert.active)).all()}
    return held | watch


def symbols_for_cycle(
    db: Session, now: datetime | None = None, settings: Settings | None = None
) -> list[str]:
    """US/TASE symbols only while their market is open; crypto always.

    FX (and the benchmark indices) ride along whenever any equity market is open.
    """
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    out: list[str] = []
    any_open = False
    for sym in sorted(symbols_to_track(db)):
        sec = db.get(Security, sym)
        market = sec.market if sec else ("TASE" if sym.endswith(".TA") else "US")
        if is_market_open(market, now, s):
            out.append(sym)
            any_open = any_open or market != "CRYPTO"
    if any_open or is_market_open("US", now, s) or is_market_open("TASE", now, s):
        out.extend([s.fx_symbol])
    return out


def run_quotes_cycle(
    db: Session,
    provider: QuoteProvider,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """One batched quote fetch, then the price-alert check. Returns the number of quotes stored."""
    s = settings or get_settings()
    symbols = symbols_for_cycle(db, now, s)
    stored = refresh_symbols(db, symbols, provider)
    check_price_alerts(db, s)
    return stored


def run_daily_snapshots(db: Session, settings: Settings | None = None) -> int:
    n = 0
    today = local_today()
    for p in db.exec(select(Portfolio)).all():
        if take_snapshot(db, p, today, settings) is not None:
            n += 1
    return n


def run_score_refresh(
    db: Session, history: HistoryProvider, settings: Settings | None = None
) -> int:
    s = settings or get_settings()
    n = 0
    for sym in sorted({h.symbol for h in db.exec(select(Holding)).all()}):
        if is_fresh(db, sym, s):
            continue
        try:
            refresh_scorecard(db, sym, history, s)
            n += 1
        except Exception as exc:
            db.rollback()
            log.warning("score refresh failed for %s: %s", sym, exc)
    return n
