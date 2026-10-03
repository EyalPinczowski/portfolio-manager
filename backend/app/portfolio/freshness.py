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
