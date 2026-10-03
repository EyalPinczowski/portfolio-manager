"""Parse OCR output into holding rows and validate quantity x price ~ value."""

from __future__ import annotations

import itertools
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.providers.base import OcrResult, OcrRow

Unit = Literal["ILS", "agorot", "USD"]

NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\(?\d[\d,]*(?:\.\d+)?\)?%?")
SYMBOL_RE = re.compile(r"(?<![\w.$])[A-Z]{1,5}(?:[.-][A-Z]{1,3})?(?![\w])")
HEBREW_RE = re.compile(r"[֐-׿]")
AGOROT_MARKERS = ("אגורות", "אגורה", "אג'", "אג׳", "agorot", "agora")
ILS_MARKERS = ("₪", 'ש"ח', "שח", "nis", "ils")
HEADER_WORDS = (
    "שם נייר",
    "שם הנייר",
    "כמות",
    "שער",
    "שווי",
    "עלות",
    'סה"כ',
    "סהכ",
    "מספר נייר",
    "quantity",
    "price",
    "value",
    "symbol",
    "total",
    "holdings",
    "portfolio",
    "name",
)
SYMBOL_STOPWORDS = {
    "USD",
    "ILS",
    "NIS",
    "ETF",
    "ILA",
    "LTD",
    "INC",
    "CORP",
    "PLC",
    "CO",
    "THE",
    "NYSE",
}


class ParsedRow(BaseModel):
    index: int = 0
    name: str = ""
    symbol: str | None = None
    tase_number: str | None = None
    quantity: float | None = None
    price: float | None = None  # as displayed (agorot when unit == "agorot")
    value: float | None = None  # total market value as displayed
    cost: float | None = None  # average cost per unit as displayed (same unit as price)
    currency: str = "ILS"
    unit: Unit = "ILS"
    matched_name: str | None = None
    flags: list[str] = Field(default_factory=list)


def price_native(row: ParsedRow) -> float | None:
    """Price in the row's real currency (agorot -> ILS)."""
    if row.price is None:
        return None
    return row.price / 100.0 if row.unit == "agorot" else row.price


def cost_native(row: ParsedRow) -> float | None:
    if row.cost is None:
        return None
    return row.cost / 100.0 if row.unit == "agorot" else row.cost


