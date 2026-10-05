"""User risk limits: presets, exposure computation and limit breaches (with a "why" text).

Phase 1 computes exposures and breaches only. This module never produces buy/sell verdicts. In
later phases the filter may block or downgrade a recommendation but never upgrade one.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings

StopType = Literal["fixed", "trailing", "both"]
PresetName = Literal[
    "very_conservative",
    "conservative",
    "balanced",
    "balanced_aggressive",
    "aggressive",
    "very_aggressive",
]


Limit = Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)]


class RiskFilter(BaseModel):
    """The user's limits. Every number is a finite value in (0, 100]; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    preset: str | None = None
    max_position_pct: Limit
    max_sector_pct: Limit
    max_country_pct: Limit
    max_loss_per_position_pct: Limit
    max_portfolio_risk_per_trade_pct: Limit
    max_total_portfolio_risk_pct: Limit
    min_rr: Limit
    stop_type: StopType
    drawdown_defensive_pct: Limit


class RiskPreset(RiskFilter):
    name: str


def _preset(name: str, *vals: Any) -> RiskPreset:
    keys = (
        "max_position_pct",
        "max_sector_pct",
        "max_country_pct",
        "max_loss_per_position_pct",
        "max_portfolio_risk_per_trade_pct",
        "max_total_portfolio_risk_pct",
        "min_rr",
        "stop_type",
        "drawdown_defensive_pct",
    )
    return RiskPreset(name=name, preset=name, **dict(zip(keys, vals, strict=True)))


PRESETS: dict[str, RiskPreset] = {
    p.name: p
    for p in (
        _preset("very_conservative", 5, 20, 60, 5, 0.25, 2, 3.0, "fixed", 5),
        _preset("conservative", 8, 25, 65, 7, 0.5, 4, 2.5, "fixed", 8),
        _preset("balanced", 10, 30, 70, 10, 0.75, 6, 2.0, "both", 10),
        _preset("balanced_aggressive", 12, 35, 80, 12, 1.0, 8, 1.5, "both", 15),
        _preset("aggressive", 15, 40, 90, 15, 1.5, 12, 1.5, "trailing", 20),
        _preset("very_aggressive", 20, 50, 100, 20, 2.0, 20, 1.2, "trailing", 30),
    )
}
DEFAULT_PRESET = "balanced_aggressive"


def list_presets() -> list[RiskPreset]:
    return list(PRESETS.values())


def resolve_risk_filter(raw: dict[str, Any] | None, settings: Settings | None = None) -> RiskFilter:
    """Preset defaults overlaid with any explicit numeric overrides stored on the portfolio.

    Stored JSON is untrusted (older versions saved unvalidated input): an override that is not a
    finite number in (0, 100] (or a bad `stop_type`) is ignored and the preset value is used, so
    the X-ray and the other readers never fail on bad stored data.
    """
    s = settings or get_settings()
    raw = raw if isinstance(raw, dict) else {}
    name = raw.get("preset") or s.default_risk_preset
    base = PRESETS.get(name, PRESETS[DEFAULT_PRESET]).model_dump(exclude={"name"})
    base["preset"] = name if name in PRESETS else DEFAULT_PRESET
    merged = dict(base)
    for key, value in raw.items():
        if key not in base or key == "preset" or value is None:
            continue
        if key == "stop_type":
            if value in ("fixed", "trailing", "both"):
                merged[key] = value
        elif _valid_limit(value):
            merged[key] = float(value)
    return RiskFilter.model_validate(merged)


def _valid_limit(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 < value <= 100
    )


@dataclass
class Position:
    symbol: str
    name: str
    value_ils: float
    sector: str
    country: str
    currency: str
    asset_type: str = "stock"
    max_position_pct: float | None = None  # per-holding override
    dual_group: str | None = None  # same company listed on TASE and in the US


class ExposureItem(BaseModel):
    name: str
    weight_pct: float


class Exposures(BaseModel):
    concentration: list[dict[str, Any]]
    currency: list[ExposureItem]
    country: list[ExposureItem]
    sector: list[ExposureItem]
    home_bias_pct: float


