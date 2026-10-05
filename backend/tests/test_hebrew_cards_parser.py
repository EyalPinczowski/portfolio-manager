"""The `hebrew_broker_cards` layout, with parity against the shared fixture the on-device (TypeScript)
parser is tested with (`frontend/tests/fixtures/hebrew_broker_cards.json`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.importer.layouts import detect_layout, parse_screenshot_text
from app.importer.match import MatchResult, apply_match
from app.importer.parse import ParsedRow

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "tests"
    / "fixtures"
    / "hebrew_broker_cards.json"
)


def load_fixture() -> dict[str, Any]:
    assert FIXTURE.is_file(), f"shared fixture is missing: {FIXTURE}"
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _variants() -> list[tuple[str, str]]:
    return [(c["id"], v) for c in load_fixture()["cases"] for v in c["images"]]


@pytest.mark.parametrize(("case_id", "variant"), _variants())
def test_parity_with_the_on_device_parser(case_id: str, variant: str) -> None:
    case = next(c for c in load_fixture()["cases"] if c["id"] == case_id)
    layout, rows = parse_screenshot_text(case["images"][variant])
    assert layout == case["expected"]["layout"]
    assert len(rows) == len(case["expected"]["rows"])
    for i, (r, e) in enumerate(zip(rows, case["expected"]["rows"], strict=True)):
        assert r.index == i
        assert r.name == e["name"]
        assert r.symbol == e["symbol"]
        assert r.quantity == e["quantity"]
        assert r.price == e["price"]
        assert r.value is None and r.cost is None
        assert (r.currency, r.unit) == (e["currency"], e["unit"])
        assert r.flags == e["flags"]


def test_detection_and_old_layouts_unchanged() -> None:
    assert detect_layout("IBB כמות 9 205.29 -0.33%") == "hebrew_broker_cards"
    assert detect_layout("שם נייר כמות שער שווי\nטבע 1,000 6,500 65,000") == "generic"
    assert detect_layout("NASDAQ • ACME\nכמות 5") == "meitav_trade"
    layout, rows = parse_screenshot_text("NVDA NVIDIA Corp 10 $120.50 $1,205.00")
    assert layout == "generic" and rows[0].quantity == 10


def test_hebrew_name_keeps_its_leading_number_and_loses_the_quantity_word() -> None:
    _, rows = parse_screenshot_text('35 מחקה ת"א MTF כמות 2,140 424.62 +0.34%')
    assert rows[0].name == '35 מחקה ת"א MTF'
    assert rows[0].quantity == 2140 and rows[0].price == 424.62


def test_currency_flag_survives_matching_so_the_user_must_confirm() -> None:
    row = ParsedRow(name="IBB", symbol="IBB", quantity=9, price=205.29, currency="USD", unit="USD")
    row.flags = ["currency_changed"]
    out = apply_match(row, MatchResult(None, "none", 0.0))
    assert "currency_changed" in out.flags
