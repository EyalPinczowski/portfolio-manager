"""Telegram sender: only active if TELEGRAM_BOT_TOKEN is set and the user is linked."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def send_telegram(chat_id: str | None, text: str, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
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
