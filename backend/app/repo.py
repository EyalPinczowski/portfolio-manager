"""Owner-scoped repository functions. Every user-owned query goes through here with `user_id`.

A resource that does not exist or belongs to someone else raises the same 404, so ids cannot be
probed across accounts.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app.models import (
    Holding,
    ImportDraft,
    Notification,
    Portfolio,
    PriceAlert,
)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def list_portfolios(db: Session, user_id: int) -> list[Portfolio]:
    return list(
        db.exec(
            select(Portfolio).where(Portfolio.owner_id == user_id).order_by(col(Portfolio.id))
        ).all()
    )


def get_portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    p = db.get(Portfolio, portfolio_id)
    if p is None or p.owner_id != user_id:
        raise _not_found("Portfolio")
    return p


def list_holdings(db: Session, user_id: int, portfolio_id: int) -> list[Holding]:
    get_portfolio(db, user_id, portfolio_id)
    return list(
        db.exec(
            select(Holding).where(Holding.portfolio_id == portfolio_id).order_by(col(Holding.id))
        ).all()
    )


def get_holding(db: Session, user_id: int, holding_id: int) -> tuple[Holding, Portfolio]:
    h = db.get(Holding, holding_id)
    if h is None:
        raise _not_found("Holding")
    p = db.get(Portfolio, h.portfolio_id)
    if p is None or p.owner_id != user_id:
        raise _not_found("Holding")
    return h, p


def get_holding_in_portfolio(
    db: Session, user_id: int, portfolio_id: int, holding_id: int
) -> tuple[Holding, Portfolio]:
    h, p = get_holding(db, user_id, holding_id)
    if p.id != portfolio_id:
        raise _not_found("Holding")
    return h, p


def get_draft(db: Session, user_id: int, draft_id: int) -> tuple[ImportDraft, Portfolio]:
    d = db.get(ImportDraft, draft_id)
    if d is None:
        raise _not_found("Import draft")
    p = db.get(Portfolio, d.portfolio_id)
    if p is None or p.owner_id != user_id:
        raise _not_found("Import draft")
    return d, p


def list_alerts(db: Session, user_id: int) -> list[PriceAlert]:
    return list(
        db.exec(
            select(PriceAlert).where(PriceAlert.user_id == user_id).order_by(col(PriceAlert.id))
        ).all()
    )


def get_alert(db: Session, user_id: int, alert_id: int) -> PriceAlert:
    a = db.get(PriceAlert, alert_id)
    if a is None or a.user_id != user_id:
        raise _not_found("Alert")
    return a


def list_notifications(db: Session, user_id: int, limit: int = 100) -> list[Notification]:
    return list(
        db.exec(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(col(Notification.id).desc())
            .limit(limit)
        ).all()
    )


def get_notification(db: Session, user_id: int, notification_id: int) -> Notification:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user_id:
        raise _not_found("Notification")
    return n
