"""Local daily-history store for the backtest, with strict as-of slicing.

One CSV per symbol under `Settings.backtest_history_dir` (default `backend/data/history/`, git-ignored)
plus a `manifest.json` that says where the data came from. The point of this module is the
look-ahead guard: `asof(symbol, date)` returns rows dated on or before `date` and nothing later,
so a decision made on day D can never see D+1.

`fetch_history` is the only function that talks to a provider, and the CLI (`fetch-history`) is its
only caller; no test calls it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import pandas as pd

from app.config import Settings, get_settings
from app.providers.base import HistoryProvider

log = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "history"
MANIFEST = "manifest.json"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def history_dir(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return Path(s.backtest_history_dir) if s.backtest_history_dir else DEFAULT_DIR


def _file_name(symbol: str) -> str:
    return (
        quote(symbol.upper(), safe=".-") + ".csv"
    )  # '^GSPC' -> '%5EGSPC.csv', 'ILS=X' -> 'ILS%3DX.csv'


def as_timestamp(day: date | datetime | str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(day)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


class HistoryStore:
    def __init__(self, root: Path | str | None = None, settings: Settings | None = None) -> None:
        self.root = Path(root) if root is not None else history_dir(settings)
        self._cache: dict[str, pd.DataFrame | None] = {}

    # ---- writing
    def save_history(self, symbol: str, df: pd.DataFrame) -> int:
        """Write a daily OHLCV frame (sorted, de-duplicated, tz-naive, no NaN close). Returns rows."""
        clean = _normalise(df)
        self.root.mkdir(parents=True, exist_ok=True)
        clean.to_csv(self.root / _file_name(symbol), index_label="Date")
        self._cache.pop(symbol.upper(), None)
        return len(clean)

    def write_manifest(self, **fields: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.manifest()
        current.update(fields)
        (self.root / MANIFEST).write_text(json.dumps(current, indent=2, default=str), "utf-8")

    def manifest(self) -> dict[str, Any]:
        try:
            raw = json.loads((self.root / MANIFEST).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @property
    def is_synthetic(self) -> bool:
        """True when the manifest says the data is synthetic. A store without a manifest is not
        trusted either: `provenance_known` is False for it."""
        return bool(self.manifest().get("synthetic", False))

    @property
    def provenance_known(self) -> bool:
        return "synthetic" in self.manifest()

    # ---- reading
    def symbols(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(unquote(p.stem) for p in self.root.glob("*.csv"))

    def has(self, symbol: str) -> bool:
        return (self.root / _file_name(symbol)).is_file()

    def load_history(self, symbol: str) -> pd.DataFrame | None:
        """The whole stored frame. Backtest code must use `asof`, never this, to make decisions."""
        key = symbol.upper()
        if key not in self._cache:
            path = self.root / _file_name(key)
            try:
                df = pd.read_csv(path, index_col="Date", parse_dates=True)
            except (OSError, ValueError, KeyError):
                df = None
            self._cache[key] = None if df is None or df.empty else _normalise(df)
        got = self._cache[key]
        return None if got is None else got.copy()

    def asof(
        self,
        symbol: str,
        day: date | datetime | str | pd.Timestamp,
        lookback_days: int | None = None,
    ) -> pd.DataFrame | None:
        """Rows dated on or before `day` (optionally only the last `lookback_days` calendar days).

        This is the only door the simulator uses. It returns a copy, so a caller cannot change
        the store, and never a row after `day`.
        """
        key = symbol.upper()
        if key not in self._cache:
            self.load_history(key)
        full = self._cache.get(key)
        if full is None:
            return None
        end = as_timestamp(day)
        out = full.loc[full.index <= end]
        if lookback_days is not None:
            out = out.loc[out.index > end - pd.Timedelta(days=lookback_days)]
        return None if out.empty else out.copy()

    def date_range(self, symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        full = self.load_history(symbol)
        if full is None:
            return None
        return pd.Timestamp(full.index[0]), pd.Timestamp(full.index[-1])


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    out: pd.DataFrame = df.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    out.index = idx.normalize()
    out.index.name = "Date"
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = float("nan") if col != "Volume" else 0.0
    out = out[COLUMNS].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=["Close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low"):  # a missing Open/High/Low falls back to the close
        out[col] = out[col].fillna(out["Close"])
    return out


# ---- module-level helpers over the default store (the shape the spec names)
def load_history(symbol: str, store: HistoryStore | None = None) -> pd.DataFrame | None:
    return (store or HistoryStore()).load_history(symbol)


def asof(
    symbol: str,
    day: date | datetime | str | pd.Timestamp,
    store: HistoryStore | None = None,
    lookback_days: int | None = None,
) -> pd.DataFrame | None:
    return (store or HistoryStore()).asof(symbol, day, lookback_days)


# ---- the one network entry point (CLI only)
def fetch_history(
    symbols: Iterable[str],
    years: int,
    provider: HistoryProvider,
    store: HistoryStore,
    *,
    pause_seconds: float = 0.5,
) -> dict[str, int]:
    """Download `years` of daily bars per symbol through the HistoryProvider and store them.

    Returns rows stored per symbol (0 = the provider had nothing). Never called from tests.
    """
    days = int(years * 366)
    got: dict[str, int] = {}
    for sym in symbols:
        try:
            df = provider.get_history(sym, days)
        except Exception as exc:
            log.warning("fetch-history failed for %s: %s", sym, exc)
            df = None
        got[sym] = store.save_history(sym, df) if df is not None and not df.empty else 0
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    store.write_manifest(
        synthetic=False,
        source="HistoryProvider (yfinance)",
        years=years,
        fetched_at=datetime.now(UTC).isoformat(),
        symbols=sorted(s for s, n in got.items() if n),
    )
    return got
