"""Analyst price targets (yfinance `Ticker.analyst_price_targets`; free, unofficial, may be absent).

Display only: nothing here feeds a score. Prices are normalised to major units (TASE agorot / 100).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.providers.base import market_of_symbol
from app.providers.cache import TTLCache

log = logging.getLogger(__name__)


class AnalystTargets(BaseModel):
    low: float | None = None
    mean: float | None = None
    high: float | None = None
    currency: str | None = None


class AnalystTargetProvider(Protocol):
    def targets(self, symbol: str) -> AnalystTargets | None: ...


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def normalise_targets(raw: Any, symbol: str) -> AnalystTargets | None:
    """Pure: a yfinance targets dict -> major-unit targets; None when no usable figure."""
    if not isinstance(raw, dict):
        return None
    vals = {k: _num(raw.get(k)) for k in ("low", "mean", "high")}
    if all(v is None for v in vals.values()):
        return None
    market = market_of_symbol(symbol)
    scale = 100.0 if symbol.strip().upper().endswith(".TA") else 1.0  # agorot -> ILS
    cur = "ILS" if market == "TASE" else "USD" if market == "US" else None
    return AnalystTargets(
        low=None if vals["low"] is None else vals["low"] / scale,
        mean=None if vals["mean"] is None else vals["mean"] / scale,
        high=None if vals["high"] is None else vals["high"] / scale,
        currency=cur,
    )


class YFinanceTargetProvider:
    name = "yfinance"

    def __init__(
        self, settings: Settings | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings or get_settings()
        self._cache: TTLCache[AnalystTargets | bool] = TTLCache(
            self.settings.analyst_cache_ttl_seconds, clock, max_entries=2000
        )

    def _fetch(self, symbol: str) -> Any:
        import yfinance as yf

        return yf.Ticker(symbol).analyst_price_targets

    def targets(self, symbol: str) -> AnalystTargets | None:
        if market_of_symbol(symbol) == "CRYPTO" or symbol.startswith("^"):
            return None
        hit = self._cache.get(symbol)
        if hit is not None:
            return hit if isinstance(hit, AnalystTargets) else None  # False = cached "none"
        try:
            out = normalise_targets(self._fetch(symbol), symbol)
        except Exception as exc:
            log.warning("analyst targets failed for %s: %s", symbol, type(exc).__name__)
            return None
        self._cache.set(symbol, out if out is not None else False)
        return out
