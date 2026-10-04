"""Per-user search history and watchlist storage (symbols only; never any analysis output)."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.models import SearchHistory, Security, WatchlistItem
from app.timeutil import utcnow


def record_search(db: Session, user_id: int, symbol: str, cap: int) -> None:
    """Upsert (user, symbol) with a fresh `searched_at`, then prune the oldest rows past `cap`."""
    now = utcnow()
    row = db.exec(
        select(SearchHistory).where(
            SearchHistory.user_id == user_id, SearchHistory.symbol == symbol
        )
    ).first()
    if row is not None:
        row.searched_at = now
        db.add(row)
    else:
        db.add(SearchHistory(user_id=user_id, symbol=symbol, searched_at=now))
    try:
        db.commit()
    except IntegrityError:  # a parallel request inserted the same pair first: its row stands
        db.rollback()
        return
    stale = db.exec(
        select(SearchHistory)
        .where(SearchHistory.user_id == user_id)
        .order_by(col(SearchHistory.searched_at).desc(), col(SearchHistory.id).desc())
        .offset(cap)
    ).all()
    for old in stale:
        db.delete(old)
    if stale:
        db.commit()


def list_history(db: Session, user_id: int) -> list[SearchHistory]:
    return list(
        db.exec(
            select(SearchHistory)
            .where(SearchHistory.user_id == user_id)
            .order_by(col(SearchHistory.searched_at).desc(), col(SearchHistory.id).desc())
        ).all()
    )


def list_watchlist(db: Session, user_id: int) -> list[WatchlistItem]:
    return list(
        db.exec(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(col(WatchlistItem.added_at).desc(), col(WatchlistItem.id).desc())
        ).all()
    )


def watchlist_item(db: Session, user_id: int, symbol: str) -> WatchlistItem | None:
    return db.exec(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol
        )
    ).first()


def new_watchlist_item(db: Session, user_id: int, symbol: str) -> WatchlistItem:
    known = db.get(Security, symbol)
    return WatchlistItem(
        user_id=user_id,
        symbol=symbol,
        market=known.market if known else None,
        name=(known.name_en or None) if known else None,
    )
