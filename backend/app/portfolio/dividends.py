"""Dividend calendar and a 12-month income estimate for the symbols a portfolio holds.

Facts come from a `DividendProvider` (yfinance corporate actions): past payments with their
ex-dates, and at most the next ex-date Yahoo reports. Anything the source does not know stays
unknown: a missing pay date is None, a symbol with no dividend on record is `no_data` (a non-payer
and an unknown symbol look the same to the source), and a failed lookup says `unavailable`.

The income figure is an **estimate**: the dividends paid per share in the last 12 months times the
quantity held now, assuming the company pays the same again. A company that stopped paying
(`dividend_stale_after_days`) is left out and named. Informational only; it never feeds a score.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from app.config import DISCLAIMER, Settings
from app.models import Portfolio
from app.portfolio.valuation import value_portfolio
from app.providers.base import DividendEvent, DividendProvider
from app.providers.fx_provider import UnknownCurrencyError, to_ils
from app.timeutil import as_utc, utcnow

DataStatus = Literal[
    "ok",  # dividends on record
    "no_data",  # nothing on record (a non-payer and an unknown symbol look the same)
    "stopped",  # the last payment is older than `dividend_stale_after_days`
    "not_applicable",  # funds and crypto have no dividends here
    "unavailable",  # the lookup failed
    "rate_limited",
    "not_checked",  # over the per-request cap
]
ESTIMATE_NOTE = (
    "An estimate: the dividends paid per share in the last {months} months times the quantity "
    "you hold now, assuming the same payments again. Companies can change or stop a dividend, "
    "and the dates and amounts are not announcements."
)


class _Out(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class UpcomingDividend(_Out):
    symbol: str
    name_en: str
    ex_date: date
    pay_date: date | None = None  # None: the source does not give it
    amount_per_share: float
    currency: str  # normalised: TASE amounts are ILS, never agorot
    quantity: float
    expected_amount: float  # in `currency`
    expected_amount_ils: float | None = None
    amount_is_estimate: bool  # True: the last payment carried forward (no announced amount)
    source: str


class SymbolDividendStatus(_Out):
    symbol: str
    data_status: DataStatus
    last_ex_date: date | None = None
    last_amount_per_share: float | None = None
    currency: str | None = None
    payments_in_period: int = 0
    per_share_in_period: float | None = None


class IncomeLine(_Out):
    symbol: str
    quantity: float
    per_share: float
    currency: str
    amount: float  # in `currency`
    amount_ils: float | None = None  # None: no rate for that currency
    payments_counted: int


class IncomeEstimate(_Out):
    is_estimate: bool = True
    basis: Literal["trailing_payments_repeated"] = "trailing_payments_repeated"
    months: int
    total_ils: float | None = None  # None: no symbol has data
    lines: list[IncomeLine]
    symbols_without_data: list[str]
    note: str


class DividendsOut(_Out):
    portfolio_id: int
    as_of: datetime
    window_days: int
    upcoming: list[UpcomingDividend]
    symbols: list[SymbolDividendStatus]
    income_estimate: IncomeEstimate
    source: str
    credit: str
    disclaimer: str = DISCLAIMER


def _period_start(today: date, months: int) -> date:
    return today - timedelta(days=round(months * 365.25 / 12))


def _ils(amount: float, currency: str, usd_ils: float) -> float | None:
    try:
        return round(to_ils(amount, currency, usd_ils), 2)
    except UnknownCurrencyError:
        return None


def build_dividends(
    db: Session,
    portfolio: Portfolio,
    provider: DividendProvider,
    settings: Settings,
    today: date,
) -> DividendsOut:
    val = value_portfolio(db, portfolio, settings)
    start = _period_start(today, settings.dividend_estimate_months)
    horizon_end = today + timedelta(days=settings.dividend_upcoming_days)
    upcoming: list[UpcomingDividend] = []
    statuses: list[SymbolDividendStatus] = []
    lines: list[IncomeLine] = []
    without: list[str] = []
    checked = 0
    for v in sorted(val.holdings, key=lambda x: x.holding.symbol):
        h, sec = v.holding, v.security
        if sec.asset_type in ("fund", "crypto", "cash", "bond"):
            statuses.append(SymbolDividendStatus(symbol=h.symbol, data_status="not_applicable"))
            continue
        if checked >= settings.dividend_max_symbols_per_request:
            statuses.append(SymbolDividendStatus(symbol=h.symbol, data_status="not_checked"))
            without.append(h.symbol)
            continue
        checked += 1
        field = provider.get_dividends(h.symbol)
        if field.value is None:
            status: DataStatus = (
                "unavailable"
                if field.missing_reason == "unavailable"
                else "rate_limited"
                if field.missing_reason == "rate_limited"
                else "not_applicable"
                if field.missing_reason == "coverage"
                else "no_data"
            )
            statuses.append(SymbolDividendStatus(symbol=h.symbol, data_status=status))
            if status != "not_applicable":
                without.append(h.symbol)
            continue
        events: list[DividendEvent] = field.value.events
        paid = [e for e in events if not e.announced and e.ex_date <= today]
        last = paid[-1] if paid else None
        for e in events:
            if e.ex_date >= today and e.ex_date <= horizon_end:
                upcoming.append(
                    UpcomingDividend(
                        symbol=h.symbol,
                        name_en=sec.name_en,
                        ex_date=e.ex_date,
                        pay_date=e.pay_date,
                        amount_per_share=e.amount,
                        currency=e.currency,
                        quantity=h.quantity,
                        expected_amount=round(e.amount * h.quantity, 4),
                        expected_amount_ils=_ils(e.amount * h.quantity, e.currency, val.usd_ils),
                        amount_is_estimate=e.amount_is_estimate,
                        source=field.source,
                    )
                )
        in_period = [e for e in paid if e.ex_date > start]
        stopped = last is None or (today - last.ex_date).days > settings.dividend_stale_after_days
        currency = last.currency if last else None
        per_share = round(sum(e.amount for e in in_period), 6) if in_period else None
        status = "stopped" if stopped else "ok" if in_period else "stopped"
        statuses.append(
            SymbolDividendStatus(
                symbol=h.symbol,
                data_status=status,
                last_ex_date=last.ex_date if last else None,
                last_amount_per_share=last.amount if last else None,
                currency=currency,
                payments_in_period=len(in_period),
                per_share_in_period=per_share,
            )
        )
        if status == "ok" and per_share is not None and currency is not None:
            amount = round(per_share * h.quantity, 4)
            lines.append(
                IncomeLine(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    per_share=per_share,
                    currency=currency,
                    amount=amount,
                    amount_ils=_ils(amount, currency, val.usd_ils),
                    payments_counted=len(in_period),
                )
            )
        else:
            without.append(h.symbol)
    upcoming.sort(key=lambda u: (u.ex_date, u.symbol))
    convertible = [ln.amount_ils for ln in lines if ln.amount_ils is not None]
    return DividendsOut(
        portfolio_id=portfolio.id or 0,
        as_of=as_utc(utcnow()),
        window_days=settings.dividend_upcoming_days,
        upcoming=upcoming,
        symbols=statuses,
        income_estimate=IncomeEstimate(
            months=settings.dividend_estimate_months,
            total_ils=round(sum(convertible), 2) if convertible else None,
            lines=lines,
            symbols_without_data=without,
            note=ESTIMATE_NOTE.format(months=settings.dividend_estimate_months),
        ),
        source=provider.name,
        credit=settings.dividend_credit,
    )
