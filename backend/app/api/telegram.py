"""Telegram link flow: code, bot webhook, status, unlink (docs/settings-spec.md, section 3).

`POST /telegram/webhook` is called by Telegram, not by a signed-in user: it is protected by the
secret-token header (constant-time compare) and answers 200 to every well-formed call so Telegram
does not retry. The bot token comes from the environment only; the chat id is never logged or
returned.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Response, status
from pydantic import BaseModel

from app.alerts.telegram import TelegramSender, get_sender
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.auth.ratelimit import enforce_limit, telegram_code_limiter, telegram_link_limiter
from app.config import DISCLAIMER
from app.errors import ApiError
from app.outbound import render
from app.strictjson import StrictJsonRoute
from app.telegram_link import bind_from_start, issue_code, parse_start, unlink
from app.timeutil import as_utc

router = APIRouter(tags=["telegram"], route_class=StrictJsonRoute)

LINKED = (
    "Telegram is now linked to your account. Weekly reviews and price alerts arrive here. "
    + DISCLAIMER
)
INVALID = "That code is invalid or has expired. Create a new one in the app settings. " + DISCLAIMER
TAKEN = "This chat is already linked to another account. Disconnect it there first. " + DISCLAIMER
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class LinkCodeOut(BaseModel):
    code: str
    command: str
    expires_at: datetime
    ttl_minutes: int
    deep_link: str | None = None


class TelegramStatusOut(BaseModel):
    configured: bool  # a bot token is set
    webhook_ready: bool  # the webhook secret is set too
    linked: bool
    bot_username: str | None = None


@router.post("/telegram/link-code", response_model=LinkCodeOut, status_code=status.HTTP_201_CREATED)
def create_link_code(user: UserDep, db: DbDep, settings: SettingsDep) -> LinkCodeOut:
    assert user.id is not None
    if not settings.telegram_bot_token:
        raise ApiError(503, "telegram_not_configured", "Telegram is not set up on this server.")
    enforce_limit(
        telegram_code_limiter, f"user:{user.id}", settings.telegram_link_codes_per_hour, 3600.0
    )
    issued = issue_code(db, user, settings)
    bot = settings.telegram_bot_username
    return LinkCodeOut(
        code=issued.code,
        command=f"/start {issued.code}",
        expires_at=as_utc(issued.expires_at),
        ttl_minutes=settings.telegram_link_code_ttl_minutes,
        deep_link=f"https://t.me/{bot}?start={issued.code}" if bot else None,
    )


@router.get("/telegram/status", response_model=TelegramStatusOut)
def telegram_status(user: UserDep, settings: SettingsDep) -> TelegramStatusOut:
    return TelegramStatusOut(
        configured=bool(settings.telegram_bot_token),
        webhook_ready=bool(settings.telegram_bot_token and settings.telegram_webhook_secret),
        linked=user.telegram_chat_id is not None,
        bot_username=settings.telegram_bot_username,
    )


@router.delete("/telegram/link", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(user: UserDep, db: DbDep) -> Response:
    unlink(db, user)  # idempotent: unlinking an unlinked account is a 204 too
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _reply(sender: TelegramSender, chat_id: str, result: str, settings: SettingsDep) -> None:
    try:
        if result == "linked":
            text = render(LINKED, settings=settings)
        elif result == "chat_taken":
            text = render(TAKEN, settings=settings)
        else:
            text = render(INVALID, settings=settings)
        sender.send(chat_id, text)
    except Exception:  # a failed reply never fails the webhook (Telegram would retry the update)
        return


def _private_start(update: dict[str, Any]) -> tuple[str, str] | None:
    """(chat id, code) of a `/start <code>` sent in a private chat, else None."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    chat_id = chat.get("id")
    text = message.get("text")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or not isinstance(text, str):
        return None
    code = parse_start(text[:200])
    return (str(chat_id), code) if code else None


@router.post("/telegram/webhook")
def webhook(
    update: dict[str, Any],
    db: DbDep,
    settings: SettingsDep,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    secret = settings.telegram_webhook_secret
    if not settings.telegram_bot_token or not secret:
        raise ApiError(503, "telegram_not_configured", "Telegram is not set up on this server.")
    sent = (x_telegram_bot_api_secret_token or "").encode()
    if not hmac.compare_digest(sent, secret.encode()):
        raise ApiError(403, "forbidden", "Forbidden.")
    found = _private_start(update)
    if found is None:
        return {"ok": True}
    chat_id, code = found
    if telegram_link_limiter.hit(
        f"chat:{chat_id}", settings.telegram_link_attempts_per_hour, 3600.0
    ):
        return {"ok": True}  # silently dropped: a guesser learns nothing from the limit
    _reply(get_sender(settings), chat_id, bind_from_start(db, chat_id, code, settings), settings)
    return {"ok": True}
