"""Provider registry so the API, the scheduler and tests share one swap point."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.config import get_settings
from app.providers.analyst_targets import AnalystTargetProvider, YFinanceTargetProvider
from app.providers.analyst_trends import AnalystTrendProvider, default_analyst_chain
from app.providers.base import (
    DividendProvider,
    FundProvider,
    HistoryProvider,
    OcrProvider,
    QuoteProvider,
    SymbolSearchProvider,
)
from app.providers.chain import ChainedHistoryProvider, ChainedQuoteProvider, build_sources
from app.providers.dividends import YFinanceDividendProvider
from app.providers.gemelnet import GemelNetProvider
from app.providers.ocr import get_ocr_provider
from app.providers.yfinance_provider import YFinanceProvider


@dataclass
class Providers:
    quotes: QuoteProvider
    history: HistoryProvider
    ocr_factory: object = field(default=None)
    # Optional: tests set a fake; None means the shared default (built on first use).
    funds: FundProvider | None = None
    dividends: DividendProvider | None = None
    symbol_search: SymbolSearchProvider | None = None
    analyst_trends: AnalystTrendProvider | None = None
    analyst_targets: AnalystTargetProvider | None = None

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
    settings = get_settings()
    sources = build_sources(settings)  # shared: one budget and cache per vendor
    return Providers(
        quotes=ChainedQuoteProvider(yf, settings, sources),
        history=ChainedHistoryProvider(yf, settings, sources),
    )


def get_providers() -> Providers:
    return _override or _default()


def set_providers(providers: Providers | None) -> None:
    """Install (or clear with None) a provider set. Used by tests."""
    global _override
    _override = providers


@lru_cache
def _default_funds() -> FundProvider:
    # one budget and cache per process (name/markets are ClassVars there: same shape, no setter)
    return GemelNetProvider(get_settings())  # type: ignore[return-value]


@lru_cache
def _default_dividends() -> DividendProvider:
    return YFinanceDividendProvider(get_settings(), currency_lookup=_default_currency_lookup)


@lru_cache
def _currency_yf() -> YFinanceProvider:
    yf = YFinanceProvider()
    yf.currency_hint = _security_currency
    yf.stored_currency = _stored_yahoo_currency
    return yf


def _default_currency_lookup(symbol: str) -> str | None:
    return _currency_yf().raw_currency(symbol)


def get_fund_provider() -> FundProvider:
    return (_override.funds if _override else None) or _default_funds()


def get_dividend_provider() -> DividendProvider:
    return (_override.dividends if _override else None) or _default_dividends()


@lru_cache
def _default_symbol_search() -> SymbolSearchProvider:
    from app.providers.symbol_search import default_symbol_search

    return default_symbol_search(get_settings())


def get_symbol_search() -> SymbolSearchProvider:
    return (_override.symbol_search if _override else None) or _default_symbol_search()


@lru_cache
def _default_analyst_trends() -> AnalystTrendProvider:
    return default_analyst_chain(get_settings())


@lru_cache
def _default_analyst_targets() -> AnalystTargetProvider:
    return YFinanceTargetProvider(get_settings())


def get_analyst_trends() -> AnalystTrendProvider:
    return (_override.analyst_trends if _override else None) or _default_analyst_trends()


def get_analyst_targets() -> AnalystTargetProvider:
    return (_override.analyst_targets if _override else None) or _default_analyst_targets()
