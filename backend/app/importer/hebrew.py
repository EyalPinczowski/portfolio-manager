"""Hebrew-aware token comparison (port of `frontend/lib/ocr/hebrew.ts`).

Tesseract returns right-to-left lines in visual order, so a Hebrew word can come out reversed
("125כשתא" for "תא125": the letters flip, digits keep their place). Two tokens are the same when
they are equal, or equal after reversing the letter runs, or equal after reversing the whole
string. Only tokens that contain Hebrew letters get the reversed comparison; Latin tokens must
match as written.
"""

from __future__ import annotations

import re
import unicodedata

HEB = re.compile("[֐-׿]")
HEB_RUN = re.compile("[֐-׿]+")
QUOTES = re.compile("[\"'`׳״]")
WORD_SPLIT = re.compile(r"[\s\-–—,.;:()/]+")


def has_hebrew(s: str) -> bool:
    return HEB.search(s) is not None


def norm_token(s: str) -> str:
    """Stable form for comparison: NFC, lower case, geresh/gershayim and quotes removed."""
    return QUOTES.sub("", unicodedata.normalize("NFC", s).lower())


def hebrew_variants(token: str) -> set[str]:
    t = norm_token(token)
    if not has_hebrew(t):
        return {t}
    return {t, HEB_RUN.sub(lambda m: m.group()[::-1], t), t[::-1]}


def tokens_match(a: str, b: str) -> bool:
    return bool(hebrew_variants(a) & hebrew_variants(b))


def _words(s: str) -> list[str]:
    return [w for w in (norm_token(x) for x in WORD_SPLIT.split(s)) if w]


def names_match(a: str, b: str) -> bool:
    """Same words (any order of the words is NOT accepted; each word may be reversed). Empty names
    never match."""
    wa, wb = _words(a), _words(b)
    if not wa or len(wa) != len(wb):
        return False
    fwd = all(tokens_match(x, y) for x, y in zip(wa, wb, strict=True))
    # A fully reversed line reverses the word order as well.
    bwd = all(tokens_match(x, y) for x, y in zip(wa, reversed(wb), strict=True))
    return fwd or bwd
