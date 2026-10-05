"""Second Hebrew broker app, "תיק אישי" holdings list (`hebrew_broker_cards`), see docs/import-formats.md.

A port of `frontend/lib/ocr/hebrewCards.ts` (same rules, same results on the shared fixture
`frontend/tests/fixtures/hebrew_broker_cards.json`).

One card per holding: the last price, a coloured daily-change chip (`-0.33%`), a name or symbol and
`כמות N` (quantity). OCR gives them on one line or stacked, in any order, so each `כמות N` line is
an anchor and its card is the few lines around it. Rules: quantity = the number after `כמות`;
price = the nearest decimal number; `כמות` never stays in the name; a number inside a Hebrew name
(`35 מחקה ת"א MTF`) is part of the name. There is no value or cost on this screen (left null).
Currency is guessed from the name (US ticker = USD, Hebrew fund = ILS) and every row is flagged
`currency_changed`, which blocks confirming until the user has checked the currency.
"""

from __future__ import annotations

import re

from app.importer.parse import (
    HEBREW_RE,
    NUMBER_RE,
    QTY_MAX,
    SYMBOL_RE,
    SYMBOL_STOPWORDS,
    ParsedRow,
    clean_number,
    mask_digit_runs,
)
from app.importer.parse import _to_number as to_number

QTY_WORD = "כמות"
_QTY_AFTER = re.compile(rf"{QTY_WORD}\s*:?\s*(\d[\d,]*(?:\.\d+)?)")
_QTY_BEFORE_LINE = re.compile(rf"^\s*(\d[\d,]*(?:\.\d+)?)\s*{QTY_WORD}\s*$")
_CHIP = re.compile(r"[-+]?\(?\d[\d,]*(?:\.\d+)?\)?\s*%")
_DECIMAL = re.compile(r"\d[\d,]*\.\d+")
CARD_LINES = 4  # lines of a stacked card besides the one with `כמות`
NAME_JUNK = re.compile(r"[₪$%|▲▼△▽↑↓()+\-:,.]")
_SKIP_WORDS = ("האחזקות", "נתוני", "מיון", "סוג נייר", "כח קניה", "אחזקות", "פילוח", "יתרות")


def detect_hebrew_cards(text: str) -> bool:
    """`כמות` followed by a number (or a line `N כמות`), the one thing a table header never has."""
    return any(_quantity(line) is not None for line in text.splitlines())


def _quantity(line: str) -> tuple[float | None, str] | None:
    """(quantity, the line without the quantity phrase) when the line has one."""
    m = _QTY_AFTER.search(line)
    if m is None:
        m = _QTY_BEFORE_LINE.match(line)
    if m is None:
        return None
    return to_number(m.group(1)), (line[: m.start()] + " " + line[m.end() :]).strip()


def _tokens(line: str) -> list[str]:
    return [t for t in NUMBER_RE.findall(line) if not t.endswith("%")]


def _decimal_token(line: str) -> str | None:
    line = _CHIP.sub(" ", line)
    for t in _tokens(line):
        if _DECIMAL.fullmatch(t.strip("+-()")):
            return t
    return None


def _is_skipped(line: str) -> bool:
    return any(w in line for w in _SKIP_WORDS)


def _letters(line: str) -> str:
    """The text of a line that is not a number, chip or sign: what could be a name."""
    s = _CHIP.sub(" ", line)
    s = NAME_JUNK.sub(" ", re.sub(r"(?<![\w])[-+]?\d[\d,]*\.\d+(?![\w])", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _card_row(chunk: list[str], at: int) -> ParsedRow | None:
    """One card: `chunk[at]` holds `כמות N`; the other lines are searched nearest first."""
    found = _quantity(chunk[at])
    if found is None:
        return None
    qty, rest = found
    order = [rest] + [
        chunk[i]
        for i in sorted((i for i in range(len(chunk)) if i != at), key=lambda i: abs(i - at))
    ]
    price: float | None = None
    price_token: str | None = None
    price_from = -1
    for k, seg in enumerate(order):
        tok = _decimal_token(seg)
        if tok is not None:
            price, price_token, price_from = to_number(tok), tok, k
            break
    if price is None:  # no decimal anywhere: a whole-number price on a line of its own
        for k, seg in enumerate(order):
            if _letters(seg) == "" and (toks := _tokens(_CHIP.sub(" ", seg))):
                price, price_token, price_from = to_number(toks[-1]), toks[-1], k
                break
    name = ""
    for k, seg in enumerate(order):
        s = seg
        if k == price_from and price_token:
            s = s.replace(price_token, " ", 1)
        s = _letters(s) if k else _name_of_rest(s)
        if s and not _is_skipped(s) and re.search(r"[A-Za-z֐-׿]", s):
            name = s
            break
    if not name and price is None:
        return None
    hebrew = bool(HEBREW_RE.search(name))
    symbol = None
    if not hebrew:
        for sym in SYMBOL_RE.findall(name):
            if sym not in SYMBOL_STOPWORDS and len(sym) <= 5:
                symbol = sym
                break
    usd = symbol is not None
    return ParsedRow(
        name=mask_digit_runs(name),
        symbol=symbol,
        quantity=clean_number(qty, QTY_MAX),
        price=clean_number(price),
        currency="USD" if usd else "ILS",
        unit="USD" if usd else "ILS",
        flags=["currency_changed"],
    )


def _name_of_rest(rest: str) -> str:
    """The name on the `כמות` line: what is left once the chip and the price are removed. A leading
    number stays when letters follow it (it is part of a Hebrew name); decimals and chips go."""
    s = _CHIP.sub(" ", rest)
    s = re.sub(r"(?<![\w])[-+]?\d[\d,]*\.\d+(?![\w])", " ", s)
    s = NAME_JUNK.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # a trailing bare integer with no decimal price present is the price, not the name
    return s


def parse_hebrew_cards_text(text: str) -> list[ParsedRow]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    anchors = [i for i, ln in enumerate(lines) if _quantity(ln) is not None]
    if not anchors:
        return []
    one_line = all(_decimal_token(_quantity(lines[a])[1]) is not None for a in anchors)  # type: ignore[index]
    chunks: list[tuple[list[str], int]] = []
    if one_line:
        chunks = [([lines[a]], 0) for a in anchors]
    else:
        last = anchors[-1]
        after_first = any(_decimal_token(ln) for ln in lines[last + 1 : last + 1 + CARD_LINES])
        for k, a in enumerate(anchors):
            if after_first:  # `כמות N` first, price / chip / name follow
                end = min(
                    anchors[k + 1] if k + 1 < len(anchors) else len(lines), a + 1 + CARD_LINES
                )
                chunks.append((lines[a:end], 0))
            else:  # `כמות N` last, the card is the lines above it
                start = max(anchors[k - 1] + 1 if k else 0, a - CARD_LINES)
                chunks.append((lines[start : a + 1], a - start))
    rows: list[ParsedRow] = []
    for chunk, at in chunks:
        row = _card_row(chunk, at)
        if row is not None:
            row.index = len(rows)
            rows.append(row)
    return rows
