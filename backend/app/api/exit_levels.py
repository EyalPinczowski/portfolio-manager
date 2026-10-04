"""Exit levels: stop-loss and take-profit levels for one holding, and a whole-portfolio review.

Both endpoints are owner-scoped (a holding or portfolio of someone else is a 404) and read-only:
nothing is saved, the horizon is never filled in for the user, and nothing here places an order.
`prior_stop` / `prior_stops` carry a stop the caller already has, so the ratchet (a stop only
moves up) works without storing a trailing-stop state yet.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.schemas import BIG, Body, Horizon, Positive, Symbol
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.config import DISCLAIMER, Settings
from app.models import Holding, Portfolio
from app.portfolio.freshness import StalePriceError, exit_level_price
from app.portfolio.valuation import PortfolioValuation, ValuedHolding, value_portfolio
from app.providers.fx_provider import to_ils
from app.providers.registry import get_providers
from app.repo import get_holding, get_portfolio
from app.scoring.exit_levels import (
    AnalystTargets,
    ExitLevelsResult,
    ReasonCode,
    StopFit,
    StopState,
    compute_exit_levels,
    no_levels_result,
    parse_horizon,
)
from app.scoring.risk import PRESETS, PresetName, RiskFilter, resolve_risk_filter
from app.strictjson import StrictJsonRoute

log = logging.getLogger(__name__)
router = APIRouter(tags=["exit-levels"], route_class=StrictJsonRoute)

PriceQuery = Annotated[float, Query(gt=0, le=BIG, allow_inf_nan=False)]


# ---------------------------------------------------------------- schemas
class AnalystTargetsIn(Body):
    mean: Positive | None = None
    high: Positive | None = None


class ExitReviewIn(Body):
    """A what-if: `horizon` / `risk` override every holding's own for this review only."""

    horizon: Horizon | None = None
    risk: PresetName | None = None
    prior_stops: dict[Symbol, Positive] = Field(default_factory=dict, max_length=500)
    analyst_targets: dict[Symbol, AnalystTargetsIn] = Field(default_factory=dict, max_length=500)


class ReviewRow(BaseModel):
    holding_id: int
    symbol: str
    name_en: str
    name_he: str
    quantity: float
    currency: str
    avg_cost: float | None = None
    horizon: Horizon | None = None
    horizon_source: Literal["holding", "override"] | None = None
    status: Literal["levels", "needs_horizon", "no_levels"]
    reason_code: ReasonCode | None = None
    price: float | None = None
    stop_price: float | None = None
    stop_distance_pct: float | None = None
    take_profit_prices: list[float] = Field(default_factory=list)
    risk_ils: float | None = None
    risk_usd: float | None = None
    risk_pct_of_portfolio: float | None = None
    smaller_size_needed: bool = False
    keep_fraction: float | None = None
    stop_fit: StopFit | None = None
    levels: ExitLevelsResult


class RiskContributor(BaseModel):
    symbol: str
    risk_ils: float
    risk_pct_of_portfolio: float
    share_of_total_risk_pct: float


class MissingStop(BaseModel):
    symbol: str
    reason_code: ReasonCode | None = None
    reason: str


class ReviewTotals(BaseModel):
    total_risk_ils: float
    total_risk_usd: float
    total_risk_pct_of_portfolio: float
    limit_pct: float  # max_total_portfolio_risk_pct of the filter in force
    over_limit: bool
    positions_with_stop: int
    top_contributors: list[RiskContributor]
    positions_without_stop: list[MissingStop]
    stops_too_tight: list[str]
    stops_too_wide: list[str]
    smaller_size_needed: list[str]


