"""Auth endpoints: invite-only signup, login (rate limited), logout, me, OCR consent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import select

from app.api.schemas import LoginIn, MeOut, SignupIn
from app.auth import account
from app.auth.deps import AuthDep, DbDep, SettingsDep
from app.auth.passwords import burn_verify, hash_password, needs_rehash, verify_password
from app.auth.ratelimit import login_limiter
from app.auth.sessions import clear_session_cookie, create_session, set_session_cookie
from app.models import AuthSession, Invite, User
from app.timeutil import utcnow

router = APIRouter(tags=["auth"])


def _me(user: User, session: AuthSession) -> MeOut:
    assert user.id is not None
    return MeOut(
        id=user.id,
        email=user.email,
        locale=user.locale,
        disclaimer_accepted=user.disclaimer_accepted_at is not None,
        ocr_consent=user.ocr_consent_at is not None,
        csrf_token=session.csrf_token,
    )


@router.post("/auth/signup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, response: Response, db: DbDep, settings: SettingsDep) -> MeOut:
    if len(body.password) < settings.password_min_length:
        raise HTTPException(
            422,
            f"Password must be at least {settings.password_min_length} characters",
        )
    invite = db.get(Invite, body.invite_code.strip())
    if invite is None or invite.used_by is not None or invite.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired invite code")
    email = body.email.strip().lower()
    if db.exec(select(User).where(User.email == email)).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not create the account")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        locale=body.locale,
        disclaimer_accepted_at=utcnow(),
    )
    db.add(user)
    db.flush()
    invite.used_by = user.id
    db.add(invite)
    db.commit()
    session, cookie = create_session(db, user, settings)
    set_session_cookie(response, cookie, settings)
    return _me(user, session)


@router.post("/auth/login", response_model=MeOut)
def login(
    body: LoginIn, request: Request, response: Response, db: DbDep, settings: SettingsDep
) -> MeOut:
    email = body.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    keys = (f"ip:{ip}", f"email:{email}")
    window = float(settings.login_rate_limit_window_seconds)
    for k in keys:
        if login_limiter.is_blocked(k, settings.login_rate_limit_attempts, window):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many login attempts. Try again later.",
                headers={"Retry-After": str(login_limiter.retry_after(k, window))},
            )
    user = db.exec(select(User).where(User.email == email)).first()
    if user is None:
        burn_verify(body.password)
        ok = False
    else:
        ok = verify_password(user.password_hash, body.password)
    if not ok or user is None:
        for k in keys:
            login_limiter.record_failure(k, window)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    for k in keys:
        login_limiter.reset(k)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        db.add(user)
        db.commit()
    session, cookie = create_session(db, user, settings)
    set_session_cookie(response, cookie, settings)
    return _me(user, session)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: AuthDep, db: DbDep, settings: SettingsDep) -> Response:
    db.delete(auth.session)
    db.commit()
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(resp, settings)
    return resp


@router.get("/auth/me", response_model=MeOut)
def me(auth: AuthDep) -> MeOut:
    return _me(auth.user, auth.session)


@router.post("/auth/consent/ocr", response_model=MeOut)
def consent_ocr(auth: AuthDep, db: DbDep) -> MeOut:
    if auth.user.ocr_consent_at is None:
        auth.user.ocr_consent_at = utcnow()
        db.add(auth.user)
        db.commit()
    return _me(auth.user, auth.session)


@router.get("/me/export")
def export_me(auth: AuthDep, db: DbDep) -> dict[str, Any]:
    return account.export_user_data(db, auth.user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(auth: AuthDep, db: DbDep, settings: SettingsDep) -> Response:
    account.delete_user(db, auth.user)
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(resp, settings)
    return resp
