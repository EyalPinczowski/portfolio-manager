"""Personal-data scrubber: runs inside `BaseLLMProvider.complete`, so no role can skip it.

Masks, in this order: e-mail addresses, header-style `X-...: value` tokens and API-key-like tokens,
the names of our users (the parts of the e-mail addresses in the `User` table, as whole words),
phone numbers, account patterns (`12-345678`, labelled account numbers, IBANs, 4-4-4-4 card
numbers) and every remaining run of `llm_scrub_min_digit_run` or more digits.

What it deliberately leaves alone: numbers with separators or decimals (`1,234.56`, `3.654321`,
`1234567.89`), percentages, dates, tickers and Hebrew text. Callers should therefore format large
plain integers with separators (a 7-digit market cap written `1250000` reads as an id and is masked).
The fixture corpus in `tests/fixtures/scrub_corpus.py` pins every rule, and a test asserts that the
provider base class cannot be bypassed. Only counts are ever logged, never the text.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.config import Settings, get_settings

log = logging.getLogger("llm.scrub")

EMAIL = "[email]"
TOKEN = "[token]"
USER = "[user]"
PHONE = "[phone]"
ACCOUNT = "[account]"
NUMBER = "[number]"

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)
# `X-Csrf-Token: abc`, `X-Proxy-Auth=abc`: keep the header name, mask the value.
_XHEADER_RE = re.compile(
    r"\b(X-[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){0,6})(\s*[:=]\s*)([^\s,;\"']{6,})"
)
_BARE_X_TOKEN_RE = re.compile(r"\bX-[A-Za-z0-9_]{16,}\b")
_KEY_RES = [
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),  # Groq
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # OpenAI-style
    re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}"),  # Telegram bot token
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"),  # JWT
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/]{16,}=*"),
    # Any long run of letters and digits that contains both (hashes, opaque tokens).
    re.compile(
        r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{28,}"
    ),
]
_PHONE_RES = [
    # Israeli: 050-123-4567, 0501234567, 03-1234567, +972 50 123 4567, 972-3-123-4567
    re.compile(
        r"(?<![\w.,])(?:\+972[-\s]?|972[-\s]?|0)(?:5\d|7\d|[23489])[-\s]?\d{3}[-\s]?\d{4}(?!\d)"
    ),
    # Other international numbers written with a plus sign.
    re.compile(r"(?<![\w.,])\+\d{1,3}[-\s]?\(?\d{2,4}\)?[-\s]?\d{3}[-\s]?\d{3,4}(?!\d)"),
]
_ACCOUNT_RES = [
    re.compile(r"\bIL\d{2}(?:[ ]?\d){19}\b"),  # Israeli IBAN
    re.compile(r"(?<![\d\-])\d{4}(?:[ \-]\d{4}){3}(?![\d\-])"),  # 4-4-4-4 card number
    re.compile(r"(?<![\d\-])\d{2,3}-\d{3}-\d{4,9}(?![\d\-])"),  # bank-branch-account
    re.compile(r"(?<![\d\-])\d{2,3}-\d{5,9}(?![\d\-])"),  # branch-account: 12-345678
]
# "account 123-456", "חשבון 12345", "acct no. 4567-89": keep the label, mask the number.
_LABELLED_ACCOUNT_RE = re.compile(
    r"(?i)((?:account|acct|a/c|חשבון|חש')\s*(?:no\.?|number|num\.?|#|מס'?|מספר)?\s*[:#]?\s*)"
    r"\d[\d\-/ ]{2,}\d"
)


def _digit_run_re(min_run: int) -> re.Pattern[str]:
    # Not part of a longer number with separators or a decimal: `1,234.56`, `3.654321`, `1234567.89`.
    return re.compile(rf"(?<![\d.,])\d{{{min_run},}}(?!\d|[.,]\d)")


@dataclass(frozen=True)
class ScrubResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.counts)


def names_from_emails(emails: Iterable[str], min_chars: int = 4) -> set[str]:
    """Whole local parts and their letter-only components (`dana.levi99@x` -> dana.levi99, dana, levi)."""
    out: set[str] = set()
    for email in emails:
        local = email.split("@", 1)[0].strip().lower()
        if len(local) >= min_chars:
            out.add(local)
        for part in re.split(r"[^a-z֐-׿]+", local):
            if len(part) >= min_chars:
                out.add(part)
    return out


def user_names_from_db(min_chars: int = 4) -> set[str]:
    """Names of the users in the `User` table. An unreadable database yields no names (the other
    rules still apply), never an exception."""
    from sqlmodel import select

    from app.db import new_session
    from app.models import User

    try:
        with new_session() as db:
            return names_from_emails(db.exec(select(User.email)).all(), min_chars)
    except Exception:
        log.warning("scrubber: user names unavailable", exc_info=True)
        return set()


class PersonalDataScrubber:
    def __init__(
        self,
        settings: Settings | None = None,
        names: Callable[[], Iterable[str]] | None = None,
        names_ttl_seconds: float = 30.0,
    ) -> None:
        s = settings or get_settings()
        self._digits = _digit_run_re(s.llm_scrub_min_digit_run)
        self._min_name = s.llm_scrub_min_name_chars
        self._names_source = names or (lambda: user_names_from_db(self._min_name))
        self._ttl = names_ttl_seconds
        self._lock = threading.Lock()
        self._names: list[re.Pattern[str]] = []
        self._names_at = float("-inf")

    def _name_patterns(self) -> list[re.Pattern[str]]:
        with self._lock:
            if time.monotonic() - self._names_at > self._ttl:
                names = sorted(
                    {n.strip().lower() for n in self._names_source() if len(n.strip()) >= 3},
                    key=len,
                    reverse=True,
                )
                self._names = [
                    re.compile(rf"(?<![A-Za-z0-9֐-׿]){re.escape(n)}(?![A-Za-z0-9֐-׿])", re.I)
                    for n in names
                ]
                self._names_at = time.monotonic()
            return self._names

    def scrub(self, text: str) -> ScrubResult:
        counts: dict[str, int] = {}

        def apply(
            pattern: re.Pattern[str], repl: str | Callable[[re.Match[str]], str], kind: str
        ) -> None:
            nonlocal text
            text, n = pattern.subn(repl, text)
            if n:
                counts[kind] = counts.get(kind, 0) + n

        apply(_EMAIL_RE, EMAIL, "email")
        apply(_XHEADER_RE, lambda m: f"{m.group(1)}{m.group(2)}{TOKEN}", "token")
        apply(_BARE_X_TOKEN_RE, TOKEN, "token")
        for rx in _KEY_RES:
            apply(rx, TOKEN, "token")
        for rx in self._name_patterns():
            apply(rx, USER, "name")
        for rx in _PHONE_RES:
            apply(rx, PHONE, "phone")
        apply(_LABELLED_ACCOUNT_RE, lambda m: f"{m.group(1)}{ACCOUNT}", "account")
        for rx in _ACCOUNT_RES:
            apply(rx, ACCOUNT, "account")
        apply(self._digits, NUMBER, "number")
        return ScrubResult(text, counts)