class ExitReviewOut(BaseModel):
    portfolio_id: int
    portfolio_value_ils: float
    rows: list[ReviewRow]
    totals: ReviewTotals
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------- helpers
def _risk_for(
    portfolio: Portfolio, holding: Holding | None, preset: str | None, settings: Settings
) -> RiskFilter:
    """An explicit preset (what-if) wins; else the holding's own override; else the portfolio's."""
    if preset is not None:
        return resolve_risk_filter({"preset": PRESETS[preset].name}, settings)
    own = (
        {k: v for k, v in (holding.risk_override or {}).items() if v is not None} if holding else {}
    )
    base = portfolio.risk_filter if isinstance(portfolio.risk_filter, dict) else {}
    if own.get("preset"):
        return resolve_risk_filter(
            own, settings
        )  # a different preset: do not mix in the old numbers
    return resolve_risk_filter({**base, **own}, settings)


def _history(v: ValuedHolding, settings: Settings) -> pd.DataFrame | None:
    """History only when a fresh price makes levels possible (saves provider calls)."""
    try:
        exit_level_price(v, None, settings)
    except StalePriceError:
        return None
    try:
        return get_providers().history.get_history(v.holding.symbol, settings.history_days)
    except Exception:  # a provider failure answers no_levels, never a 500
        log.warning("history fetch failed for %s", v.holding.symbol)
        return None


def _valued(val: PortfolioValuation, holding_id: int | None) -> ValuedHolding | None:
    return next((v for v in val.holdings if v.holding.id == holding_id), None)


def _unavailable(holding: Holding, risk: RiskFilter, horizon: str | None) -> ExitLevelsResult:
    return no_levels_result(
        holding.symbol,
        "no_levels",
        "stale_price",
        "No levels: this holding has no price yet.",
        horizon=parse_horizon(horizon),
        risk=risk,
    )


# ---------------------------------------------------------------- one holding
@router.get("/holdings/{holding_id}/exit-levels", response_model=ExitLevelsResult)
def holding_exit_levels(
    holding_id: int,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    horizon: Horizon | None = None,
    risk: PresetName | None = None,
    prior_stop: PriceQuery | None = None,
    analyst_mean: PriceQuery | None = None,
    analyst_high: PriceQuery | None = None,
) -> ExitLevelsResult:
    """Stop / take-profit levels. `horizon` overrides the holding's own (a what-if, not saved); a
    holding with no horizon and no override answers `needs_horizon`. `risk` is a preset name."""
    assert user.id is not None
    h, p = get_holding(db, user.id, holding_id)
    rf = _risk_for(p, h, risk, settings)
    hz = horizon or h.horizon
    val = value_portfolio(db, p, settings)
    v = _valued(val, h.id)
    if v is None:
        return _unavailable(h, rf, hz)
    analyst = (
        AnalystTargets(mean=analyst_mean, high=analyst_high)
        if analyst_mean is not None or analyst_high is not None
        else None
    )
    return compute_exit_levels(
        v,
        hz,
        rf,
        _history(v, settings) if hz else None,
        analyst=analyst,
        state=StopState(stop=prior_stop) if prior_stop is not None else None,
        portfolio_value_ils=val.total_ils,
        settings=settings,
    )


# ---------------------------------------------------------------- whole portfolio
def _row(
    v: ValuedHolding,
    res: ExitLevelsResult,
    source: Literal["holding", "override"] | None,
    total_ils: float,
) -> ReviewRow:
    h = v.holding
    assert h.id is not None
    eff = res.effective_stop
    risk_ils = risk_usd = pct = None
    if res.status == "levels" and res.risk_to_stop is not None and eff is not None and res.price:
        gap = max(0.0, res.price - eff.price) * h.quantity
        risk_ils = round(to_ils(gap, v.currency, v.usd_ils), 2)
        risk_usd = round(risk_ils / v.usd_ils if v.usd_ils else 0.0, 2)
        pct = round(risk_ils / total_ils * 100.0, 4) if total_ils else None
    sg = res.size_guidance
    return ReviewRow(
        holding_id=h.id,
        symbol=h.symbol,
        name_en=v.security.name_en,
        name_he=v.security.name_he,
        quantity=h.quantity,
        currency=v.currency,
        avg_cost=h.avg_cost,
        horizon=res.horizon,
        horizon_source=source if res.horizon else None,
        status=res.status,
        reason_code=res.reason_code,
        price=res.price,
        stop_price=eff.price if eff else None,
        stop_distance_pct=eff.distance_pct if eff else None,
        take_profit_prices=[t.price for t in res.take_profits],
        risk_ils=risk_ils,
        risk_usd=risk_usd,
        risk_pct_of_portfolio=pct,
        smaller_size_needed=bool(sg and sg.needed),
        keep_fraction=sg.keep_fraction if sg else None,
        stop_fit=res.stop_fit,
        levels=res,
    )