class Breach(BaseModel):
    rule: str
    value: float
    limit: float
    why: str
    symbol: str | None = None


def merge_dual_listings(positions: list[Position]) -> list[Position]:
    """Merge dual-listed lines of one company so concentration is never double-counted."""
    merged: dict[str, Position] = {}
    out: list[Position] = []
    for p in positions:
        if not p.dual_group:
            out.append(p)
            continue
        existing = merged.get(p.dual_group)
        if existing is None:
            copy = Position(**{**p.__dict__})
            merged[p.dual_group] = copy
            out.append(copy)
        else:
            existing.value_ils += p.value_ils
            existing.symbol = f"{existing.symbol}+{p.symbol}"
            overrides = [
                v for v in (existing.max_position_pct, p.max_position_pct) if v is not None
            ]
            existing.max_position_pct = min(overrides) if overrides else None  # strictest wins
    return out


def _group(positions: Iterable[Position], key: str, total: float) -> list[ExposureItem]:
    sums: dict[str, float] = {}
    for p in positions:
        label = str(getattr(p, key)) or "Unknown"
        sums[label] = sums.get(label, 0.0) + p.value_ils
    items = [
        ExposureItem(name=k, weight_pct=round(v / total * 100.0, 2) if total else 0.0)
        for k, v in sums.items()
    ]
    return sorted(items, key=lambda i: i.weight_pct, reverse=True)


def compute_exposures(
    positions: list[Position], settings: Settings | None = None
) -> tuple[Exposures, float]:
    """Returns (exposures, total_value_ils)."""
    s = settings or get_settings()
    total = sum(p.value_ils for p in positions)
    conc = sorted(merge_dual_listings(positions), key=lambda p: p.value_ils, reverse=True)[
        : s.xray_top_n
    ]
    country = _group(positions, "country", total)
    home = next((c.weight_pct for c in country if c.name == s.home_country), 0.0)
    return (
        Exposures(
            concentration=[
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "weight_pct": round(p.value_ils / total * 100.0, 2) if total else 0.0,
                }
                for p in conc
            ],
            currency=_group(positions, "currency", total),
            country=country,
            sector=_group(positions, "sector", total),
            home_bias_pct=home,
        ),
        total,
    )


def check_limits(
    positions: list[Position], limits: RiskFilter, settings: Settings | None = None
) -> list[Breach]:
    """Limit breaches with a plain-language reason. Never a buy/sell verdict."""
    s = settings or get_settings()
    exposures, total = compute_exposures(positions, s)
    if total <= 0:
        return []
    breaches: list[Breach] = []
    for p in merge_dual_listings(positions):
        pct = p.value_ils / total * 100.0
        cap = p.max_position_pct if p.max_position_pct is not None else limits.max_position_pct
        if pct > cap + 1e-9:
            breaches.append(
                Breach(
                    rule="max_position_pct",
                    value=round(pct, 2),
                    limit=cap,
                    symbol=p.symbol,
                    why=f"{p.symbol} is {pct:.1f}% of your portfolio, above your {cap:g}% single-position limit.",
                )
            )
    skip_sector = {x.lower() for x in s.diversified_sectors}
    for item in exposures.sector:
        if item.name.lower() in skip_sector:
            continue
        if item.weight_pct > limits.max_sector_pct + 1e-9:
            breaches.append(
                Breach(
                    rule="max_sector_pct",
                    value=item.weight_pct,
                    limit=limits.max_sector_pct,
                    why=f"{item.name} exposure is {item.weight_pct:.1f}%, above your {limits.max_sector_pct:g}% sector limit.",
                )
            )
    skip_country = {x.lower() for x in s.non_country_labels}
    for item in exposures.country:
        if item.name.lower() in skip_country:
            continue
        if item.weight_pct > limits.max_country_pct + 1e-9:
            breaches.append(
                Breach(
                    rule="max_country_pct",
                    value=item.weight_pct,
                    limit=limits.max_country_pct,
                    why=f"{item.name} exposure is {item.weight_pct:.1f}%, above your {limits.max_country_pct:g}% country limit.",
                )
            )
    return breaches
