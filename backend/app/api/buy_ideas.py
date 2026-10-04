"""POST /api/portfolios/{id}/buy-ideas: ranked screener candidates for new money.

Owner-scoped (someone else's portfolio is a 404), read-only and cache-only: the request never calls
a data provider. Every input is required (no default amount, horizon, risk preset or market). The
answer is a list of neutral candidates (score, confidence, size, levels, "Why?"); it carries no
verdict field. The launch gate state is reported so the UI can say why nothing stronger is shown.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import Field, field_validator

from app.api.schemas import Body, Horizon, MarketKey, Positive, Symbol
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.launchgate import GateDep
from app.repo import get_portfolio
from app.scoring.risk import PresetName
from app.scoring.screener import CandidatesOut, screen
from app.strictjson import StrictJsonRoute

router = APIRouter(tags=["screener"], route_class=StrictJsonRoute)

ScreenAssetType = Literal["stock", "etf", "crypto"]


class BuyIdeasIn(Body):
    """Everything is required except the optional exclusion list."""

    amount: Positive
    currency: Literal["ILS", "USD"]
    horizon: Horizon
    risk: PresetName
    markets: list[MarketKey] = Field(min_length=1, max_length=3)
    asset_types: list[ScreenAssetType] = Field(min_length=1, max_length=3)
    exclude_symbols: list[Symbol] = Field(default_factory=list, max_length=200)

    @field_validator("markets", "asset_types")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("values must be unique")
        return v


@router.post("/portfolios/{portfolio_id}/buy-ideas", response_model=CandidatesOut)
def buy_ideas(
    portfolio_id: int,
    body: BuyIdeasIn,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    gate: GateDep,
) -> CandidatesOut:
    """Candidates for `amount` of new money, from cached universe scores only."""
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    status = gate.evaluate()
    return screen(
        db,
        p,
        amount=body.amount,
        currency=body.currency,
        horizon=body.horizon,
        risk_preset=body.risk,
        markets=list(body.markets),
        asset_types=list(body.asset_types),
        exclude_symbols=list(body.exclude_symbols),
        gate_open=status.open,
        gate_reasons=status.reasons,
        settings=settings,
    )
