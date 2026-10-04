"""Portfolio X-ray and sector heat map (computed from valuations)."""

from __future__ import annotations

import math
from typing import Any

from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import Portfolio
from app.portfolio.valuation import PortfolioValuation, value_portfolio
from app.portfolio.xray_rules import evaluate_rules, resolve_rules
from app.scoring.risk import (
    Position,
    check_limits,
    compute_exposures,
    resolve_risk_filter,
)


def _position_override(raw: object) -> float | None:
    """The per-holding max position %, or None if absent or not a usable number (bad stored JSON)."""
    value = raw.get("max_position_pct") if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and 0 < value <= 100 else None


def to_positions(valuation: PortfolioValuation) -> list[Position]:
    out: list[Position] = []
    for v in valuation.holdings:
        override = _position_override(v.holding.risk_override)
        out.append(
            Position(
                symbol=v.security.symbol,
                name=v.security.name_en,
                value_ils=v.value_ils,
                sector=v.security.sector,
                country=v.security.country,
                currency=v.currency,
                asset_type=v.security.asset_type,
                max_position_pct=override,
                dual_group=v.security.dual_listing_group,
            )
        )
    return out


def build_xray(
    db: Session, portfolio: Portfolio, settings: Settings | None = None
) -> dict[str, Any]:
    s = settings or get_settings()
    valuation = value_portfolio(db, portfolio, s)
    positions = to_positions(valuation)
    exposures, _ = compute_exposures(positions, s)
    limits = resolve_risk_filter(portfolio.risk_filter, s)
    breaches = check_limits(positions, limits, s)
    return {
        "concentration": exposures.concentration,
        "currency_exposure": [i.model_dump() for i in exposures.currency],
        "country_exposure": [i.model_dump() for i in exposures.country],
        "sector_exposure": [i.model_dump() for i in exposures.sector],
        "home_bias": {
            "country": s.home_country,
            "israel_pct": exposures.home_bias_pct,
            "pct": exposures.home_bias_pct,
        },
        "breaches": [b.model_dump() for b in breaches],
        # Toggleable rules (informational only): breach / ok / off, each with an Explanation.
        "rules": [
            r.model_dump(mode="json")
            for r in evaluate_rules(
                positions, exposures, resolve_rules(db, portfolio, limits, s), s, valuation.as_of
            )
        ],
    }
