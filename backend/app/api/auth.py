"""Auth endpoints: invite-only signup, login (rate limited), logout, me, OCR consent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.api.schemas import LoginIn, MeOut, PasswordBody, SessionOut, SignupIn
from app.auth import account
from app.auth.deps import AuthContext, AuthDep, DbDep, SettingsDep
from app.auth.device import (
    forget_device,
    is_known_device,
    remember_device,
    rotate_device_nonce,
)
from app.auth.passwords import burn_verify, hash_password, needs_rehash, verify_password
from app.auth.ratelimit import (
    client_ip,
    enforce_limit,
    login_limiter,
    signup_limiter,
    too_many,
)
from app.auth.sessions import clear_session_cookie, create_session, set_session_cookie
from app.auth.turnstile import TurnstileDep
from app.config import Settings, turnstile_state
from app.errors import ApiError, ChallengeRequiredOut
from app.models import AuthSession, Invite, User
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, utcnow

router = APIRouter(tags=["auth"], route_class=StrictJsonRoute)


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


GENERIC_SIGNUP_ERROR = "Could not create the account. Check the invite code and try again."


def claim_invite(db: Session, code: str, user_id: int) -> bool:
    """Atomically mark an invite as used by `user_id`. False if it is unknown, used or expired.

    One conditional UPDATE decides the race: two signups with the same code can never both win.
    """
    result = db.execute(
        update(Invite)
        .where(
            col(Invite.code) == code,
            col(Invite.used_by).is_(None),
            col(Invite.expires_at) > utcnow(),
        )
        .values(used_by=user_id)
    )
    return result.rowcount == 1  # type: ignore[attr-defined,no-any-return]


@router.post("/auth/signup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def signup(
    body: SignupIn, request: Request, response: Response, db: DbDep, settings: SettingsDep
) -> MeOut:
    enforce_limit(
        signup_limiter,
        f"ip:{client_ip(request, settings)}",
        settings.signup_rate_limit_per_hour,
        3600.0,
    )
    if len(body.password) < settings.password_min_length:
        raise HTTPException(
            422,
            f"Password must be at least {settings.password_min_length} characters",
        )
    email = body.email.strip().lower()
    # Hash first on every path, so a bad invite, an existing email and a success take the same time
    # and give the same answer (no account or invite enumeration).
    pw_hash = hash_password(body.password)
    user = User(
        email=email,
        password_hash=pw_hash,
        locale=body.locale,
        disclaimer_accepted_at=utcnow(),
    )
    db.add(user)
    try:
        db.flush()  # the unique email index rejects a duplicate here
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, GENERIC_SIGNUP_ERROR) from None
    assert user.id is not None
    if not claim_invite(db, body.invite_code.strip(), user.id):
        db.rollback()  # the user row is discarded and the invite stays untouched
        raise HTTPException(status.HTTP_400_BAD_REQUEST, GENERIC_SIGNUP_ERROR)
    db.commit()
    session, cookie = create_session(db, user, settings)
    set_session_cookie(response, cookie, settings)
    return _me(user, session)


@router.post(
    "/auth/login",
    response_model=MeOut,
    responses={
        403: {
            "model": ChallengeRequiredOut,
            "description": "A Cloudflare Turnstile token is required (`code: turnstile_required`).",
        }
    },
)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    turnstile: TurnstileDep,
) -> MeOut:
    email = body.email.strip().lower()
    ip = client_ip(request, settings)
    window = float(settings.login_rate_limit_window_seconds)
    base = settings.login_backoff_base_seconds
    free = settings.login_rate_limit_attempts
    user = db.exec(select(User).where(User.email == email)).first()
    pair_key = f"pair:{email}|{ip}"
    email_key = f"email:{email}"
    # Per-IP and per-(email, IP) backoff always apply. The email-only key (all IPs together) has a
    # much higher threshold, and a device that already logged in as this user is exempt from it, so a
    # stranger cannot keep the owner out. The limiter runs before any password hashing.
    keys = [
        (f"ip:{ip}", free * settings.login_rate_limit_ip_multiplier),
        (pair_key, free),
    ]
    known = user is not None and is_known_device(request, settings, user)
    if not known:
        keys.append((email_key, free * settings.login_rate_limit_email_multiplier))
    for k, k_free in keys:
        wait = login_limiter.retry_after(k, k_free, base, window)
        if wait:
            raise too_many(wait, "Too many login attempts. Try again later.")
    challenge = login_limiter.failures(pair_key, window) >= settings.turnstile_after_failures or (
        not known
        and login_limiter.failures(email_key, window) >= settings.turnstile_email_after_failures
    )
    if turnstile_state(settings) == "on" and challenge:
        token = (body.turnstile_token or "").strip()
        verified = bool(token) and turnstile.verify(token, ip)
        if not verified:
            if token:  # a forged or replayed token is a failed attempt like a wrong password
                for k in (f"ip:{ip}", pair_key, email_key):
                    login_limiter.record_failure(k, window)
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "turnstile_required",
                "Please complete the verification and try again.",
                extra={"site_key": settings.turnstile_site_key},
            )
    if user is None:
        burn_verify(body.password)
        ok = False
    else:
        ok = verify_password(user.password_hash, body.password)
    if not ok or user is None or user.id is None:
        for k in (f"ip:{ip}", pair_key, email_key):
            login_limiter.record_failure(k, window)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if user.disabled_at is not None:  # only after the password matched: no account enumeration
        raise ApiError(status.HTTP_403_FORBIDDEN, "account_disabled", "This account is disabled.")
    # A valid login clears this pair and the email key, but never the IP's count.
    login_limiter.reset(pair_key)
    login_limiter.reset(email_key)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        db.add(user)
        db.commit()
    session, cookie = create_session(db, user, settings)
    set_session_cookie(response, cookie, settings)
    remember_device(request, response, settings, db, user)
    return _me(user, session)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: AuthDep, db: DbDep, settings: SettingsDep) -> Response:
    db.delete(auth.session)
    rotate_device_nonce(db, auth.user)  # every remembered device of this user is revoked
    db.commit()
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(resp, settings)
    forget_device(resp, settings)
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


def _check_password(auth: AuthContext, password: str) -> None:
    """Re-authenticate before a destructive or bulk-data action. Wrong password -> 403."""
    if not verify_password(auth.user.password_hash, password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Incorrect password")


def _confirm_password(
    auth: AuthContext, password: str, request: Request, settings: Settings
) -> None:
    ip = client_ip(request, settings)
    window = float(settings.login_rate_limit_window_seconds)
    keys = (
        (f"ip:{ip}", settings.login_rate_limit_attempts * settings.login_rate_limit_ip_multiplier),
        (f"user:{auth.user.id}", settings.login_rate_limit_attempts),
    )
    for k, k_free in keys:  # a stolen session must not be able to brute-force the password here
        wait = login_limiter.retry_after(k, k_free, settings.login_backoff_base_seconds, window)
        if wait:
            raise too_many(wait, "Too many attempts. Try again later.")
    try:
        _check_password(auth, password)
    except HTTPException:
        for k, _ in keys:
            login_limiter.record_failure(k, window)
        raise
    login_limiter.reset(keys[1][0])


@router.post("/me/export")
def export_me(
    body: PasswordBody, request: Request, auth: AuthDep, db: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _confirm_password(auth, body.password, request, settings)
    return account.export_user_data(db, auth.user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    body: PasswordBody, request: Request, auth: AuthDep, db: DbDep, settings: SettingsDep
) -> Response:
    _confirm_password(auth, body.password, request, settings)
    account.delete_user(db, auth.user)  # the row (and its nonce) is gone: no id reuse inheritance
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(resp, settings)
    forget_device(resp, settings)
    return resp


# ---------------------------------------------------------------- sessions
@router.get("/auth/sessions", response_model=list[SessionOut])
def list_sessions(auth: AuthDep, db: DbDep) -> list[SessionOut]:
    rows = db.exec(
        select(AuthSession)
        .where(AuthSession.user_id == auth.user.id, col(AuthSession.expires_at) > utcnow())
        .order_by(col(AuthSession.id).desc())
    ).all()
    return [
        SessionOut(
            id=r.id or 0,
            created_at=as_utc(r.created_at),
            last_seen_at=as_utc(r.last_seen_at),
            current=r.id == auth.session.id,
        )
        for r in rows
    ]


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: int, auth: AuthDep, db: DbDep, settings: SettingsDep) -> Response:
    row = db.get(AuthSession, session_id)
    if (
        row is None or row.user_id != auth.user.id
    ):  # someone else's session looks like a missing one
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    was_current = row.id == auth.session.id
    db.delete(row)
    db.commit()
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    if was_current:
        clear_session_cookie(resp, settings)
    return resp


@router.post("/auth/sessions/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
def revoke_all_sessions(
    request: Request, auth: AuthDep, db: DbDep, settings: SettingsDep
) -> Response:
    """Log out everywhere else: every session of this user except the current one.

    Other browsers also lose their "known device" status (new nonce); this one keeps it.
    """
    for row in db.exec(
        select(AuthSession).where(
            AuthSession.user_id == auth.user.id, col(AuthSession.id) != auth.session.id
        )
    ).all():
        db.delete(row)
    rotate_device_nonce(db, auth.user)
    db.commit()
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    remember_device(request, resp, settings, db, auth.user)
    return resp
