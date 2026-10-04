"""Fixture corpus for the personal-data scrubber (`app/llm/scrub.py`): text in, exact text out.

Each case is `(id, kind, text, expected)`. `kind` groups the rule under test; `keep` cases are text
that must come out **unchanged** (prices, Hebrew with numbers, tickers, dates). Add a case here
before changing a rule.

`kind == "provider"` cases go through `scrub_baseline` (what every prompt gets); all the other
kinds go through `scrub_user_text` for the requesting user (`USER_EMAILS`, id 1) with
`IMPORT_NAMES` as the owner names captured at import. Other users (`OTHER_USER_EMAILS`) are never
masked. `KNOWN_TASE`, `ALLOW_TICKERS` and `ALLOW_WORDS` stand for the `Security` table.
"""

from __future__ import annotations

from typing import NamedTuple

USER_EMAILS = ["dana.levi@example.com"]  # the requesting user
OTHER_USER_EMAILS = ["eyalpin2002@gmail.com", "moshe_cohen@example.org"]
IMPORT_NAMES = ["דנה לוי"]  # owner name captured at import
KNOWN_TASE = frozenset({"1081124", "629014"})
ALLOW_TICKERS = frozenset({"aapl", "teva", "teva.ta", "tsla"})
ALLOW_WORDS = frozenset({"tesla", "apple"})


class Case(NamedTuple):
    id: str
    kind: str
    text: str
    expected: str


def keep(id_: str, text: str) -> Case:
    return Case(id_, "keep", text, text)


def provider(id_: str, text: str, expected: str | None = None) -> Case:
    """Provider data: only secrets are masked, facts and numbers pass through."""
    return Case(id_, "provider", text, text if expected is None else expected)


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
    Case("name-case-insensitive", "name", "DANA sold half", "[user] sold half"),
    # names belong to the requesting user only: other users' names stay (review: `tesla.fan@`)
    keep("name-other-user-local-part", "Hi eyalpin2002, welcome"),
    keep("name-other-user-first", "Moshe sold half"),
    Case(
        "name-whole-word-only",
        "keep",
        "Danahar Corp and Bandana Inc",
        "Danahar Corp and Bandana Inc",
    ),
    Case("name-common-word-trade-off", "name", "Levi Strauss reported", "[user] Strauss reported"),
    # ---- leaks the Phase 2.0 diff review verified ----
    Case("leak-id-spaces", "leak", "ת.ז. 012 345 678 של הלקוח", "ת.ז. [number] של הלקוח"),
    Case("leak-id-dashes", "leak", "id 012-345-678 given", "id [number] given"),
    Case("leak-id-dots", "leak", "id 012.345.678 given", "id [number] given"),
    Case("leak-id-single-digits", "leak", "id 0-1-2-3-4-5-6-7-8 given", "id [number] given"),
    Case("leak-id-single-digits-spaces", "leak", "id 0 1 2 3 4 5 6 7 8", "id [number]"),
    Case(
        "leak-email-base64", "leak", "token ZGFuYS5sZXZpQGV4YW1wbGUuY29t end", "token [token] end"
    ),
    Case("leak-email-base64-padded", "leak", "x=am9obi5kb2VAZXhhbXBsZS5jb20=", "x=[token]"),
    Case(
        "leak-phone-base64url",
        "leak",
        "ref dXNlcjogMDUwMTIzNDU2NyBjYWxsIG1l ok",
        "ref [token] ok",
    ),
    Case(
        "leak-url-user-param",
        "leak",
        "see https://app.example.com/p?user=danalevi&sym=AAPL",
        "see https://app.example.com/p?user=[user]&sym=AAPL",
    ),
    Case("leak-url-uid", "leak", "GET /x?uid=87654&a=1", "GET /x?uid=[user]&a=1"),
    Case("leak-hebrew-owner-label", "leak", "בעל החשבון: יוסי כהן", "בעל החשבון: [user]"),
    Case(
        "leak-hebrew-owner-label-line",
        "leak",
        "שם בעל החשבון: יוסי כהן\nיתרה: 1,234.56",
        "שם בעל החשבון: [user]\nיתרה: 1,234.56",
    ),
    Case(
        "leak-english-owner-label",
        "leak",
        "Account holder: Yossi Cohen, AAPL",
        "Account holder: [user], AAPL",
    ),
    Case("leak-hebrew-import-name", "leak", "החשבון של דנה לוי נפתח", "החשבון של [user] נפתח"),
    Case("leak-hebrew-import-name-prefix", "leak", "שלחתי לדנה לוי", "שלחתי ל[user]"),
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
    # ---- numbers that are facts, not ids (the diff review's over-masking) ----
    keep("keep-isin", "ISIN US0378331005 held, also IL0011234567"),
    keep("keep-tase-numbers-known", "TASE security 1081124 and 629014 (Teva)"),
    keep("keep-dates-and-ranges", "03-10-2026, 2026-10-03, 100-200, 10-20-30"),
    keep("keep-three-groups", "scores 12-34-56"),
    keep("keep-allowed-company-word", "Tesla and Apple were discussed, TSLA, AAPL"),
    keep("keep-sku-not-base64", "internationalization characterization"),
    # ---- provider data: nothing but secrets is touched ----
    provider("provider-market-cap", "Market cap 1250000 USD, volume 12345678 shares"),
    provider("provider-tase-numbers", "Security 629014 and 1081124; ID 1081124"),
    provider("provider-isin", "ISIN US0378331005, IL0011234567"),
    provider("provider-names-and-words", "Tesla fan club, Dana Corp, Levi Strauss"),
    provider("provider-hebrew-numbers", "מחזור 12345678 מניות, נייר 629014"),
    provider("provider-long-integer-price", "Revenue 123456789012 in thousands"),
    provider("provider-email-still-masked", "Contact ir@tesla.com today", "Contact [email] today"),
    provider(
        "provider-key-still-masked",
        "api key AIzaSyA1234567890abcdefghijklmnopqrstuv in the feed",
        "api key [token] in the feed",
    ),
    provider("provider-phone-kept", "IR line 050-123-4567 for investors"),
]