@router.post("/portfolios/{portfolio_id}/exit-review", response_model=ExitReviewOut)
def exit_review(
    portfolio_id: int, body: ExitReviewIn, user: UserDep, db: DbDep, settings: SettingsDep
) -> ExitReviewOut:
    """Levels for every holding plus portfolio totals: the total risk to the stops, the biggest
    contributors, the positions with no stop and stops that look too tight or too wide."""
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    assert p.id is not None
    val = value_portfolio(db, p, settings)
    total = val.total_ils
    rows: list[ReviewRow] = []
    for v in val.holdings:
        h = v.holding
        rf = _risk_for(p, h, body.risk, settings)
        hz = body.horizon or h.horizon
        src: Literal["holding", "override"] | None = (
            "override" if body.horizon else "holding" if h.horizon else None
        )
        analyst_in = body.analyst_targets.get(h.symbol)
        prior = body.prior_stops.get(h.symbol)
        res = compute_exit_levels(
            v,
            hz,
            rf,
            _history(v, settings) if hz else None,
            analyst=AnalystTargets(mean=analyst_in.mean, high=analyst_in.high)
            if analyst_in
            else None,
            state=StopState(stop=prior) if prior is not None else None,
            portfolio_value_ils=total,
            settings=settings,
        )
        rows.append(_row(v, res, src, total))
    rows.sort(key=lambda r: (r.status != "needs_horizon", -(r.risk_ils or 0.0), r.symbol))

    limits = _risk_for(p, None, body.risk, settings)
    with_stop = [r for r in rows if r.risk_ils is not None]
    total_risk = round(sum(r.risk_ils or 0.0 for r in with_stop), 2)
    rate = val.usd_ils or 1.0
    top = sorted(with_stop, key=lambda r: -(r.risk_ils or 0.0))[
        : settings.exit_review_top_contributors
    ]
    totals = ReviewTotals(
        total_risk_ils=total_risk,
        total_risk_usd=round(total_risk / rate, 2),
        total_risk_pct_of_portfolio=round(total_risk / total * 100.0, 4) if total else 0.0,
        limit_pct=limits.max_total_portfolio_risk_pct,
        over_limit=bool(total and total_risk / total * 100.0 > limits.max_total_portfolio_risk_pct),
        positions_with_stop=len(with_stop),
        top_contributors=[
            RiskContributor(
                symbol=r.symbol,
                risk_ils=r.risk_ils or 0.0,
                risk_pct_of_portfolio=r.risk_pct_of_portfolio or 0.0,
                share_of_total_risk_pct=round((r.risk_ils or 0.0) / total_risk * 100.0, 2)
                if total_risk
                else 0.0,
            )
            for r in top
            if (r.risk_ils or 0.0) > 0
        ],
        positions_without_stop=[
            MissingStop(symbol=r.symbol, reason_code=r.reason_code, reason=r.levels.reason)
            for r in rows
            if r.stop_price is None
        ],
        stops_too_tight=[r.symbol for r in rows if r.stop_fit == "too_tight"],
        stops_too_wide=[r.symbol for r in rows if r.stop_fit == "too_wide"],
        smaller_size_needed=[r.symbol for r in rows if r.smaller_size_needed],
    )
    return ExitReviewOut(
        portfolio_id=p.id, portfolio_value_ils=round(total, 2), rows=rows, totals=totals
    )
