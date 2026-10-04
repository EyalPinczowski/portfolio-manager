"""Block 2.0-E item 3: overlapping screenshots are de-duplicated, never summed."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import new_session
from app.importer.hebrew import names_match, tokens_match
from app.importer.merge import merge_rows, merge_screenshots
from app.importer.parse import ParsedRow
from app.models import Holding
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]


def mk(symbol: str | None = "AAPL", **kw: Any) -> ParsedRow:
    base: dict[str, Any] = {
        "name": "Apple",
        "symbol": symbol,
        "quantity": 12,
        "price": 200.0,
        "value": 2400.0,
        "currency": "USD",
        "unit": "USD",
    }
    return ParsedRow.model_validate({**base, **kw})


def test_identical_rows_become_one_row_flagged_duplicate_removed() -> None:
    (row,) = merge_screenshots([[mk()], [mk()]])
    assert row.quantity == 12 and row.value == 2400.0  # not 24 / 4800
    assert "duplicate_removed" in row.flags and "conflict" not in row.flags


def test_a_cut_off_copy_loses_to_the_complete_copy_in_either_order() -> None:
    cut = mk(price=None, value=None, quantity=None, name="")
    full = mk()
    for parts in ([[cut], [full]], [[full], [cut]]):
        (row,) = merge_screenshots(parts)
        assert (row.price, row.value, row.quantity) == (200.0, 2400.0, 12)
        assert row.name == "Apple"
        assert "duplicate_removed" in row.flags and "conflict" not in row.flags


def test_two_complete_but_different_copies_the_later_wins_and_the_other_is_reported() -> None:
    early = mk(price=200.0, value=2400.0, quantity=12)
    late = mk(price=210.0, value=2520.0, quantity=12)
    (row,) = merge_screenshots([[early], [late]])
    assert (row.price, row.value) == (210.0, 2520.0)
    assert "conflict" in row.flags
    assert row.conflict is not None
    assert (row.conflict.price, row.conflict.value, row.conflict.quantity) == (200.0, 2400.0, 12)


def test_rows_are_never_summed_and_inputs_are_not_modified() -> None:
    a, b = mk(), mk()
    merge_screenshots([[a], [b]])
    assert a.flags == [] and a.quantity == 12 and b.flags == []


def test_different_securities_are_kept_in_order_of_first_appearance() -> None:
    rows = merge_screenshots([[mk("AAA"), mk("BBB")], [mk("BBB"), mk("CCC")]], reindex=True)
    assert [r.symbol for r in rows] == ["AAA", "BBB", "CCC"]
    assert [r.index for r in rows] == [0, 1, 2]


def test_tase_funds_are_the_same_card_by_number_and_names_are_only_for_unidentified_rows() -> None:
    f1 = mk(None, tase_number="1234567", name="א", currency="ILS", unit="agorot")
    f2 = mk(None, tase_number="1234567", name="ב", currency="ILS", unit="agorot")
    assert len(merge_screenshots([[f1], [f2]])) == 1
    assert len(merge_screenshots([[mk(None, name="Foo Bar")], [mk(None, name="Foo Bar")]])) == 1
    assert len(merge_screenshots([[mk(None, name="Foo")], [mk("FOO", name="Foo")]])) == 2


def test_a_single_screenshot_is_not_merged() -> None:
    assert len(merge_screenshots([[mk(), mk()]])) == 2


def test_hebrew_reversal_tolerant_names() -> None:
    assert tokens_match("אמש", "שמא")  # the whole token reversed
    assert tokens_match("תא125", "תא125")
    assert not tokens_match("abc", "cba")  # Latin tokens must match as written
    assert names_match("מחקה מדד", "דדמ הקחמ")
    assert not names_match("", "")
    assert not names_match("Apple", "apple pie")
    assert names_match("Apple, Inc.", "APPLE inc")


@pytest.fixture
def pc(signup: SignupFn) -> tuple[TestClient, int]:
    c = signup()
    return c, make_portfolio(c)


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "name": "Apple",
        "symbol": "AAPL",
        "quantity": 12,
        "price": 200.0,
        "value": 2400.0,
        "currency": "USD",
        "unit": "USD",
    }
    return {**base, **kw}


def test_api_overlapping_rows_import_without_double_counting(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    r = c.post(
        f"/api/portfolios/{pid}/imports/rows",
        json={"rows": [_row(index=0), _row(index=1)]},
    )
    assert r.status_code == 201, r.text
    rows = r.json()["rows"]
    assert len(rows) == 1 and "duplicate_removed" in rows[0]["flags"]
    assert c.post(f"/api/imports/{r.json()['id']}/confirm").status_code == 200
    with new_session() as db:
        (h,) = db.exec(select(Holding).where(Holding.portfolio_id == pid)).all()
    assert h.quantity == 12


def test_api_conflict_is_reported_in_a_typed_field(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    r = c.post(
        f"/api/portfolios/{pid}/imports/rows",
        json={"rows": [_row(), _row(price=210.0, value=2520.0)]},
    )
    row = r.json()["rows"][0]
    assert row["price"] == 210.0 and "conflict" in row["flags"]
    assert row["conflict"] == {"price": 200.0, "value": 2400.0, "quantity": 12.0}


def test_confirm_never_sums_two_rows_for_the_same_security(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    r = c.post(
        f"/api/portfolios/{pid}/imports/rows",
        json={"rows": [_row(), _row(symbol="MSFT", name="Microsoft", price=400.0, value=4800.0)]},
    )
    draft = r.json()
    rows = draft["rows"]
    rows[1] = {**rows[1], "symbol": "AAPL", "name": "Apple", "price": 200.0, "value": 2400.0}
    rows[1].pop("matched_name")
    patched = c.patch(f"/api/imports/{draft['id']}", json={"rows": rows})
    assert patched.status_code == 200, patched.text
    assert c.post(f"/api/imports/{draft['id']}/confirm").status_code == 200
    with new_session() as db:
        (h,) = db.exec(select(Holding).where(Holding.portfolio_id == pid)).all()
    assert h.quantity == 12  # not 24


def test_merge_rows_keeps_the_first_rows_index() -> None:
    rows = merge_rows([mk(index=4), mk("MSFT", index=8), mk(index=9)])
    assert [(r.symbol, r.index) for r in rows] == [("AAPL", 4), ("MSFT", 8)]
