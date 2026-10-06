"""Third broker layout, the IBI app's "תיק ההשקעות שלי" cards (`ibi_cards`), see docs/import-formats.md.

A port of `frontend/lib/ocr/ibi.ts` (same rules, same results on the shared fixture
`frontend/tests/fixtures/ibi_cards.json`).

One card per holding. Right side: a ticker (`ACME`) or a Hebrew name (which may contain digits,
`תכ.תא35`) and `N יחידות` (units, may be fractional). Left side: the last price, a coloured
day-change chip (`-0.31%`) and the day's profit or loss with a currency sign (`-$0.89`, `+₪6.40`).
No value, cost or currency column. OCR gives a card on one line or stacked in any order, so each
`N יחידות` line (or `יחידות N`, when OCR reverses it) is an anchor and its card is the few lines
around it.

Rules: quantity = the number next to `יחידות`; price = the nearest decimal that is not an amount;
`$` on the day amount = USD; `₪` = ILS, and unit `agorot` when the amount fits
`quantity x price/100 x chip` better than `quantity x price x chip`. A leading number in a Hebrew
name is part of the name; `יחידות` never stays in it. Value and cost stay null, and every row is
flagged `currency_changed`, so the user must confirm the currency and unit before saving.
"""

from __future__ import annotations

import re

from app.importer.parse import (
    HEBREW_RE,
    QTY_MAX,
    SYMBOL_RE,
    SYMBOL_STOPWORDS,
    ParsedRow,
    clean_number,
    mask_digit_runs,
)

UNITS_WORD = "יחידות"
_NUM = r"\d[\d,]*(?:\.\d+)?"
_UNITS_BEFORE = re.compile(rf"(?<![\w.,])({_NUM})\s*{UNITS_WORD}")
_UNITS_AFTER = re.compile(rf"{UNITS_WORD}\s*:?\s*({_NUM})(?![\w])")
_CHIP = re.compile(rf"([-+−])?\(?({_NUM})\)?\s*%")
_AMOUNT = re.compile(rf"(?<![\d.,])[-+−]?\s?[$₪]\s?[-+−]?{_NUM}[-+−]?|(?<![\d.,]){_NUM}[$₪]")
_DECIMAL = re.compile(r"(?<![\w.,])\d[\d,]*\.\d+(?![\w])")
_INTEGER = re.compile(r"(?<![\w.,])\d[\d,]*(?![\w.,])")
NAME_JUNK = re.compile(r"[₪$%|▲▼△▽↑↓()+\-−:,]")
_LETTER = re.compile(r"[A-Za-z֐-׿]")
CARD_LINES = 5  # lines of a stacked card besides the one with `יחידות`
_SKIP_WORDS = (
    "תיק",
    "מחיר",
    "רווח",
    "הפסד",
    "טרייד",
    "מרכז ידע",
    "הוראות",
    "שוק",
    "מיון",
    "נתוני",
)
_ILS_CHARS = "₪"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _units(line: str) -> tuple[float, str] | None:
    """(quantity, the line without the units phrase) when the line has one."""
    m = _UNITS_BEFORE.search(line) or _UNITS_AFTER.search(line)
    if m is None:
        return None
    return _num(m.group(1)), (line[: m.start()] + " " + line[m.end() :]).strip()


def detect_ibi(text: str) -> bool:
    """A number next to `יחידות`, the one thing no other layout has."""
    return any(_units(line) is not None for line in text.splitlines())


def _bare(line: str) -> str:
    """The line without chips and signed amounts."""
    return _AMOUNT.sub(" ", _CHIP.sub(" ", line))


def _decimal(line: str) -> str | None:
    m = _DECIMAL.search(_bare(line))
    return m.group(0) if m else None


def _clean(line: str) -> str:
    """What could be a name: no chip, amount or decimal number; a leading integer stays."""
    s = _DECIMAL.sub(" ", _bare(line))
    s = NAME_JUNK.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def _name_ok(s: str) -> bool:
    return bool(s) and _LETTER.search(s) is not None and not any(w in s for w in _SKIP_WORDS)


def _amount_of(line: str) -> tuple[float, str] | None:
    """(signed amount, currency sign) of the first amount with a sign in the line."""
    m = _AMOUNT.search(_CHIP.sub(" ", line))
    if m is None:
        return None
    text = m.group(0)
    value = _num(re.search(_NUM, text).group(0))  # type: ignore[union-attr]
    return (-value if re.search(r"[-−]", text) else value), ("$" if "$" in text else "₪")


