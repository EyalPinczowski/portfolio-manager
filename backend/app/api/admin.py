"""Admin endpoints (docs/settings-spec.md, section 6): invites and users. Admin-only.

Every route depends on `AdminDep`: a signed-in non-admin gets 403, an anonymous caller 401. Nothing
here returns a portfolio, holding, transaction or any other user data beyond email, created and
last-seen times. Invite codes travel in request bodies, never in URLs (they would end up in access
logs). Each change writes an `audit_log` row (admin id, fixed action name, target user id) and an
`audit` log line; neither contains an email, a code or free text.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.api.schemas import Body
from app.auth.deps import AdminDep, DbDep, SettingsDep
from app.auth.device import rotate_device_nonce
from app.auth.ratelimit import admin_invite_limiter, enforce_limit
from app.errors import ApiError
from app.models import AuditLog, AuthSession, Invite, User
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, utcnow

router = APIRouter(prefix="/admin", tags=["admin"], route_class=StrictJsonRoute)
audit_log = logging.getLogger("audit")


def record(db: Session, actor: User, action: str, target_user_id: int | None = None) -> None:
    """Add (not commit) an audit row; the caller commits it together with the change."""
    db.add(AuditLog(actor_user_id=actor.id, action=action, target_user_id=target_user_id))
    audit_log.info("admin_action action=%s actor=%s target=%s", action, actor.id, target_user_id)


# ---------------------------------------------------------------- invites
class InviteCreate(Body):
    days: int | None = Field(default=None, ge=1, strict=True)  # null: the configured default


class InviteRevoke(Body):
    code: str = Field(min_length=1, max_length=128)


class InviteOut(BaseModel):
    code: str
    status: Literal["unused", "used", "expired"]
    created_at: datetime
    expires_at: datetime
    created_by_me: bool


def _invite_out(inv: Invite, admin: User) -> InviteOut:
    now = utcnow()
    state: Literal["unused", "used", "expired"] = (
        "used" if inv.used_by is not None else "expired" if inv.expires_at <= now else "unused"
    )
    return InviteOut(
        # a used or expired code is useless; do not hand it out again
        code=inv.code if state == "unused" else "",
        status=state,
        created_at=as_utc(inv.created_at),
        expires_at=as_utc(inv.expires_at),
        created_by_me=inv.created_by == admin.id,
    )


@router.get("/invites", response_model=list[InviteOut])
def list_invites(admin: AdminDep, db: DbDep) -> list[InviteOut]:
    rows = db.exec(select(Invite).order_by(col(Invite.created_at).desc())).all()
    return [_invite_out(r, admin) for r in rows]


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def create_invite(
    body: InviteCreate, admin: AdminDep, db: DbDep, settings: SettingsDep
) -> InviteOut:
    assert admin.id is not None
    enforce_limit(
        admin_invite_limiter, f"admin:{admin.id}", settings.admin_invites_per_hour, 3600.0
    )
    days = body.days or settings.invite_ttl_days
    if days > settings.admin_max_invite_days:
        raise ApiError(
            422,
            "invite_too_long",
            f"An invite can last at most {settings.admin_max_invite_days} days.",
        )
    inv = Invite(
        code=secrets.token_urlsafe(12),
        created_by=admin.id,
        expires_at=utcnow() + timedelta(days=days),
    )
    db.add(inv)
    record(db, admin, "invite.create")
    db.commit()
    return _invite_out(inv, admin)


@router.post("/invites/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(body: InviteRevoke, admin: AdminDep, db: DbDep) -> Response:
    inv = db.get(Invite, body.code.strip())
    if inv is None:
        raise ApiError(404, "not_found", "No such invite.")
    if inv.used_by is not None:
        raise ApiError(409, "invite_used", "That invite was already used.")
    db.delete(inv)
    record(db, admin, "invite.revoke")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- users
class AdminUserOut(BaseModel):
    id: int
    email: str
    created_at: datetime
    last_seen_at: datetime | None
    is_admin: bool
    active: bool
    telegram_linked: bool


def _user_out(u: User, last_seen: datetime | None) -> AdminUserOut:
    assert u.id is not None
    return AdminUserOut(
        id=u.id,
        email=u.email,
        created_at=as_utc(u.created_at),
        last_seen_at=as_utc(last_seen) if last_seen else None,
        is_admin=u.is_admin,
        active=u.disabled_at is None,
        telegram_linked=u.telegram_chat_id is not None,
    )


def _last_seen(db: Session, user_id: int) -> datetime | None:
    return db.exec(
        select(func.max(AuthSession.last_seen_at)).where(AuthSession.user_id == user_id)
    ).one()


@router.get("/users", response_model=list[AdminUserOut])
def list_users(_admin: AdminDep, db: DbDep) -> list[AdminUserOut]:
    seen = dict(
        db.exec(
            select(AuthSession.user_id, func.max(AuthSession.last_seen_at)).group_by(
                col(AuthSession.user_id)
            )
        ).all()
    )
    return [
        _user_out(u, seen.get(u.id))
        for u in db.exec(select(User).order_by(col(User.id))).all()
        if u.id is not None
    ]


def _target(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise ApiError(404, "not_found", "No such user.")
    return u


@router.post("/users/{user_id}/disable", response_model=AdminUserOut)
def disable_user(user_id: int, admin: AdminDep, db: DbDep) -> AdminUserOut:
    """Deactivate: no login, every session revoked, remembered devices forgotten. Reversible."""
    u = _target(db, user_id)
    if u.id == admin.id:
        raise ApiError(409, "cannot_disable_self", "You cannot disable your own account.")
    if u.disabled_at is None:
        u.disabled_at = utcnow()
        db.add(u)
        for s in db.exec(select(AuthSession).where(AuthSession.user_id == u.id)).all():
            db.delete(s)
        rotate_device_nonce(db, u)
        record(db, admin, "user.disable", u.id)
        db.commit()
    return _user_out(u, _last_seen(db, user_id))


@router.post("/users/{user_id}/enable", response_model=AdminUserOut)
def enable_user(user_id: int, admin: AdminDep, db: DbDep) -> AdminUserOut:
    u = _target(db, user_id)
    if u.disabled_at is not None:
        u.disabled_at = None
        db.add(u)
        record(db, admin, "user.enable", u.id)
        db.commit()
    return _user_out(u, _last_seen(db, user_id))
