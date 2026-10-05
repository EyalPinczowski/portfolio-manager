"""Persistent daily bars (`daily_bar`): read what is stored, upsert what was fetched.

Prices are stored already normalised to major units (the provider normalises before saving).
Every function swallows database errors and degrades to "nothing stored", so a broken store only
costs a full fetch, never a failed request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.models import DailyBar

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _default_factory() -> Session:
    from app.db import new_session

    return new_session()


def load_bars(
    symbol: str, since: date, factory: SessionFactory | None = None
) -> pd.DataFrame | None:
    """Stored bars from `since` on as an OHLCV frame (naive DatetimeIndex), or None."""
    import pandas as pd

    try:
        with (factory or _default_factory)() as db:
            rows = db.exec(
                select(DailyBar)
                .where(DailyBar.symbol == symbol, col(DailyBar.day) >= since)
                .order_by(col(DailyBar.day))
            ).all()
            data = [(r.day, r.open, r.high, r.low, r.close, r.volume) for r in rows]
    except Exception as exc:
        log.warning("could not read stored bars for %s: %s", symbol, type(exc).__name__)
        return None
    if not data:
        return None
    df = pd.DataFrame(
        [r[1:] for r in data], index=pd.DatetimeIndex([r[0] for r in data]), columns=list(COLUMNS)
    )
    return df


def save_bars(symbol: str, df: pd.DataFrame, factory: SessionFactory | None = None) -> int:
    """Upsert `df` (normalised OHLCV, DatetimeIndex): rows from its first day on are replaced.
    Returns the number of rows written (0 on error)."""
    import pandas as pd

    if df is None or df.empty:
        return 0
    rows = [
        DailyBar(
            symbol=symbol,
            day=pd.Timestamp(str(ts)).date(),
            open=float(r["Open"]),
            high=float(r["High"]),
            low=float(r["Low"]),
            close=float(r["Close"]),
            volume=float(r["Volume"]) if r["Volume"] == r["Volume"] else 0.0,
        )
        for ts, r in df.iterrows()
    ]
    first = min(r.day for r in rows)
    try:
        with (factory or _default_factory)() as db:
            db.execute(
                delete(DailyBar).where(DailyBar.symbol == symbol, col(DailyBar.day) >= first)  # type: ignore[arg-type]
            )
            db.add_all(rows)
            db.commit()
    except Exception as exc:  # e.g. a concurrent writer; the next fetch repairs it
        log.warning("could not store bars for %s: %s", symbol, type(exc).__name__)
        return 0
    return len(rows)
