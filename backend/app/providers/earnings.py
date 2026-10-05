"""Next-earnings date: a pluggable provider interface (Finnhub free calendar, yfinance fallback).

Informational by default. TASE has no free earnings calendar, so it is `unknown` (never a guess).
Finnhub runs only when `FINNHUB_API_KEY` is set, inside the shared call budget, cached 12 h.
yfinance is called only inside `YFinanceEarningsProvider`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol

import pandas as pd
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.providers.base import Market, market_of_symbol
from app.providers.cache import TTLCache
from app.providers.fallback_sources import FallbackSource, SourceBlockedError, SourceError

log = logging.getLogger(__name__)


class EarningsInfo(BaseModel):
    """When the next earnings report is (or `unknown`)."""

    symbol: str
    status: Literal["known", "unknown"]
    next_date: date | None = None
    source: str = ""

    def days_until(self, today: date) -> int | None:
        if self.next_date is None:
            return None
        return (self.next_date - today).days


def unknown(symbol: str, source: str = "") -> EarningsInfo:
    return EarningsInfo(symbol=symbol, status="unknown", source=source)


def in_window(info: EarningsInfo | None, today: date, settings: Settings) -> bool:
    """True when a known report is today or within `earnings_window_days` days ahead."""
    if info is None:
        return False
    n = info.days_until(today)
    return n is not None and 0 <= n <= settings.earnings_window_days


def earnings_line(info: EarningsInfo, today: date) -> str:
    n = info.days_until(today)
    if n is None:
        return "Earnings date unknown (no free calendar for this market)."
    if n < 0:
        return f"Last earnings report was {-n} day(s) ago."
    return "Earnings today." if n == 0 else f"Earnings in {n} day{'s' if n != 1 else ''}."


class EarningsProvider(Protocol):
    def next_earnings(self, symbol: str, today: date) -> EarningsInfo | None: ...


class FinnhubEarningsProvider(FallbackSource):
    """Finnhub free `/calendar/earnings` (US only, one symbol, a window ahead). Needs a key."""

    name = "finnhub"
    markets = frozenset({"US"})
    key_attr = "finnhub_api_key"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._earn_cache: TTLCache[EarningsInfo] = TTLCache(
            self.settings.earnings_cache_ttl_seconds, self._clock, max_entries=2000
        )

    def next_earnings(self, symbol: str, today: date) -> EarningsInfo | None:
        if not self.available() or not self.covers(symbol):
            return None
        hit = self._earn_cache.get(symbol)
        if hit is not None:
            return hit
        try:
            resp = self._get(
                f"{self.settings.finnhub_base_url}/calendar/earnings",
                params={
                    "symbol": symbol.upper().replace("-", "."),
                    "from": today.isoformat(),
                    "to": (
                        today + timedelta(days=self.settings.earnings_lookahead_days)
                    ).isoformat(),
                },
                headers={"X-Finnhub-Token": self.api_key or ""},
            )
            info = self.parse(symbol, resp.json(), today)
        except (SourceBlockedError, SourceError, ValueError):
            return None
        self._earn_cache.set(symbol, info)
        return info

    def parse(self, symbol: str, data: Any, today: date) -> EarningsInfo:
        rows = data.get("earningsCalendar") if isinstance(data, dict) else None
        days: list[date] = []
        for row in rows or []:
            try:
                d = date.fromisoformat(str(row.get("date")))
            except (ValueError, AttributeError):
                continue
            if d >= today:
                days.append(d)
        if not days:
            return unknown(symbol, self.name)
        return EarningsInfo(symbol=symbol, status="known", next_date=min(days), source=self.name)


class YFinanceEarningsProvider:
    """yfinance earnings dates (free). The only place this signal family touches yfinance."""

    name = "yfinance"
    markets: frozenset[Market] = frozenset({"US"})

    def __init__(
        self, settings: Settings | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings or get_settings()
        self._cache: TTLCache[EarningsInfo] = TTLCache(
            self.settings.earnings_cache_ttl_seconds, clock, max_entries=2000
        )

    def _fetch_dates(self, symbol: str) -> list[Any]:
        import yfinance as yf

        cal = yf.Ticker(symbol).calendar
        raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
        return list(raw) if isinstance(raw, list | tuple) else ([raw] if raw is not None else [])

    def next_earnings(self, symbol: str, today: date) -> EarningsInfo | None:
        if market_of_symbol(symbol) not in self.markets or symbol.startswith("^"):
            return None
        hit = self._cache.get(symbol)
        if hit is not None:
            return hit
        try:
            raw = self._fetch_dates(symbol)
        except Exception:  # yfinance raises many types; a failure is just "no answer"
            return None
        days: list[date] = []
        for r in raw:
            try:
                ts = pd.Timestamp(r)
            except (TypeError, ValueError):
                continue
            if not pd.isna(ts) and ts.date() >= today:
                days.append(ts.date())
        info = (
            EarningsInfo(symbol=symbol, status="known", next_date=min(days), source=self.name)
            if days
            else unknown(symbol, self.name)
        )
        self._cache.set(symbol, info)
        return info


class EarningsChain:
    """Finnhub first (when keyed), yfinance as the fallback; TASE is `unknown` without a call."""

    def __init__(self, providers: Sequence[EarningsProvider]) -> None:
        self.providers = list(providers)

    def next_earnings(self, symbol: str, today: date | None = None) -> EarningsInfo:
        day = today or datetime.now().date()
        if market_of_symbol(symbol) != "US":
            return unknown(symbol)
        for p in self.providers:
            info = p.next_earnings(symbol, day)
            if info is not None and info.status == "known":
                return info
        return unknown(symbol)


def default_earnings_chain(settings: Settings | None = None) -> EarningsChain:
    s = settings or get_settings()
    return EarningsChain([FinnhubEarningsProvider(s), YFinanceEarningsProvider(s)])
