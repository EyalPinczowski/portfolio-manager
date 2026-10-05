"""FastAPI dependencies: the authenticated user, with the CSRF header enforced on mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, col, select

from app.auth.sessions import csrf_ok, load_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuthSession, TermsAcceptance, User

DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# Routes an authenticated user may call before accepting the current terms: signing in and out,
# the terms themselves, the language setting (so the terms page renders in the right language),
# and the user's own data export / account deletion. Matched on the path below `/api`.
TERMS_EXEMPT_PREFIXES = ("/auth/", "/terms")
TERMS_EXEMPT_PATHS = frozenset({"/settings", "/me", "/me/export", "/health"})


def terms_accepted(db: Session, user: User, settings: Settings) -> tuple[bool, datetime | None]:
    """Whether `user` accepted the current terms version, and when (None if not)."""
    row = db.exec(
        select(TermsAcceptance)
        .where(
            col(TermsAcceptance.user_id) == user.id,
            col(TermsAcceptance.version) == settings.terms_version,
        )
        .order_by(col(TermsAcceptance.accepted_at).desc())
    ).first()
    return (row is not None, row.accepted_at if row else None)


def _terms_exempt(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/api"):
        path = path[4:]
    return path in TERMS_EXEMPT_PATHS or path.startswith(TERMS_EXEMPT_PREFIXES)


@dataclass
class AuthContext:
    user: User
    session: AuthSession


def get_auth(request: Request, db: DbDep, settings: SettingsDep) -> AuthContext:
    session = load_session(db, request, settings)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, session.user_id)
    if user is None or user.disabled_at is not None:  # a disabled account has no valid session
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if not csrf_ok(request, session):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token")
    if not _terms_exempt(request) and not terms_accepted(db, user, settings)[0]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "terms_not_accepted")
    return AuthContext(user, session)


AuthDep = Annotated[AuthContext, Depends(get_auth)]


def get_current_user(auth: AuthDep) -> User:
    return auth.user


UserDep = Annotated[User, Depends(get_current_user)]


def get_admin(auth: AuthDep) -> User:
    """The signed-in user, only when an admin. Anyone else gets 403 (never 404: the route exists)."""
    if not auth.user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return auth.user


AdminDep = Annotated[User, Depends(get_admin)]
