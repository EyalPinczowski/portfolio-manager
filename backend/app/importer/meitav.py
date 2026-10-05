"""Meitav Trade (מיטב טרייד) mobile "My portfolio" layout, see docs/import-formats.md.

A port of the on-device parser `frontend/lib/ocr/meitav.ts`: the same cards, the same rules, the
same results on the shared fixtures (`frontend/tests/fixtures/meitav/`, see the parity test).

One card per holding: `<EXCHANGE> • <TICKER>` (or `TLV • <6-8 digit security number>`), a name,
the position value (`$` or `₪`), the broker's total P&L %, and on the other side the price and the
day change %. There is no quantity and no cost column, so `quantity = value / price` and
`cost = price / (1 + P&L % / 100)`.

OCR gives these in any order (right-to-left lines in visual order, columns split), so the text is
cut into cards at each anchor and every figure is found anywhere inside its card's block:
value = the `$`/`₪` amount; percentages = tokens with `%`; price = the remaining number that is not
the security number. Nothing is guessed when it is ambiguous: no P&L % means no cost, no value
means no quantity.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.importer.parse import QTY_MAX, ParsedRow, clean_number

STRICT_BULLET = "[•·●∙▪*|]"
LOOSE_BULLET = "[•·●∙▪*|:+–—-]"
TICKER = "[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z]{1,2})?"
TASE_NO = "[0-9]{6,8}"
NUM = r"[0-9][0-9,]*(?:\.[0-9]+)?"
ARROWS = frozenset("↑↓▲▼⬆⬇△▽⇧⇩⬈⬊↗↘")
US_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")

# Section header words (letters only), as printed and as a right-to-left line may come out.
_HEADERS = ("קרןסל", "קרנותסל")
SECTION_HEADERS = frozenset(w for h in _HEADERS for w in (h, h[::-1], "ןרקלס", "תונרקלס"))

_FWD = re.compile(
    rf"(?<![A-Za-z])(NASDAQ|NYSE|AMEX|TLV)\s*({LOOSE_BULLET})?\s*({TICKER}|{TASE_NO})(?![A-Za-z0-9])"
)
# Visual-order (reversed) line: `ACME • NASDAQ`. The bullet is required to avoid false hits.
_REV = re.compile(
    rf"(?<![A-Za-z0-9])({TICKER}|{TASE_NO})\s*({STRICT_BULLET})\s*(NASDAQ|NYSE|AMEX|TLV)(?![A-Za-z])"
)
_STRICT = re.compile(f"^{STRICT_BULLET}$")
_DIGITS = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class Anchor:
    start: int
    end: int
    exchange: str
    ticker: str
    strict: bool


def find_anchors(line: str) -> list[Anchor]:
    out: list[Anchor] = []
    for m in _FWD.finditer(line):
        ticker = m.group(3)
        if (m.group(1) == "TLV") != bool(_DIGITS.match(ticker)):
            continue  # TLV needs a number, US exchanges need a letter ticker
        bullet = m.group(2)
        out.append(
            Anchor(
                m.start(),
                m.end(),
                m.group(1),
                ticker,
                bullet is not None and _STRICT.match(bullet) is not None,
            )
        )
    for m in _REV.finditer(line):
        ticker = m.group(1)
        if (m.group(3) == "TLV") != bool(_DIGITS.match(ticker)):
            continue
        start, end = m.start(), m.end()
        if any(start < a.end and end > a.start for a in out):
            continue
        out.append(Anchor(start, end, m.group(3), ticker, True))
    return sorted(out, key=lambda a: a.start)


def _is_section_line(line: str) -> bool:
    return "".join(ch for ch in line if ch.isalpha()) in SECTION_HEADERS


def _lines(text: str) -> list[str]:
    return re.split(r"\r?\n", text)


def detect_meitav(text: str) -> bool:
    """True when the text looks like a Meitav Trade list: an `exchange • ticker` anchor with a
    bullet, or two anchors, or one anchor next to the `קרן סל` section header."""
    anchors = 0
    strict = 0
    section = False
    for line in _lines(text):
        if _is_section_line(line.strip()):
            section = True
        found = find_anchors(line)
        anchors += len(found)
        if any(a.strict for a in found):
            strict += 1
    return strict >= 1 or anchors >= 2 or (section and anchors >= 1)


@dataclass
class _Block:
    anchor: Anchor
    lines: list[str] = field(default_factory=list)


# OCR often prints a minus as an en/em dash or a Unicode hyphen: a dash glued to a digit or % is a minus.
_MINUS_RE = re.compile(r"(?<!\d)[−–—‐‑](?=[\d%])")


def _to_blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    for raw in _lines(text):
        line = _MINUS_RE.sub("-", raw.replace("−", "-")).strip()
        if not line:
            continue
        anchors = find_anchors(line)
        if not anchors:
            if blocks and not _is_section_line(line):
                blocks[-1].lines.append(line)
            continue
        for i, a in enumerate(anchors):
            start = 0 if i == 0 else a.start
            end = anchors[i + 1].start if i + 1 < len(anchors) else len(line)
            rest = f"{line[start : a.start]} {line[a.end : end]}".strip()
            blocks.append(_Block(a, [rest] if rest else []))
    return blocks


def _to_num(raw: str) -> float:
    return float(raw.replace(",", "").rstrip(".,"))


def _decimals(raw: str) -> int:
    m = re.search(r"\.([0-9]+)$", raw.rstrip(".,"))
    return len(m.group(1)) if m else 0


def _blank(s: str, start: int, end: int) -> str:
    return s[:start] + " " * (end - start) + s[end:]


@dataclass
class _Amt:
    value: float
    raw: str
    symbol: str
    line: int
    pos: int


@dataclass
class _Pct:
    value: float
    line: int
    pos: int
    arrow: bool


@dataclass
class _Num:
    value: float
    raw: str
    line: int
    start: int
    end: int


@dataclass
class _Line:
    work: str
    text: bool
    nums: list[_Num]


_AMOUNT_TRIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"^({NUM})"), "after"),
    (re.compile(rf"(?<![0-9,.])({NUM})$"), "before"),
    (re.compile(rf"^\s{{1,2}}({NUM})"), "after"),
    (re.compile(rf"(?<![0-9,.])({NUM})\s{{1,2}}$"), "before"),
]


def _take_amounts(work: str, line: int) -> tuple[str, list[_Amt]]:
    """`$`/`₪` amount: the number touching the symbol, preferring "symbol then number", no gap."""
    amts: list[_Amt] = []
    for i in range(len(work)):
        sym = work[i]
        if sym not in ("$", "₪"):
            continue
        for rx, side in _AMOUNT_TRIES:
            base = i + 1 if side == "after" else 0
            m = rx.search(work[i + 1 :] if side == "after" else work[:i])
            if m is None:
                continue
            raw = m.group(1)
            idx = base + m.start(1)
            amts.append(_Amt(_to_num(raw), raw, sym, line, idx))
            work = _blank(_blank(work, idx, idx + len(raw)), i, i + 1)
            break
    return work, amts


_PCT = re.compile(rf"(?<![0-9.,])([-+]?)\s?({NUM})\s*%")
_PCT_REV = re.compile(rf"%([-+]?)({NUM})([-+]?)(?![0-9])")


def _take_percents(work: str, original: str, line: int) -> tuple[str, list[_Pct]]:
    pcts: list[_Pct] = []

    def arrow_near(a: int, b: int) -> bool:
        return any(ch in ARROWS for ch in original[max(0, a - 3) : b + 3])

    for m in _PCT.finditer(work):
        v = _to_num(m.group(2))
        pcts.append(
            _Pct(-v if m.group(1) == "-" else v, line, m.start(), arrow_near(m.start(), m.end()))
        )
    w = _PCT.sub(lambda m: " " * len(m.group(0)), work)
    # Reversed (visual-order) form: "%-0.31" or "%0.31-", number glued to the percent sign.
    for m in _PCT_REV.finditer(w):
        v = _to_num(m.group(2))
        neg = m.group(1) == "-" or (m.group(1) == "" and m.group(3) == "-")
        pcts.append(_Pct(-v if neg else v, line, m.start(), arrow_near(m.start(), m.end())))
    w = _PCT_REV.sub(lambda m: " " * len(m.group(0)), w)
    return w, pcts


_NUMBER = re.compile(rf"(?<![\w.,:])({NUM})(?![\w:])")


def _take_numbers(work: str, line: int) -> list[_Num]:
    out: list[_Num] = []
    for m in _NUMBER.finditer(work):
        raw = m.group(1).rstrip(".,")
        if "." not in raw and len(raw.replace(",", "")) >= 7:
            continue  # security-number-like, never a price
        out.append(_Num(_to_num(raw), raw, line, m.start(1), m.end(1)))
    return out


def _pick_pnl(pcts: list[_Pct], value: _Amt | None, price_line: int | None) -> float | None:
    """Which percentage is the broker's total P&L %? None when it cannot be told apart from the
    day change."""
    if not pcts:
        return None
    if value is not None:
        on_value = [p for p in pcts if p.line == value.line]
        if on_value:
            best = on_value[0]
            for p in on_value[1:]:
                best = best if abs(best.pos - value.pos) <= abs(p.pos - value.pos) else p
            return best.value
    arrows = [p for p in pcts if p.arrow]
    if len(arrows) == 1:
        return arrows[0].value
    if len(pcts) == 2:
        zeros = [p for p in pcts if p.value == 0]
        if len(zeros) == 1:
            return next(p for p in pcts if p.value != 0).value
        if value is not None and price_line is not None:
            a, b = pcts
            score_a = abs(a.line - value.line) - abs(a.line - price_line)
            score_b = abs(b.line - value.line) - abs(b.line - price_line)
            if score_a < score_b:
                return a.value
            if score_b < score_a:
                return b.value
    return None


def _whole_quantity(value: _Amt, price: _Num, agorot: bool, slack: float) -> int | None:
    """value / price as a whole number, allowing for the rounding of both figures; None when it
    is not whole."""
    unit_price = price.value / 100 if agorot else price.value
    if not (unit_price > 0) or not (value.value > 0):
        return None
    q = value.value / unit_price
    r = _js_round(q)
    rel = (0.5 * 10.0 ** -_decimals(value.raw)) / value.value + (
        0.5 * 10.0 ** -_decimals(price.raw)
    ) / price.value
    return r if r >= 1 and abs(q - r) <= q * rel * slack + 1e-9 else None


def _js_round(x: float) -> int:
    """`Math.round`: halves go up (Python's `round` goes to even)."""
    return math.floor(x + 0.5)


def _round4(n: float) -> float:
    return _js_round(n * 1e4) / 1e4


_NAME_KEEP = set(".&'’\"-–,/()…")
_NAME_EDGES = re.compile(r"^[\s.…\-–,]+|[\s\-–,]+$")


def _clean_name(w: str) -> str:
    kept = "".join(ch if (ch.isalnum() or ch.isspace() or ch in _NAME_KEEP) else " " for ch in w)
    return re.sub(r"\s+", " ", _NAME_EDGES.sub("", kept))


@dataclass
class _Meta:
    quantity_uncertain: bool = False
    quantity_fractional: bool = False
    cost_inferred: bool = False


def _parse_block(b: _Block, s: Settings) -> ParsedRow:
    tlv = b.anchor.exchange == "TLV"
    lines: list[_Line] = []
    amts: list[_Amt] = []
    pcts: list[_Pct] = []
    for i, orig in enumerate(b.lines):
        work, line_amts = _take_amounts(orig, i)
        work, line_pcts = _take_percents(work, orig, i)
        amts.extend(line_amts)
        pcts.extend(line_pcts)
        letters = sum(1 for ch in re.sub(NUM, " ", work) if ch.isalpha())
        lines.append(_Line(work, letters >= 3, _take_numbers(work, i)))

    value = amts[0] if amts else None
    currency = ("USD" if value.symbol == "$" else "ILS") if value else ("ILS" if tlv else "USD")
    agorot = tlv and currency == "ILS"

    # Price candidates: numbers on figure-only lines first, then numbers at the edge of name lines.
    tier1 = [n for ln in lines if not ln.text for n in ln.nums]
    tier2 = [
        n
        for ln in lines
        if ln.text
        for n in ln.nums
        if ln.work[: n.start].strip() == "" or ln.work[n.end :].strip() == ""
    ]
    cands = tier1 + tier2
    price: _Num | None = None
    if value is not None:
        price = next(
            (c for c in cands if _whole_quantity(value, c, agorot, s.import_infer_round_slack)),
            cands[0] if cands else None,
        )
    pnl = _pick_pnl(pcts, value, price.line if price else None)

    meta = _Meta()
    quantity: float | None = None
    if value is not None and price is not None and value.value >= s.import_infer_min_value:
        whole = _whole_quantity(value, price, agorot, s.import_infer_round_slack)
        if whole is not None:
            quantity = float(whole)
        else:
            unit_price = price.value / 100 if agorot else price.value
            if unit_price > 0:
                quantity = _round4(value.value / unit_price)
                meta.quantity_fractional = True
    quantity = clean_number(quantity, QTY_MAX)
    if quantity is None:
        meta.quantity_fractional = False
        meta.quantity_uncertain = True

    cost: float | None = None
    if price is not None and pnl is not None and pnl > s.import_infer_min_pnl_pct:
        cost = clean_number(_round4(price.value / (1 + pnl / 100)))
        meta.cost_inferred = cost is not None

    # Name: the first line that reads as text (names are truncated and unreliable; the ticker or
    # the security number is the identity).
    name = ""
    for i, ln in enumerate(lines):
        if not ln.text:
            continue
        w = ln.work
        if price is not None and price.line == i:
            w = _blank(w, price.start, price.end)
        name = _clean_name(w)
        if name:
            break
    if not name:
        name = "" if tlv else b.anchor.ticker

    flags: list[str] = []
    if meta.quantity_uncertain:
        flags.append("quantity_uncertain")
    if meta.quantity_fractional:
        flags.append("quantity_fractional")
    if meta.cost_inferred:
        flags.append("cost_inferred")
    unit = "agorot" if agorot else ("USD" if currency == "USD" else "ILS")
    return ParsedRow.model_validate(
        {
            "index": 0,
            "name": name,
            "symbol": None if tlv else b.anchor.ticker,
            "tase_number": b.anchor.ticker if tlv else None,
            "exchange": None if tlv else b.anchor.exchange,
            "quantity": quantity,
            "price": clean_number(price.value) if price else None,
            "value": clean_number(value.value) if value else None,
            "cost": cost,
            "currency": currency,
            "unit": unit,
            "flags": flags,
        }
    )


def parse_meitav_text(text: str, settings: Settings | None = None) -> list[ParsedRow] | None:
    """Parse the OCR text of one Meitav Trade screenshot. None when the text has no card."""
    s = settings or get_settings()
    blocks = _to_blocks(text)
    if not blocks:
        return None
    rows = [_parse_block(b, s) for b in blocks]
    for i, r in enumerate(rows):
        r.index = i
    return rows
