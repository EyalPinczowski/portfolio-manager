"""Parse OCR output into holding rows and validate quantity x price ~ value."""

from __future__ import annotations

import itertools
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.config import Settings, get_settings
from app.providers.base import OcrResult, OcrRow

Unit = Literal["ILS", "agorot", "USD"]
Currency = Literal["ILS", "USD"]

SYMBOL_PATTERN = r"^[A-Z0-9.^=\-]{1,20}$"
TASE_NUMBER_PATTERN = r"^\d{5,9}$"
NAME_MAX_CHARS = 200
QTY_MAX = 1e12  # sanity ceilings: one malformed row must not break the dashboard
MONEY_MAX = 1e15
DIGIT_RUN_RE = re.compile(r"\d{6,}")  # an account / ID number, never part of a security name


def norm_symbol(v: Any) -> Any:
    """Trim and upper-case; an empty string means "no symbol"."""
    if isinstance(v, str):
        v = v.strip().upper()
        return v or None
    return v


def mask_digit_runs(v: Any) -> Any:
    """Names are a few words: cap them and mask runs of 6+ digits (account numbers) server-side."""
    if isinstance(v, str):
        return DIGIT_RUN_RE.sub("***", v[:NAME_MAX_CHARS])[:NAME_MAX_CHARS]
    return v


RowSymbol = Annotated[
    Annotated[str, Field(pattern=SYMBOL_PATTERN)] | None, BeforeValidator(norm_symbol)
]
RowQuantity = Annotated[float, Field(ge=0, le=QTY_MAX, allow_inf_nan=False)]
RowMoney = Annotated[float, Field(ge=0, le=MONEY_MAX, allow_inf_nan=False)]

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


class MatchCandidate(BaseModel):
    """A possible security for a row whose name match was too weak to accept automatically."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(max_length=20)
    name: str = Field(max_length=NAME_MAX_CHARS)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)  # 0-100 name similarity


# "MANUAL": no broker exchange, the user typed the ticker of a US-dollar row themselves.
Exchange = Literal["NASDAQ", "NYSE", "AMEX", "MANUAL"]


class RowConflict(BaseModel):
    """The numbers of the copy that lost when the same card is in two screenshots with different
    values (the later screenshot wins). Reported so the user can see what was dropped."""

    model_config = ConfigDict(extra="forbid")

    price: RowMoney | None = None
    value: RowMoney | None = None
    quantity: RowQuantity | None = None


class ParsedRow(BaseModel):
    """One broker row. Bounded and strict: it is also the request schema of the review screen.

    `unit` is the single source of truth for the money fields: `agorot` and `ILS` mean the
    currency is ILS, `USD` means USD (`row_currency`). `currency` must agree (validated).
    """

    index: int = Field(default=0, ge=0, le=10_000)
    name: Annotated[str, BeforeValidator(mask_digit_runs)] = Field(default="", max_length=200)
    symbol: RowSymbol = None
    tase_number: str | None = Field(default=None, pattern=TASE_NUMBER_PATTERN)
    quantity: RowQuantity | None = None
    price: RowMoney | None = None  # as displayed (agorot when unit == "agorot")
    value: RowMoney | None = None  # total market value as displayed
    cost: RowMoney | None = None  # average cost per unit as displayed (same unit as price)
    currency: Currency = "ILS"
    unit: Unit = "ILS"
    matched_name: str | None = Field(default=None, max_length=NAME_MAX_CHARS)
    # Weak name matches are never picked silently: `symbol` stays None, `flags` contains
    # "low_confidence_match" and the best guesses are listed here (best first) for the user.
    candidates: list[MatchCandidate] = Field(default_factory=list, max_length=20)
    flags: list[str] = Field(default_factory=list, max_length=12)
    # The listing exchange when the broker prints `NASDAQ • TICKER`. With it, a ticker that is not
    # in the seed is accepted as a user-scoped, unverified security (see `importer/match.py`).
    exchange: Exchange | None = None
    # Set with the `conflict` flag: what the other (earlier) screenshot said.
    conflict: RowConflict | None = None

    @model_validator(mode="after")
    def _unit_agrees_with_currency(self) -> ParsedRow:
        if row_currency(self) != self.currency:
            raise ValueError(f"unit {self.unit} does not agree with currency {self.currency}")
        return self


def row_currency(row: ParsedRow) -> Currency:
    """The currency of the row's money fields, from `unit` (agorot is ILS). Used by matching,
    the diff and confirm alike, so they can never disagree about what is stored."""
    return "USD" if row.unit == "USD" else "ILS"


def price_native(row: ParsedRow) -> float | None:
    """Price in the row's real currency (agorot -> ILS)."""
    if row.price is None:
        return None
    return row.price / 100.0 if row.unit == "agorot" else row.price


def cost_native(row: ParsedRow) -> float | None:
    if row.cost is None:
        return None
    return row.cost / 100.0 if row.unit == "agorot" else row.cost


def clean_number(x: float | None, ceiling: float = MONEY_MAX) -> float | None:
    """A displayed number the row model accepts, else None (so the row is flagged, not crashed)."""
    if x is None or not math.isfinite(x) or x < 0 or x > ceiling:
        return None
    return x


TOTAL_TOLERANCE = 0.02  # rows' values vs the screen's displayed total (relative)
UNIT_100X_TOLERANCE = 0.05  # how close qty x price must be to 100x (or 1/100 of) the value
TOTAL_LINE_RE = re.compile(
    r"סה\"כ|סהכ|שווי\s*(?:ה)?תיק|שווי\s*כולל|total|portfolio\s*value|market\s*value", re.IGNORECASE
)
# Flags computed from the numbers alone: recomputed on every validation (never stale after an edit).
COMPUTED_FLAGS = ("missing_fields", "value_mismatch", "quantity_uncertain", "price_unit_100x")

