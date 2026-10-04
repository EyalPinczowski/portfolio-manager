"""Dividend history and the next ex-date through yfinance corporate actions (free, no key).

Behind the `DividendProvider` interface; nothing else calls yfinance for dividends. Yahoo quotes
TASE dividends in agorot like its prices, so the amount is divided by 100 by the *reported
currency* (never by the ".TA" suffix) and the currency is reported as ILS. A symbol with no
dividend on record, or a lookup that fails, is a missing `Field`, never a zero.

Pay dates are not part of Yahoo's dividend series: `pay_date` is filled only when the calendar
reports one (usually US stocks).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from app.config import Settings, get_settings
from app.providers.base import (
    DividendEvent,
    DividendHistory,
    Field,
    Market,
    MissingReason,
    normalize_price,
)
from app.providers.cache import TTLCache, is_rate_limited
from app.timeutil import utcnow

log = logging.getLogger(__name__)
SOURCE = "yfinance"

CurrencyLookup = Callable[[str], str | None]


def _as_date(raw: Any) -> date | None:
    if raw is None:
        return None
    try:
        ts = pd.Timestamp(raw)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


class YFinanceDividendProvider:
    """Implements `DividendProvider` (US and TASE; crypto has no dividends)."""

    name = SOURCE
    markets: frozenset[Market] = frozenset({"US", "TASE"})

    def __init__(
        self,
        settings: Settings | None = None,
        currency_lookup: CurrencyLookup | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings or get_settings()
        self.currency_lookup = currency_lookup
        self._cache: TTLCache[Field[DividendHistory]] = TTLCache(
            self.settings.dividend_cache_ttl_seconds,
            clock,
            max_entries=self.settings.dividend_cache_max_entries,
        )

    # -- network (the only methods that touch yfinance; tests override them) ---------------
    def _fetch_series(self, symbol: str) -> pd.Series:
        import yfinance as yf

        series = yf.Ticker(symbol).dividends
        return series if isinstance(series, pd.Series) else pd.Series(dtype=float)

    def _fetch_calendar(self, symbol: str) -> dict[str, Any]:
        import yfinance as yf

        cal = yf.Ticker(symbol).calendar
        return cal if isinstance(cal, dict) else {}

    def _currency(self, symbol: str) -> str | None:
        if self.currency_lookup is not None:
            return self.currency_lookup(symbol)
        import yfinance as yf

        try:
            info: Any = yf.Ticker(symbol).fast_info
            raw = info.get("currency") if hasattr(info, "get") else info["currency"]
        except Exception:
            return None
        return str(raw).strip() if raw else None

    # -- DividendProvider --------------------------------------------------------------------
    def get_dividends(self, symbol: str) -> Field[DividendHistory]:
        sym = symbol.strip().upper()
        if sym.endswith("-USD") or sym.startswith("^"):
            return Field.missing(SOURCE, "coverage")
        hit = self._cache.get(sym)
        if hit is not None:
            return hit
        try:
            result = self._build(sym)
        except Exception as exc:
            log.warning("dividend lookup failed for %s (%s)", sym, type(exc).__name__)
            reason: MissingReason = "rate_limited" if is_rate_limited(exc) else "unavailable"
            return Field.missing(SOURCE, reason)  # never cached: the next call retries
        self._cache.set(sym, result)
        return result

    def _build(self, sym: str) -> Field[DividendHistory]:
        series = self._fetch_series(sym)
        paid: list[tuple[date, float]] = []
        for idx, value in series.items():
            d = _as_date(idx)
            try:
                amount = float(value)
            except (TypeError, ValueError):
                continue
            if d is not None and amount > 0 and amount == amount and amount != float("inf"):
                paid.append((d, amount))
        if not paid:
            return Field.missing(
                SOURCE, "not_found"
            )  # a non-payer and an unknown symbol look alike
        raw_currency = self._currency(sym)
        if raw_currency is None:
            # Without the currency an agorot amount could be 100x too big: refuse to guess.
            log.warning("no currency for %s: dividends not used", sym)
            return Field.missing(SOURCE, "unavailable")
        paid.sort()
        events: list[DividendEvent] = []
        currency = ""
        for d, raw_amount in paid:
            amount, cur = normalize_price(raw_amount, raw_currency)
            currency = cur or raw_currency
            events.append(DividendEvent(ex_date=d, amount=amount, currency=currency))
        last_ex = paid[-1][0]
        try:
            cal = self._fetch_calendar(sym)
        except Exception as exc:  # the calendar is optional
            log.info("no dividend calendar for %s (%s)", sym, type(exc).__name__)
            cal = {}
        next_ex = _as_date(cal.get("Ex-Dividend Date"))
        if next_ex is not None and next_ex > last_ex:
            events.append(
                DividendEvent(
                    ex_date=next_ex,
                    pay_date=_as_date(cal.get("Dividend Date")),
                    amount=events[-1].amount,  # carried forward: the source gives no amount
                    currency=currency,
                    announced=True,
                    amount_is_estimate=True,
                )
            )
        return Field.ok(DividendHistory(symbol=sym, events=events), SOURCE, utcnow())
