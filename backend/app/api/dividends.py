"""Dividend calendar for a portfolio's symbols (informational, owner-scoped, read-only)."""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.deps import DbDep, SettingsDep, UserDep
from app.portfolio.dividends import DividendsOut, build_dividends
from app.providers.registry import get_dividend_provider
from app.repo import get_portfolio
from app.strictjson import StrictJsonRoute
from app.timeutil import local_today

router = APIRouter(tags=["dividends"], route_class=StrictJsonRoute)


@router.get("/portfolios/{portfolio_id}/dividends", response_model=DividendsOut)
def dividends(portfolio_id: int, user: UserDep, db: DbDep, settings: SettingsDep) -> DividendsOut:
    """Upcoming ex-dividend and pay dates with amounts for the held stocks and ETFs, plus an
    estimate of the next 12 months' income. A symbol the source knows nothing about says so
    (`no_data`); nothing is guessed."""
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    return build_dividends(db, p, get_dividend_provider(), settings, local_today())
