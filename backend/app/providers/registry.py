"""Provider registry so the API, the scheduler and tests share one swap point."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.providers.base import HistoryProvider, OcrProvider, QuoteProvider
from app.providers.ocr import get_ocr_provider
from app.providers.yfinance_provider import YFinanceProvider


@dataclass
class Providers:
    quotes: QuoteProvider
    history: HistoryProvider
    ocr_factory: object = field(default=None)

    def ocr(self) -> OcrProvider:
        if callable(self.ocr_factory):
            result: OcrProvider = self.ocr_factory()
            return result
        return get_ocr_provider()


_override: Providers | None = None


@lru_cache
def _default() -> Providers:
    yf = YFinanceProvider()
    return Providers(quotes=yf, history=yf)


def get_providers() -> Providers:
    return _override or _default()


def set_providers(providers: Providers | None) -> None:
    """Install (or clear with None) a provider set. Used by tests."""
    global _override
    _override = providers
