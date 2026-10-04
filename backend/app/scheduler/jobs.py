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
from app.scheduler.calendars import is_market_open, is_post_close_fetch_due
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
    """US/TASE symbols only while their market is open (plus one fetch ~15 min after the close,
    so the stored close is final); crypto always.

    FX (and the benchmark indices) ride along whenever any equity market is open.
    """
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    out: list[str] = []
    any_open = False
    for sym in sorted(symbols_to_track(db)):
        sec = db.get(Security, sym)
        market = sec.market if sec else ("TASE" if sym.endswith(".TA") else "US")
        if is_market_open(market, now, s) or is_post_close_fetch_due(market, now, s):
            out.append(sym)
            any_open = any_open or market != "CRYPTO"
    equity_active = any(
        is_market_open(m, now, s) or is_post_close_fetch_due(m, now, s) for m in ("US", "TASE")
    )
    if any_open or equity_active:
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


def run_catchup_snapshots(db: Session, settings: Settings | None = None) -> int:
    """On scheduler start: fill in yesterday's snapshot if the 23:59 job was missed."""
    from app.portfolio.valuation import take_catchup_snapshots

    return take_catchup_snapshots(db, local_today(), settings)


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


def run_universe_score_refresh(
    db: Session,
    history: HistoryProvider,
    quotes: QuoteProvider | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Keep the screener's universe warm: score cards + bars (batched, paced) and live quotes."""
    from app.scoring.universe import refresh_universe

    return refresh_universe(db, history, quotes, settings, now)


def run_draft_purge(db: Session, settings: Settings | None = None) -> int:
    """Retention rule: unconfirmed import drafts are deleted 24 h after creation."""
    from app.importer.service import purge_expired_drafts

    return purge_expired_drafts(db, settings)


def run_session_purge(db: Session) -> int:
    """Hygiene: delete expired `session` rows."""
    from app.auth.sessions import purge_expired_sessions

    return purge_expired_sessions(db)


def run_weekly_review_job(
    db: Session, history: HistoryProvider | None = None, settings: Settings | None = None
) -> int:
    """Send the weekly reviews that are due (per-user day, time, quiet hours; one per week)."""
    from app.alerts.weekly_review import run_weekly_review

    return run_weekly_review(db, history, settings)
