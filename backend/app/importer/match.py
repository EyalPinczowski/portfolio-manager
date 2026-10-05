"""Match parsed rows to Security rows: exact symbol, TASE number, then fuzzy Hebrew/English name."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.config import Settings, get_settings
from app.importer.parse import MatchCandidate, ParsedRow, row_currency
from app.models import Security

QUOTES_RE = re.compile(r"[\"'`׳״’‘“”.,]")
SEPARATORS_RE = re.compile(r"[()\-/]")
CLASS_RE = re.compile(r"\b(?:class|series)\s+[a-z0-9]\b")
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


def family_key(name_en: str) -> str:
    """The company name without a share-class designator ("Alphabet Class A" -> "alphabet")."""
    return " ".join(CLASS_RE.sub(" ", normalize_name(name_en)).split())


def has_class_designator(normalized: str) -> bool:
    return CLASS_RE.search(normalized) is not None


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
        # A verified security wins a TASE number: a user-scoped one never shadows a seeded one.
        self.by_tase: dict[str, Security] = {}
        for s in sorted(securities, key=lambda x: (x.verified, x.symbol)):
            if s.tase_number:
                self.by_tase[s.tase_number] = s
        self.groups: dict[str, list[Security]] = {}
        for s in securities:
            if s.dual_listing_group:
                self.groups.setdefault(s.dual_listing_group, []).append(s)
        # Share classes of one company (GOOG / GOOGL): a name that does not say which class is
        # ambiguous even when it equals one security's name exactly (Hebrew "אלפבית").
        self.families: dict[str, list[Security]] = {}
        for s in securities:
            key = family_key(s.name_en)
            if key:
                self.families.setdefault(key, []).append(s)
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
        want = "TASE" if row_currency(row) == "ILS" else "US"
        for sibling in self.groups.get(sec.dual_listing_group, []):
            if sibling.market == want:
                return sibling
        return sec


def _similarity(query: str, choice: str) -> float:
    return float(max(fuzz.token_sort_ratio(query, choice), fuzz.ratio(query, choice)))


def match_row(
    row: ParsedRow, index: SecurityIndex, settings: Settings | None = None
) -> MatchResult:
    """Match a row to a security. Only an exact symbol, an exact TASE number or an exact
    (normalised) name is accepted. Anything weaker returns candidates and `low_confidence=True`
    with `security=None`, so the user decides (a superstring like "Apple Hospitality REIT" must
    never become AAPL)."""
    s = settings or get_settings()
    if row.symbol:
        sym = row.symbol.upper()
        variants = [f"{sym}.TA", sym] if row_currency(row) == "ILS" else [sym, f"{sym}.TA"]
        for v in variants:
            sec = index.by_symbol.get(v)
            if sec is not None:
                return MatchResult(index.prefer_listing(sec, row), "symbol", 100.0)
    if row.tase_number and row.tase_number in index.by_tase:
        return MatchResult(index.by_tase[row.tase_number], "tase_number", 100.0)
    query = normalize_name(row.name)
    if not query or not index.choices:
        return MatchResult(None, "none", 0.0)
    exact = {key[0] for key, text in index.choices.items() if text == query}
    if exact:
        # Dual listings share one name: pick the listing in the row's currency. Two unrelated
        # securities with the same name are ambiguous, so they fall through to candidates.
        secs = [index.by_symbol[sym.upper()] for sym in sorted(exact)]
        groups = {x.dual_listing_group or x.symbol for x in secs}
        if len(groups) == 1:
            siblings = index.families.get(family_key(secs[0].name_en), [])
            sib_groups = {x.dual_listing_group or x.symbol for x in siblings}
            if len(sib_groups) > 1 and not has_class_designator(query):
                # several share classes and the name does not pick one: the user chooses
                ranked_sibs = sorted(siblings, key=lambda x: x.symbol)
                return MatchResult(
                    None,
                    "none",
                    100.0,
                    [(x.symbol, 100.0) for x in ranked_sibs][: s.match_max_candidates],
                    low_confidence=True,
                )
            return MatchResult(index.prefer_listing(secs[0], row), "name", 100.0)
    scored: dict[str, float] = {}
    for (symbol, _lang), text in index.choices.items():
        shorter, longer = sorted((len(query), len(text)))
        if longer == 0:
            continue
        score = 0.0
        if shorter / longer >= s.match_min_length_ratio:
            score = _similarity(query, text)  # a very different length is not "the same name"
        if shorter >= s.match_containment_min_chars and fuzz.partial_ratio(query, text) >= 95:
            # One name contains the other ("בנק הפועלים" / "פועלים"): worth suggesting, never
            # accepted on its own (it is how "Apple Hospitality REIT" looked like AAPL).
            score = max(score, s.match_containment_score)
        if score:
            scored[symbol] = max(score, scored.get(symbol, 0.0))
    want = "TASE" if row_currency(row) == "ILS" else "US"

    def order(kv: tuple[str, float]) -> tuple[float, int, str]:
        market_ok = index.by_symbol[kv[0].upper()].market == want
        return (-kv[1], 0 if market_ok else 1, kv[0])  # ties: the listing in the row's currency

    ranked = sorted(scored.items(), key=order)
    candidates = [(sym, sc) for sym, sc in ranked if sc >= s.match_suggest_threshold][
        : s.match_max_candidates
    ]
    if not candidates:
        return MatchResult(None, "none", ranked[0][1] if ranked else 0.0)
    return MatchResult(None, "none", candidates[0][1], candidates, low_confidence=True)


US_TICKER = re.compile(r"^[A-Z]{1,5}$")
# What a user may type for a TASE security that is not in the seed: Yahoo's `<id>.TA`.
MANUAL_TASE_SYMBOL = re.compile(r"^[A-Z0-9-]{1,15}\.TA$")


def new_security_kind(row: ParsedRow) -> str | None:
    """Can this row introduce a security the seed does not know? The evidence is explicit:

    - `us`: the broker printed `NASDAQ|NYSE|AMEX • TICKER` (`row.exchange`), the ticker is 1-5
      capital letters and the row is in dollars. Accepted as an unverified, user-scoped security;
      a successful quote verifies it.
    - `tase`: a TASE security number plus a symbol the user typed (`<id>.TA`) for a shekel row.
    Names are never evidence (they are truncated, reversed and unreliable).
    """
    sym = (row.symbol or "").upper()
    if row.exchange is not None and US_TICKER.fullmatch(sym) and row_currency(row) == "USD":
        return "us"
    if row.tase_number and MANUAL_TASE_SYMBOL.fullmatch(sym) and row_currency(row) == "ILS":
        return "tase"
    return None


def resolve_row(
    row: ParsedRow, index: SecurityIndex, settings: Settings | None = None
) -> MatchResult:
    """`match_row`, except that an explicit exchange + ticker is an identity: it matches the
    security with that symbol or nothing (never a fuzzy name), and an unknown one is accepted."""
    if row.exchange is not None and row.symbol:
        sec = index.by_symbol.get(row.symbol.upper())
        if sec is not None:
            return MatchResult(sec, "symbol", 100.0)
        if new_security_kind(row) == "us":
            return MatchResult(None, "new", 100.0)
    result = match_row(row, index, settings)
    if result.security is None and new_security_kind(row) == "tase":
        return MatchResult(None, "new", 100.0)
    return result


def currency_flag(row: ParsedRow, sec: Security) -> str | None:
    """`currency_changed` / `unit_mismatch` when the row's currency or unit disagrees with the
    security's own. Compares what confirm stores (`row_currency`, from the unit)."""
    shown = row_currency(row)
    if row.unit != "agorot" and sec.currency != shown and sec.currency in ("ILS", "USD"):
        # A broker may legitimately show a US stock in shekels: the user must confirm (the flag
        # blocks confirming the import), the displayed currency is never overwritten.
        return "currency_changed"
    if row.unit == "agorot" and sec.currency != "ILS":
        return "unit_mismatch"
    return None