COST_PLAUSIBLE_MIN = 0.05  # a per-unit cost below 0.05x the price is not believable
COST_PLAUSIBLE_MAX = 20.0  # nor above 20x the price


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
    # Mirror of frontend/lib/ocr/parse.ts: a leftover number is only a guess (P&L, total cost...).
    # Keep it as a per-unit cost only when plausible next to the price, and mark it inferred so
    # confirm never overwrites a holding's existing average cost with it.
    if row.cost is not None:
        if (
            row.price is not None
            and row.price > 0
            and COST_PLAUSIBLE_MIN * row.price <= row.cost <= COST_PLAUSIBLE_MAX * row.price
        ):
            row.flags = [*row.flags, "cost_inferred"]
        else:
            row.cost = None
    clean = re.sub(NUMBER_RE, " ", line)
    clean = re.sub(r"[₪$%|]", " ", clean)
    for marker in AGOROT_MARKERS:
        clean = re.sub(re.escape(marker), " ", clean, flags=re.IGNORECASE)
    for sym in SYMBOL_RE.findall(clean):
        if sym not in SYMBOL_STOPWORDS and row.symbol is None and (len(sym) <= 5):
            row.symbol = sym
            break
    name = re.sub(r"\s+", " ", clean).strip(" -:,.")
    row.name = mask_digit_runs(name)
    row.quantity = clean_number(row.quantity, QTY_MAX)
    row.price, row.value, row.cost = (clean_number(x) for x in (row.price, row.value, row.cost))
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
    if cur not in ("ILS", "USD"):
        cur, unit = "ILS", "ILS"  # an unsupported currency is never guessed: no usable numbers
        r = r.model_copy(update={"price": None, "value": None, "cost": None})
    symbol = norm_symbol(r.symbol)
    if not isinstance(symbol, str) or not re.fullmatch(SYMBOL_PATTERN, symbol):
        symbol = None
    tase = re.sub(r"\D", "", r.tase_number or "")
    low = r.confidence is not None and "low" in r.confidence.model_dump().values()
    return ParsedRow(
        index=index,
        name=mask_digit_runs(r.name),
        symbol=symbol,
        tase_number=tase if re.fullmatch(TASE_NUMBER_PATTERN, tase) else None,
        quantity=clean_number(r.quantity, QTY_MAX),
        price=clean_number(r.price),
        value=clean_number(r.value),
        cost=clean_number(r.cost),
        currency=cur,  # type: ignore[arg-type]
        unit=unit,
        flags=["ocr_low_confidence"] if low else [],
    )


def parse_ocr_result(result: OcrResult, settings: Settings | None = None) -> list[ParsedRow]:
    if result.rows is not None:
        return [_from_ocr_row(r, i) for i, r in enumerate(result.rows)]
    return parse_ocr_text(result.text or "", settings)


def validate_row(row: ParsedRow, settings: Settings | None = None) -> ParsedRow:
    """Flag incomplete rows and rows where quantity x price differs from value by > tolerance."""
    s = settings or get_settings()
    flags = [f for f in row.flags if f not in COMPUTED_FLAGS]
    if row.quantity is None:
        flags.append("quantity_uncertain")  # the user must enter one (confirm stays blocked)
    if row.quantity is None or row.price is None or row.value is None:
        flags.append("missing_fields")
    else:
        price = price_native(row) or 0.0
        expected = row.quantity * price
        if row.value == 0 or abs(expected - row.value) / abs(row.value) > s.import_value_tolerance:
            flags.append("value_mismatch")
            if unit_off_by_100(row):
                flags.append("price_unit_100x")
    row.flags = flags
    return row


def unit_off_by_100(row: ParsedRow) -> bool:
    """A shekel row whose quantity x price is ~100x (or ~1/100 of) the value: the price (or value)
    was probably read in the wrong unit (agorot vs shekels). Only a flag: never auto-corrected."""
    if row.currency != "ILS" or not row.quantity or not row.price or not row.value:
        return False
    expected = row.quantity * (price_native(row) or 0.0)
    if expected <= 0:
        return False
    ratio = expected / row.value
    return any(abs(ratio - k) / k <= UNIT_100X_TOLERANCE for k in (100.0, 0.01))


def detect_total(text: str) -> float | None:
    """The portfolio total the screen displays, if a total line is found: the largest number on a
    line that has a total keyword and at most two numbers (so it is not a holding row)."""
    best: float | None = None
    for line in text.splitlines():
        if not TOTAL_LINE_RE.search(line):
            continue
        nums = [_to_number(t) for t in NUMBER_RE.findall(line) if not t.endswith("%")]
        vals = [n for n in nums if n is not None and n > 0]
        if 1 <= len(vals) <= 2 and (best is None or max(vals) > best):
            best = max(vals)
    return best


def flag_total_mismatch(rows: list[ParsedRow], total: float | None) -> None:
    """Add `total_mismatch` to every row when the rows' values EXCEED the displayed total by more
    than the tolerance (same currency only). A partial screenshot sums below it: not flagged."""
    if total is None or total <= 0 or not rows:
        return
    if len({r.currency for r in rows}) != 1 or any(r.value is None for r in rows):
        return
    summed = sum(r.value or 0.0 for r in rows)
    if (summed - total) / total > TOTAL_TOLERANCE:  # only an excess is impossible
        for r in rows:
            if "total_mismatch" not in r.flags:
                r.flags = [*r.flags, "total_mismatch"]
