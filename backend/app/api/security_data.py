"""Per-symbol display data for the holding page: analyst view and daily candles. Login required.

Public market data, nothing user-owned, so only the login and a per-user rate limit apply. The
analyst view is the analysts' own distribution (never the app's verdict) and feeds no score.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.analyze.chartist import chart_candles
from app.analyze.schemas import Candle
from app.auth.deps import SettingsDep, UserDep
from app.auth.ratelimit import enforce_limit, security_data_limiter
from app.strictjson import StrictJsonRoute

router = APIRouter(tags=["securities"], route_class=StrictJsonRoute)

SymbolPath = Annotated[str, Path(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9.^=\-]+$")]


class AnalystCounts(BaseModel):
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0


class AnalystTargetsOut(BaseModel):
    low: float | None = None
    mean: float | None = None
    high: float | None = None
    currency: str | None = None


class AnalystsOut(BaseModel):
    symbol: str
    status: Literal["ok", "no_coverage"]
    as_of: date | None = None
    counts: AnalystCounts = AnalystCounts()
    analysts_total: int = 0
    targets: AnalystTargetsOut | None = None
    source: str | None = None


class CandlesOut(BaseModel):
    symbol: str
    candles: list[Candle]


def _limit(user_id: int | None, settings: SettingsDep) -> None:
    enforce_limit(
        security_data_limiter,
        f"user:{user_id}",
        settings.security_data_rate_limit_per_hour,
        3600.0,
    )


@router.get("/securities/{symbol}/analysts", response_model=AnalystsOut)
def analysts(symbol: SymbolPath, user: UserDep, settings: SettingsDep) -> AnalystsOut:
    """Analyst rating counts (latest month) and price targets when available; `no_coverage`
    when neither exists or a provider fails. Never raises for missing data."""
    from app.providers.registry import get_analyst_targets, get_analyst_trends

    _limit(user.id, settings)
    sym = symbol.upper()
    try:
        trends = get_analyst_trends().trends(sym)
    except Exception:
        trends = None
    try:
        targets = get_analyst_targets().targets(sym)
    except Exception:
        targets = None
    latest = trends[0] if trends else None
    if latest is None or latest.total == 0:
        latest = None
    if latest is None and targets is None:
        return AnalystsOut(symbol=sym, status="no_coverage")
    return AnalystsOut(
        symbol=sym,
        status="ok",
        as_of=latest.period if latest else None,
        counts=AnalystCounts(
            strong_buy=latest.strong_buy,
            buy=latest.buy,
            hold=latest.hold,
            sell=latest.sell,
            strong_sell=latest.strong_sell,
        )
        if latest
        else AnalystCounts(),
        analysts_total=latest.total if latest else 0,
        targets=AnalystTargetsOut(**targets.model_dump()) if targets else None,
        source="finnhub" if (settings.finnhub_api_key and latest) else "yfinance",
    )


@router.get("/securities/{symbol}/candles", response_model=CandlesOut)
def candles(
    symbol: SymbolPath,
    user: UserDep,
    settings: SettingsDep,
    days: Annotated[int | None, Query(ge=20)] = None,
) -> CandlesOut:
    """Daily OHLC bars (listing currency, agorot already converted) from the history provider,
    which reads the stored `daily_bar` rows first. Display only; empty when no history."""
    from app.providers.registry import get_providers

    _limit(user.id, settings)
    sym = symbol.upper()
    n = min(days or settings.candles_default_days, settings.candles_max_days)
    try:
        df = get_providers().history.get_history(sym, n)
    except Exception:
        df = None
    return CandlesOut(symbol=sym, candles=[] if df is None or df.empty else chart_candles(df, n))
