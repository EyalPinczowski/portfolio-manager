"""The screener's universe: which symbols are scored in the background, and the cached chart bars.

The request path of the screener never calls a provider. A scheduled job (`run_universe_score_refresh`
in `app/scheduler/jobs.py`, built on `refresh_universe` here) keeps three things warm for every
universe symbol: the score card (`SignalCache[symbol]`, the same row the holdings use), a compact
copy of the daily bars (`SignalCache["bars:<symbol>"]`: the exit-levels engine needs them) and the
live quote (`PriceQuote`). The job is batched and pauses between history calls so free tiers hold.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import PriceQuote, Security, SignalCache
from app.portfolio.quotes import refresh_symbols
from app.providers.base import HistoryProvider, QuoteProvider
from app.scheduler.calendars import is_market_open, is_post_close_fetch_due
from app.scoring.scorecard import refresh_scorecard
from app.timeutil import utcnow

log = logging.getLogger(__name__)

DEFAULT_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "universe_seed.txt"
BARS_PREFIX = "bars:"
_EPOCH = datetime(1970, 1, 1)


def load_universe(settings: Settings | None = None) -> list[str]:
    """Symbols from the universe file (comments with `#`, blank lines ignored), in file order, no
    duplicates. A missing file gives an empty universe, never an error."""
    s = settings or get_settings()
    path = Path(s.universe_file) if s.universe_file else DEFAULT_UNIVERSE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        log.warning("universe file %s cannot be read", path)
        return []
    out: list[str] = []
    for line in lines:
        sym = line.split("#", 1)[0].strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out


def bars_payload(df: pd.DataFrame | None) -> dict[str, list[list[float | str]]]:
    """Compact daily bars [date, high, low, close] for the cache (rounded, NaN rows dropped)."""
    if df is None or df.empty or not {"High", "Low", "Close"} <= set(df.columns):
        return {"bars": []}
    clean = df.dropna(subset=["High", "Low", "Close"]).sort_index()
    rows: list[list[float | str]] = [
        [pd.Timestamp(idx).strftime("%Y-%m-%d"), round(h, 6), round(lo, 6), round(c, 6)]
        for idx, h, lo, c in zip(
            clean.index, clean["High"], clean["Low"], clean["Close"], strict=True
        )
    ]
    return {"bars": rows}


def bars_frame(payload: dict[str, object] | None) -> pd.DataFrame | None:
    """The inverse of `bars_payload`: a DataFrame the exit-levels engine accepts, or None."""
    raw = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    try:
        idx = pd.DatetimeIndex([pd.Timestamp(str(r[0])) for r in raw])
        df = pd.DataFrame(
            {
                "High": [float(r[1]) for r in raw],
                "Low": [float(r[2]) for r in raw],
                "Close": [float(r[3]) for r in raw],
            },
            index=idx,
        )
    except (TypeError, ValueError, IndexError):
        return None
    df["Open"] = df["Close"]
    return df


def _store_bars(db: Session, symbol: str, df: pd.DataFrame | None) -> None:
    key = BARS_PREFIX + symbol
    payload: dict[str, object] = dict(bars_payload(df))
    row = db.get(SignalCache, key)
    now = utcnow()
    if row is None:
        db.add(SignalCache(symbol=key, computed_at=now, payload=payload))
    else:
        row.payload, row.computed_at = payload, now
        db.add(row)
    db.commit()


class _OneFrame:
    """A `HistoryProvider` that hands back the frame already fetched (one provider call per symbol)."""

    def __init__(self, df: pd.DataFrame | None) -> None:
        self.df = df

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        return self.df


def universe_securities(db: Session, settings: Settings | None = None) -> list[Security]:
    """Universe symbols that exist in the security table (the rest are ignored)."""
    out: list[Security] = []
    for sym in load_universe(settings):
        sec = db.get(Security, sym)
        if sec is not None:
            out.append(sec)
    return out


def _quote_symbols(secs: list[Security], now: datetime, s: Settings) -> list[str]:
    """Symbols whose market is open (or just closed, for the final quote); crypto always."""
    return [
        x.symbol
        for x in secs
        if is_market_open(x.market, now, s) or is_post_close_fetch_due(x.market, now, s)
    ]


def refresh_universe(
    db: Session,
    history: HistoryProvider,
    quotes: QuoteProvider | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """One run of the job. Returns the number of score cards refreshed.

    Quotes for every universe symbol whose market is open go out in chunked batch calls; score cards
    and bars are refreshed for the `universe_refresh_batch_size` stalest symbols only.
    """
    s = settings or get_settings()
    at = now or datetime.now(UTC)
    secs = universe_securities(db, s)
    if quotes is not None:
        syms = _quote_symbols(secs, at, s)
        for i in range(0, len(syms), s.universe_quote_chunk_size):
            refresh_symbols(db, syms[i : i + s.universe_quote_chunk_size], quotes)

    ttl_s = s.universe_score_ttl_minutes * 60.0
    due: list[tuple[datetime, str]] = []
    for sec in secs:
        row = db.get(SignalCache, sec.symbol)
        computed = row.computed_at if row is not None else _EPOCH
        if row is not None and (utcnow() - computed).total_seconds() < ttl_s:
            continue
        due.append((computed, sec.symbol))
    due.sort()
    n = 0
    for _, sym in due[: s.universe_refresh_batch_size]:
        try:
            df = history.get_history(sym, s.history_days)
        except Exception as exc:
            log.warning("universe history failed for %s: %s", sym, exc)
            df = None
        try:
            refresh_scorecard(db, sym, _OneFrame(df), s)
            _store_bars(db, sym, df)
            n += 1
        except Exception as exc:
            db.rollback()
            log.warning("universe score refresh failed for %s: %s", sym, exc)
        if s.universe_pause_seconds > 0:
            time.sleep(s.universe_pause_seconds)
    return n


def cached_quote(db: Session, symbol: str) -> PriceQuote | None:
    return db.get(PriceQuote, symbol)
