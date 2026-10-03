"""USD/ILS rates. Reads the cached quote written by the scheduler and says when it is stale."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import PriceQuote, Transaction
from app.timeutil import utcnow

SUPPORTED_CURRENCIES = ("ILS", "USD")


class UnknownCurrencyError(ValueError):
    """A money amount carries a currency the app cannot convert (never guess USD)."""


@dataclass(frozen=True)
class FxRate:
    usd_ils: float  # ILS per 1 USD
    stale: bool  # True when the rate is old, or is a last-known / configured fallback
    as_of: datetime | None  # naive UTC; None when the value is the configured constant
    source: str  # quote | stale_quote | last_transaction | config


def get_fx_rate(
    db: Session, settings: Settings | None = None, now: datetime | None = None
) -> FxRate:
    """ILS per 1 USD with a staleness flag. The fallbacks are never silent.

    Order: fresh `ILS=X` quote -> an old quote (flagged stale) -> the FX stored on the newest USD
    transaction (flagged stale) -> the configured constant (flagged stale).
    """
    s = settings or get_settings()
    now_dt = now or utcnow()
    quote = db.get(PriceQuote, s.fx_symbol)
    if quote is not None and quote.price > 0:
        age = now_dt - quote.as_of
        stale = age > timedelta(hours=s.fx_stale_after_hours)
        return FxRate(float(quote.price), stale, quote.as_of, "stale_quote" if stale else "quote")
    last = db.exec(
        select(Transaction)
        .where(Transaction.currency == "USD", Transaction.fx_to_ils > 0)
        .order_by(col(Transaction.date).desc(), col(Transaction.id).desc())
    ).first()
    if last is not None:
        return FxRate(float(last.fx_to_ils), True, None, "last_transaction")
    return FxRate(s.fx_fallback_usd_ils, True, None, "config")


def get_usd_ils(db: Session, settings: Settings | None = None) -> float:
    """ILS per 1 USD (see `get_fx_rate` for the staleness flag)."""
    return get_fx_rate(db, settings).usd_ils


def to_ils(amount: float, currency: str, usd_ils: float) -> float:
    cur = currency.strip().upper()
    if cur == "ILS":
        return amount
    if cur == "USD":
        return amount * usd_ils
    raise UnknownCurrencyError(f"Cannot convert unknown currency {currency!r} to ILS")


def to_usd(amount: float, currency: str, usd_ils: float) -> float:
    return to_ils(amount, currency, usd_ils) / usd_ils if usd_ils else 0.0
