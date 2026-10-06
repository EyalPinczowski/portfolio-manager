"""Symbol search beyond the built-in list (Yahoo via yfinance by default, Finnhub when keyed).

Only US listings (NYSE/NASDAQ/AMEX, plain 1-5 letter tickers) and TASE `.TA` are kept, each with
its currency. Results are cached (`symbol_search_ttl_s`) and every vendor call is rate limited.
Any failure is an empty list. Nothing here reaches a score: it only helps the user pick a ticker.
yfinance is called only inside `YahooSymbolSearch`.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from app.config import Settings, get_settings
from app.providers.base import SymbolHit, SymbolSearchProvider
from app.providers.cache import TTLCache
from app.providers.fallback_sources import FallbackSource, SourceBlockedError, SourceError

log = logging.getLogger(__name__)

US_PLAIN = re.compile(r"^[A-Z]{1,5}$")
TASE_SYMBOL = re.compile(r"^[A-Z0-9-]{1,15}\.TA$")
# Yahoo exchange codes -> our label (Arca is NYSE's ETF venue).
YAHOO_US_EXCHANGES = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "ASE": "AMEX",
    "PCX": "NYSE",
}
YAHOO_TASE_EXCHANGES = {"TLV"}
KEPT_TYPES = {"EQUITY", "ETF"}


def classify(symbol: str, name: str, exchange: str, source: str) -> SymbolHit | None:
    """A hit when it is a plain US ticker on a US exchange or a TASE `.TA` symbol, else None."""
    sym = symbol.strip().upper()
    if TASE_SYMBOL.fullmatch(sym):
        return SymbolHit(
            symbol=sym, name=name or sym, exchange="TASE", market="TASE", currency="ILS",
            source=source,
        )  # fmt: skip
    label = YAHOO_US_EXCHANGES.get(exchange) or ("US" if exchange == "US" else None)
    if label is not None and US_PLAIN.fullmatch(sym):
        return SymbolHit(
            symbol=sym, name=name or sym, exchange=label, market="US", currency="USD",
            source=source,
        )  # fmt: skip
    return None


class _Limited:
    """Cache + per-minute budget shared by the implementations."""

    name = ""

    def __init__(
        self, settings: Settings | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings or get_settings()
        self._clock = clock
        self._cache: TTLCache[list[SymbolHit]] = TTLCache(
            self.settings.symbol_search_ttl_s, clock, max_entries=500
        )
        self._calls: list[float] = []
        self._blocked_until = 0.0

    def _take(self) -> bool:
        now = self._clock()
        self._calls = [t for t in self._calls if now - t < 60.0]
        if now < self._blocked_until or len(self._calls) >= self.settings.symbol_search_per_minute:
            return False
        self._calls.append(now)
        return True

    def _trip(self) -> None:
        self._blocked_until = self._clock() + self.settings.symbol_search_cooldown_s


class YahooSymbolSearch(_Limited):
    """`yfinance.Search` (free)."""

    name = "yahoo"

    def _fetch(self, query: str, limit: int) -> list[dict[str, Any]]:
        import yfinance as yf

        search = yf.Search(
            query,
            max_results=limit * 3,
            news_count=0,
            lists_count=0,
            timeout=self.settings.symbol_search_timeout_s,
        )
        return list(search.quotes)

    def search(self, query: str, limit: int = 8) -> list[SymbolHit]:
        key = f"{query.strip().lower()}|{limit}"
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if not self._take():
            return []
        try:
            raw = self._fetch(query.strip(), limit)
        except Exception as exc:  # yfinance raises many types: no answer, never a 500
            log.info("yahoo symbol search failed: %s", type(exc).__name__)
            self._trip()
            return []
        out = self.parse(raw, limit)
        self._cache.set(key, out)
        return out

    def parse(self, raw: Sequence[Any], limit: int) -> list[SymbolHit]:
        out: list[SymbolHit] = []
        seen: set[str] = set()
        for q in raw:
            if not isinstance(q, dict) or str(q.get("quoteType", "")).upper() not in KEPT_TYPES:
                continue
            exch = str(q.get("exchange", "")).upper()
            if exch in YAHOO_TASE_EXCHANGES:
                exch = "TLV"
            h = classify(
                str(q.get("symbol", "")),
                str(q.get("longname") or q.get("shortname") or ""),
                exch,
                self.name,
            )
            if h is None or h.symbol in seen:
                continue
            seen.add(h.symbol)
            out.append(h)
            if len(out) >= limit:
                break
        return out


class FinnhubSymbolSearch(FallbackSource):
    """Finnhub free `/search` (US symbols). Runs only when `FINNHUB_API_KEY` is set."""

    name = "finnhub"
    key_attr = "finnhub_api_key"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._search_cache: TTLCache[list[SymbolHit]] = TTLCache(
            self.settings.symbol_search_ttl_s, self._clock, max_entries=500
        )

    def search(self, query: str, limit: int = 8) -> list[SymbolHit]:
        if not self.available():
            return []
        key = f"{query.strip().lower()}|{limit}"
        cached = self._search_cache.get(key)
        if cached is not None:
            return cached
        try:
            resp = self._get(
                f"{self.settings.finnhub_base_url}/search",
                params={"q": query.strip()},
                headers={"X-Finnhub-Token": self.api_key or ""},
            )
            out = self.parse(resp.json(), limit)
        except (SourceBlockedError, SourceError, ValueError):
            return []
        self._search_cache.set(key, out)
        return out

    def parse(self, data: Any, limit: int) -> list[SymbolHit]:
        rows = data.get("result") if isinstance(data, dict) else None
        out: list[SymbolHit] = []
        seen: set[str] = set()
        for r in rows or []:
            if not isinstance(r, dict) or r.get("type") not in ("Common Stock", "ETP", "ADR"):
                continue
            # Finnhub does not name the venue: a plain ticker is a US listing, `.TA` is TASE.
            sym = str(r.get("symbol", "")).upper()
            h = classify(sym, str(r.get("description", "")).title(), "US", self.name)
            if h is None or h.symbol in seen:
                continue
            seen.add(h.symbol)
            out.append(h)
            if len(out) >= limit:
                break
        return out


class SymbolSearchChain:
    """Merge the providers' hits in order (Finnhub first when keyed, Yahoo adds TASE)."""

    name = "chain"

    def __init__(self, providers: Sequence[SymbolSearchProvider]) -> None:
        self.providers = list(providers)

    def search(self, query: str, limit: int = 8) -> list[SymbolHit]:
        out: list[SymbolHit] = []
        seen: set[str] = set()
        for p in self.providers:
            try:
                hits = p.search(query, limit)
            except Exception:  # a provider must not raise, but never trust that
                continue
            for h in hits:
                if h.symbol not in seen:
                    seen.add(h.symbol)
                    out.append(h)
        return out[:limit]


def default_symbol_search(settings: Settings | None = None) -> SymbolSearchChain:
    s = settings or get_settings()
    return SymbolSearchChain([FinnhubSymbolSearch(s), YahooSymbolSearch(s)])
