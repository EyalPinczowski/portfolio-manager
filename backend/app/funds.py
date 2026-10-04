"""Israeli fund holdings: symbol convention, return maths and the valuation rules.

A fund is held under the symbol `GEMEL-<fund number>`, a `Security` with `asset_type="fund"`,
market TASE, currency ILS (never agorot: the dataset is not a Yahoo quote). GemelNet gives monthly
returns, not a unit price, so a fund's value is the user's **manual value** (`FundHolding`); with
none the holding is valued at its cost (stale) or at 0, and says so. Nothing is invented.

Funds are outside the time-weighted return (like any holding without a real market price): a
manual value moves by deposits, withdrawals and gains the app cannot tell apart. They are in the
displayed total, flagged `price_stale`. No exit levels: a fund has no tradable intraday price.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.providers.base import FundSeries

FUND_SYMBOL_PREFIX = "GEMEL-"
_FUND_SYMBOL = re.compile(r"GEMEL-(\d{1,9})")

ValueBasis = Literal["manual_value", "cost_only", "no_value"]
FUND_FLAGS = {
    "monthly_data_only": "The dataset is monthly: there is no daily price for a fund.",
    "no_price_manual_value": "No fund price is available: the value is the one you entered.",
    "no_value": "No value yet: enter the value from your fund statement.",
    "cost_only": "No value entered: shown at cost until you enter the current value.",
}


def is_fund_symbol(symbol: str) -> bool:
    return _FUND_SYMBOL.fullmatch(symbol.strip().upper()) is not None


def fund_id_of(symbol: str) -> str | None:
    m = _FUND_SYMBOL.fullmatch(symbol.strip().upper())
    return m.group(1) if m else None


def fund_symbol(fund_id: str) -> str:
    return f"{FUND_SYMBOL_PREFIX}{fund_id}"


def manual_as_of(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def value_flags(basis: ValueBasis) -> list[str]:
    flags = ["monthly_data_only"]
    if basis == "manual_value":
        flags.append("no_price_manual_value")
    else:
        flags.append(basis)
    return flags


# ---------------------------------------------------------------- returns
HORIZON_MONTHS: dict[str, int] = {"1m": 1, "3m": 3, "1y": 12, "3y": 36}
ReturnMissing = Literal["not_enough_months", "gap_in_series", "no_value"]


class FundReturn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    horizon: Literal["1m", "3m", "1y", "3y"]
    months: int
    return_pct: float | None = None  # compounded from the monthly returns, cumulative
    annualised_pct: float | None = None  # only for horizons of a year or more
    category_avg_pct: float | None = None  # only 1m: the dataset gives peers per month
    vs_category_pts: float | None = None  # return_pct - category_avg_pct, percentage points
    missing_reason: ReturnMissing | None = None


def _next_month(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def compute_returns(series: FundSeries) -> list[FundReturn]:
    """Cumulative returns over 1m/3m/1y/3y ending at the latest reported month.

    A horizon needs that many *consecutive* months, each with a value. A shorter series is
    `not_enough_months`, a hole or an empty month is `gap_in_series`: never filled with 0.
    """
    out: list[FundReturn] = []
    months = series.months
    for horizon, n in HORIZON_MONTHS.items():
        window = months[-n:]
        row = FundReturn(horizon=horizon, months=n)  # type: ignore[arg-type]
        if len(window) < n:
            row.missing_reason = "not_enough_months"
        elif any(m.monthly_return_pct is None for m in window) or any(
            _next_month(a.period) != b.period for a, b in pairwise(window)
        ):
            row.missing_reason = "gap_in_series"
        else:
            growth = 1.0
            for m in window:
                assert m.monthly_return_pct is not None
                growth *= 1.0 + m.monthly_return_pct / 100.0
            if growth > 0:
                row.return_pct = round((growth - 1.0) * 100.0, 4)
                if n >= 12:
                    row.annualised_pct = round((growth ** (12.0 / n) - 1.0) * 100.0, 4)
            else:
                row.missing_reason = "gap_in_series"  # a -100% month: the data is not usable
        if (
            horizon == "1m"
            and row.return_pct is not None
            and series.category_avg_monthly_return_pct is not None
            and series.category_period == months[-1].period
        ):
            row.category_avg_pct = round(series.category_avg_monthly_return_pct, 4)
            row.vs_category_pts = round(row.return_pct - row.category_avg_pct, 4)
        out.append(row)
    return out


def data_is_stale(series: FundSeries, today: date, settings: Settings) -> bool:
    """The latest reporting month ended more than `gemelnet_stale_after_days` ago."""
    last = series.months[-1].period
    y, m = int(last[:4]), int(last[5:7])
    end = date(y + (m == 12), m % 12 + 1, 1)
    return (today - end).days > settings.gemelnet_stale_after_days