def _chip_of(line: str) -> float | None:
    m = _CHIP.search(line)
    if m is None:
        return None
    value = _num(m.group(2))
    return -value if m.group(1) in ("-", "−") else value


def _agorot(qty: float, price: float, amount: float, chip: float) -> bool:
    """True when the day amount fits price in agorot (price/100) better than price in ILS."""
    if chip == 0 or amount == 0:
        return False
    day, pct = abs(amount), abs(chip) / 100
    err_ils = abs(qty * price * pct - day)
    err_agorot = abs(qty * price / 100 * pct - day)
    return err_agorot < err_ils


def _card_row(chunk: list[str], at: int) -> ParsedRow | None:
    found = _units(chunk[at])
    if found is None:
        return None
    qty, rest = found
    lines = list(chunk)
    lines[at] = rest
    order = [at, *sorted((i for i in range(len(lines)) if i != at), key=lambda i: abs(i - at))]
    price: float | None = None
    price_token: str | None = None
    price_line = -1
    for i in order:
        tok = _decimal(lines[i])
        if tok is not None:
            price, price_token, price_line = _num(tok), tok, i
            break
    if price is None:  # no decimal anywhere: a whole-number price on a line of its own
        for i in order:
            if _LETTER.search(_clean(lines[i])) is None:
                ints = _INTEGER.findall(_bare(lines[i]))
                if ints:
                    price, price_token, price_line = _num(ints[-1]), ints[-1], i
                    break
    amount = next((a for i in order if (a := _amount_of(lines[i])) is not None), None)
    chip = next((c for i in order if (c := _chip_of(lines[i])) is not None), None)

    texts = [
        _clean(ln.replace(price_token, " ", 1) if i == price_line and price_token else ln)
        for i, ln in enumerate(lines)
    ]
    at_name = next((i for i in order if _name_ok(texts[i])), None)
    name = ""
    if at_name is not None:
        lo = hi = at_name
        if at_name != at:  # a name split over several lines
            while lo - 1 >= 0 and lo - 1 != at and _name_ok(texts[lo - 1]):
                lo -= 1
            while hi + 1 < len(texts) and hi + 1 != at and _name_ok(texts[hi + 1]):
                hi += 1
        name = " ".join(texts[lo : hi + 1])
    if not name and price is None:
        return None
    symbol = None
    if not HEBREW_RE.search(name):
        for sym in SYMBOL_RE.findall(name):
            if sym not in SYMBOL_STOPWORDS and len(sym) <= 5:
                symbol = sym
                break
    if amount is not None and amount[1] == "$":
        currency = unit = "USD"
    elif amount is not None:
        currency = "ILS"
        agorot = price is not None and chip is not None and _agorot(qty, price, amount[0], chip)
        unit = "agorot" if agorot else "ILS"
    else:  # no amount on the card: guess from the name
        currency = unit = "USD" if symbol is not None else "ILS"
    return ParsedRow(
        name=mask_digit_runs(name),
        symbol=symbol,
        quantity=clean_number(qty, QTY_MAX),
        price=clean_number(price),
        currency=currency,
        unit=unit,
        flags=["currency_changed"],
    )


def parse_ibi_text(text: str) -> list[ParsedRow]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    anchors = [i for i, ln in enumerate(lines) if _units(ln) is not None]
    if not anchors:
        return []
    one_line = all(_decimal(_units(lines[a])[1]) is not None for a in anchors)  # type: ignore[index]
    chunks: list[tuple[list[str], int]] = []
    if one_line:
        chunks = [([lines[a]], 0) for a in anchors]
    else:
        last = anchors[-1]
        after_first = any(_decimal(ln) for ln in lines[last + 1 : last + 1 + CARD_LINES])
        for k, a in enumerate(anchors):
            if after_first:  # `N יחידות` first, name / price / chip / amount follow
                nxt = anchors[k + 1] if k + 1 < len(anchors) else len(lines)
                chunks.append((lines[a : min(nxt, a + 1 + CARD_LINES)], 0))
            else:  # `N יחידות` last, the card is the lines above it
                start = max(anchors[k - 1] + 1 if k else 0, a - CARD_LINES)
                chunks.append((lines[start : a + 1], a - start))
    rows: list[ParsedRow] = []
    for chunk, at in chunks:
        row = _card_row(chunk, at)
        if row is not None:
            row.index = len(rows)
            rows.append(row)
    return rows
