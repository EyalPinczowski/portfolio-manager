"""Per-portfolio X-ray rule switches: `GET/PATCH /api/portfolios/{id}/xray-rules`.

Informational only. Every rule is ON by default with its threshold from the portfolio's RiskFilter
(see `portfolio/xray_rules.py`). PATCH changes `enabled` and/or a threshold override per rule; an
explicit `threshold_pct: null` removes the override. Overrides must lie within the per-rule bounds
in `Settings.xray_rule_override_bounds`. Strict bodies; strictly the owner's portfolios (404).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictBool, model_validator
from sqlmodel import Session, col, select

from app.api.schemas import Body, Pct
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.errors import ApiError
from app.models import Portfolio, XrayRuleSetting
from app.portfolio.xray_rules import (
    RULE_NAMES,
    RuleName,
    ThresholdSource,
    resolve_rules,
)
from app.repo import get_portfolio
from app.scoring.risk import resolve_risk_filter
from app.strictjson import StrictJsonRoute
from app.timeutil import utcnow

router = APIRouter(tags=["xray-rules"], route_class=StrictJsonRoute)


class XrayRuleOut(BaseModel):
    rule: RuleName
    enabled: bool
    threshold_pct: float  # the one in force
    threshold_source: ThresholdSource
    default_threshold_pct: float
    override_pct: float | None
    min_pct: float
    max_pct: float


class XrayRulesOut(BaseModel):
    rules: list[XrayRuleOut]


class XrayRuleChange(Body):
    rule: RuleName
    enabled: StrictBool | None = Field(default=None)
    threshold_pct: Pct | None = None  # explicit null clears the override

    @model_validator(mode="after")
    def _something_to_change(self) -> XrayRuleChange:
        if not self.model_fields_set & {"enabled", "threshold_pct"} or (
            "enabled" in self.model_fields_set and self.enabled is None
        ):
            raise ValueError("send enabled (true/false) and/or threshold_pct for each rule")
        return self


class XrayRulesPatch(Body):
    rules: list[XrayRuleChange] = Field(min_length=1, max_length=len(RULE_NAMES))

    @model_validator(mode="after")
    def _unique(self) -> XrayRulesPatch:
        names = [r.rule for r in self.rules]
        if len(set(names)) != len(names):
            raise ValueError("each rule may appear once")
        return self


def _out(db: Session, p: Portfolio, settings: SettingsDep) -> XrayRulesOut:
    resolved = resolve_rules(db, p, resolve_risk_filter(p.risk_filter, settings), settings)
    return XrayRulesOut(
        rules=[
            XrayRuleOut(
                rule=r.rule,
                enabled=r.enabled,
                threshold_pct=r.threshold_pct,
                threshold_source=r.threshold_source,
                default_threshold_pct=r.default_threshold_pct,
                override_pct=r.override_pct,
                min_pct=r.min_pct,
                max_pct=r.max_pct,
            )
            for r in resolved
        ]
    )


@router.get("/portfolios/{portfolio_id}/xray-rules", response_model=XrayRulesOut)
def get_xray_rules(
    portfolio_id: int, user: UserDep, db: DbDep, settings: SettingsDep
) -> XrayRulesOut:
    assert user.id is not None
    return _out(db, get_portfolio(db, user.id, portfolio_id), settings)


@router.patch("/portfolios/{portfolio_id}/xray-rules", response_model=XrayRulesOut)
def patch_xray_rules(
    portfolio_id: int, body: XrayRulesPatch, user: UserDep, db: DbDep, settings: SettingsDep
) -> XrayRulesOut:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    for change in body.rules:  # validate everything before touching anything
        lo, hi = settings.xray_rule_override_bounds[change.rule]
        if change.threshold_pct is not None and not lo <= change.threshold_pct <= hi:
            raise ApiError(
                422,
                "threshold_out_of_bounds",
                f"The {change.rule} threshold must be between {lo:g}% and {hi:g}%.",
                {"rule": change.rule, "min_pct": lo, "max_pct": hi},
            )
    existing = {
        r.rule: r
        for r in db.exec(select(XrayRuleSetting).where(col(XrayRuleSetting.portfolio_id) == p.id))
    }
    for change in body.rules:
        row = existing.get(change.rule) or XrayRuleSetting(
            portfolio_id=p.id,  # type: ignore[arg-type]
            rule=change.rule,
        )
        if "enabled" in change.model_fields_set and change.enabled is not None:
            row.enabled = change.enabled
        if "threshold_pct" in change.model_fields_set:
            row.threshold_pct = change.threshold_pct
        if row.enabled and row.threshold_pct is None:
            if row.id is not None:
                db.delete(row)  # back to the defaults: no row
            continue
        row.updated_at = utcnow()
        db.add(row)
    db.commit()
    return _out(db, p, settings)
