"""Personal-data scrubber: runs inside `BaseLLMProvider.complete`, so no role can skip it.

Two entry points, because provider data and user-supplied text are different things:

- `scrub_baseline(text)`: applied to **everything** that is sent (system, prompt, news, provider
  data). Only high-confidence secrets: e-mail addresses, `X-...` header tokens, API keys. Numbers,
  names and tickers are never touched, so market caps, volumes, ISINs and TASE security numbers
  reach the model intact.
- `scrub_user_text(text, user_id=..., names=...)`: applied to text a person wrote or uploaded
  (notes, chat, OCR). On top of the baseline it masks: base64-encoded e-mails, `user=name` style
  URL parameters, labelled owner names (English and Hebrew), the **requesting user's own** names
  (parts of their e-mail address plus `names`, such as owner names captured at import), phone
  numbers, account patterns (`12-345678`, labelled accounts, IBANs, 4-4-4-4 cards), ids written with
  separators (`012 345 678`, `012-345-678`, `0-1-2-3-4-5-6-7-8`) and every remaining run of
  `llm_scrub_min_digit_run` or more digits. It leaves ISINs, known TASE security numbers, tickers and
  company words (read from the `Security` table) alone.

What it deliberately leaves alone: numbers with separators or decimals (`1,234.56`, `3.654321`),
percentages, dates, ranges and Hebrew text. The fixture corpus in `tests/fixtures/scrub_corpus.py`
pins every rule, and a test asserts that the provider base class cannot be bypassed. Only counts are
ever logged, never the text.
"""

from __future__ import annotations

import base64
import binascii
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
]
# Any long run of letters and digits that contains both (hashes, opaque tokens): user text only.
_GENERIC_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{28,}"
)
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


# Ids written with separators: `012 345 678`, `012-345-678`, `012.345.678`, `0-1-2-3-4-5-6-7-8`.
_SEPARATED_ID_RES = [
    re.compile(r"(?<![\d.,\-])\d{3}[ .\-]\d{3}[ .\-]\d{3}(?!\d|[.,]\d|-\d)"),
    re.compile(r"(?<![\d.,\-])\d(?:[ \-]\d){7,}(?!\d)"),
    re.compile(r"(?<![\d.,\-])\d{1,3}(?:[ \-]\d{1,3}){2,}(?!\d)"),  # checked: >= 8 digits
]
_SEPARATED_MIN_DIGITS = 8
# A parameter that names a person in a URL or query string: `?user=danalevi`.
_URL_PARAM_RE = re.compile(
    r"(?i)([?&;\s](?:user(?:name|id)?|usr|uid|login|owner|account(?:_?id)?|acct|e-?mail|"
    r"full_?name|first_?name|last_?name)=)([^&\s#\"'<>]+)"
)
# "Owner: Dana Levi", "בעל החשבון: דנה לוי": keep the label, mask the (up to four word) name.
_OWNER_RE = re.compile(
    r"(?i)((?:שם\s+)?(?:בעל(?:ת)?\s+ה?חשבון|הלקוח|לכבוד|שם\s+ה?לקוח|שם\s+ה?משתמש|"
    r"account\s+(?:holder|owner)(?:\s+name)?|owner|holder|client\s+name|customer\s+name)"
    r"\s*[:\-\u2013]\s*)((?:[A-Za-z\u0590-\u05ff'\u2019.\-]{2,}[ \t]?){1,4})"
)
_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")
_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{16,}={0,2}(?![A-Za-z0-9+/_=\-])")
_HEBREW = re.compile("[\u0590-\u05ff]")


@dataclass(frozen=True)
class ScrubResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.counts)


@dataclass(frozen=True)
class AllowList:
    """Things that look like personal data but are market facts: never masked."""

    tickers: frozenset[str] = frozenset()  # lower case, with and without the exchange suffix
    tase_numbers: frozenset[str] = frozenset()
    words: frozenset[str] = frozenset()  # lower-case words of company names (`tesla`, `apple`)


