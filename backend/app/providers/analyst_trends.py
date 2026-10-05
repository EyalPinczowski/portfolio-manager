"""Monthly analyst recommendation trends (Finnhub free `/stock/recommendation`, yfinance fallback).

Feeds the "revisions" analyst signal. TASE and unknown symbols have no free coverage: `None`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, Protocol

import pandas as pd
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.providers.base import Market, market_of_symbol
from app.providers.cache import TTLCache
from app.providers.fallback_sources import FallbackSource, SourceBlockedError, SourceError

log = logging.getLogger(__name__)


class RecommendationTrend(BaseModel):
    """One month of analyst rating counts."""

    period: date
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell


class AnalystTrendProvider(Protocol):
    def trends(self, symbol: str) -> list[RecommendationTrend] | None: ...


def _count(v: Any) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


class FinnhubRecommendationProvider(FallbackSource):
    name = "finnhub"
    markets = frozenset({"US"})
    key_attr = "finnhub_api_key"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._rec_cache: TTLCache[list[RecommendationTrend]] = TTLCache(
            self.settings.analyst_cache_ttl_seconds, self._clock, max_entries=2000
        )

    def trends(self, symbol: str) -> list[RecommendationTrend] | None:
        if not self.available() or not self.covers(symbol):
            return None
        hit = self._rec_cache.get(symbol)
        if hit is not None:
            return hit
        try:
            resp = self._get(
                f"{self.settings.finnhub_base_url}/stock/recommendation",
                params={"symbol": symbol.upper().replace("-", ".")},
                headers={"X-Finnhub-Token": self.api_key or ""},
            )
            out = self.parse(resp.json())
        except (SourceBlockedError, SourceError, ValueError):
            return None
        self._rec_cache.set(symbol, out)
        return out

    @staticmethod
    def parse(data: Any) -> list[RecommendationTrend]:
        out: list[RecommendationTrend] = []
        for row in data if isinstance(data, list) else []:
            try:
                period = date.fromisoformat(str(row.get("period")))
            except (ValueError, AttributeError):
                continue
            out.append(
                RecommendationTrend(
                    period=period,
                    strong_buy=_count(row.get("strongBuy")),
                    buy=_count(row.get("buy")),
                    hold=_count(row.get("hold")),
                    sell=_count(row.get("sell")),
                    strong_sell=_count(row.get("strongSell")),
                )
            )
        return sorted(out, key=lambda t: t.period, reverse=True)


class YFinanceRecommendationProvider:
    """yfinance `recommendations` (rows 0m, -1m, ...). The only yfinance call for this signal."""

    name = "yfinance"
    markets: frozenset[Market] = frozenset({"US"})

    def __init__(
        self, settings: Settings | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings or get_settings()
        self._cache: TTLCache[list[RecommendationTrend]] = TTLCache(
            self.settings.analyst_cache_ttl_seconds, clock, max_entries=2000
        )

    def _fetch_frame(self, symbol: str) -> pd.DataFrame | None:
        import yfinance as yf

        df = yf.Ticker(symbol).recommendations
        return df if isinstance(df, pd.DataFrame) else None

    def trends(self, symbol: str) -> list[RecommendationTrend] | None:
        if market_of_symbol(symbol) not in self.markets or symbol.startswith("^"):
            return None
        hit = self._cache.get(symbol)
        if hit is not None:
            return hit
        try:
            df = self._fetch_frame(symbol)
        except Exception:
            return None
        if df is None or df.empty:
            return None
        today = date.today()
        out: list[RecommendationTrend] = []
        for _, row in df.iterrows():
            label = str(row.get("period", "0m"))
            try:
                back = abs(int(label.rstrip("m")))
            except ValueError:
                continue
            month = today.year * 12 + today.month - 1 - back
            out.append(
                RecommendationTrend(
                    period=date(month // 12, month % 12 + 1, 1),
                    strong_buy=_count(row.get("strongBuy")),
                    buy=_count(row.get("buy")),
                    hold=_count(row.get("hold")),
                    sell=_count(row.get("sell")),
                    strong_sell=_count(row.get("strongSell")),
                )
            )
        out.sort(key=lambda t: t.period, reverse=True)
        self._cache.set(symbol, out)
        return out or None


class AnalystTrendChain:
    def __init__(self, providers: Sequence[AnalystTrendProvider]) -> None:
        self.providers = list(providers)

    def trends(self, symbol: str) -> list[RecommendationTrend] | None:
        if market_of_symbol(symbol) != "US":
            return None
        for p in self.providers:
            rows = p.trends(symbol)
            if rows:
                return rows
        return None


def default_analyst_chain(settings: Settings | None = None) -> AnalystTrendChain:
    s = settings or get_settings()
    return AnalystTrendChain([FinnhubRecommendationProvider(s), YFinanceRecommendationProvider(s)])
