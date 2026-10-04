"""Fallback chains behind the existing `QuoteProvider` / `HistoryProvider` interfaces (block 2.1).

Quotes: Yahoo first; symbols it did not return go to the market's fallback sources in configured
order (`Settings.quote_fallback_order`). TASE has no free fallback: those symbols are simply absent
and the stored last close stays, labelled with its date (never fresh). A cross-check asks one live
fallback about a bounded number of symbols the primary did return; two live prices more than
`quote_disagreement_pct` apart flag the stored quote `price_disagreement` instead of passing
silently. The primary's price is kept (nothing is averaged).

History: the primary first. When it has nothing, a fallback frame is used and labelled:
`attrs["source"]`, and `attrs["history_source_mismatch"]` when it does not match the last good
primary frame on overlapping days within `history_mismatch_tolerance_pct`, or
`attrs["history_source_unverified"]` when there is nothing to compare with.
"""

from __future__ import annotations

import logging
from typing import Protocol

import pandas as pd

from app.config import Settings, get_settings
from app.providers.base import (
    QUOTE_FLAG_DISAGREEMENT,
    HistoryProvider,
    Market,
    Quote,
    QuoteProvider,
    market_of_symbol,
)
from app.providers.fallback_sources import QUOTE_SOURCE_CLASSES, FallbackSource

log = logging.getLogger(__name__)

HISTORY_MISMATCH = "history_source_mismatch"
HISTORY_UNVERIFIED = "history_source_unverified"


class _Source(Protocol):
    name: str

    def available(self) -> bool: ...
    def covers(self, symbol: str) -> bool: ...
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...


def build_sources(settings: Settings) -> dict[str, FallbackSource]:
    return {name: cls(settings) for name, cls in QUOTE_SOURCE_CLASSES.items()}


def route_of(symbol: str, settings: Settings) -> str:
    """The fallback-order key of a symbol: its market, or "FX" for the USD/ILS symbol."""
    if symbol == settings.fx_symbol:
        return "FX"
    market: Market = market_of_symbol(symbol)
    return market


class ChainedQuoteProvider:
    def __init__(
        self,
        primary: QuoteProvider,
        settings: Settings | None = None,
        sources: dict[str, FallbackSource] | None = None,
    ) -> None:
        self.primary = primary
        self.settings = settings or get_settings()
        self.sources: dict[str, FallbackSource] = (
            sources if sources is not None else build_sources(self.settings)
        )

    def __getattr__(self, name: str) -> object:  # e.g. currency_hint on the Yahoo provider
        return getattr(self.primary, name)

    def _chain(self, symbol: str) -> list[FallbackSource]:
        names = self.settings.quote_fallback_order.get(route_of(symbol, self.settings), [])
        return [self.sources[n] for n in names if n in self.sources]

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        try:
            out = dict(self.primary.get_quotes(symbols))
        except Exception as exc:
            log.warning("primary quote provider failed: %s", exc)
            out = {}
        if not self.settings.quote_fallback_enabled:
            return out
        missing = [s for s in symbols if s not in out]
        for sym, q in self._from_fallbacks(missing).items():
            out[sym] = q
        self._crosscheck(out, [s for s in symbols if s in out and out[s].source == "yfinance"])
        return out

    def _from_fallbacks(self, missing: list[str]) -> dict[str, Quote]:
        found: dict[str, Quote] = {}
        for sym in missing:
            for src in self._chain(sym):
                if not (src.available() and src.covers(sym)):
                    continue
                q = src.get_quotes([sym]).get(sym)
                if q is not None:
                    found[sym] = q
                    break
        return found

    def _crosscheck(self, out: dict[str, Quote], candidates: list[str]) -> None:
        limit = self.settings.quote_crosscheck_max_symbols
        if limit <= 0:
            return
        checked = 0
        for sym in candidates:
            if checked >= limit:
                break
            primary = out[sym]
            live = [s for s in self._chain(sym) if s.available() and s.covers(sym) and _is_live(s)]
            if not live:
                continue
            other = live[0].get_quotes([sym]).get(sym)
            checked += 1
            if other is None or other.basis != "live" or other.currency != primary.currency:
                continue
            gap = abs(primary.price - other.price) / other.price * 100.0
            if gap > self.settings.quote_disagreement_pct:
                log.warning(
                    "price disagreement on %s: %s vs %s (%.1f%%)",
                    sym,
                    primary.source,
                    other.source,
                    gap,
                )
                out[sym] = primary.model_copy(update={"flag": QUOTE_FLAG_DISAGREEMENT})


def _is_live(src: FallbackSource) -> bool:
    """Only a live-priced source (Finnhub, CoinGecko) can contradict a live primary quote."""
    return src.name in ("finnhub", "coingecko")


class ChainedHistoryProvider:
    def __init__(
        self,
        primary: HistoryProvider,
        settings: Settings | None = None,
        sources: dict[str, FallbackSource] | None = None,
    ) -> None:
        self.primary = primary
        self.settings = settings or get_settings()
        self.sources: dict[str, FallbackSource] = (
            sources if sources is not None else build_sources(self.settings)
        )
        self._last_good: dict[str, pd.DataFrame] = {}  # the primary's most recent frame per symbol

    def __getattr__(self, name: str) -> object:
        return getattr(self.primary, name)

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        try:
            df = self.primary.get_history(symbol, days)
        except Exception as exc:
            log.warning("primary history failed for %s: %s", symbol, exc)
            df = None
        if df is not None and not df.empty:
            self._last_good[symbol] = df
            return df
        if not self.settings.quote_fallback_enabled:
            return df
        names = self.settings.history_fallback_order.get(market_of_symbol(symbol), [])
        for name in names:
            src = self.sources.get(name)
            getter = getattr(src, "get_history", None)
            if src is None or getter is None or not src.available() or not src.covers(symbol):
                continue
            alt = getter(symbol, days)
            if alt is None or alt.empty:
                continue
            return self.label(symbol, alt.copy(), name)
        return df

    def label(self, symbol: str, alt: pd.DataFrame, source: str) -> pd.DataFrame:
        alt.attrs["source"] = source
        ref = self._last_good.get(symbol)
        verdict = compare_overlap(
            ref,
            alt,
            self.settings.history_mismatch_tolerance_pct,
            self.settings.history_overlap_min_days,
        )
        if verdict is None:
            alt.attrs[HISTORY_UNVERIFIED] = True
        elif not verdict:
            alt.attrs[HISTORY_MISMATCH] = True
        return alt


def compare_overlap(
    ref: pd.DataFrame | None, alt: pd.DataFrame, tolerance_pct: float, min_days: int
) -> bool | None:
    """True when closes agree on the overlapping days, False when not, None with too little overlap
    to tell (no reference frame, or fewer than `min_days` common days)."""
    if ref is None or ref.empty:
        return None
    a = ref["Close"].copy()
    b = alt["Close"].copy()
    a.index, b.index = pd.to_datetime(a.index).normalize(), pd.to_datetime(b.index).normalize()
    common = a.index.intersection(b.index)
    if len(common) < min_days:
        return None
    rel = ((a.loc[common] - b.loc[common]).abs() / b.loc[common]) * 100.0
    return bool(rel.max() <= tolerance_pct)


def history_usable_for_exit_levels(df: pd.DataFrame | None) -> bool:
    """Exit levels use no history that a fallback could not confirm against the primary."""
    return df is not None and not df.empty and not df.attrs.get(HISTORY_MISMATCH)
