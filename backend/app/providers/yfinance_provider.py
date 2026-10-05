"""Yahoo Finance provider (free default). Network calls are isolated here."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.config import Settings, get_settings
from app.providers.bar_store import SessionFactory, load_bars, save_bars
from app.providers.base import Quote, market_of_symbol, normalize_history, normalize_price
from app.providers.cache import TTLCache, retry_with_backoff
from app.scheduler.calendars import current_session_date, session_close_utc_naive
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
        small = self.settings.provider_small_cache_max_entries
        self._currency: TTLCache[str] = TTLCache(
            self.settings.currency_cache_ttl_seconds, max_entries=small
        )
        # A changed currency seen once, waiting for a second lookup to agree ("" = nothing pending).
        self._pending: TTLCache[str] = TTLCache(24 * 3600.0, max_entries=small)
        self._history: TTLCache[tuple[pd.DataFrame, str]] = TTLCache(
            self.settings.history_cache_ttl_seconds,
            max_entries=self.settings.history_cache_max_entries,
        )
        self._failures: TTLCache[bool] = TTLCache(
            self.settings.history_failure_ttl_seconds, max_entries=small
        )
        self.bar_session: SessionFactory | None = None  # None = app.db.new_session
        self.currency_hint: CurrencyHint | None = None  # e.g. Security.currency from the DB
        # Persistence of Yahoo's own (raw) currency, e.g. `Security.yahoo_currency`. Never raises.
        self.stored_currency: StoredCurrency | None = None
        self.store_currency: StoreCurrency | None = None
        self._retry_tick = 0
        self._retry_last: dict[str, int] = {}  # symbol -> tick of its last single-ticker retry
        self._empty_fetches = 0
        self._breaker_until = 0.0

    # -- currency -------------------------------------------------------------------------
    def _plausible(self, symbol: str, currency: str) -> bool:
        """Is `currency` something Yahoo can legitimately report for this symbol's market?"""
        sym, cur = symbol.upper(), currency.strip().upper()
        for suffix, allowed in self.settings.yahoo_currency_allowed.items():
            if sym.endswith(suffix.upper()):
                return cur in {a.upper() for a in allowed}
        return True

    def raw_currency(self, symbol: str) -> str | None:
        """The currency Yahoo reports for the symbol (e.g. ILA for TASE stocks).

        None when unknown. The stored value (`store_currency`, survives restarts) is trusted:

        - a lookup that fails, returns nothing, or returns a currency that is implausible for the
          symbol's market (`yahoo_currency_allowed`: `.TA` accepts only ILA/ILS) is ignored and the
          stored value is used;
        - a plausible answer that differs from the stored value is adopted only when a second,
          later lookup returns the same one (a flapping answer never changes it);
        - with nothing stored, the first plausible answer is stored.
        """
        cached = self._currency.get(symbol)
        if cached is not None:
            return cached or None
        import yfinance as yf

        stored = self._stored(symbol)
        try:
            info: Any = yf.Ticker(symbol).fast_info
            raw = info.get("currency") if hasattr(info, "get") else info["currency"]
        except Exception as exc:
            log.warning("currency lookup failed for %s: %s", symbol, exc)
            if stored:
                self._currency.set(symbol, stored)
                return stored
            return self._currency.get_stale(symbol) or None
        cur = str(raw).strip() if raw else None
        if cur and not self._plausible(symbol, cur):
            log.warning("ignoring implausible currency %r for %s", cur, symbol)
            cur = None
        if cur is None:  # empty or implausible: keep what we know, never blank the quote
            self._currency.set(symbol, stored or "")
            return stored
        if stored is not None and stored != cur:
            if self._pending.get(symbol) != cur:
                self._pending.set(symbol, cur)  # first sighting: wait for a second lookup
                self._currency.set(symbol, stored)
                return stored
            log.warning(
                "currency of %s changed from %s to %s (two lookups agree)", symbol, stored, cur
            )
        self._pending.set(symbol, "")  # the answer agrees with (or replaces) the stored value
        self._currency.set(symbol, cur)
        if self.store_currency is not None and stored != cur:
            try:
                self.store_currency(symbol, cur)
            except Exception as exc:  # persistence must never break a quote cycle
                log.warning("could not persist the currency of %s: %s", symbol, exc)
        return cur

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
                threads=False,
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
        q = build_quote(sym, last, cur, change, now)
        return _mark_stale_bar(q, closes.index[-1], now, self.settings)

    # -- history --------------------------------------------------------------------------
    def _stale_history(self, key: str) -> pd.DataFrame | None:
        item = self._history.get_stale(key)
        return None if item is None else item[0]

    def _fetch_frame(
        self, symbol: str, days: int, stored: pd.DataFrame | None
    ) -> pd.DataFrame | None:
        """One yfinance call: the whole window, or only the tail after the last stored bar."""
        import yfinance as yf

        s = self.settings
        kwargs: dict[str, Any] = {"interval": "1d", "auto_adjust": False}
        if stored is None or stored.empty:
            kwargs["period"] = f"{max(days, 30)}d"
        else:
            last = stored.index[-1].date()
            if (utcnow().date() - last).days <= s.history_tail_period_days:
                kwargs["period"] = f"{s.history_tail_period_days}d"
            else:
                kwargs["start"] = last.isoformat()  # the last bar is re-read (it may be partial)
        df: pd.DataFrame | None = retry_with_backoff(
            lambda: yf.Ticker(symbol).history(**kwargs),
            retries=s.provider_max_retries,
            base_seconds=s.provider_backoff_base_seconds,
            sleep=self._sleep,
        )
        return df

    @staticmethod
    def _clean(df: pd.DataFrame, cur: str) -> pd.DataFrame:
        """OHLCV columns only (the rest is dropped right after the fetch), no timezone, no empty
        closes, one row per day, normalised to major units."""
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        df.index = idx.normalize()
        df = df[~df.index.duplicated(keep="last")]
        return normalize_history(df, cur)

    def _stored_window(self, symbol: str, days: int) -> pd.DataFrame | None:
        if not self.settings.history_incremental_enabled:
            return None
        since = utcnow().date() - timedelta(days=max(days, 30))
        stored = load_bars(symbol, since, self.bar_session)
        if stored is None:
            return None
        slack = timedelta(days=self.settings.history_store_slack_days)
        if stored.index[0].date() > since + slack:
            return None  # the store does not reach back far enough: fetch the whole window
        return stored

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
        stored = self._stored_window(symbol, days)
        try:
            df = self._fetch_frame(symbol, days, stored)
        except Exception as exc:
            log.warning("history failed for %s: %s", symbol, exc)
            self._failures.set(key, True)
            return self._fallback_frame(key, stored)
        return self._merge_and_cache(symbol, key, cur, df, stored)

    def _fallback_frame(self, key: str, stored: pd.DataFrame | None) -> pd.DataFrame | None:
        stale = self._stale_history(key)
        return stale if stale is not None else stored

    def _merge_and_cache(
        self,
        symbol: str,
        key: str,
        cur: str,
        df: pd.DataFrame | None,
        stored: pd.DataFrame | None,
    ) -> pd.DataFrame | None:
        fresh = None if df is None or df.empty else self._clean(df, cur)
        if fresh is None or fresh.empty:
            self._failures.set(key, True)
            return self._fallback_frame(key, stored)
        if self.settings.history_incremental_enabled:
            save_bars(symbol, fresh, self.bar_session)
        if stored is not None and not stored.empty:
            merged: pd.DataFrame = pd.concat([stored[stored.index < fresh.index[0]], fresh])
        else:
            merged = fresh
        self._history.set(key, (merged, cur))
        return merged

    def prefetch_history(self, symbols: list[str], days: int) -> int:
        """Warm history for many symbols with batched `yf.download` calls (no thread pool).

        Symbols with a fresh in-memory frame are skipped; the others are fetched as a tail (when
        bars are stored) or in full. Whatever is missing from the batch is left to `get_history`.
        Returns the number of symbols warmed.
        """
        if not symbols or self.breaker_open():
            return 0
        s = self.settings
        key_of = {sym: f"{sym}:{days}" for sym in symbols}
        plan: dict[str, tuple[str, pd.DataFrame | None]] = {}
        for sym in symbols:
            if self._history.get(key_of[sym]) is not None:
                continue
            cur = self.resolve_currency(sym)
            if cur is not None:
                plan[sym] = (cur, self._stored_window(sym, days))
        full = [x for x, (_, st) in plan.items() if st is None]
        tail = [x for x, (_, st) in plan.items() if st is not None]
        warmed = 0
        for group, period in (
            (full, f"{max(days, 30)}d"),
            (tail, f"{s.history_tail_period_days}d"),
        ):
            if not group:
                continue
            data = self._download_group(group, period)
            for sym in group:
                frame = frame_for_symbol(data, sym, len(group))
                if frame is None or frame.empty:
                    continue
                cur, stored = plan[sym]
                try:
                    if self._merge_and_cache(sym, key_of[sym], cur, frame, stored) is not None:
                        warmed += 1
                except Exception as exc:
                    log.warning("prefetched history unusable for %s: %s", sym, exc)
        return warmed

    def _download_group(self, tickers: list[str], period: str) -> pd.DataFrame | None:
        import yfinance as yf

        s = self.settings
        try:
            frame: pd.DataFrame | None = retry_with_backoff(
                lambda: yf.download(
                    tickers=" ".join(tickers),
                    period=period,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                ),
                retries=s.provider_max_retries,
                base_seconds=s.provider_backoff_base_seconds,
                sleep=self._sleep,
            )
        except Exception as exc:
            log.warning("batch history download failed: %s", exc)
            return None
        return frame


def _mark_stale_bar(q: Quote, bar_ts: Any, now: datetime, settings: Settings) -> Quote:
    """A last bar older than the market's current session is a last close, not a live price."""
    if not isinstance(bar_ts, pd.Timestamp) or q.symbol.endswith("=X"):
        return q
    market = market_of_symbol(q.symbol)
    current = current_session_date(market, now, settings)
    if current is None:
        return q
    bar_day = bar_ts.date()
    if bar_day >= current:
        return q
    close = session_close_utc_naive(market, bar_day, settings)
    if close is None:
        return q
    return q.model_copy(update={"basis": "last_close", "as_of": close})


def build_quote(
    symbol: str, raw_price: float, raw_currency: str | None, change_pct: float | None, now: datetime
) -> Quote:
    """Pure helper: normalise a raw Yahoo price into a Quote (agorot rule lives in base)."""
    price, cur = normalize_price(raw_price, raw_currency)
    return Quote(symbol=symbol, price=price, currency=cur or "", change_pct=change_pct, as_of=now)
