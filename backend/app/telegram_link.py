"""Link a Telegram chat to an account with a one-time code (docs/settings-spec.md, section 3).

Flow: the signed-in user asks for a code (`POST /api/telegram/link-code`), sends `/start <code>` to
the bot, the webhook (`POST /api/telegram/webhook`) calls `bind_from_start`. Only an HMAC of the
code is stored; a code is single-use, expires after `telegram_link_code_ttl_minutes` and a chat can
belong to one account only. The chat id is never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.config import Settings
from app.models import TelegramLinkCode, User
from app.timeutil import utcnow

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: it is typed by hand
START = re.compile(r"^/start(?:@\w{1,64})?\s+([A-Za-z0-9]{6,16})\s*$")

BindResult = Literal["linked", "invalid", "chat_taken", "ignored"]


def hash_code(code: str, settings: Settings) -> str:
    return hmac.new(
        settings.secret_key.encode(), b"tg-link:" + code.strip().upper().encode(), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class IssuedCode:
    code: str
    expires_at: datetime


def issue_code(db: Session, user: User, settings: Settings) -> IssuedCode:
    """A fresh code; earlier unused codes of this user stop working."""
    assert user.id is not None
    code = "".join(secrets.choice(ALPHABET) for _ in range(settings.telegram_link_code_length))
    expires = utcnow() + timedelta(minutes=settings.telegram_link_code_ttl_minutes)
    db.execute(
        delete(TelegramLinkCode).where(
            col(TelegramLinkCode.user_id) == user.id, col(TelegramLinkCode.used_at).is_(None)
        )
    )
    # expired and used rows are housekeeping: keep only this user's latest used one
    db.execute(
        delete(TelegramLinkCode).where(
            col(TelegramLinkCode.user_id) == user.id, col(TelegramLinkCode.expires_at) < utcnow()
        )
    )
    db.add(
        TelegramLinkCode(user_id=user.id, code_hash=hash_code(code, settings), expires_at=expires)
    )
    db.commit()
    return IssuedCode(code, expires)


def parse_start(text: str | None) -> str | None:
    m = START.match(text or "")
    return m.group(1) if m else None


def _replayed(db: Session, user: User, chat_id: str) -> BindResult:
    """A spent code seen again (Telegram redelivers an update, e.g. after a cold start): the same
    chat gets the same answer as the first time, and nothing is bound twice."""
    db.refresh(user)
    if user.telegram_chat_id == chat_id:
        return "linked"
    holder = db.exec(select(User).where(User.telegram_chat_id == chat_id)).first()
    return "chat_taken" if holder is not None and holder.id != user.id else "invalid"


def bind_from_start(db: Session, chat_id: str, code: str, settings: Settings) -> BindResult:
    """Bind `chat_id` to the code's user. Idempotent for the same chat and the same user."""
    digest = hash_code(code, settings)
    row = db.exec(select(TelegramLinkCode).where(TelegramLinkCode.code_hash == digest)).first()
    if row is None:
        return "invalid"
    user = db.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return "invalid"
    if row.used_at is not None:
        return _replayed(db, user, chat_id)
    claim = db.execute(
        update(TelegramLinkCode)
        .where(
            col(TelegramLinkCode.id) == row.id,
            col(TelegramLinkCode.used_at).is_(None),
            col(TelegramLinkCode.expires_at) > utcnow(),
        )
        .values(used_at=utcnow())
    )
    if claim.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        db.refresh(row)  # a concurrent delivery of the same update may have spent it a moment ago
        return _replayed(db, user, chat_id) if row.used_at is not None else "invalid"
    other = db.exec(
        select(User).where(User.telegram_chat_id == chat_id, col(User.id) != user.id)
    ).first()
    if other is not None:
        db.commit()  # the code is spent
        return "chat_taken"
    user.telegram_chat_id = chat_id
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # another request bound the chat between the check and the write
        db.rollback()
        return "chat_taken"
    return "linked"


def unlink(db: Session, user: User) -> None:
    assert user.id is not None
    user.telegram_chat_id = None
    db.add(user)
    db.execute(delete(TelegramLinkCode).where(col(TelegramLinkCode.user_id) == user.id))
    db.commit()
