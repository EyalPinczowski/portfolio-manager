"""Better screenshot reading: preprocessing, cross-check flags, Gemini confidence (fixtures only)."""

from __future__ import annotations

import json

from PIL import Image, ImageDraw

from app.importer.parse import (
    ParsedRow,
    detect_total,
    flag_total_mismatch,
    parse_ocr_result,
    validate_row,
)
from app.importer.preprocess import mean_luminance, otsu_threshold, preprocess_for_ocr
from app.providers.ocr.gemini import parse_gemini_json


def _screen(bg: int, fg: int, size: tuple[int, int]) -> Image.Image:
    img = Image.new("RGB", size, (bg, bg, bg))
    d = ImageDraw.Draw(img)
    for y in range(5, size[1] - 5, 12):
        d.rectangle((5, y, size[0] // 2, y + 5), fill=(fg, fg, fg))
    return img


def test_dark_image_is_inverted_and_small_image_upscaled() -> None:
    out = preprocess_for_ocr(_screen(15, 240, (400, 200)))
    assert out.size == (800, 400)
    assert out.mode == "L"
    assert mean_luminance(out) > 128  # light background after inversion


def test_light_wide_image_keeps_size() -> None:
    out = preprocess_for_ocr(_screen(250, 20, (1200, 100)))
    assert out.size == (1200, 100)
    assert mean_luminance(out) > 128


def test_otsu_separates_two_tones() -> None:
    t, sep = otsu_threshold(_screen(240, 10, (200, 100)).convert("L"))
    assert 10 <= t < 240
    assert sep > 0.9


def test_agorot_100x_flag_not_silent() -> None:
    row = ParsedRow(name="x", quantity=10, price=2000, value=200, unit="ILS", currency="ILS")
    validate_row(row)
    assert "price_unit_100x" in row.flags
    assert row.price == 2000  # never auto-corrected
    ok = ParsedRow(name="x", quantity=10, price=20, value=200, unit="ILS", currency="ILS")
    validate_row(ok)
    assert "price_unit_100x" not in ok.flags and "value_mismatch" not in ok.flags


def test_flag_recomputed_after_edit() -> None:
    row = ParsedRow(name="x", quantity=10, price=2000, value=200)
    validate_row(row)
    row.price = 20
    validate_row(row)
    assert "price_unit_100x" not in row.flags


def test_total_check() -> None:
    text = 'סה"כ שווי תיק 1,000\nfoo'
    assert detect_total(text) == 1000
    rows = [ParsedRow(name="a", value=700), ParsedRow(name="b", value=700)]
    flag_total_mismatch(rows, 1000)
    assert all("total_mismatch" in r.flags for r in rows)
    partial = [ParsedRow(name="a", value=300)]
    flag_total_mismatch(partial, 1000)
    assert partial[0].flags == []
    rows = [ParsedRow(name="a", value=600), ParsedRow(name="b", value=400)]
    flag_total_mismatch(rows, 1000)
    assert all("total_mismatch" not in r.flags for r in rows)
    assert detect_total("Teva 10 20 200") is None


def test_gemini_low_confidence_maps_to_flag() -> None:
    payload = json.dumps(
        {
            "rows": [
                {
                    "name": "A",
                    "quantity": 1,
                    "price": 2,
                    "value": 2,
                    "confidence": {"price": "low", "name": "ok"},
                },
                {"name": "B", "quantity": 1, "price": 2, "value": 2},
            ]
        }
    )
    rows = parse_ocr_result(parse_gemini_json(payload))
    assert rows[0].flags == ["ocr_low_confidence"]
    assert rows[1].flags == []
