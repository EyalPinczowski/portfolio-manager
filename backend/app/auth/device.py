"""Known-device cookie: a signed, httpOnly cookie set after a successful login (90 days).

It lists `[user_id, nonce]` pairs for the users that logged in from this browser. A request from a
known device is exempt from the email-only login backoff and from the email-key Turnstile trigger,
so a stranger who keeps one email in backoff cannot lock its owner out of their own phone. It never
skips the per-(email, IP) or per-IP limits, the password check or the pair-key Turnstile.

The nonce is random per user (`User.device_nonce`) and is rotated on logout, "log out everywhere"
and account deletion (the row is gone: a new user that reuses the id, as SQLite does, has another
nonce). A copied cookie therefore stops working, and it holds no personal data (ids and random
strings, signed).
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlmodel import Session

from app.config import Settings
from app.models import User


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="pm-device-v2")


def known_devices(request: Request, settings: Settings) -> dict[int, str]:
    """`{user_id: nonce}` from a valid cookie (empty for a missing, expired or forged one)."""
    raw = request.cookies.get(settings.device_cookie_name)
    if not raw:
        return {}
    try:
        data = _serializer(settings).loads(raw, max_age=settings.device_cookie_days * 86400)
    except BadSignature:
        return {}
    out: dict[int, str] = {}
    if isinstance(data, list):
        for item in data:
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], int)
                and not isinstance(item[0], bool)
                and isinstance(item[1], str)
            ):
                out[item[0]] = item[1]
    return out


def is_known_device(request: Request, settings: Settings, user: User) -> bool:
    if user.id is None or not user.device_nonce:
        return False
    sent = known_devices(request, settings).get(user.id)
    return sent is not None and hmac.compare_digest(sent.encode(), user.device_nonce.encode())


def rotate_device_nonce(db: Session, user: User) -> None:
    """Revoke every remembered device of the user (the caller commits when it likes)."""
    user.device_nonce = secrets.token_urlsafe(16)
    db.add(user)


def remember_device(
    request: Request, response: Response, settings: Settings, db: Session, user: User
) -> None:
    assert user.id is not None
    if not user.device_nonce:
        rotate_device_nonce(db, user)
        db.commit()
    assert user.device_nonce is not None
    entries = {i: n for i, n in known_devices(request, settings).items() if i != user.id}
    entries[user.id] = user.device_nonce
    pairs = [[i, n] for i, n in entries.items()][-settings.device_cookie_max_users :]
    response.set_cookie(
        settings.device_cookie_name,
        _serializer(settings).dumps(pairs),
        max_age=settings.device_cookie_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/api/auth",
    )


def forget_device(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.device_cookie_name, path="/api/auth")