def allow_list_from_db() -> AllowList:
    """Tickers, TASE security numbers and company-name words of the `Security` table. An unreadable
    database yields an empty list (the scrubber then masks more, never less)."""
    from sqlmodel import select

    from app.db import new_session
    from app.models import Security

    try:
        with new_session() as db:
            rows = db.exec(select(Security.symbol, Security.name_en, Security.tase_number)).all()
    except Exception:
        log.warning("scrubber: allow-list unavailable", exc_info=True)
        return AllowList()
    tickers: set[str] = set()
    numbers: set[str] = set()
    words: set[str] = set()
    for symbol, name, tase in rows:
        tickers |= {symbol.lower(), symbol.lower().split(".")[0]}
        if tase:
            numbers.add(str(tase))
        words |= {w for w in re.split(r"[^a-z]+", (name or "").lower()) if len(w) >= 4}
    return AllowList(frozenset(tickers), frozenset(numbers), frozenset(words))


def names_from_emails(emails: Iterable[str], min_chars: int = 4) -> set[str]:
    """Whole local parts and their letter-only components (`dana.levi99@x` -> dana.levi99, dana, levi)."""
    out: set[str] = set()
    for email in emails:
        local = email.split("@", 1)[0].strip().lower()
        if len(local) >= min_chars:
            out.add(local)
        for part in re.split(r"[^a-z\u0590-\u05ff]+", local):
            if len(part) >= min_chars:
                out.add(part)
    return out


def user_names_from_db(user_id: int | None = None, min_chars: int = 4) -> set[str]:
    """Names of ONE user (the requesting one): the parts of their e-mail address. Other users'
    names are never read (one user's `tesla.fan@` must not mask "Tesla" for everybody). No user id,
    or an unreadable database, yields no names (the other rules still apply), never an exception."""
    if user_id is None:
        return set()
    from app.db import new_session
    from app.models import User

    try:
        with new_session() as db:
            user = db.get(User, user_id)
            return names_from_emails([user.email] if user else [], min_chars)
    except Exception:
        log.warning("scrubber: user names unavailable", exc_info=True)
        return set()


