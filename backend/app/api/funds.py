"""Israeli funds from GemelNet (data.gov.il): search and detail with returns. Login required.

The data is monthly and public; nothing here is user-owned, so there is nothing to scope except the
login itself (and a per-user rate limit, because a search may call the dataset). A fund is added
to a portfolio with the ordinary `POST /portfolios/{id}/holdings` using the symbol
`GEMEL-<fund number>`. The dataset has returns, not a unit price: the value is the user's own entry.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.auth.deps import SettingsDep, UserDep
from app.auth.ratelimit import enforce_limit, fund_search_limiter
from app.config import DISCLAIMER
from app.errors import ApiError
from app.funds import FundReturn, compute_returns, data_is_stale, fund_symbol
from app.providers.base import FundInfo, FundMonth, MissingReason
from app.providers.gemelnet import period_end
from app.providers.registry import get_fund_provider
from app.strictjson import StrictJsonRoute
from app.timeutil import local_today

router = APIRouter(tags=["funds"], route_class=StrictJsonRoute)

DataStatus = Literal["ok", "no_data", "unavailable", "rate_limited"]
_STATUS: dict[MissingReason, DataStatus] = {
    "not_found": "no_data",
    "coverage": "no_data",
    "unavailable": "unavailable",
    "rate_limited": "rate_limited",
    "stale": "unavailable",
}


class FundSearchItem(BaseModel):
    fund_id: str
    symbol: str  # what to send to POST /portfolios/{id}/holdings
    name: str
    classification: str | None = None
    managing_corporation: str | None = None


class FundSearchOut(BaseModel):
    query: str
    data_status: DataStatus
    results: list[FundSearchItem]
    source: str
    credit: str
    disclaimer: str = DISCLAIMER


class FundDetailOut(BaseModel):
    fund_id: str
    symbol: str
    name: str
    classification: str | None = None
    managing_corporation: str | None = None
    latest_period: str  # "YYYY-MM": the newest reporting month
    data_as_of: datetime  # end of that month
    data_stale: bool  # monthly data older than `gemelnet_stale_after_days`
    total_assets: float | None = None  # as the dataset states it
    management_fee_pct: float | None = None
    returns: list[FundReturn]  # 1m / 3m / 1y / 3y, cumulative, with the gap reason when missing
    category_peer_count: int  # funds behind the category average (0: no average)
    monthly_series: list[FundMonth]  # ascending
    price_available: bool = False  # always false: no unit price in the dataset
    value_note: str = (
        "The dataset has monthly returns only. Enter the value from your fund statement as the "
        "holding's manual value."
    )
    source: str
    credit: str
    disclaimer: str = DISCLAIMER


def _status(value: object, reason: MissingReason | None) -> DataStatus:
    if value is not None:
        return "ok"
    return _STATUS[reason] if reason is not None else "no_data"


@router.get("/funds/search", response_model=FundSearchOut)
def search_funds(
    user: UserDep,
    settings: SettingsDep,
    q: Annotated[str, Query(min_length=1, max_length=80)],
) -> FundSearchOut:
    """Funds by name or number. `data_status` says why a list is empty: `no_data` (no match),
    `unavailable` (the dataset cannot be reached or is not configured) or `rate_limited`."""
    assert user.id is not None
    enforce_limit(
        fund_search_limiter, f"user:{user.id}", settings.fund_search_rate_limit_per_hour, 3600.0
    )
    field = get_fund_provider().search_funds(q)
    funds: list[FundInfo] = field.value or []
    return FundSearchOut(
        query=q,
        data_status=_status(field.value, field.missing_reason),
        results=[
            FundSearchItem(
                fund_id=f.fund_id,
                symbol=fund_symbol(f.fund_id),
                name=f.name,
                classification=f.classification,
                managing_corporation=f.managing_corporation,
            )
            for f in funds
        ],
        source=field.source,
        credit=settings.gemelnet_credit,
    )


@router.get("/funds/{fund_id}", response_model=FundDetailOut)
def fund_detail(
    fund_id: Annotated[str, Path(pattern=r"^\d{1,9}$")], user: UserDep, settings: SettingsDep
) -> FundDetailOut:
    """One fund with 1m/3m/1y/3y returns compounded from its monthly series, and the 1m return
    against the category average when enough peers reported."""
    assert user.id is not None
    enforce_limit(
        fund_search_limiter, f"user:{user.id}", settings.fund_search_rate_limit_per_hour, 3600.0
    )
    field = get_fund_provider().get_fund(fund_id)
    series = field.value
    if series is None:
        status = _status(field.value, field.missing_reason)
        if status == "no_data":
            raise ApiError(404, "fund_not_found", "No data for this fund number")
        if status == "rate_limited":
            raise ApiError(429, "fund_data_rate_limited", "Fund data is rate limited, try later")
        raise ApiError(503, "fund_data_unavailable", "Fund data is unavailable right now")
    last = series.months[-1]
    today: date = local_today()
    return FundDetailOut(
        fund_id=series.info.fund_id,
        symbol=fund_symbol(series.info.fund_id),
        name=series.info.name,
        classification=series.info.classification,
        managing_corporation=series.info.managing_corporation,
        latest_period=last.period,
        data_as_of=period_end(last.period),
        data_stale=data_is_stale(series, today, settings),
        total_assets=last.total_assets,
        management_fee_pct=last.management_fee_pct,
        returns=compute_returns(series),
        category_peer_count=series.category_peer_count,
        monthly_series=series.months,
        source=field.source,
        credit=settings.gemelnet_credit,
    )
