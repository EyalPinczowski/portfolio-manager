"""Telegram sender: only active if TELEGRAM_BOT_TOKEN is set and the user is linked."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.outbound import OutboundBlocked, release_text

log = logging.getLogger(__name__)


def send_telegram(chat_id: str | None, text: str, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    try:
        text = release_text(text, settings=s)  # every outgoing message passes the launch gate
    except OutboundBlocked as exc:
        log.warning("telegram message refused: %s", exc)
        return False
    if not s.telegram_bot_token or not chat_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=s.telegram_timeout_seconds,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("telegram send failed: %s", type(exc).__name__)
        return False
    return True


# ---------------------------------------------------------------- the sender interface
class TelegramSender(Protocol):
    """What the app needs from Telegram: send one text to one chat, True when it was delivered.

    Every implementation applies the outbound gate (`release_text`); the chat id is never logged.
    """

    def send(self, chat_id: str | None, text: str) -> bool: ...


class HttpTelegramSender:
    """The real sender (Bot API over HTTPS, token from the environment)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def send(self, chat_id: str | None, text: str) -> bool:
        return send_telegram(chat_id, text, self._settings)


class FakeTelegramSender:
    """For tests: records what would be sent and does no network call. It still applies the gate."""

    def __init__(self, ok: bool = True, settings: Settings | None = None) -> None:
        self.ok = ok
        self.sent: list[tuple[str, str]] = []
        self._settings = settings

    def send(self, chat_id: str | None, text: str) -> bool:
        text = release_text(text, settings=self._settings)
        if not chat_id or not self.ok:
            return False
        self.sent.append((chat_id, text))
        return True


_sender: TelegramSender | None = None


def set_sender(sender: TelegramSender | None) -> None:
    """Install a sender (tests); None restores the real one."""
    global _sender
    _sender = sender


def get_sender(settings: Settings | None = None) -> TelegramSender:
    return _sender or HttpTelegramSender(settings)
