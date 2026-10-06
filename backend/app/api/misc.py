"""Risk presets, security search, score card, price alerts, notifications."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.schemas import (
    AlertCreate,
    AlertOut,
    LaunchGateOut,
    NotificationOut,
    RiskPresetOut,
    ScoreCardDetail,
    SecurityHit,
)
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.auth.ratelimit import enforce_limit, symbol_search_limiter
from app.config import DISCLAIMER
from app.launchgate import GateDep
from app.models import PriceAlert, Security
from app.providers.registry import get_providers, get_symbol_search
from app.repo import (
    get_alert,
    get_holding,
    get_notification,
    list_alerts,
    list_notifications,
)
from app.scoring.risk import list_presets
from app.scoring.scorecard import get_cached_scorecard, is_fresh, refresh_scorecard
from app.securities import get_or_create_security, search_securities
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc

router = APIRouter(tags=["misc"], route_class=StrictJsonRoute)


@router.get("/launch-gate", response_model=LaunchGateOut)
def launch_gate(user: UserDep, gate: GateDep) -> LaunchGateOut:
    """Whether live buy/sell verdicts may be served, and if not, why not."""
    result = gate.evaluate()
    return LaunchGateOut(open=result.open, reasons=result.reasons)


@router.get("/risk/presets", response_model=list[RiskPresetOut])
def risk_presets(user: UserDep) -> list[RiskPresetOut]:
    return [RiskPresetOut.model_validate(p.model_dump()) for p in list_presets()]


@router.get("/securities/search", response_model=list[SecurityHit])
def securities_search(
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    q: Annotated[str, Query(max_length=64)],
    remote: bool = False,
) -> list[SecurityHit]:
    """Seeded or provider-verified securities first (never another user's unverified ticker).
    With `remote=1` the symbol-search provider adds US/TASE listings we do not know yet (`new`)."""
    out = [
        SecurityHit(
            symbol=s.symbol,
            name_en=s.name_en,
            name_he=s.name_he,
            market=s.market,  # type: ignore[arg-type]
            source="known",
            currency=s.currency if s.currency in ("USD", "ILS") else None,  # type: ignore[arg-type]
        )
        for s in search_securities(db, q)
    ]
    query = q.strip()
    if remote and len(query) >= 2:
        assert user.id is not None
        enforce_limit(
            symbol_search_limiter, f"user:{user.id}", settings.symbol_search_user_per_hour, 3600.0
        )
        known = {h.symbol.upper() for h in out}
        for hit in get_symbol_search().search(query, settings.symbol_search_max_results):
            sym = hit.symbol.upper()
            if sym in known:
                continue
            known.add(sym)
            out.append(
                SecurityHit(
                    symbol=sym,
                    name_en=hit.name,
                    name_he="",
                    market=hit.market,
                    source="new",
                    currency=hit.currency,
                    exchange=hit.exchange,
                )
            )
    return out


@router.get("/holdings/{holding_id}/scorecard", response_model=ScoreCardDetail)
def scorecard(holding_id: int, user: UserDep, db: DbDep, settings: SettingsDep) -> ScoreCardDetail:
    assert user.id is not None
    h, p = get_holding(db, user.id, holding_id)
    assert h.id is not None and p.id is not None
    payload = get_cached_scorecard(db, h.symbol)
    if payload is None or not is_fresh(db, h.symbol, settings):
        payload = refresh_scorecard(db, h.symbol, get_providers().history, settings)
    sec = db.get(Security, h.symbol)
    assert sec is not None
    return ScoreCardDetail(
        holding_id=h.id,
        portfolio_id=p.id,
        symbol=h.symbol,
        name_en=sec.name_en,
        name_he=sec.name_he,
        horizon=h.horizon,  # type: ignore[arg-type]
        total=payload["total"],
        confidence=payload["confidence"],
        available=payload["available"],
        validated=False,
        signals=payload["signals"],
        explanation=payload["explanation"],
        disclaimer=DISCLAIMER,
    )


def _alert_out(a: PriceAlert) -> AlertOut:
    assert a.id is not None
    return AlertOut(
        id=a.id,
        symbol=a.symbol,
        op=a.op,
        price=a.price,
        active=a.active,
        triggered_at=as_utc(a.triggered_at) if a.triggered_at else None,
    )


@router.get("/alerts", response_model=list[AlertOut])
def alerts(user: UserDep, db: DbDep) -> list[AlertOut]:
    assert user.id is not None
    return [_alert_out(a) for a in list_alerts(db, user.id)]


@router.post("/alerts", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
def create_alert(body: AlertCreate, user: UserDep, db: DbDep, settings: SettingsDep) -> AlertOut:
    assert user.id is not None
    if len(list_alerts(db, user.id)) >= settings.max_alerts_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You can have at most {settings.max_alerts_per_user} alerts; delete one first",
        )
    sym = body.symbol  # trimmed, upper-cased and pattern-checked by AlertCreate
    get_or_create_security(db, sym)
    a = PriceAlert(user_id=user.id, symbol=sym, op=body.op, price=body.price)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _alert_out(a)


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(alert_id: int, user: UserDep, db: DbDep) -> Response:
    assert user.id is not None
    db.delete(get_alert(db, user.id, alert_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notifications", response_model=list[NotificationOut])
def notifications(user: UserDep, db: DbDep) -> list[NotificationOut]:
    assert user.id is not None
    return [
        NotificationOut(
            id=n.id or 0,
            kind=n.kind,
            title=n.title,
            body=n.body,
            created_at=as_utc(n.created_at),
            read=n.read,
        )
        for n in list_notifications(db, user.id)
    ]


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def read_notification(notification_id: int, user: UserDep, db: DbDep) -> NotificationOut:
    assert user.id is not None
    n = get_notification(db, user.id, notification_id)
    n.read = True
    db.add(n)
    db.commit()
    return NotificationOut(
        id=n.id or 0,
        kind=n.kind,
        title=n.title,
        body=n.body,
        created_at=as_utc(n.created_at),
        read=n.read,
    )