def apply_match(
    row: ParsedRow, result: MatchResult, index: SecurityIndex | None = None
) -> ParsedRow:
    managed = ("unmatched", "low_confidence_match", "currency_changed", "unit_mismatch")
    flags = [f for f in row.flags if f not in managed]
    if "currency_changed" in row.flags:
        # Set by a layout that cannot know the currency (`hebrew_broker_cards`): kept until the
        # user has checked it, even when the matched security agrees with the guess.
        flags.append("currency_changed")
    row.candidates = []
    if result.method == "new":
        # Not in the seed, but the row carries the evidence for a user-scoped, unverified
        # security (created at confirm, verified by the first quote). The symbol stays.
        row.matched_name = None
    elif result.security is None:
        flags.append("unmatched")
        row.symbol = None
        row.matched_name = None
        if result.low_confidence:
            flags.append("low_confidence_match")
            row.candidates = [
                MatchCandidate(
                    symbol=sym,
                    name=(index.by_symbol[sym.upper()].name_en if index else sym),
                    score=round(score, 1),
                )
                for sym, score in result.candidates
            ]
    else:
        sec = result.security
        row.symbol = sec.symbol
        row.matched_name = sec.name_en
        flag = currency_flag(row, sec)
        if flag is not None and flag not in flags:
            flags.append(flag)
    row.flags = flags
    return row
