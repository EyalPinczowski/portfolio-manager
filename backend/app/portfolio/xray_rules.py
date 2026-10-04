"""Toggleable X-ray rules: concentration, currency, country / home bias, sector.

Informational only: a rule result is `breach`, `ok` or `off` and never blocks or changes anything
(the risk engine and the `breaches` list are separate). Defaults: every rule ON, threshold taken
from the portfolio's `RiskFilter` (concentration -> max_position_pct, sector -> max_sector_pct,
country -> max_country_pct). The currency rule has no RiskFilter field, so its default is a config
value. A user may override a rule's threshold within `Settings.xray_rule_override_bounds`.
Each result carries a typed `Explanation` that feeds "Why?".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.config import Settings
from app.models import Portfolio, XrayRuleSetting
from app.scoring.risk import (
    ExposureItem,
    Exposures,
    Position,
    RiskFilter,
    merge_dual_listings,
)
from app.signals.base import Explanation, ExplanationSource

RuleName = Literal["concentration", "currency", "country_home", "sector"]
RULE_NAMES: tuple[RuleName, ...] = ("concentration", "currency", "country_home", "sector")
RuleState = Literal["breach", "ok", "off"]
ThresholdSource = Literal["risk_filter", "config_default", "override"]

_LABEL: dict[str, str] = {
    "concentration": "Single-position concentration",
    "currency": "Currency exposure",
    "country_home": "Country and home-country exposure",
    "sector": "Sector exposure",
}


class RuleItem(BaseModel):
    name: str
    value_pct: float


class XrayRuleResult(BaseModel):
    rule: RuleName
    label: str
    state: RuleState
    enabled: bool
    threshold_pct: float
    threshold_source: ThresholdSource
    value_pct: float | None  # the largest exposure the rule looks at (None when off or empty)
    items: list[RuleItem]  # what is above the threshold (empty when ok or off)
    explanation: Explanation


@dataclass(frozen=True)
class ResolvedRule:
    rule: RuleName
    enabled: bool
    threshold_pct: float
    threshold_source: ThresholdSource
    default_threshold_pct: float
    default_source: ThresholdSource
    override_pct: float | None
    min_pct: float
    max_pct: float


def default_threshold(
    rule: RuleName, limits: RiskFilter, s: Settings
) -> tuple[float, ThresholdSource]:
    if rule == "concentration":
        return limits.max_position_pct, "risk_filter"
    if rule == "sector":
        return limits.max_sector_pct, "risk_filter"
    if rule == "country_home":
        return limits.max_country_pct, "risk_filter"
    return s.xray_currency_default_max_pct, "config_default"


def resolve_rules(
    db: Session, portfolio: Portfolio, limits: RiskFilter, s: Settings
) -> list[ResolvedRule]:
    assert portfolio.id is not None
    rows = {
        r.rule: r
        for r in db.exec(
            select(XrayRuleSetting).where(col(XrayRuleSetting.portfolio_id) == portfolio.id)
        )
    }
    out: list[ResolvedRule] = []
    for rule in RULE_NAMES:
        base, base_src = default_threshold(rule, limits, s)
        row = rows.get(rule)
        override = row.threshold_pct if row is not None else None
        lo, hi = s.xray_rule_override_bounds[rule]
        out.append(
            ResolvedRule(
                rule=rule,
                enabled=True if row is None else row.enabled,
                threshold_pct=base if override is None else override,
                threshold_source=base_src if override is None else "override",
                default_threshold_pct=base,
                default_source=base_src,
                override_pct=override,
                min_pct=lo,
                max_pct=hi,
            )
        )
    return out


def _above(items: list[ExposureItem], threshold: float, skip: set[str]) -> list[RuleItem]:
    return [
        RuleItem(name=i.name, value_pct=i.weight_pct)
        for i in items
        if i.name.lower() not in skip and i.weight_pct > threshold + 1e-9
    ]


def _explain(
    r: ResolvedRule, state: RuleState, top: RuleItem | None, items: list[RuleItem],
    extra_inputs: dict[str, float], as_of: datetime | None,
) -> Explanation:  # fmt: skip
    label = _LABEL[r.rule]
    src = {
        "risk_filter": "your risk filter",
        "config_default": "the app default (your risk filter has no limit for this)",
        "override": "your own setting for this rule",
    }[r.threshold_source]
    if state == "off":
        summary = f"{label} is switched off for this portfolio, so it is not checked."
    elif top is None:
        summary = f"{label}: nothing to measure yet (no valued holdings)."
    elif state == "breach":
        names = ", ".join(f"{i.name} {i.value_pct:.1f}%" for i in items[:5])
        summary = f"{label}: {names} is above the {r.threshold_pct:g}% threshold from {src}."
    else:
        summary = (
            f"{label}: the largest is {top.name} at {top.value_pct:.1f}%, within the "
            f"{r.threshold_pct:g}% threshold from {src}."
        )
    inputs: dict[str, float | str] = {"threshold_pct": r.threshold_pct, **extra_inputs}
    if top is not None and state != "off":
        inputs["largest_pct"] = top.value_pct
    return Explanation(
        summary=summary,
        inputs=inputs,
        rules_applied=[f"xray_rule:{r.rule}:{state}"],
        as_of=as_of,
        risk_rules_applied=[f"threshold from {src}"],
        invalidation_risks=[
            "Based on the last known prices and the sector and country labels of each holding; "
            "a stale price or a wrong label changes the result.",
            "Informational only: this rule never blocks or changes anything.",
        ],
        sources=[ExplanationSource(name="Portfolio valuation", as_of=as_of)],
    )


def evaluate_rules(
    positions: list[Position],
    exposures: Exposures,
    resolved: list[ResolvedRule],
    s: Settings,
    as_of: datetime | None = None,
) -> list[XrayRuleResult]:
    total = sum(p.value_ils for p in positions)
    skip_sector = {x.lower() for x in s.diversified_sectors}
    skip_country = {x.lower() for x in s.non_country_labels}
    merged = merge_dual_listings(positions)
    out: list[XrayRuleResult] = []
    for r in resolved:
        items: list[RuleItem] = []
        top: RuleItem | None = None
        extra: dict[str, float] = {}
        if r.enabled and total > 0:
            if r.rule == "concentration":
                weights = sorted(
                    ((p, p.value_ils / total * 100.0) for p in merged),
                    key=lambda t: t[1],
                    reverse=True,
                )
                if weights:
                    top = RuleItem(name=weights[0][0].symbol, value_pct=round(weights[0][1], 2))
                for p, pct in weights:
                    # a per-holding limit wins over the portfolio-wide one (CLAUDE.md)
                    cap = p.max_position_pct if p.max_position_pct is not None else r.threshold_pct
                    if pct > cap + 1e-9:
                        items.append(RuleItem(name=p.symbol, value_pct=round(pct, 2)))
            else:
                groups, skip = {
                    "currency": (exposures.currency, set()),
                    "country_home": (exposures.country, skip_country),
                    "sector": (exposures.sector, skip_sector),
                }[r.rule]
                counted = [g for g in groups if g.name.lower() not in skip]
                if counted:
                    top = RuleItem(name=counted[0].name, value_pct=counted[0].weight_pct)
                items = _above(groups, r.threshold_pct, skip)
                if r.rule == "country_home":
                    extra["home_country_pct"] = exposures.home_bias_pct
        state: RuleState = "off" if not r.enabled else ("breach" if items else "ok")
        out.append(
            XrayRuleResult(
                rule=r.rule,
                label=_LABEL[r.rule],
                state=state,
                enabled=r.enabled,
                threshold_pct=r.threshold_pct,
                threshold_source=r.threshold_source,
                value_pct=top.value_pct if top is not None else None,
                items=items,
                explanation=_explain(r, state, top, items, extra, as_of),
            )
        )
    return out
