"""Account export and deletion (privacy)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, select

from app.models import (
    AuthSession,
    Holding,
    HoldingsSnapshot,
    ImportDraft,
    Invite,
    Notification,
    Portfolio,
    PortfolioSnapshot,
    PriceAlert,
    Transaction,
    User,
)
from app.timeutil import utcnow


def export_user_data(db: Session, user: User) -> dict[str, Any]:
    assert user.id is not None
    portfolios = db.exec(select(Portfolio).where(Portfolio.owner_id == user.id)).all()
    out_portfolios: list[dict[str, Any]] = []
    for p in portfolios:
        pid = p.id
        out_portfolios.append(
            {
                **p.model_dump(mode="json"),
                "holdings": [
                    h.model_dump(mode="json")
                    for h in db.exec(select(Holding).where(Holding.portfolio_id == pid))
                ],
                "holdings_snapshots": [
                    x.model_dump(mode="json")
                    for x in db.exec(
                        select(HoldingsSnapshot).where(HoldingsSnapshot.portfolio_id == pid)
                    )
                ],
                "transactions": [
                    x.model_dump(mode="json")
                    for x in db.exec(select(Transaction).where(Transaction.portfolio_id == pid))
                ],
                "portfolio_snapshots": [
                    x.model_dump(mode="json")
                    for x in db.exec(
                        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pid)
                    )
                ],
                "import_drafts": [
                    x.model_dump(mode="json")
                    for x in db.exec(select(ImportDraft).where(ImportDraft.portfolio_id == pid))
                ],
            }
        )
    return {
        "exported_at": utcnow().isoformat() + "Z",
        "user": user.model_dump(mode="json", exclude={"password_hash"}),
        "portfolios": out_portfolios,
        "alerts": [
            a.model_dump(mode="json")
            for a in db.exec(select(PriceAlert).where(PriceAlert.user_id == user.id))
        ],
        "notifications": [
            n.model_dump(mode="json")
            for n in db.exec(select(Notification).where(Notification.user_id == user.id))
        ],
    }


def delete_user(db: Session, user: User) -> None:
    """Permanently delete the user and everything they own."""
    assert user.id is not None
    uid = user.id
    for p in db.exec(select(Portfolio).where(Portfolio.owner_id == uid)).all():
        pid = p.id
        for model in (Holding, HoldingsSnapshot, Transaction, PortfolioSnapshot, ImportDraft):
            for row in db.exec(select(model).where(col(model.portfolio_id) == pid)).all():  # type: ignore[attr-defined]
                db.delete(row)
        db.delete(p)
    for model2 in (PriceAlert, Notification, AuthSession):
        for row2 in db.exec(select(model2).where(col(model2.user_id) == uid)).all():  # type: ignore[attr-defined]
            db.delete(row2)
    for inv in db.exec(select(Invite).where(Invite.created_by == uid)).all():
        db.delete(inv)
    db.flush()
    db.delete(user)
    db.commit()
