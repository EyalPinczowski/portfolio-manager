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
_HEADERS = ("קרןסל", "קרנותסל", "אחר", "מניות", "תעודותסל", "קרנותנאמנות")
SECTION_HEADERS = frozenset(w for h in _HEADERS for w in (h, h[::-1], "ןרקלס", "תונרקלס"))
# Bottom navigation words (Hebrew app tab bar), as printed and reversed.
_NAV = ("הוראות", "ניירות", "במעקב", "מסחר", "התיק", "שלי", "ראשי", "בית", "תיק", "שוק", "עוד")
NAV_TOKENS = frozenset(w for n in _NAV for w in (n, n[::-1]))

# The label `מספר ני"ע` (security number) next to a TASE number, and its reversed spelling.
_SEP = r"[\s•·●∙▪*|:.\-–—]"
_Q = "[\"'״׳”“]"
_LABEL = rf"(?:מספר\s*ני{_Q}{{0,2}}ע|ע{_Q}{{0,2}}ינ\s*רפסמ)"
_LABEL_RE = re.compile(_LABEL)
_LABEL_NUM = re.compile(
    rf"{_LABEL}{_SEP}{{0,3}}([0-9]{{4,8}})(?![0-9])|(?<![0-9])([0-9]{{4,8}}){_SEP}{{0,3}}{_LABEL}"
)
_AMOUNT_PRESENT = re.compile(r"[$₪]\s{0,2}[0-9]|[0-9]\s{0,2}[$₪]")

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
    labeled: bool = False  # the line carries the `מספר ני"ע` label: the name is ABOVE the anchor


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
    has_amount: bool = False


@dataclass
class _Entry:
    text: str
    kind: str  # "line", "section" (grey bar) or "nav" (bottom navigation)
    anchors: list[Anchor]
    labeled: bool = False


# OCR often prints a minus as an en/em dash or a Unicode hyphen: a dash glued to a digit or % is a minus.
_MINUS_RE = re.compile(r"(?<!\d)[−–—‐‑](?=[\d%])")
_LETTERS = re.compile(r"[^\W\d_]+")
_SIMPLE = Anchor(0, 0, "", "", False)


def _has_amount(s: str) -> bool:
    return _AMOUNT_PRESENT.search(s) is not None


def _is_text_line(s: str) -> bool:
    return sum(1 for ch in re.sub(NUM, " ", s) if ch.isalpha()) >= 3


def _is_price_line(s: str) -> bool:
    """A figure-only line that can be a price: no letters, no amount, no percent."""
    return (
        not _is_text_line(s)
        and not _has_amount(s)
        and "%" not in s
        and any(ch.isdigit() for ch in s)
    )


def _is_percent_line(s: str) -> bool:
    return "%" in s and not _is_text_line(s) and not _has_amount(s)


def _is_nav_line(line: str) -> bool:
    if any(ch.isdigit() or ch in "$₪%" for ch in line):
        return False
    words = _LETTERS.findall(line)
    return bool(words) and all(w in NAV_TOKENS for w in words)


def _prepare(text: str) -> list[_Entry]:
    out: list[_Entry] = []
    for raw in _lines(text):
        line = _MINUS_RE.sub("-", raw.replace("−", "-")).strip()
        if not line:
            continue
        if _is_section_line(line):
            out.append(_Entry(line, "section", []))
            continue
        if _is_nav_line(line):
            out.append(_Entry(line, "nav", []))
            continue
        anchors = find_anchors(line)
        labeled = False
        if _LABEL_RE.search(line):
            m = _LABEL_NUM.search(line)
            if anchors:
                line = _LABEL_RE.sub(lambda mm: " " * len(mm.group(0)), line)
                labeled = len(anchors) == 1
                anchors = [
                    Anchor(a.start, a.end, a.exchange, a.ticker, a.strict, labeled) for a in anchors
                ]
            elif m is not None:
                num = m.group(1) or m.group(2)
                anchors = [Anchor(m.start(), m.end(), "", num, True, True)]
                labeled = True
            else:
                line = _LABEL_RE.sub(lambda mm: " " * len(mm.group(0)), line)
        out.append(_Entry(line, "line", anchors, labeled))
    return out


