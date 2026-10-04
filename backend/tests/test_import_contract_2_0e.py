"""Block 2.0-E item 1: the contract the on-device Meitav parser needs from the server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import new_session
from app.models import Holding
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]
NEW_FLAGS = {
    "quantity_uncertain",
    "cost_inferred",
    "duplicate_removed",
    "conflict",
    "quantity_fractional",
}
ROW: dict[str, Any] = {
    "name": "Apple",
    "symbol": "AAPL",
    "quantity": 10,
    "price": 200.0,
    "value": 2000.0,
    "currency": "USD",
    "unit": "USD",
}


def _post(c: TestClient, pid: int, rows: list[dict[str, Any]], **extra: Any) -> Any:
    return c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows, **extra})


@pytest.fixture
def pc(signup: SignupFn) -> tuple[TestClient, int]:
    c = signup()
    return c, make_portfolio(c)


def test_openapi_lists_the_new_import_flags() -> None:
    from app.main import create_app

    flags = create_app().openapi()["components"]["schemas"]["ImportRowModel"]["properties"]["flags"]
    assert set(flags["items"]["enum"]) >= NEW_FLAGS


def test_new_flags_are_accepted_and_kept(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    sent = ["cost_inferred", "quantity_fractional", "duplicate_removed"]
    r = _post(c, pid, [{**ROW, "flags": sent}])
    assert r.status_code == 201, r.text
    assert set(sent) <= set(r.json()["rows"][0]["flags"])
    assert _post(c, pid, [{**ROW, "flags": ["bogus_flag"]}]).status_code == 422


def test_index_is_echoed_unchanged_on_create_and_patch(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    msft = {**ROW, "name": "Microsoft", "symbol": "MSFT", "index": 9}
    junk = {  # a line that is not a stock row is dropped; the others keep their own index
        "name": "account 12",
        "quantity": None,
        "price": None,
        "value": None,
        "currency": "USD",
        "unit": "USD",
        "index": 7,
    }
    r = _post(c, pid, [{**ROW, "index": 5}, junk, msft])
    assert r.status_code == 201, r.text
    assert [x["index"] for x in r.json()["rows"]] == [5, 9]
    draft = r.json()
    p = c.patch(f"/api/imports/{draft['id']}", json={"rows": draft["rows"]})
    assert p.status_code == 200, p.text
    assert [x["index"] for x in p.json()["rows"]] == [5, 9]


def test_rows_without_an_index_are_numbered_in_order(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    r = _post(c, pid, [ROW, {**ROW, "symbol": "MSFT", "name": "Microsoft"}])
    assert [x["index"] for x in r.json()["rows"]] == [0, 1]


def test_duplicate_explicit_indexes_are_a_422(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    r = _post(c, pid, [{**ROW, "index": 3}, {**ROW, "symbol": "MSFT", "index": 3}])
    assert r.status_code == 422


def test_null_quantity_is_valid_flagged_and_blocks_confirm_until_set(
    pc: tuple[TestClient, int],
) -> None:
    c, pid = pc
    tiny = {**ROW, "quantity": None, "price": 0.0107, "value": 0.03}
    r = _post(c, pid, [tiny])
    assert r.status_code == 201, r.text
    row = r.json()["rows"][0]
    assert row["quantity"] is None
    assert "quantity_uncertain" in row["flags"]
    draft_id = r.json()["id"]
    blocked = c.post(f"/api/imports/{draft_id}/confirm")
    assert blocked.status_code == 422 and "quantity" in blocked.text
    fixed = c.patch(f"/api/imports/{draft_id}", json={"rows": [{**row, "quantity": 3}]})
    assert fixed.status_code == 200, fixed.text
    assert "quantity_uncertain" not in fixed.json()["rows"][0]["flags"]
    assert c.post(f"/api/imports/{draft_id}/confirm").status_code == 200
    # a quantity of zero is still not a quantity
    r2 = _post(c, pid, [tiny])
    row2 = r2.json()["rows"][0]
    z = c.patch(f"/api/imports/{r2.json()['id']}", json={"rows": [{**row2, "quantity": 0}]})
    assert z.status_code == 200
    assert c.post(f"/api/imports/{r2.json()['id']}/confirm").status_code == 422


def test_cost_is_in_the_unit_of_price_agorot_for_tlv_rows(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    row = {
        "name": "טבע",
        "symbol": "TEVA.TA",
        "quantity": 100,
        "price": 6272,
        "value": 6272.0,
        "cost": 6020,
        "currency": "ILS",
        "unit": "agorot",
    }
    r = _post(c, pid, [row])
    assert r.status_code == 201, r.text
    assert c.post(f"/api/imports/{r.json()['id']}/confirm").status_code == 200
    with new_session() as db:
        from sqlmodel import select

        h = db.exec(select(Holding).where(Holding.portfolio_id == pid)).one()
    assert h.avg_cost == pytest.approx(60.2)
    assert h.cost_currency == "ILS"


@pytest.mark.parametrize(
    "patch",
    [
        {"quantity": 1e308},
        {"price": 1e308},
        {"conflict": {"price": 1e308}},
        {"conflict": {"value": -1}},
        {"conflict": {"quantity": 1e308}},
        {"conflict": {"unknown": 1}},
        {"exchange": "LSE"},
        {"flags": ["quantity_uncertain"] * 13},
    ],
)
def test_strict_bounds_still_reject_out_of_range_numbers(
    pc: tuple[TestClient, int], patch: dict[str, Any]
) -> None:
    c, pid = pc
    assert _post(c, pid, [{**ROW, **patch}]).status_code == 422


def test_conflict_field_is_typed_in_openapi() -> None:
    from app.main import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    assert {"price", "value", "quantity"} <= set(schemas["RowConflict"]["properties"])
