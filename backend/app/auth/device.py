"""Known-device cookie: a signed, httpOnly cookie set after a successful login (90 days).

It lists the user ids that logged in from this browser. A request from a known device is exempt from
the email-only login backoff, so a stranger who keeps one email in backoff cannot lock its owner out
of their own phone. It never skips the per-(email, IP) or per-IP limits, the password check or
Turnstile, and it holds no personal data (ids only, signed, not guessable).
"""

from __future__ import annotations

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import Settings


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="pm-device")


def known_user_ids(request: Request, settings: Settings) -> list[int]:
    raw = request.cookies.get(settings.device_cookie_name)
    if not raw:
        return []
    try:
        data = _serializer(settings).loads(raw, max_age=settings.device_cookie_days * 86400)
    except BadSignature:
        return []
    if not isinstance(data, list):
        return []
    return [i for i in data if isinstance(i, int) and not isinstance(i, bool)]


def is_known_device(request: Request, settings: Settings, user_id: int) -> bool:
    return user_id in known_user_ids(request, settings)


def remember_device(request: Request, response: Response, settings: Settings, user_id: int) -> None:
    ids = [i for i in known_user_ids(request, settings) if i != user_id]
    ids.append(user_id)
    ids = ids[-settings.device_cookie_max_users :]
    response.set_cookie(
        settings.device_cookie_name,
        _serializer(settings).dumps(ids),
        max_age=settings.device_cookie_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/api/auth",
    )
