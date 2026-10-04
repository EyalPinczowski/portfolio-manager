"""Is a holding's price good enough to suggest exit levels on?

Exit levels (stop-loss, take-profit) must never be built on a number that is not a recent market
price: a cost fallback or a broker-screenshot price is only a placeholder, and a quote older than
the market's freshness window (`Settings.price_fresh_window_minutes`) may be a halted or broken
feed. While a market is closed, the last session's closing quote is as fresh as a price can be, so
it counts if it is within the same window of that close.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import Settings, get_settings
from app.portfolio.valuation import ValuedHolding
from app.scheduler.calendars import is_market_open, last_session_close
from app.timeutil import as_utc, utcnow


def price_is_fresh(
    holding: ValuedHolding, now: datetime | None = None, settings: Settings | None = None
) -> bool:
    s = settings or get_settings()
    if holding.price_source != "quote" or holding.stale or holding.as_of is None:
        return False  # a cost or screenshot price is never fresh
    if holding.price_basis != "live" or holding.quote_flag is not None:
        return False  # a daily/last-close fallback, or two sources that disagree: never fresh
    market = holding.security.market
    window = timedelta(
        minutes=s.price_fresh_window_minutes.get(market, s.quote_stale_after_minutes)
    )
    at = as_utc(now or utcnow())
    quote_at = as_utc(holding.as_of)
    if quote_at > at + window:
        return False  # from the future: a broken clock or feed
    if at - quote_at <= window:
        return True
    if is_market_open(market, at, s):
        return False  # the market is open and the quote did not move for a whole window
    close = last_session_close(market, at, s)
    return close is not None and quote_at >= close - window


class StalePriceError(ValueError):
    """Exit levels were asked for on a price that is not a fresh market price."""

    def __init__(self, symbol: str, reason: str) -> None:
        super().__init__(f"{symbol}: no fresh price for exit levels ({reason})")
        self.symbol, self.reason = symbol, reason


def exit_level_price(
    holding: ValuedHolding, now: datetime | None = None, settings: Settings | None = None
) -> float:
    """The only price exit-level code may start from: a fresh live quote, never a cost price, a
    screenshot price, a last-close fallback, a flagged quote or an old row. Raises
    `StalePriceError` (the API answers `needs_fresh_price`) instead of guessing."""
    sym = holding.holding.symbol
    if holding.price_source != "quote":
        raise StalePriceError(sym, f"price is a {holding.price_source} placeholder")
    if holding.quote_flag is not None:
        raise StalePriceError(sym, holding.quote_flag)
    if holding.price_basis != "live":
        raise StalePriceError(sym, "last close only, dated " + str(holding.as_of))
    if not price_is_fresh(holding, now, settings) or holding.price <= 0:
        raise StalePriceError(sym, f"quote from {holding.as_of} is too old")
    return holding.price
