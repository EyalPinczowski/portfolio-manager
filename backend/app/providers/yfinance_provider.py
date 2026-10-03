"""Yahoo Finance provider (free default). Network calls are isolated here."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from app.config import Settings, get_settings
from app.providers.base import Quote, normalize_history, normalize_price
from app.providers.cache import TTLCache, retry_with_backoff
from app.timeutil import utcnow

log = logging.getLogger(__name__)


class YFinanceProvider:
    """Implements QuoteProvider and HistoryProvider on top of yfinance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._currency: TTLCache[str] = TTLCache(self.settings.currency_cache_ttl_seconds)
        self._history: TTLCache[pd.DataFrame] = TTLCache(self.settings.history_cache_ttl_seconds)
        self._failures: TTLCache[bool] = TTLCache(self.settings.history_failure_ttl_seconds)

    # -- currency -------------------------------------------------------------------------
    def raw_currency(self, symbol: str) -> str | None:
        """The currency Yahoo reports for the symbol (e.g. ILA for TASE stocks, '' for indices)."""
        cached = self._currency.get(symbol)
        if cached is not None:
            return cached or None
        import yfinance as yf

        try:
            info: Any = yf.Ticker(symbol).fast_info
            cur = info.get("currency") if hasattr(info, "get") else info["currency"]
        except Exception as exc:
            log.warning("currency lookup failed for %s: %s", symbol, exc)
            return None
        self._currency.set(symbol, cur or "")
        return str(cur) if cur else None

    # -- quotes ---------------------------------------------------------------------------
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        import yfinance as yf

        s = self.settings
        data = retry_with_backoff(
            lambda: yf.download(
                tickers=" ".join(symbols),
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            ),
            retries=s.provider_max_retries,
            base_seconds=s.provider_backoff_base_seconds,
        )
        out: dict[str, Quote] = {}
        now = utcnow()
        for sym in symbols:
            try:
                frame = data[sym] if len(symbols) > 1 else data
                q = self._quote_from_frame(sym, frame, now)
            except Exception as exc:
                log.warning("no quote for %s: %s", sym, exc)
                continue
            if q is not None:
                out[sym] = q
        return out

    def _quote_from_frame(self, sym: str, frame: pd.DataFrame, now: datetime) -> Quote | None:
        closes = frame["Close"].dropna()
        if closes.empty:
            return None
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else None
        change = ((last / prev) - 1.0) * 100.0 if prev else None
        return build_quote(sym, last, self.raw_currency(sym), change, now)

    # -- history --------------------------------------------------------------------------
    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        key = f"{symbol}:{days}"
        cached = self._history.get(key)
        if cached is not None:
            return cached
        if self._failures.get(key):
            return self._history.get_stale(key)
        import yfinance as yf

        s = self.settings
        try:
            df = retry_with_backoff(
                lambda: yf.Ticker(symbol).history(
                    period=f"{max(days, 30)}d", interval="1d", auto_adjust=False
                ),
                retries=s.provider_max_retries,
                base_seconds=s.provider_backoff_base_seconds,
            )
        except Exception as exc:
            log.warning("history failed for %s: %s", symbol, exc)
            self._failures.set(key, True)
            return self._history.get_stale(key)
        if df is None or df.empty:
            self._failures.set(key, True)
            return self._history.get_stale(key)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        df = normalize_history(df, self.raw_currency(symbol))
        self._history.set(key, df)
        return df


def build_quote(
    symbol: str, raw_price: float, raw_currency: str | None, change_pct: float | None, now: datetime
) -> Quote:
    """Pure helper: normalise a raw Yahoo price into a Quote (agorot rule lives in base)."""
    price, cur = normalize_price(raw_price, raw_currency)
    return Quote(symbol=symbol, price=price, currency=cur or "", change_pct=change_pct, as_of=now)