def _to_number(token: str) -> float | None:
    t = token.strip().rstrip("%")
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("+-()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _is_id_like(token: str) -> bool:
    t = token.strip()
    return t.isdigit() and 6 <= len(t) <= 8


def _find_triple(
    nums: list[float], unit: Unit, tol: float, ordered_only: bool = False
) -> tuple[int, int, int] | None:
    """Indices (quantity, price, value) with quantity x price ~ value.

    `ordered_only` restricts the search to the printed left-to-right order (index ascending),
    which is tried first because it is by far the most common layout.
    """
    limit = min(len(nums), 6)
    candidates = (
        itertools.combinations(range(limit), 3)
        if ordered_only
        else itertools.permutations(range(limit), 3)
    )
    for q, p, v in candidates:
        if nums[v] <= 0 or nums[q] <= 0 or nums[p] <= 0:
            continue
        price = nums[p] / 100.0 if unit == "agorot" else nums[p]
        if abs(nums[q] * price - nums[v]) / nums[v] <= tol:
            return q, p, v
    return None


def parse_line(line: str, agorot_global: bool, settings: Settings) -> ParsedRow | None:
    low = line.lower()
    if any(h in low for h in HEADER_WORDS) and len(NUMBER_RE.findall(line)) < 2:
        return None
    tokens = [t for t in NUMBER_RE.findall(line) if not t.endswith("%")]
    if len(tokens) < 2:
        return None
    letters = re.sub(NUMBER_RE, " ", line)
    if not re.search(r"[A-Za-z֐-׿]", letters):
        return None
    agorot = agorot_global or any(m in low for m in AGOROT_MARKERS)
    if "$" in line or "usd" in low:
        currency = "USD"
    elif agorot or any(m in low for m in ILS_MARKERS) or HEBREW_RE.search(letters):
        currency = "ILS"
    else:
        currency = "USD"
    unit: Unit = (
        "agorot" if (agorot and currency == "ILS") else ("USD" if currency == "USD" else "ILS")
    )

    values = [(_to_number(t), t) for t in tokens]
    nums = [v for v, _ in values if v is not None]
    raw_tokens = [t for v, t in values if v is not None]
    row = ParsedRow(currency=currency, unit=unit)
    tol = settings.import_value_tolerance
    alt: Unit = "ILS" if unit == "agorot" else "agorot"
    # Unit candidates: the detected unit first; for shekel rows the page header may say agorot while
    # this row is in shekels (or the reverse), so try the other unit too.
    units: list[Unit] = [unit] + ([alt] if currency == "ILS" else [])
    triple: tuple[int, int, int] | None = None
    for ordered in (True, False):
        for u in units:
            triple = _find_triple(nums, u, tol, ordered_only=ordered)
            if triple is not None:
                unit = u
                row.unit = u
                break
        if triple is not None:
            break
    used: set[int] = set()
    if triple:
        q, p, v = triple
        row.quantity, row.price, row.value = nums[q], nums[p], nums[v]
        used = {q, p, v}
    else:
        flags_order = nums[:3]
        if len(flags_order) == 3:
            row.quantity, row.price, row.value = flags_order
            used = {0, 1, 2}
        elif len(flags_order) == 2:
            row.quantity, row.value = flags_order
            used = {0, 1}
    leftovers = [i for i in range(len(nums)) if i not in used]
    for i in leftovers:
        if _is_id_like(raw_tokens[i]) and row.tase_number is None:
            row.tase_number = raw_tokens[i]
        elif row.cost is None and nums[i] > 0:
            row.cost = nums[i]
    clean = re.sub(NUMBER_RE, " ", line)
    clean = re.sub(r"[₪$%|]", " ", clean)
    for marker in AGOROT_MARKERS:
        clean = re.sub(re.escape(marker), " ", clean, flags=re.IGNORECASE)
    for sym in SYMBOL_RE.findall(clean):
        if sym not in SYMBOL_STOPWORDS and row.symbol is None and (len(sym) <= 5):
            row.symbol = sym
            break
    name = re.sub(r"\s+", " ", clean).strip(" -:,.")
    row.name = name
    return row


def parse_ocr_text(text: str, settings: Settings | None = None) -> list[ParsedRow]:
    s = settings or get_settings()
    lowered = text.lower()
    agorot_global = any(m in lowered for m in AGOROT_MARKERS)
    rows: list[ParsedRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = parse_line(line, agorot_global, s)
        if row is not None:
            row.index = len(rows)
            rows.append(row)
    return rows


def _from_ocr_row(r: OcrRow, index: int) -> ParsedRow:
    unit_raw = (r.unit or "").strip()
    cur = (r.currency or "").upper()
    if unit_raw.lower() in ("agorot", "agora", "ila"):
        unit: Unit = "agorot"
        cur = "ILS"
    elif unit_raw.upper() == "USD" or cur == "USD":
        unit, cur = "USD", "USD"
    else:
        unit, cur = "ILS", cur or "ILS"
    return ParsedRow(
        index=index,
        name=r.name,
        symbol=(r.symbol or None),
        tase_number=r.tase_number,
        quantity=r.quantity,
        price=r.price,
        value=r.value,
        cost=r.cost,
        currency=cur,
        unit=unit,
    )


def parse_ocr_result(result: OcrResult, settings: Settings | None = None) -> list[ParsedRow]:
    if result.rows is not None:
        return [_from_ocr_row(r, i) for i, r in enumerate(result.rows)]
    return parse_ocr_text(result.text or "", settings)


def validate_row(row: ParsedRow, settings: Settings | None = None) -> ParsedRow:
    """Flag incomplete rows and rows where quantity x price differs from value by > tolerance."""
    s = settings or get_settings()
    flags = [f for f in row.flags if f not in ("missing_fields", "value_mismatch")]
    if row.quantity is None or row.price is None or row.value is None:
        flags.append("missing_fields")
    else:
        price = price_native(row) or 0.0
        expected = row.quantity * price
        if row.value == 0 or abs(expected - row.value) / abs(row.value) > s.import_value_tolerance:
            flags.append("value_mismatch")
    row.flags = flags
    return row
