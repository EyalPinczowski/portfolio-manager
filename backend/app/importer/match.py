"""Match parsed rows to Security rows: exact symbol, TASE number, then fuzzy Hebrew/English name."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.config import Settings, get_settings
from app.importer.parse import ParsedRow
from app.models import Security

QUOTES_RE = re.compile(r"[\"'`׳״’‘“”.,]")
SEPARATORS_RE = re.compile(r"[()\-/]")
STOPWORDS = {
    "בעמ",
    "ltd",
    "inc",
    "corp",
    "corporation",
    "co",
    "plc",
    "ords",
    "ordinary",
    "shares",
    "limited",
    "nyse",
    "nasdaq",
}


def normalize_name(name: str) -> str:
    text = SEPARATORS_RE.sub(" ", QUOTES_RE.sub("", name.lower()))
    words = [w for w in text.split() if w not in STOPWORDS]
    return " ".join(words)


@dataclass
class MatchResult:
    security: Security | None
    method: str  # symbol | tase_number | name | none
    score: float
    candidates: list[tuple[str, float]] = field(default_factory=list)
    low_confidence: bool = False


class SecurityIndex:
    def __init__(self, securities: Sequence[Security]) -> None:
        self.by_symbol = {s.symbol.upper(): s for s in securities}
        self.by_tase = {s.tase_number: s for s in securities if s.tase_number}
        self.groups: dict[str, list[Security]] = {}
        for s in securities:
            if s.dual_listing_group:
                self.groups.setdefault(s.dual_listing_group, []).append(s)
        self.choices: dict[tuple[str, str], str] = {}
        for s in securities:
            en = normalize_name(s.name_en)
            if en:
                self.choices[(s.symbol, "en")] = en
            he = normalize_name(s.name_he)
            if he:
                self.choices[(s.symbol, "he")] = he

    def prefer_listing(self, sec: Security, row: ParsedRow) -> Security:
        """For dual listings choose the TASE line for shekel rows, the US line for dollar rows."""
        if not sec.dual_listing_group:
            return sec
        want = "TASE" if row.currency == "ILS" else "US"
        for sibling in self.groups.get(sec.dual_listing_group, []):
            if sibling.market == want:
                return sibling
        return sec


def match_row(
    row: ParsedRow, index: SecurityIndex, settings: Settings | None = None
) -> MatchResult:
    s = settings or get_settings()
    if row.symbol:
        sym = row.symbol.upper()
        variants = [f"{sym}.TA", sym] if row.currency == "ILS" else [sym, f"{sym}.TA"]
        for v in variants:
            sec = index.by_symbol.get(v)
            if sec is not None:
                return MatchResult(index.prefer_listing(sec, row), "symbol", 100.0)
    if row.tase_number and row.tase_number in index.by_tase:
        return MatchResult(index.by_tase[row.tase_number], "tase_number", 100.0)
    query = normalize_name(row.name)
    if not query or not index.choices:
        return MatchResult(None, "none", 0.0)
    hits = process.extract(query, index.choices, scorer=fuzz.WRatio, limit=6)
    if not hits:
        return MatchResult(None, "none", 0.0)
    best_score = float(hits[0][1])
    candidates = [(key[0], float(score)) for _text, score, key in hits]
    if best_score < s.match_suggest_threshold:
        return MatchResult(None, "none", best_score, candidates)
    # Among hits tied with the best score, prefer a listing in the row's own currency/market.
    top = [h for h in hits if float(h[1]) >= best_score - 1e-6]
    best_sym = top[0][2][0]
    sec = index.by_symbol[best_sym.upper()]
    sec = index.prefer_listing(sec, row)
    return MatchResult(
        sec, "name", best_score, candidates, low_confidence=best_score < s.match_auto_threshold
    )


def apply_match(row: ParsedRow, result: MatchResult) -> ParsedRow:
    flags = [f for f in row.flags if f not in ("unmatched", "low_confidence_match")]
    if result.security is None:
        flags.append("unmatched")
        row.symbol = None
        row.matched_name = None
    else:
        sec = result.security
        row.symbol = sec.symbol
        row.matched_name = sec.name_en
        if row.unit != "agorot" and sec.currency != row.currency and sec.currency in ("ILS", "USD"):
            # The security's own currency is the truth for non-agorot rows.
            row.currency = sec.currency
            row.unit = "USD" if sec.currency == "USD" else "ILS"
        elif row.unit == "agorot" and sec.currency != "ILS":
            flags.append("unit_mismatch")
        if result.low_confidence:
            flags.append("low_confidence_match")
    row.flags = flags
    return row
