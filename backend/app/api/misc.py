"""Risk presets, security search, score card, price alerts, notifications."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.schemas import (
    AlertCreate,
    AlertOut,
    NotificationOut,
    RiskPresetOut,
    ScoreCardDetail,
    SecurityHit,
)
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.config import DISCLAIMER
from app.models import PriceAlert, Security
from app.providers.registry import get_providers
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
from app.timeutil import as_utc

router = APIRouter(tags=["misc"])


@router.get("/risk/presets", response_model=list[RiskPresetOut])
def risk_presets(user: UserDep) -> list[RiskPresetOut]:
    return [RiskPresetOut.model_validate(p.model_dump()) for p in list_presets()]


@router.get("/securities/search", response_model=list[SecurityHit])
def securities_search(q: str, user: UserDep, db: DbDep) -> list[SecurityHit]:
    return [
        SecurityHit(symbol=s.symbol, name_en=s.name_en, name_he=s.name_he, market=s.market)
        for s in search_securities(db, q)
    ]


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
def create_alert(body: AlertCreate, user: UserDep, db: DbDep) -> AlertOut:
    assert user.id is not None
    sym = body.symbol.strip().upper()
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
