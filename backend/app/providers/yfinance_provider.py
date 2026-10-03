"""Yahoo Finance provider (free default). Network calls are isolated here."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pandas as pd

from app.config import Settings, get_settings
from app.providers.base import Quote, normalize_history, normalize_price
from app.providers.cache import TTLCache, retry_with_backoff
from app.timeutil import utcnow

log = logging.getLogger(__name__)

CurrencyHint = Callable[[str], str | None]
StoredCurrency = Callable[[str], str | None]  # symbol -> the raw currency Yahoo last reported
StoreCurrency = Callable[[str, str], None]  # (symbol, raw currency) after a successful lookup


def is_index_symbol(symbol: str) -> bool:
    """Yahoo indices (^GSPC, ^TA125.TA) are quoted in points and have no currency."""
    return symbol.startswith("^")


def frame_for_symbol(data: pd.DataFrame | None, symbol: str, n_symbols: int) -> pd.DataFrame | None:
    """Pick one ticker's OHLC frame out of a `yf.download` result.

    yfinance returns a MultiIndex for several tickers and, since 1.x, also for a single ticker
    (with the ticker on either level). Older versions return flat columns for a single ticker.
    """
    if data is None or data.empty:
        return None
    cols = data.columns
    if isinstance(cols, pd.MultiIndex):
        for level in range(cols.nlevels):
            if symbol in cols.get_level_values(level):
                sub = data.xs(symbol, axis=1, level=level)
                return sub if isinstance(sub, pd.DataFrame) else None
        return None
    return data if n_symbols == 1 else None


class YFinanceProvider:
    """Implements QuoteProvider and HistoryProvider on top of yfinance."""

    def __init__(
        self,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self._clock = clock
        self._sleep = sleep
        self._currency: TTLCache[str] = TTLCache(self.settings.currency_cache_ttl_seconds)
        self._history: TTLCache[tuple[pd.DataFrame, str]] = TTLCache(
            self.settings.history_cache_ttl_seconds
        )
        self._failures: TTLCache[bool] = TTLCache(self.settings.history_failure_ttl_seconds)
        self.currency_hint: CurrencyHint | None = None  # e.g. Security.currency from the DB
        # Persistence of Yahoo's own (raw) currency, e.g. `Security.yahoo_currency`. Never raises.
        self.stored_currency: StoredCurrency | None = None
        self.store_currency: StoreCurrency | None = None
        self._retry_tick = 0
        self._retry_last: dict[str, int] = {}  # symbol -> tick of its last single-ticker retry
        self._empty_fetches = 0
        self._breaker_until = 0.0

    # -- currency -------------------------------------------------------------------------
    def raw_currency(self, symbol: str) -> str | None:
        """The currency Yahoo reports for the symbol (e.g. ILA for TASE stocks).

        None when unknown. A successful lookup is persisted (`store_currency`). If the live lookup
        fails (rate limits), the persisted value is used first (it survives a restart), then the
        in-memory value even if it has expired.
        """
        cached = self._currency.get(symbol)
        if cached is not None:
            return cached or None
        import yfinance as yf

        try:
            info: Any = yf.Ticker(symbol).fast_info
            cur = info.get("currency") if hasattr(info, "get") else info["currency"]
        except Exception as exc:
            log.warning("currency lookup failed for %s: %s", symbol, exc)
            stored = self._stored(symbol)
            if stored:
                self._currency.set(symbol, stored)
                return stored
            return self._currency.get_stale(symbol) or None
        self._currency.set(symbol, cur or "")
        if cur and self.store_currency is not None:
            try:
                if self._stored(symbol) != str(cur):
                    self.store_currency(symbol, str(cur))
            except Exception as exc:  # persistence must never break a quote cycle
                log.warning("could not persist the currency of %s: %s", symbol, exc)
        return str(cur) if cur else None

    def _stored(self, symbol: str) -> str | None:
        if self.stored_currency is None:
            return None
        try:
            return self.stored_currency(symbol) or None
        except Exception as exc:
            log.warning("could not read the stored currency of %s: %s", symbol, exc)
            return None

    def resolve_currency(self, symbol: str) -> str | None:
        """Currency used to normalise raw prices, or None when it cannot be determined safely.

        Order: what Yahoo reports; "" (points) for indices; the `Security.currency` hint. A hint
        of ILS on a `.TA` symbol is ambiguous (Yahoo quotes most TASE stocks in agorot), so it is
        NOT used: no price is better than a x100 price.
        """
        raw = self.raw_currency(symbol)
        if raw:
            return raw
        if is_index_symbol(symbol):
            return ""
        hint = self.currency_hint(symbol) if self.currency_hint else None
        if hint and not (hint.upper() == "ILS" and symbol.upper().endswith(".TA")):
            return hint
        return None

    # -- quotes ---------------------------------------------------------------------------
    def _download(self, tickers: list[str]) -> pd.DataFrame | None:
        import yfinance as yf

        s = self.settings
        frame: pd.DataFrame | None = retry_with_backoff(
            lambda: yf.download(
                tickers=" ".join(tickers),
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            ),
            retries=s.provider_max_retries,
            base_seconds=s.provider_backoff_base_seconds,
            sleep=self._sleep,
        )
        return frame

    def breaker_open(self) -> bool:
        return self._clock() < self._breaker_until

    def _collect(
        self, data: pd.DataFrame | None, symbols: list[str], now: datetime, out: dict[str, Quote]
    ) -> None:
        for sym in symbols:
            frame = frame_for_symbol(data, sym, len(symbols))
            if frame is None:
                continue
            try:
                q = self._quote_from_frame(sym, frame, now)
            except Exception as exc:
                log.warning("no quote for %s: %s", sym, exc)
                continue
            if q is not None:
                out[sym] = q

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batched latest quotes.

        Yahoo does not raise on rate limits: it logs and returns an empty/partial frame. Empty
        tickers are therefore retried one by one; when everything stays empty the fetch counts as
        rate-limited and a circuit breaker pauses further calls for a cool-down.
        """
        if not symbols:
            return {}
        s = self.settings
        if self.breaker_open():
            log.warning("quote circuit breaker open; skipping fetch of %d symbols", len(symbols))
            return {}
        out: dict[str, Quote] = {}
        now = utcnow()
        try:
            self._collect(self._download(symbols), symbols, now, out)
        except Exception as exc:
            log.warning("batch quote download failed: %s", exc)
        missing = [x for x in symbols if x not in out]
        if missing:
            for sym in self._retry_order(missing)[: s.provider_single_retry_max]:
                try:
                    self._collect(self._download([sym]), [sym], now, out)
                except Exception as exc:
                    log.warning("single quote retry failed for %s: %s", sym, exc)
        if out:
            self._empty_fetches = 0
        else:
            self._empty_fetches += 1
            if self._empty_fetches >= s.provider_breaker_threshold:
                self._breaker_until = self._clock() + s.provider_breaker_cooldown_seconds
                self._empty_fetches = 0
                log.warning("no quotes returned repeatedly; pausing for the cool-down")
        return out

    def _retry_order(self, missing: list[str]) -> list[str]:
        """Least recently retried first, so symbols Yahoo never answers (junk alert tickers) cannot
        take every single-retry slot cycle after cycle. Never-tried symbols come first."""
        order = sorted(missing, key=lambda x: (self._retry_last.get(x, -1), x))
        picks = order[: self.settings.provider_single_retry_max]
        self._retry_tick += 1
        for sym in picks:
            self._retry_last[sym] = self._retry_tick
        # forget symbols that are not missing any more (they resolved or left the cycle)
        wanted = set(missing)
        self._retry_last = {k: v for k, v in self._retry_last.items() if k in wanted}
        return order

    def _quote_from_frame(self, sym: str, frame: pd.DataFrame, now: datetime) -> Quote | None:
        closes = frame["Close"].dropna()
        if closes.empty:
            return None
        cur = self.resolve_currency(sym)
        if cur is None:
            log.warning("unknown currency for %s: quote not stored", sym)
            return None
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else None
        change = ((last / prev) - 1.0) * 100.0 if prev else None
        return build_quote(sym, last, cur, change, now)

    # -- history --------------------------------------------------------------------------
    def _stale_history(self, key: str) -> pd.DataFrame | None:
        item = self._history.get_stale(key)
        return None if item is None else item[0]

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        key = f"{symbol}:{days}"
        cur = self.resolve_currency(symbol)
        cached = self._history.get(key)
        if cached is not None and (cur is None or cached[1] == cur):
            return cached[0]  # a changed currency invalidates the cached (normalised) frame
        if cur is None:
            log.warning("unknown currency for %s: history not used", symbol)
            return self._stale_history(key)
        if self._failures.get(key):
            return self._stale_history(key)
        import yfinance as yf

        s = self.settings
        try:
            df = retry_with_backoff(
                lambda: yf.Ticker(symbol).history(
                    period=f"{max(days, 30)}d", interval="1d", auto_adjust=False
                ),
                retries=s.provider_max_retries,
                base_seconds=s.provider_backoff_base_seconds,
                sleep=self._sleep,
            )
        except Exception as exc:
            log.warning("history failed for %s: %s", symbol, exc)
            self._failures.set(key, True)
            return self._stale_history(key)
        if df is None or df.empty:
            self._failures.set(key, True)
            return self._stale_history(key)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        df = normalize_history(df, cur)
        self._history.set(key, (df, cur))
        return df


def build_quote(
    symbol: str, raw_price: float, raw_currency: str | None, change_pct: float | None, now: datetime
) -> Quote:
    """Pure helper: normalise a raw Yahoo price into a Quote (agorot rule lives in base)."""
    price, cur = normalize_price(raw_price, raw_currency)
    return Quote(symbol=symbol, price=price, currency=cur or "", change_pct=change_pct, as_of=now)
