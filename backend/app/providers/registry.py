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


def _security_currency(symbol: str) -> str | None:
    """Fallback currency from the Security table when Yahoo's currency lookup fails."""
    from app.db import new_session
    from app.models import Security

    try:
        with new_session() as db:
            sec = db.get(Security, symbol)
            return sec.currency if sec is not None else None
    except Exception:
        return None


def _stored_yahoo_currency(symbol: str) -> str | None:
    from app.db import new_session
    from app.models import Security

    with new_session() as db:
        sec = db.get(Security, symbol)
        return sec.yahoo_currency if sec is not None else None


def _store_yahoo_currency(symbol: str, currency: str) -> None:
    """Remember what Yahoo reported (only for securities we know: no rows are created here)."""
    from app.db import new_session
    from app.models import Security

    with new_session() as db:
        sec = db.get(Security, symbol)
        if sec is not None and sec.yahoo_currency != currency:
            sec.yahoo_currency = currency
            db.add(sec)
            db.commit()


@lru_cache
def _default() -> Providers:
    yf = YFinanceProvider()
    yf.currency_hint = _security_currency
    yf.stored_currency = _stored_yahoo_currency
    yf.store_currency = _store_yahoo_currency
    return Providers(quotes=yf, history=yf)


def get_providers() -> Providers:
    return _override or _default()


def set_providers(providers: Providers | None) -> None:
    """Install (or clear with None) a provider set. Used by tests."""
    global _override
    _override = providers
