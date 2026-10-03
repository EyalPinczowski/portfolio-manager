"""The one list of verdict words, shared by the outbound gate and the verdict contract test.

`app/outbound.py` scans outgoing text with it (defence in depth: while the launch gate is closed
only `TemplateText` may leave at all) and `tests/verdict_contract.py` builds its field-name and
enum-value checks from the same constants, so the two can never drift apart (a test pins this).

Text is normalised before it is scanned: NFKC (fullwidth `ＢＵＹ`), invisible format characters
(zero-width space/joiner, soft hyphen, bidi marks), a small table of Greek/Cyrillic look-alikes
(`Bυy`), Hebrew niqqud and final letters. Inflections are explicit (`buying`, `selling`, `sold`)
so ordinary nouns stay legal: `holding(s)` is not `hold`.

`bullish` / `bearish` are the one exception: they are verdict words in a sentence of their own
("Bullish outlook") but also the standard name of an indicator event ("MACD bullish cross"), so
they are ignored in a sentence that also names an indicator term (`TECHNICAL_CONTEXT`).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# --- the core list: words that read as a verdict anywhere (config default for the outbound gate) ---
VERDICT_WORDS: tuple[str, ...] = (
    "buy", "sell", "hold", "accumulate", "recommend", "recommendation", "verdict", "upgrade",
    "downgrade", "bullish", "bearish", "rating", "outlook", "trim", "opinion", "stance",
    "conviction", "outperform", "underperform", "overweight", "underweight", "avoid",
)  # fmt: skip

# Words that are verdicts as a field name or an enum value but are ordinary in prose
# ("target 120 not reached", "a long-term view"), so the text scan does not use them.
FIELD_ONLY_TOKENS: tuple[str, ...] = (
    "action", "actions", "advice", "suggestion", "suggestions", "target", "targets", "strong",
    "add", "call", "calls", "signal", "grade", "grades", "side", "decision", "bias", "pick",
    "picks", "opinions", "ratings", "recommended", "recommendations", "buys", "sells", "verdicts",
)  # fmt: skip
VALUE_ONLY_TOKENS: tuple[str, ...] = ("long", "short", "exit")  # enum values: `long`, `exit` ...
# Multi-word phrases (text) and glued names (fields): `go long`, `exit_now`, `top_pick`.
VERDICT_PHRASES: tuple[str, ...] = (
    r"go(?:es|ing)?\s+(?:long|short)",
    r"exit(?:s|ing)?\s+(?:the|your|this|that)\s+position",
    r"(?:add|adding)\s+to\s+(?:the|your|this)?\s*position",
    r"top\s+picks?",
    r"take\s+profits?\s+now",
)
FIELD_PHRASES: tuple[str, ...] = (
    "signal_strength", "signalstrength", "buy_idea", "buyidea", "go_long", "go_short", "exit_now",
    "top_pick", "toppick",
)  # fmt: skip
HEBREW_VERDICT_WORDS: tuple[str, ...] = (
    "קנייה", "קניה", "קנה", "קנו", "לקנות", "קנו", "מכור", "מכירה", "למכור", "מכרו",
    "החזק", "להחזיק", "המלצה", "המלצות", "ממליץ", "ממליצה", "מומלץ", "להמליץ", "שורי", "דובי",
    "שורית", "שוריים", "דובית", "דוביים", "צבירה", "לצבור", "דירוג", "תחזית חיובית", "הערכת יתר", "ביצועי יתר", "ביצועי חסר",
)  # fmt: skip

# Indicator terms that make `bullish` / `bearish` an indicator description, not a verdict.
TECHNICAL_CONTEXT: frozenset[str] = frozenset(
    {
        "macd", "rsi", "ema", "sma", "moving average", "crossover", "cross", "crossed",
        "divergence", "engulfing", "candle", "candlestick", "stochastic", "bollinger", "signal line",
        "histogram", "pattern", "breakout", "support", "resistance", "trend line", "trendline",
        "hammer", "harami", "doji", "flag", "pennant", "wedge", "adx", "momentum",
    }
)  # fmt: skip
CONTEXT_EXEMPT = frozenset({"bullish", "bearish"})

_IRREGULAR: dict[str, tuple[str, ...]] = {
    "buy": ("buy", "buys", "buying", "bought"),
    "sell": ("sell", "sells", "selling", "sold"),
    "hold": ("hold", "holds"),  # `holding(s)` is the portfolio noun, never a verdict
    "rating": ("rating", "ratings", "rated"),
    "outlook": ("outlook", "outlooks"),
    "opinion": ("opinion", "opinions"),
    "stance": ("stance", "stances"),
    "conviction": ("conviction", "convictions"),
    "verdict": ("verdict", "verdicts"),
    "recommendation": ("recommendation", "recommendations"),
    "bullish": ("bullish",),
    "bearish": ("bearish",),
    "overweight": ("overweight",),
    "underweight": ("underweight",),
    "avoid": ("avoid", "avoids", "avoided", "avoiding"),
}

_CONFUSABLES = str.maketrans(
    {
        # Greek
        "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
        "υ": "u", "χ": "x", "γ": "y",
        # Cyrillic
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c",
        "т": "t", "у": "y", "х": "x", "і": "i", "ѕ": "s", "ј": "j", "ӏ": "l",
        # Hebrew final letters -> medial
        "ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ",
    }
)  # fmt: skip
_CONFUSABLES.update(
    {
        ord(k.upper()): v.upper()
        for k, v in ((chr(c), t) for c, t in list(_CONFUSABLES.items()))
        if k.upper() != k and v.isascii()
    }
)
_HEBREW_MARKS = re.compile("[֑-ׇ]")  # cantillation and niqqud


def _regular_forms(word: str) -> tuple[str, ...]:
    forms = {word, word + "s", word + "es", word + "ed", word + "ing", word + "er", word + "ers"}
    if word.endswith("e"):
        forms |= {word + "d", word[:-1] + "ing", word + "r", word + "rs"}
    if len(word) > 2 and word[-1] not in "aeiouy" and word[-2] in "aeiou":
        forms |= {word + word[-1] + "ed", word + word[-1] + "ing"}  # trim -> trimming
    if word.endswith("y"):
        forms |= {word[:-1] + "ies", word[:-1] + "ied"}
    return tuple(sorted(forms))


def forms_of(word: str) -> tuple[str, ...]:
    """All the inflections of a verdict word that count (`buy` -> buying, bought ...)."""
    return _IRREGULAR.get(word.lower()) or _regular_forms(word.lower())


def normalize_text(text: str, *, lower: bool = True) -> str:
    """NFKC, invisible characters removed, look-alike letters folded, lower case (optional)."""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Cf", "Cc") or ch in "\n\t")
    t = _HEBREW_MARKS.sub("", t)
    t = (t.lower() if lower else t).translate(_CONFUSABLES)
    return t.replace("’", "'")


def _alternation(words: tuple[str, ...]) -> str:
    return "|".join(
        sorted({re.escape(f) for w in words for f in forms_of(w)}, key=len, reverse=True)
    )


@lru_cache(maxsize=16)
def _word_regex(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"(?<![^\W_])(?:{_alternation(words)})(?![^\W_])", re.IGNORECASE)


@lru_cache(maxsize=1)
def _phrase_regex() -> re.Pattern[str]:
    return re.compile(rf"(?<![^\W_])(?:{'|'.join(VERDICT_PHRASES)})(?![^\W_])", re.IGNORECASE)


@lru_cache(maxsize=4)
def _hebrew_regex(words: tuple[str, ...]) -> re.Pattern[str]:
    stems = "|".join(
        sorted(
            {re.escape(normalize_text(w)) for w in words},
            key=len,
            reverse=True,
        )
    )
    # one-letter prefixes: ו ה ב ל מ ש כ (and, the, in, to, from, that, as), up to three of them
    return re.compile(rf"(?<![א-ת])[והבלמשכ]{{0,3}}(?:{stems})(?![א-ת])")


def _technical(sentence: str) -> bool:
    return any(
        re.search(rf"(?<![^\W_]){re.escape(t)}(?![^\W_])", sentence) for t in TECHNICAL_CONTEXT
    )


def verdict_words_in_text(
    text: str,
    words: tuple[str, ...] | list[str] = VERDICT_WORDS,
    *,
    ignore: tuple[str, ...] = (),
) -> list[str]:
    """The verdict words found in `text` (root forms, in the order of `words`).

    `ignore` strings (the disclaimer) are removed first. English words use `forms_of`; phrases and
    Hebrew words are always checked. A phrase hit is reported as the phrase text.
    """
    cleaned = text
    for chunk in ignore:
        cleaned = cleaned.replace(chunk, " ")
    norm = normalize_text(cleaned)
    word_tuple = tuple(w.lower() for w in words)
    found: list[str] = []
    sentences = [s for s in re.split(r"[.;!?\n]+", norm) if s.strip()] or [norm]
    for w in word_tuple:
        rx = _word_regex((w,))
        for sentence in sentences:
            if rx.search(sentence) and not (w in CONTEXT_EXEMPT and _technical(sentence)):
                found.append(w)
                break
    for m in _phrase_regex().finditer(norm):
        found.append(m.group(0))
    for m in _hebrew_regex(HEBREW_VERDICT_WORDS).finditer(norm):
        found.append(m.group(0))
    return list(dict.fromkeys(found))
