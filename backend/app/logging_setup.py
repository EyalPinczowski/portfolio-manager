"""Logging hygiene: no secrets or personal data in logs.

- `httpx` / `httpcore` loggers are set to WARNING (at INFO httpx logs full request URLs, and a
  Telegram URL contains the bot token).
- A log-record redaction filter masks Telegram bot tokens (`bot<digits>:<secret>`) wherever they
  appear, in the message or in its arguments. It is installed as a record factory so it applies to
  every logger and handler, in both the API and the scheduler process.
"""

from __future__ import annotations

import logging
import re
from typing import Any

BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
REDACTED = "bot<redacted>"
QUIET_LOGGERS = ("httpx", "httpcore")

_installed = False


def redact_text(text: str) -> str:
    return BOT_TOKEN_RE.sub(REDACTED, text)


class RedactionFilter(logging.Filter):
    """Usable on a handler or logger (`handler.addFilter(RedactionFilter())`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record)
        return True


def _redact_record(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:  # a malformed log call must never break the app
        return
    cleaned = redact_text(message)
    if cleaned != message:
        record.msg, record.args = cleaned, ()


def configure_logging() -> None:
    """Idempotent. Call once at API and scheduler start."""
    global _installed
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        _redact_record(record)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
