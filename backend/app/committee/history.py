"""Ask chat history: user-scoped conversations and messages, retention purge.

Every function takes the user id and filters on it, so another user's conversation is simply
"not found". Only the text, the cited tool names and the tools called are stored; a raw tool
payload (holdings, amounts) never is.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import AskConversation, AskMessage
from app.timeutil import utcnow

TITLE_CHARS = 80


def get_conversation(db: Session, user_id: int, conversation_id: int) -> AskConversation | None:
    row = db.get(AskConversation, conversation_id)
    return row if row is not None and row.user_id == user_id else None


def list_conversations(db: Session, user_id: int, limit: int = 100) -> list[AskConversation]:
    return list(
        db.exec(
            select(AskConversation)
            .where(AskConversation.user_id == user_id)
            .order_by(col(AskConversation.updated_at).desc(), col(AskConversation.id).desc())
            .limit(limit)
        ).all()
    )


def list_messages(db: Session, user_id: int, conversation_id: int) -> list[AskMessage]:
    return list(
        db.exec(
            select(AskMessage)
            .where(AskMessage.conversation_id == conversation_id, AskMessage.user_id == user_id)
            .order_by(col(AskMessage.id))
        ).all()
    )


def start_conversation(
    db: Session, user_id: int, first_question: str, portfolio_id: int | None, settings: Settings
) -> AskConversation:
    """A new conversation; the oldest ones beyond the per-user cap are dropped."""
    conv = AskConversation(
        user_id=user_id, portfolio_id=portfolio_id, title=first_question.strip()[:TITLE_CHARS]
    )
    db.add(conv)
    db.flush()
    keep = settings.ask_max_conversations_per_user
    old = db.exec(
        select(AskConversation.id)
        .where(AskConversation.user_id == user_id)
        .order_by(col(AskConversation.updated_at).desc(), col(AskConversation.id).desc())
        .offset(keep)
    ).all()
    if old:
        _delete_ids(db, [i for i in old if i is not None])
    return conv


def add_message(
    db: Session,
    conv: AskConversation,
    role: str,
    content: str,
    *,
    cites: list[str] | None = None,
    tools_called: list[str] | None = None,
    source: str | None = None,
    declined: bool = False,
) -> AskMessage:
    assert conv.id is not None
    msg = AskMessage(
        conversation_id=conv.id,
        user_id=conv.user_id,
        role=role,
        content=content,
        cites=list(cites or []),
        tools_called=list(tools_called or []),
        source=source,
        declined=declined,
    )
    db.add(msg)
    conv.updated_at = utcnow()
    db.add(conv)
    return msg


def _delete_ids(db: Session, ids: list[int]) -> None:
    # Messages first (explicitly, so SQLite without foreign-key enforcement leaves no orphans).
    db.execute(delete(AskMessage).where(col(AskMessage.conversation_id).in_(ids)))
    db.execute(delete(AskConversation).where(col(AskConversation.id).in_(ids)))


def delete_conversation(db: Session, user_id: int, conversation_id: int) -> bool:
    if get_conversation(db, user_id, conversation_id) is None:
        return False
    _delete_ids(db, [conversation_id])
    db.commit()
    return True


def purge_expired_conversations(
    db: Session, settings: Settings | None = None, now: datetime | None = None
) -> int:
    """Delete conversations idle for the retention period. Returns how many were removed."""
    s = settings or get_settings()
    cutoff = (now or utcnow()) - timedelta(days=s.ask_history_retention_days)
    ids = [
        i
        for i in db.exec(
            select(AskConversation.id).where(col(AskConversation.updated_at) < cutoff)
        ).all()
        if i is not None
    ]
    if ids:
        _delete_ids(db, ids)
    db.commit()
    return len(ids)
