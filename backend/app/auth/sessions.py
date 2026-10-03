"""Server-side sessions with a signed httpOnly cookie and a per-session CSRF token."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.config import Settings
from app.models import AuthSession, User
from app.timeutil import utcnow

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="pm-session")


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, settings: Settings) -> tuple[AuthSession, str]:
    """Returns (row, signed cookie value). Only the sha256 of the token is stored."""
    assert user.id is not None
    token = secrets.token_urlsafe(32)
    row = AuthSession(
        token_hash=_session_id(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(row)
    db.commit()
    return row, _serializer(settings).dumps(token)


def set_session_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        settings.cookie_name,
        value,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def load_session(db: Session, request: Request, settings: Settings) -> AuthSession | None:
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        return None
    try:
        token = _serializer(settings).loads(raw, max_age=settings.session_ttl_hours * 3600)
    except BadSignature:
        return None
    row = db.exec(
        select(AuthSession).where(col(AuthSession.token_hash) == _session_id(str(token)))
    ).first()
    now = utcnow()
    if row is None or row.expires_at <= now:
        return None
    if (now - row.last_seen_at).total_seconds() >= settings.session_touch_seconds:
        row.last_seen_at = now  # at most one write per interval, not one per request
        db.add(row)
        db.commit()
    return row


def csrf_ok(request: Request, session: AuthSession) -> bool:
    if request.method in SAFE_METHODS:
        return True
    sent = request.headers.get(CSRF_HEADER, "")
    return hmac.compare_digest(sent.encode(), session.csrf_token.encode())


def purge_expired_sessions(db: Session, now: datetime | None = None) -> int:
    """Delete session rows whose `expires_at` has passed (they are already refused at login time)."""
    result = db.execute(delete(AuthSession).where(col(AuthSession.expires_at) <= (now or utcnow())))
    db.commit()
    return int(result.rowcount)  # type: ignore[attr-defined]
