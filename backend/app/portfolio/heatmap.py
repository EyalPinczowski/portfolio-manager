"""Sector heat map data."""

from __future__ import annotations

from typing import Any

from app.portfolio.valuation import PortfolioValuation, ValuedHolding


def build_heatmap(valuation: PortfolioValuation) -> list[dict[str, Any]]:
    total = valuation.total_ils
    items = [_heat_item(v, total) for v in valuation.holdings]
    return sorted(items, key=lambda i: i["weight_pct"], reverse=True)


def _heat_item(v: ValuedHolding, total: float) -> dict[str, Any]:
    return {
        "symbol": v.security.symbol,
        "sector": v.security.sector,
        "weight_pct": round(v.value_ils / total * 100.0, 2) if total else 0.0,
        "day_change_pct": round(v.day_change_pct, 2),
    }