def _pre_range(entries: list[_Entry], i: int, lo: int) -> tuple[int, int] | None:
    """Lines ABOVE a labeled anchor (`TLV • 1180422 מספר ני"ע`) that belong to its card: the
    nearest name line, the figures between it and the anchor, and up to two price lines above the
    name. Never across an amount line, a section bar, the navigation or another anchor."""
    k = i - 1
    while k >= lo and i - k <= 4:
        s = entries[k].text
        if _has_amount(s):
            return None
        if _is_text_line(s):
            start = k
            while start - 1 >= lo and k - start < 2 and _is_price_line(entries[start - 1].text):
                start -= 1
            return start, i
        k -= 1
    return None


def _simple_cards(run: list[str]) -> list[list[str]]:
    """Cards with no exchange line and no security number: a name, a `$`/`₪` value and a price.
    `run` is a stretch of lines that belong to no anchored card. A card starts at a name line
    (text, no amount) once the card before it has its amount; up to two price lines just above the
    name come with it. Stretches without an amount (status bar, header) give no card."""
    groups: list[list[str]] = [[]]  # groups[0] holds the lines before the first name
    for s in run:
        last = groups[-1]
        starts = (
            _is_text_line(s)
            and not _has_amount(s)
            and (len(groups) == 1 or any(_has_amount(x) for x in last))
        )
        if starts:
            carry: list[str] = []
            while last and len(carry) < 2 and _is_price_line(last[-1]):
                carry.insert(0, last.pop())
            groups.append([*carry, s])
        else:
            last.append(s)
    return [g for g in groups[1:] if any(_has_amount(x) for x in g)]


def _to_blocks(text: str) -> list[_Block]:
    entries = _prepare(text)
    # Pass 1: the lines above each labeled anchor belong to that anchor's card.
    claimed: dict[int, int] = {}
    pre: dict[int, list[str]] = {}
    lo = 0
    for i, e in enumerate(entries):
        if e.kind != "line" or e.anchors:
            if e.kind == "line" and e.labeled and (rng := _pre_range(entries, i, lo)):
                pre[i] = [entries[k].text for k in range(*rng)]
                claimed.update({k: i for k in range(*rng)})
            lo = i + 1
    # Pass 2: walk the lines, a line goes to the open card above it or waits in `orphans`.
    blocks: list[_Block] = []
    cur: _Block | None = None
    orphans: list[str] = []

    def flush() -> None:
        nonlocal orphans
        blocks.extend(_Block(_SIMPLE, g, True) for g in _simple_cards(orphans))
        orphans = []

    for i, e in enumerate(entries):
        if e.kind != "line":
            flush()
            cur = None
            continue
        if i in claimed:
            continue
        if not e.anchors:
            closed = cur is not None and cur.anchor.labeled and cur.has_amount
            # After its amount a labeled card still takes a stray percent line (the P&L %).
            if cur is not None and (not closed or _is_percent_line(e.text)):
                cur.lines.append(e.text)
                cur.has_amount = cur.has_amount or _has_amount(e.text)
            else:
                orphans.append(e.text)
            continue
        flush()
        for j, a in enumerate(e.anchors):
            start = 0 if j == 0 else a.start
            end = e.anchors[j + 1].start if j + 1 < len(e.anchors) else len(e.text)
            rest = f"{e.text[start : a.start]} {e.text[a.end : end]}".strip()
            lines = list(pre.get(i, [])) if j == 0 else []
            if rest:
                lines.append(rest)
            cur = _Block(a, lines, any(_has_amount(x) for x in lines))
            blocks.append(cur)
    flush()
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
    has_symbol = b.anchor.exchange in US_EXCHANGES
    # No exchange line: a simple value card or a security-number card (currency, fund).
    simple = not has_symbol and not tlv
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
    currency = (
        ("USD" if value.symbol == "$" else "ILS") if value else ("USD" if has_symbol else "ILS")
    )
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
    # A figure-only line wins: a number glued to a name (`חיסכון ירוק 41`) is part of the name.
    cands = tier1 or tier2
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
        elif not simple:
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
        name = b.anchor.ticker if has_symbol else ""

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
            "symbol": b.anchor.ticker if has_symbol else None,
            "tase_number": b.anchor.ticker
            if (tlv or b.anchor.labeled) and b.anchor.ticker
            else None,
            "exchange": b.anchor.exchange if has_symbol else None,
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
