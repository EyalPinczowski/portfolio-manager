"""The `ibi_cards` layout, with parity against the shared fixture the on-device (TypeScript)
parser is tested with (`frontend/tests/fixtures/ibi_cards.json`). All numbers are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.importer.layouts import detect_layout, parse_screenshot_text

FIXTURE = Path(__file__).resolve().parents[2] / "frontend" / "tests" / "fixtures" / "ibi_cards.json"


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
    assert detect_layout("ACME 7 יחידות 41.25 -0.31% -$0.89") == "ibi_cards"
    assert detect_layout("ACME יחידות 7 41.25 -0.31% -$0.89") == "ibi_cards"
    assert detect_layout("IBB כמות 9 205.29 -0.33%") == "hebrew_broker_cards"
    assert detect_layout("שם נייר יחידות שער שווי\nטבע 1,000 6,500 65,000") == "generic"
    assert detect_layout("NASDAQ • ACME\nכמות 5") == "meitav_trade"
    layout, rows = parse_screenshot_text("NVDA NVIDIA Corp 10 $120.50 $1,205.00")
    assert layout == "generic" and rows[0].quantity == 10


def test_leading_number_stays_in_the_name_and_units_word_does_not() -> None:
    layout, rows = parse_screenshot_text("תכ.תא90 50 יחידות 2,310.00 -0.40% -₪4.62")
    assert layout == "ibi_cards"
    assert rows[0].name == "תכ.תא90"
    assert rows[0].quantity == 50 and rows[0].price == 2310.0
    assert "יחידות" not in rows[0].name
    assert (rows[0].currency, rows[0].unit) == ("ILS", "agorot")


def test_fractional_units_and_ils_price_without_agorot() -> None:
    _, rows = parse_screenshot_text("קרן דוגמה 6.47 יחידות 25.40 +1.00% +₪10.16")
    assert rows[0].quantity == 6.47 and rows[0].unit == "ILS"
