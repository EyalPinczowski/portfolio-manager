"""Fixture corpus for the personal-data scrubber (`app/llm/scrub.py`): text in, exact text out.

Each case is `(id, kind, text, expected)`. `kind` groups the rule under test; `keep` cases are text
that must come out **unchanged** (prices, Hebrew with numbers, tickers, dates). Add a case here
before changing a rule. The users in the "User table" are `USER_EMAILS`.
"""

from __future__ import annotations

from typing import NamedTuple

USER_EMAILS = ["dana.levi@example.com", "eyalpin2002@gmail.com", "moshe_cohen@example.org"]


class Case(NamedTuple):
    id: str
    kind: str
    text: str
    expected: str


def keep(id_: str, text: str) -> Case:
    return Case(id_, "keep", text, text)


CORPUS: list[Case] = [
    # ---- e-mail addresses ----
    Case(
        "email-plain",
        "email",
        "Contact dana.levi@example.com for details",
        "Contact [email] for details",
    ),
    Case("email-tag-subdomain", "email", "Mail: someone+tag@sub.domain.co.il.", "Mail: [email]."),
    Case("email-hebrew-around", "email", "שלח ל-yossi@walla.co.il בבקשה", "שלח ל-[email] בבקשה"),
    Case("email-two", "email", "a.b@x.io and c_d@y.com", "[email] and [email]"),
    Case("email-user-table", "email", "login: eyalpin2002@gmail.com", "login: [email]"),
    # ---- runs of 6+ digits ----
    Case("digits-7", "digits", "ID 1234567 owner", "ID [number] owner"),
    Case("digits-6-boundary", "digits", "ref 123456 done", "ref [number] done"),
    Case("digits-5-kept", "keep", "order 12345 ok", "order 12345 ok"),
    Case("digits-israeli-id", "digits", "ת.ז. 123456789", "ת.ז. [number]"),
    Case("digits-sentence-end", "digits", "Total 1234567.", "Total [number]."),
    Case("digits-hebrew-sentence", "digits", "המספר 1234567 הוא מזהה", "המספר [number] הוא מזהה"),
    Case("digits-isin-trade-off", "digits", "ISIN US0378331005 held", "ISIN US[number] held"),
    Case(
        "digits-unformatted-cap-trade-off",
        "digits",
        "Market cap 1250000 USD",
        "Market cap [number] USD",
    ),
    Case("digits-two-runs", "digits", "a 111111 b 2222222", "a [number] b [number]"),
    # ---- Israeli phone numbers (and + international) ----
    Case("phone-dashes", "phone", "Call 050-123-4567", "Call [phone]"),
    Case("phone-plain", "phone", "mobile 0501234567", "mobile [phone]"),
    Case("phone-landline", "phone", "office 03-1234567", "office [phone]"),
    Case("phone-plus972", "phone", "WhatsApp +972 50 123 4567 now", "WhatsApp [phone] now"),
    Case("phone-972-dashes", "phone", "tel 972-3-123-4567", "tel [phone]"),
    Case("phone-hebrew", "phone", "טלפון: 052-7654321", "טלפון: [phone]"),
    Case("phone-intl-plus", "phone", "NY desk +1 212 555 1234", "NY desk [phone]"),
    # ---- account patterns ----
    Case("acct-labelled", "account", "Account 12-345678", "Account [account]"),
    Case("acct-bare-pattern", "account", "bank 12-345678 branch", "bank [account] branch"),
    Case("acct-hebrew-label", "account", "חשבון 123-456789 בבנק", "חשבון [account] בבנק"),
    Case("acct-no-dot-label", "account", "acct no. 4567-89 closed", "acct no. [account] closed"),
    Case("acct-iban", "account", "IBAN IL62 0108 0000 0009 9999 999", "IBAN [account]"),
    Case("acct-card", "account", "card 4580 1234 5678 9012", "card [account]"),
    Case("acct-bank-branch-account", "account", "12-345-678901", "[account]"),
    # ---- API keys and X- tokens ----
    Case("key-google", "token", "key AIzaSyA1234567890abcdefghijklmnopqrstuv", "key [token]"),
    Case("key-groq", "token", "GROQ gsk_abcdefghijklmnopqrstuvwxyz0123456789", "GROQ [token]"),
    Case("key-openai-style", "token", "sk-proj-abcdefghijklmnop1234", "[token]"),
    Case("key-github", "token", "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "[token]"),
    Case(
        "key-bearer",
        "token",
        "Authorization: Bearer abcdefghijklmnop1234567890",
        "Authorization: [token]",
    ),
    Case("key-x-header-colon", "token", "X-CSRF-Token: 9f8e7d6c5b4a", "X-CSRF-Token: [token]"),
    Case("key-x-header-equals", "token", "X-Proxy-Auth=supersecretvalue", "X-Proxy-Auth=[token]"),
    Case(
        "key-jwt",
        "token",
        "t=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123_-xyz",
        "t=[token]",
    ),
    Case(
        "key-telegram", "token", "bot 123456789:AAH-abcdefghijklmnopqrstuvwxyz012345", "bot [token]"
    ),
    Case("key-hex-hash", "token", "hash 5d41402abc4b2a76b9719d911017c592", "hash [token]"),
    # ---- names of users in the User table ----
    Case("name-first", "name", "Dana asked about AAPL", "[user] asked about AAPL"),
    Case("name-local-part", "name", "Hi eyalpin2002, welcome", "Hi [user], welcome"),
    Case("name-case-insensitive", "name", "MOSHE sold half", "[user] sold half"),
    Case(
        "name-whole-word-only",
        "keep",
        "Danahar Corp and Bandana Inc",
        "Danahar Corp and Bandana Inc",
    ),
    Case("name-common-word-trade-off", "name", "Levi Strauss reported", "[user] Strauss reported"),
    # ---- several rules at once ----
    Case(
        "mixed-contact-block",
        "mixed",
        "Dana, dana.levi@example.com, 050-1234567, acct 12-345678, key AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "[user], [email], [phone], acct [account], key [token]",
    ),
    Case(
        "mixed-hebrew",
        "mixed",
        "נייד 050-1234567 ואימייל dana.levi@example.com",
        "נייד [phone] ואימייל [email]",
    ),
    Case(
        "mixed-idempotent",
        "mixed",
        "[email] [phone] [token] [number]",
        "[email] [phone] [token] [number]",
    ),
    # ---- must NOT be damaged ----
    keep("keep-price-comma-decimal", "Price 1,234.56 USD"),
    keep("keep-shekel-millions", "₪1,234,567.89 and 12,345,678 shares"),
    keep("keep-long-decimal-int-part", "Value 1234567.89 USD"),
    keep("keep-fx-six-decimals", "FX 3.654321 ILS per USD"),
    keep("keep-leading-zero-decimal", "ratio 0.123456"),
    keep("keep-english-analysis", "AAPL closed at 189.45, up 1.2% (RSI 61, SMA50 182.10)"),
    keep("keep-hebrew-percent-price", "המניה עלתה ב-5.2% והגיעה ל-1,234.56 ש״ח"),
    keep("keep-hebrew-volume", "מחזור של 12,345,678 מניות"),
    keep("keep-hebrew-quarter", "דוח רבעון 3 לשנת 2026"),
    keep("keep-hebrew-ticker", "טבע (TEVA.TA) נסחרת ב-52.30 ₪"),
    keep("keep-hebrew-index", "מדד ת״א 35: 2,450.12 נקודות"),
    keep("keep-date-iso", "as of 2026-10-03 and 2026-10-03T12:30:45+00:00"),
    keep("keep-ranges", "range 100-200, 10-20% and 52-week high"),
    keep("keep-tickers", "BRK-B, BTC-USD, ^TA125.TA, ^GSPC, S&P 500"),
    keep("keep-x-ray-word", "X-ray: concentration is high in tech"),
    keep("keep-empty", ""),
]