class PersonalDataScrubber:
    def __init__(
        self,
        settings: Settings | None = None,
        names: Callable[[int | None], Iterable[str]] | None = None,
        names_ttl_seconds: float = 30.0,
        allow: Callable[[], AllowList] | None = None,
    ) -> None:
        s = settings or get_settings()
        self._digits = _digit_run_re(s.llm_scrub_min_digit_run)
        self._min_name = s.llm_scrub_min_name_chars
        self._names_source = names or (lambda uid: user_names_from_db(uid, self._min_name))
        self._allow_source = allow or allow_list_from_db
        self._ttl = names_ttl_seconds
        self._lock = threading.Lock()
        self._names_cache: dict[int | None, tuple[float, set[str]]] = {}
        self._allow: AllowList = AllowList()
        self._allow_at = float("-inf")

    # ------------------------------------------------------------ cached sources
    def _allow_list(self) -> AllowList:
        with self._lock:
            if time.monotonic() - self._allow_at > self._ttl:
                self._allow = self._allow_source()
                self._allow_at = time.monotonic()
            return self._allow

    def _name_patterns(self, user_id: int | None, extra: Iterable[str]) -> list[re.Pattern[str]]:
        with self._lock:
            hit = self._names_cache.get(user_id)
            if hit is None or time.monotonic() - hit[0] > self._ttl:
                hit = (time.monotonic(), {n.strip().lower() for n in self._names_source(user_id)})
                self._names_cache[user_id] = hit
                if len(self._names_cache) > 256:  # bounded: one entry per recent user
                    self._names_cache.pop(next(iter(self._names_cache)))
        allow = self._allow_list()
        names = {n for n in hit[1] if n not in allow.words and n not in allow.tickers}
        names |= {n.strip().lower() for n in extra if n.strip()}  # owner names captured at import
        patterns: list[re.Pattern[str]] = []
        for n in sorted((n for n in names if len(n) >= 3), key=len, reverse=True):
            if _HEBREW.search(n):  # Hebrew: allow the one-letter prefixes (ל, ב, ה ...), keep them
                patterns.append(
                    re.compile(
                        rf"(?<![\u0590-\u05ffA-Za-z0-9])([והבלמשכ]?){re.escape(n)}(?![\u0590-\u05ffA-Za-z0-9])"
                    )
                )
            else:
                patterns.append(
                    re.compile(
                        rf"(?<![A-Za-z0-9\u0590-\u05ff]){re.escape(n)}(?![A-Za-z0-9\u0590-\u05ff])",
                        re.I,
                    )
                )
        return patterns

    # ------------------------------------------------------------ the two entry points
    def scrub_baseline(self, text: str) -> ScrubResult:
        """E-mails, `X-` tokens and API keys only: safe on any text, including provider data."""
        counts: dict[str, int] = {}
        text = self._baseline(text, counts)
        return ScrubResult(text, counts)

    def scrub_user_text(
        self, text: str, *, user_id: int | None = None, names: Iterable[str] = ()
    ) -> ScrubResult:
        """Everything in the module docstring, for text a person wrote or uploaded."""
        counts: dict[str, int] = {}
        text = self._baseline(text, counts)  # keys first: a JWT is base64 too
        text = self._mask_encoded_pii(text, counts)
        text = self._apply(_URL_PARAM_RE, lambda m: f"{m.group(1)}{USER}", "name", text, counts)
        text = self._apply(_OWNER_RE, lambda m: f"{m.group(1)}{USER}", "name", text, counts)
        for rx in self._name_patterns(user_id, names):
            text = self._apply(
                rx, lambda m: f"{m.group(1) if m.groups() else ''}{USER}", "name", text, counts
            )
        text = self._apply(_GENERIC_TOKEN_RE, TOKEN, "token", text, counts)
        for rx in _PHONE_RES:
            text = self._apply(rx, PHONE, "phone", text, counts)
        text = self._apply(
            _LABELLED_ACCOUNT_RE, lambda m: f"{m.group(1)}{ACCOUNT}", "account", text, counts
        )
        for rx in _ACCOUNT_RES:
            text = self._apply(rx, ACCOUNT, "account", text, counts)
        allow = self._allow_list()
        protected = [m.span() for m in _ISIN_RE.finditer(text)]

        def keep_known(m: re.Match[str]) -> str:
            known = m.group(0) in allow.tase_numbers or any(
                a <= m.start() and m.end() <= b for a, b in protected
            )
            return m.group(0) if known else NUMBER

        def separated(m: re.Match[str]) -> str:
            digits = sum(ch.isdigit() for ch in m.group(0))
            return NUMBER if digits >= _SEPARATED_MIN_DIGITS else m.group(0)

        for rx in _SEPARATED_ID_RES:
            text = self._apply(rx, separated, "number", text, counts, only_changes=True)
        text = self._apply(self._digits, keep_known, "number", text, counts, only_changes=True)
        return ScrubResult(text, counts)

    # ------------------------------------------------------------ pieces
    @staticmethod
    def _apply(
        pattern: re.Pattern[str],
        repl: str | Callable[[re.Match[str]], str],
        kind: str,
        text: str,
        counts: dict[str, int],
        only_changes: bool = False,
    ) -> str:
        n = 0

        def counted(m: re.Match[str]) -> str:
            nonlocal n
            out = repl(m) if callable(repl) else repl
            if not only_changes or out != m.group(0):
                n += 1
            return out

        new = pattern.sub(counted, text)
        if n:
            counts[kind] = counts.get(kind, 0) + n
        return new

    def _baseline(self, text: str, counts: dict[str, int]) -> str:
        text = self._apply(_EMAIL_RE, EMAIL, "email", text, counts)
        text = self._apply(
            _XHEADER_RE, lambda m: f"{m.group(1)}{m.group(2)}{TOKEN}", "token", text, counts
        )
        text = self._apply(_BARE_X_TOKEN_RE, TOKEN, "token", text, counts)
        for rx in _KEY_RES:
            text = self._apply(rx, TOKEN, "token", text, counts)
        return text

    def _mask_encoded_pii(self, text: str, counts: dict[str, int]) -> str:
        """Tokens that are base64 (or base64url) of something personal: an e-mail, a phone number
        or a long digit run (`ZGFuYS5sZXZpQGV4YW1wbGUuY29t` is dana.levi@example.com)."""

        def check(m: re.Match[str]) -> str:
            token = m.group(0)
            try:
                raw = base64.b64decode(
                    token.replace("-", "+").replace("_", "/") + "=" * (-len(token) % 4),
                    validate=True,
                )
                decoded = raw.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                return token
            if not decoded.isprintable():
                return token
            if (
                _EMAIL_RE.search(decoded)
                or any(rx.search(decoded) for rx in _PHONE_RES)
                or self._digits.search(decoded)
            ):
                return TOKEN
            return token

        return self._apply(_BASE64_RE, check, "token", text, counts, only_changes=True)
