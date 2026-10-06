"""Block 2.0-E item 4: the server-side `meitav_trade` parser, with a parity test against the
shared fixtures the on-device (TypeScript) parser is tested with."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.importer.hebrew import names_match
from app.importer.layouts import detect_layout, parse_screenshot_text, parse_screenshots
from app.importer.meitav import find_anchors
from app.importer.parse import ParsedRow

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "tests"
    / "fixtures"
    / "meitav"
    / "meitav_trade_cards.json"
)


def load_fixture() -> dict[str, Any]:
    # A missing file must fail loudly, never skip: the parity test is the contract.
    assert FIXTURE.is_file(), f"shared Meitav fixture is missing: {FIXTURE}"
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _variants() -> list[tuple[str, str]]:
    fx = load_fixture()
    return [(c["id"], v) for c in fx["cases"] for v in c["images"]]


def test_the_shared_fixture_has_the_expected_shape() -> None:
    fx = load_fixture()
    assert fx["schema_version"] == 1 and len(fx["cases"]) >= 3
    for c in fx["cases"]:
        assert {"in_order", "shuffled_columns", "hebrew_reversed"} <= set(c["images"])


@pytest.mark.parametrize(("case_id", "variant"), _variants())
def test_parity_with_the_on_device_parser(case_id: str, variant: str) -> None:
    fx = load_fixture()
    case = next(c for c in fx["cases"] if c["id"] == case_id)
    layout, rows = parse_screenshots(case["images"][variant])
    exp = case["expected"]
    assert layout == exp["layout"] == "meitav_trade"
    assert len(rows) == len(exp["rows"]), [r.symbol or r.tase_number for r in rows]
    tol = fx["number_abs_tol"]
    for i, e in enumerate(exp["rows"]):
        r = rows[i]
        where = f"{case_id}/{variant} row {i} ({e['symbol'] or e['tase_number']})"
        assert r.index == i, where
        assert r.symbol == e["symbol"], where
        assert r.tase_number == e["tase_number"], where
        assert r.currency == e["currency"], where
        assert r.unit == e["unit"], where
        assert r.quantity == e["quantity"], where
        for field in ("price", "value"):
            got, want = getattr(r, field), e[field]
            assert (got is None) == (want is None), f"{where} {field}"
            if want is not None:
                assert abs(got - want) <= abs(want) * 1e-9 + tol, f"{where} {field}"
        if e["cost"] is None:
            assert r.cost is None, where
        else:
            assert r.cost is not None, where
            assert abs(r.cost - e["cost"]) <= abs(e["cost"]) * fx["cost_rel_tol"] + tol, where
        assert sorted(r.flags) == sorted(e["flags"]), f"{where}: {r.flags}"
        assert names_match(r.name, e["name"]), f"{where}: {r.name!r} vs {e['name']!r}"


def test_conflict_rows_carry_the_other_copys_numbers() -> None:
    fx = load_fixture()
    case = next(c for c in fx["cases"] if c["id"] == "overlap_truncated_and_conflict")
    _, rows = parse_screenshots(case["images"]["in_order"])
    flagged = [r for r in rows if "conflict" in r.flags]
    assert flagged and all(r.conflict is not None for r in flagged)


def card(*lines: str) -> str:
    return "\n".join(lines)


def one(text: str) -> ParsedRow:
    layout, rows = parse_screenshot_text(text)
    assert layout == "meitav_trade"
    return rows[0]


def test_detects_the_layout_by_the_exchange_bullet_ticker_pattern_else_generic() -> None:
    assert detect_layout("NASDAQ • ACME\nAcme\n$100.00") == "meitav_trade"
    assert detect_layout("ACME • NASDAQ\n$100.00") == "meitav_trade"
    assert detect_layout("NYSE ABC\nNASDAQ XYZ") == "meitav_trade"
    assert detect_layout("NVDA NVIDIA Corp 10 $120.50 $1,205.00") == "generic"
    assert detect_layout("קרן סל\nטבע 1,000 6,500 65,000") == "generic"
    layout, rows = parse_screenshot_text("NVDA NVIDIA Corp 10 $120.50 $1,205.00")
    assert layout == "generic" and rows[0].symbol == "NVDA" and rows[0].quantity == 10


def test_anchors_for_dotted_tickers_tlv_numbers_and_no_false_hits() -> None:
    a = find_anchors("NYSE • BRK.B")[0]
    assert (a.exchange, a.ticker) == ("NYSE", "BRK.B")
    b = find_anchors("TLV • 1159714")[0]
    assert (b.exchange, b.ticker) == ("TLV", "1159714")
    assert find_anchors("TLV • ABC") == []
    assert find_anchors("NASDAQ • 1234567") == []
    assert find_anchors("NASDAQ • Constellation") == []


def test_the_seven_digit_tase_number_is_never_a_quantity_or_a_price() -> None:
    r = one(card("TLV • 1159714 6,272", "מחקה דמה", "-0.31%", "₪30,670.08"))
    assert (r.tase_number, r.symbol, r.price, r.value) == ("1159714", None, 6272, 30670.08)
    assert (r.quantity, r.unit, r.currency, r.exchange) == (489, "agorot", "ILS", None)
    assert not any(ch.isdigit() for ch in r.name)


def test_quantity_is_value_over_price_and_cost_comes_from_the_pnl_percent() -> None:
    r = one(card("NASDAQ • ACME", "257.49", "Acme Corp", "-0.55%", "-8.37% ↓ $3,089.88"))
    assert (r.quantity, r.price, r.value, r.unit, r.exchange) == (
        12,
        257.49,
        3089.88,
        "USD",
        "NASDAQ",
    )
    assert r.cost == pytest.approx(281, abs=0.5) and "cost_inferred" in r.flags


def test_a_row_worth_cents_is_quantity_uncertain() -> None:
    r = one(card("NASDAQ • ZZZW 0.0107", "Warrant", "-3.10%", "-91.20% ↓ $0.03"))
    assert r.quantity is None and "quantity_uncertain" in r.flags and r.value == 0.03


def test_the_minimum_value_is_a_setting() -> None:
    from app.config import Settings

    text = card("NASDAQ • ACME", "2.00", "Acme Corp", "$6.00")
    assert one(text).quantity == 3
    layout, rows = parse_screenshot_text(text, settings=Settings(import_infer_min_value=10.0))
    assert layout == "meitav_trade" and rows[0].quantity is None


def test_no_pnl_percent_means_no_cost_and_a_lone_day_change_is_not_pnl() -> None:
    r = one(card("NYSE • QQQX", "98.10", "Invesco Trust", "0.12%", "$1,962.00"))
    assert r.cost is None and "cost_inferred" not in r.flags and r.quantity == 20
    ambiguous = one(card("NYSE • QQQX", "98.10 $1,962.00", "1.5%", "2.5%", "Invesco Trust"))
    assert ambiguous.cost is None and ambiguous.quantity == 20


def test_ellipsis_and_section_header_are_dropped_from_names() -> None:
    _, rows = parse_screenshot_text(
        card(
            "NYSE • XLEX 50.00",
            "…Energy Select Sector Spdr F",
            "-0.10%",
            "+5.00% ↑ $500.00",
            "קרן סל",
            "TLV • 1111111 100",
            "קרן",
            "-0.1%",
            "₪10.00",
        )
    )
    assert rows[0].name == "Energy Select Sector Spdr F" and rows[0].symbol == "XLEX"
    assert rows[1].tase_number == "1111111"


def test_a_card_missing_its_value_invents_no_numbers() -> None:
    r = one(card("NASDAQ • ACME", "Acme Corp", "257.49", "-0.55%"))
    assert r.value is None and r.quantity is None and "quantity_uncertain" in r.flags


def test_a_fractional_quantity_is_flagged_not_rounded_silently() -> None:
    r = one(card("NASDAQ • ACME", "100.00", "Acme Corp", "$250.00"))
    assert r.quantity == 2.5 and "quantity_fractional" in r.flags


def test_text_with_no_card_returns_none_so_the_generic_parser_runs() -> None:
    from app.importer.meitav import parse_meitav_text

    assert parse_meitav_text("just some words\n12:41") is None


def test_the_server_ocr_path_uses_the_meitav_layout(signup: Any, ocr_text: dict[str, str]) -> None:
    from tests.conftest import png_bytes

    fx = load_fixture()
    case = next(c for c in fx["cases"] if c["id"] == "full_list")
    ocr_text["text"] = case["images"]["in_order"][0]
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"]
    c.post("/api/auth/consent/ocr")
    r = c.post(
        f"/api/portfolios/{pid}/imports",
        content=png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert r.status_code == 201, r.text
    rows = r.json()["rows"]
    assert [x["symbol"] for x in rows] == ["ACME", "SPYX", "ZZZW", "QQQX", None, None]
    assert [x["tase_number"] for x in rows[4:]] == ["1234567", "7654321"]
    assert rows[0]["quantity"] == 12 and rows[0]["exchange"] == "NASDAQ"
    assert rows[2]["quantity"] is None and "quantity_uncertain" in rows[2]["flags"]


@pytest.mark.parametrize("dash", ["–", "—", "‐", "‑", "−"])
def test_dash_variants_glued_to_the_pnl_percent_are_a_minus(dash: str) -> None:
    r = one(card("NASDAQ • ACME", "257.49", "Acme Corp", "-0.55%", f"{dash}8.37% ↓ $3,089.88"))
    assert r.cost == pytest.approx(257.49 / (1 - 0.0837), abs=1e-3)


NEW_LAYOUT = card(
    "קרן סל",
    "4,125 77רדס.XTF",
    "+0.11%",
    'TLV • 1180422 מספר ני"ע',
    "12.80% ↑ ₪24,750.00",
    "אחר",
    "418.3",
    "חיסכון ירוק 41",
    "₪418.3",
    "ראשי",
)


def test_a_tase_number_stays_with_the_name_above_it_and_bars_are_not_cards() -> None:
    layout, rows = parse_screenshot_text(NEW_LAYOUT)
    assert layout == "meitav_trade" and len(rows) == 2
    fund, simple = rows
    assert (fund.tase_number, fund.symbol, fund.price, fund.value) == ("1180422", None, 4125, 24750)
    assert fund.quantity == 600 and names_match(fund.name, "77רדס.XTF")
    assert (simple.symbol, simple.tase_number, simple.cost) == (None, None, None)
    assert (simple.price, simple.value, simple.quantity) == (418.3, 418.3, 1)
    assert simple.name == "חיסכון ירוק 41"  # its own digits, nothing from the fund above


def test_a_simple_card_whose_quantity_cannot_be_inferred_is_flagged_not_merged() -> None:
    _, rows = parse_screenshot_text(
        card(*NEW_LAYOUT.split("\n")[:5], "אחר", "חיסכון ירוק 41", "281.3", "₪2,261.17")
    )
    assert len(rows) == 2 and rows[0].tase_number == "1180422"
    assert rows[1].quantity is None and "quantity_uncertain" in rows[1].flags


def test_no_amount_no_card_for_the_status_bar_and_header() -> None:
    _, rows = parse_screenshot_text(
        card("11:41", "מיטב:טרייד", "NYSE • VNTQ", "84.15", "$1,683.00")
    )
    assert len(rows) == 1 and rows[0].symbol == "VNTQ"
