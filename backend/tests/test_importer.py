"""Importer pipeline with fixture OCR output (Hebrew names, agorot units). No live OCR."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlmodel import Session, select

from app.config import Settings
from app.importer.diff import diff_rows
from app.importer.match import SecurityIndex, apply_match, match_row, normalize_name
from app.importer.parse import (
    ParsedRow,
    parse_ocr_result,
    parse_ocr_text,
    price_native,
    validate_row,
)
from app.importer.redact import WordBox, redact_image
from app.models import Security
from app.providers.base import OcrResult, OcrRow
from app.providers.ocr.gemini import parse_gemini_json

S = Settings()

FIXTURE_AGOROT = """\
תיק השקעות - חשבון 1234567890
שם נייר   כמות   שער (אג')   שווי   עלות
טבע   1,000   6,500   65,000   5,800
לאומי  500  3,200  16,000
בנק הפועלים 200 3,450 6,900
נייס 10 8,200 820
"""

FIXTURE_USD = """\
Symbol Quantity Price Value
NVDA NVIDIA Corp 10 $120.50 $1,205.00
AAPL 5 $190.20 $951.00
"""


def test_parse_hebrew_agorot_rows() -> None:
    rows = parse_ocr_text(FIXTURE_AGOROT, S)
    assert [r.name for r in rows] == ["טבע", "לאומי", "בנק הפועלים", "נייס"]
    teva = rows[0]
    assert teva.unit == "agorot" and teva.currency == "ILS"
    assert (teva.quantity, teva.price, teva.value, teva.cost) == (1000, 6500, 65000, 5800)
    assert price_native(teva) == 65.0
    assert rows[1].cost is None
    assert all(r.unit == "agorot" for r in rows)


def test_parse_english_usd_rows_and_symbol_detection() -> None:
    rows = parse_ocr_text(FIXTURE_USD, S)
    assert len(rows) == 2
    nv, aapl = rows
    assert nv.symbol == "NVDA" and nv.currency == "USD" and nv.unit == "USD"
    assert (nv.quantity, nv.price, nv.value) == (10, 120.5, 1205.0)
    assert aapl.symbol == "AAPL"


def test_parse_handles_reordered_columns_and_security_number() -> None:
    # value, price, quantity printed in RTL order, plus a TASE security number
    rows = parse_ocr_text("טבע 629014 65,000 6,500 1,000 ₪ אג'", S)
    r = rows[0]
    assert r.value == 65000
    assert {r.quantity, r.price} == {1000, 6500}  # q x p is symmetric; the user reviews the row
    assert r.tase_number == "629014"
    assert r.unit == "agorot"
    assert "value_mismatch" not in validate_row(r, S).flags


def test_row_unit_falls_back_to_ils_when_page_header_says_agorot() -> None:
    text = "מחיר באגורות\nטבע 1,000 6,500 65,000\nאלביט 10 1,200 12,000\n"
    rows = parse_ocr_text(text, S)
    assert [r.unit for r in rows] == ["agorot", "ILS"]
    assert not any("value_mismatch" in validate_row(r, S).flags for r in rows)


def test_structured_gemini_rows_and_json_parsing() -> None:
    res = parse_gemini_json(
        '{"rows":[{"name":"טבע","symbol":null,"quantity":1000,"price":6500,"value":65000,'
        '"cost":5800,"currency":"ILS","unit":"agorot"},'
        '{"name":"Apple","symbol":"AAPL","quantity":5,"price":190.2,"value":951,"currency":"USD","unit":"USD"}]}'
    )
    rows = parse_ocr_result(res, S)
    assert rows[0].unit == "agorot" and rows[0].currency == "ILS"
    assert rows[1].unit == "USD"
    assert res.provider == "gemini"
    plain = parse_ocr_result(OcrResult(provider="x", rows=[OcrRow(name="n", unit="ILS")]), S)
    assert plain[0].currency == "ILS"


def test_validation_flags_mismatch_and_missing() -> None:
    ok = validate_row(ParsedRow(name="a", quantity=1000, price=6500, value=65000, unit="agorot"), S)
    assert ok.flags == []
    within = validate_row(
        ParsedRow(name="a", quantity=10, price=100.0, value=1015.0), S
    )  # 1.5% off
    assert within.flags == []
    bad = validate_row(ParsedRow(name="a", quantity=10, price=100.0, value=1100.0), S)
    assert "value_mismatch" in bad.flags
    # forgetting agorot makes the row inconsistent: the validator catches it
    wrong_unit = validate_row(
        ParsedRow(name="a", quantity=1000, price=6500, value=65000, unit="ILS"), S
    )
    assert "value_mismatch" in wrong_unit.flags
    missing = validate_row(ParsedRow(name="a", quantity=10, price=None, value=None), S)
    assert "missing_fields" in missing.flags
    # re-validating does not duplicate flags
    assert validate_row(bad, S).flags.count("value_mismatch") == 1


@pytest.fixture
def index(db: Session) -> SecurityIndex:
    return SecurityIndex(list(db.exec(select(Security)).all()))


def test_match_exact_symbol_and_tase_number(index: SecurityIndex) -> None:
    r = match_row(ParsedRow(name="x", symbol="NVDA", currency="USD", unit="USD"), index, S)
    assert r.security and r.security.symbol == "NVDA" and r.method == "symbol"
    r2 = match_row(ParsedRow(name="?", tase_number="629014"), index, S)
    assert r2.security and r2.security.symbol == "TEVA.TA" and r2.method == "tase_number"


def test_match_hebrew_exact_names_accepted_partial_names_become_candidates(
    index: SecurityIndex,
) -> None:
    exact = {"טבע": "TEVA.TA", "לאומי": "LUMI.TA", "פועלים": "POLI.TA"}
    for name, expected in exact.items():
        res = match_row(ParsedRow(name=name, currency="ILS", unit="agorot"), index, S)
        assert res.security is not None and res.security.symbol == expected, name
        assert res.method == "name" and not res.low_confidence
    # A partial name is only a suggestion: nothing is picked, the user chooses.
    for name, expected in {"בנק הפועלים": "POLI.TA", "אלביט": "ESLT.TA"}.items():
        res = match_row(ParsedRow(name=name, currency="ILS", unit="agorot"), index, S)
        assert res.security is None and res.low_confidence, name
        assert res.candidates and res.candidates[0][0] == expected, (name, res.candidates)


def test_match_english_name_and_unmatched(index: SecurityIndex) -> None:
    res = match_row(ParsedRow(name="Microsoft Corp", currency="USD", unit="USD"), index, S)
    assert res.security and res.security.symbol == "MSFT"  # exact after stripping "Corp"
    none = match_row(ParsedRow(name="zzzzqqq xxyy", currency="USD"), index, S)
    assert none.security is None
    row = apply_match(ParsedRow(name="zzzzqqq xxyy"), none)
    assert "unmatched" in row.flags and row.symbol is None


def test_dual_listing_prefers_listing_in_row_currency(index: SecurityIndex) -> None:
    ils = match_row(ParsedRow(name="טבע", currency="ILS", unit="agorot"), index, S)
    assert ils.security and ils.security.market == "TASE"
    usd = match_row(ParsedRow(name="Teva", symbol="TEVA", currency="USD", unit="USD"), index, S)
    assert usd.security and usd.security.symbol == "TEVA"  # US line for a dollar row
    swapped = match_row(ParsedRow(name="x", symbol="TEVA", currency="ILS", unit="agorot"), index, S)
    assert swapped.security and swapped.security.symbol == "TEVA.TA"


def test_currency_mismatch_is_flagged_not_overwritten(index: SecurityIndex) -> None:
    row = ParsedRow(name="אפל", currency="ILS", unit="ILS")
    apply_match(row, match_row(row, index, S), index)
    assert row.symbol == "AAPL" and "currency_changed" in row.flags
    assert row.currency == "ILS" and row.unit == "ILS"  # what the broker displays is kept


def test_apple_hospitality_reit_must_not_become_aapl(index: SecurityIndex) -> None:
    row = ParsedRow(name="Apple Hospitality REIT", currency="USD", unit="USD")
    res = match_row(row, index, S)
    assert res.security is None  # the old WRatio scorer returned AAPL at 90 with no warning
    apply_match(row, res, index)
    assert row.symbol is None and row.matched_name is None
    assert "unmatched" in row.flags and "low_confidence_match" in row.flags
    # AAPL may be *offered* (the name contains "Apple") but is never chosen
    assert all(c.score <= S.match_containment_score for c in row.candidates)


def test_microstrategy_must_not_become_msft(index: SecurityIndex) -> None:
    row = ParsedRow(name="Microstrategy", currency="USD", unit="USD")
    res = match_row(row, index, S)
    assert res.security is None and res.low_confidence
    apply_match(row, res, index)
    assert row.symbol is None and row.matched_name is None
    assert "low_confidence_match" in row.flags and "unmatched" in row.flags
    assert [c.symbol for c in row.candidates] == ["MSFT"]  # offered, never chosen
    assert row.candidates[0].name == "Microsoft" and 60 <= row.candidates[0].score < 100


def test_exact_name_shared_by_two_unrelated_securities_is_ambiguous(db: Session) -> None:
    from app.importer.match import SecurityIndex as Idx

    twins = [
        Security(symbol="AAA", name_en="Delta", market="US", currency="USD"),
        Security(symbol="BBB", name_en="Delta Ltd", market="US", currency="USD"),
    ]
    res = match_row(ParsedRow(name="delta", currency="USD", unit="USD"), Idx(twins), S)
    assert res.security is None and res.low_confidence
    assert {sym for sym, _ in res.candidates} == {"AAA", "BBB"}


def test_normalize_name_strips_quotes_and_suffixes() -> None:
    assert normalize_name('בנק לאומי בע"מ') == "בנק לאומי"
    assert normalize_name("Apple Inc.") == "apple"


# ------------------------------------------------------------------ diff
def row(
    sym: str | None, qty: float | None, price: float | None = 10.0, cur: str = "USD", index: int = 0
) -> dict[str, object]:
    return {"index": index, "symbol": sym, "quantity": qty, "price_native": price, "currency": cur}


def test_first_import_has_no_changes() -> None:
    assert diff_rows([row("AAPL", 5)], None) == []


def test_diff_buy_sell_new_and_vanished() -> None:
    last = [
        {"symbol": "AAPL", "quantity": 5, "price_native": 100.0, "currency": "USD"},
        {"symbol": "MSFT", "quantity": 3, "price_native": 400.0, "currency": "USD"},
        {"symbol": "TEVA.TA", "quantity": 100, "price_native": 60.0, "currency": "ILS"},
        {"symbol": "KO", "quantity": 7, "price_native": 60.0, "currency": "USD"},
    ]
    new = [
        row("AAPL", 8, 110.0, index=0),  # +3 -> buy
        row("MSFT", 1, 410.0, index=1),  # -2 -> sell
        row("TEVA.TA", 100, 65.0, "ILS", index=2),  # unchanged
        row("NVDA", 4, 120.0, index=3),  # new -> buy
        row(None, 9, index=4),  # unmatched rows are ignored
    ]
    changes = {(c.symbol, c.type): c for c in diff_rows(new, last)}
    assert set(changes) == {("AAPL", "buy"), ("MSFT", "sell"), ("NVDA", "buy"), ("KO", "sell")}
    assert changes[("AAPL", "buy")].quantity == 3 and changes[("AAPL", "buy")].amount == 330.0
    assert changes[("MSFT", "sell")].quantity == 2
    assert changes[("KO", "sell")].row_index == -1 and changes[("KO", "sell")].quantity == 7
    assert changes[("NVDA", "buy")].amount == 480.0


def test_diff_with_agorot_price_uses_normalised_native_price() -> None:
    parsed = parse_ocr_text("טבע 1,200 6,500 78,000", S)[0]
    last = [{"symbol": "TEVA.TA", "quantity": 1000, "price_native": 60.0, "currency": "ILS"}]
    new = [
        {
            "index": 0,
            "symbol": "TEVA.TA",
            "quantity": parsed.quantity,
            "price_native": price_native(parsed),
            "currency": parsed.currency,
        }
    ]
    (ch,) = diff_rows(new, last)
    assert ch.type == "buy" and ch.quantity == 200 and ch.amount == pytest.approx(13000.0)
    assert ch.currency == "ILS"


# ------------------------------------------------------------------ redaction
def _img(w: int = 300, h: int = 400) -> bytes:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for x in range(w):
        for y in range(h):
            if (x // 4 + y // 4) % 2 == 0:
                img.putpixel((x, y), (0, 0, 0))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_redaction_blurs_header_and_long_digit_runs() -> None:
    src = _img()
    boxes = [
        WordBox("1234567890", 20, 200, 80, 20),  # 10-digit account number -> blurred
        WordBox("1,234,567", 20, 250, 80, 20),  # thousands-separated -> kept
        WordBox("65000", 20, 300, 60, 20),  # 5 digits -> kept
    ]
    out, report = redact_image(src, S, word_boxes=boxes)
    assert report.header_blurred and report.boxes_blurred == 1
    a, b = Image.open(io.BytesIO(src)).convert("RGB"), Image.open(io.BytesIO(out)).convert("RGB")

    def changed(box: tuple[int, int, int, int]) -> bool:
        return a.crop(box).tobytes() != b.crop(box).tobytes()

    assert changed((0, 0, 300, 40))  # header (top 12% of 400px = 48px)
    assert changed((20, 200, 100, 220))
    assert not changed((20, 250, 100, 270))
    assert not changed((20, 300, 80, 320))
    assert not changed((0, 100, 300, 150))


def test_redaction_without_tesseract_still_blurs_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.importer.redact.tesseract_available", lambda: False)
    out, report = redact_image(_img(), S)
    assert (
        report.header_blurred and report.word_boxes_available is False and report.boxes_blurred == 0
    )
    assert Image.open(io.BytesIO(out)).size == (300, 400)
