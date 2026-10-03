"""USD/ILS rates. Reads the cached quote written by the scheduler; falls back to config."""

from __future__ import annotations

from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import PriceQuote


def get_usd_ils(db: Session, settings: Settings | None = None) -> float:
    """ILS per 1 USD: the cached `ILS=X` quote, else the configured fallback."""
    s = settings or get_settings()
    quote = db.get(PriceQuote, s.fx_symbol)
    if quote is not None and quote.price > 0:
        return float(quote.price)
    return s.fx_fallback_usd_ils


def to_ils(amount: float, currency: str, usd_ils: float) -> float:
    cur = currency.upper()
    if cur == "ILS":
        return amount
    if cur == "USD":
        return amount * usd_ils
    return amount * usd_ils  # other currencies are treated as USD-like (Phase 1)


def to_usd(amount: float, currency: str, usd_ils: float) -> float:
    return to_ils(amount, currency, usd_ils) / usd_ils if usd_ils else 0.0
