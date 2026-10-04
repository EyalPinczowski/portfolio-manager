"""Account export and deletion (privacy)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, select

from app.committee.history import list_conversations, list_messages
from app.models import (
    FundHolding,
    Holding,
    HoldingsSnapshot,
    ImportDraft,
    Notification,
    Portfolio,
    PortfolioSnapshot,
    PriceAlert,
    SearchHistory,
    Transaction,
    User,
    UserSettings,
    WatchlistItem,
    XrayRuleSetting,
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
                "fund_holdings": [
                    x.model_dump(mode="json")
                    for x in db.exec(
                        select(FundHolding)
                        .join(Holding, col(Holding.id) == col(FundHolding.holding_id))
                        .where(Holding.portfolio_id == pid)
                    )
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
                "xray_rules": [
                    x.model_dump(mode="json")
                    for x in db.exec(
                        select(XrayRuleSetting).where(XrayRuleSetting.portfolio_id == pid)
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
        "search_history": [
            h.model_dump(mode="json")
            for h in db.exec(select(SearchHistory).where(SearchHistory.user_id == user.id))
        ],
        "watchlist": [
            w.model_dump(mode="json")
            for w in db.exec(select(WatchlistItem).where(WatchlistItem.user_id == user.id))
        ],
        "settings": (
            row.model_dump(mode="json") if (row := db.get(UserSettings, user.id)) else None
        ),
        "ask_conversations": [
            {
                **c.model_dump(mode="json"),
                "messages": [m.model_dump(mode="json") for m in list_messages(db, user.id, c.id)],
            }
            for c in list_conversations(db, user.id, limit=10_000)
            if c.id is not None
        ],
        "notifications": [
            n.model_dump(mode="json")
            for n in db.exec(select(Notification).where(Notification.user_id == user.id))
        ],
    }


def delete_user(db: Session, user: User) -> None:
    """Permanently delete the user and everything they own.

    One DELETE: the foreign keys cascade (portfolios -> holdings, transactions, snapshots, drafts, X-ray rule settings;
    sessions, alerts, notifications, search history, watchlist, settings, Telegram link codes, invites they created) and set `Invite.used_by` to NULL.
    """
    db.delete(user)
    db.commit()
