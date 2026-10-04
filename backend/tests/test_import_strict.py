"""Strict import bodies (2.0-A item 2): no NaN/Infinity, bounded text, one unit/currency rule."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import new_session
from app.importer.parse import ParsedRow
from app.models import Holding
from tests.test_api_portfolio import make_portfolio

SignupFn = Callable[..., TestClient]
ROW: dict[str, Any] = {
    "name": "Apple",
    "symbol": "AAPL",
    "quantity": 10,
    "price": 200.0,
    "value": 2000.0,
    "currency": "USD",
    "unit": "USD",
}


def _post_rows(c: TestClient, pid: int, rows: list[dict[str, Any]]) -> Any:
    return c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows})


def _raw(c: TestClient, method: str, path: str, raw: str) -> Any:
    return c.request(method, path, content=raw, headers={"content-type": "application/json"})


@pytest.fixture
def pc(signup: SignupFn) -> tuple[TestClient, int]:
    c = signup()
    return c, make_portfolio(c)


def test_valid_rows_still_pass(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    r = _post_rows(c, pid, [ROW])
    assert r.status_code == 201, r.text
    assert r.json()["rows"][0]["symbol"] == "AAPL"


@pytest.mark.parametrize(
    "patch",
    [
        {"quantity": 1e308},
        {"value": 1e308},
        {"price": -1},
        {"quantity": -5},
        {"index": 10**9},
        {"currency": "USD" * 40_000},  # ~100 KB
        {"currency": "EUR"},
        {"tase_number": "1" * 1024},
        {"tase_number": "12ab"},
        {"tase_number": "1234"},
        {"symbol": "A" * 500},
        {"symbol": "A B"},
        {"currency": "USD", "unit": "ILS"},
        {"currency": "USD", "unit": "agorot"},
        {"currency": "ILS", "unit": "USD"},
        {"matched_name": "x" * 5000},
    ],
)
def test_bad_row_fields_are_422(pc: tuple[TestClient, int], patch: dict[str, Any]) -> None:
    c, pid = pc
    r = _post_rows(c, pid, [{**ROW, **patch}])
    assert r.status_code == 422, (patch.keys(), r.text[:200])
    assert len(r.text) < 5000  # the error does not echo a 100 KB input back


def test_nan_and_infinity_are_rejected_in_every_json_body(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    base = '{"rows":[{"name":"Apple","symbol":"AAPL","quantity":10,"price":200,"value":%s,"currency":"USD","unit":"USD"}]}'
    for token in ("Infinity", "-Infinity", "NaN", "1e999"):
        r = _raw(c, "POST", f"/api/portfolios/{pid}/imports/rows", base % token)
        assert r.status_code == 422, (token, r.text[:200])
    # not just imports: every JSON body goes through the same strict parser
    r = _raw(c, "POST", f"/api/portfolios/{pid}/holdings", '{"symbol":"AAPL","quantity":NaN}')
    assert r.status_code == 422
    r = _raw(c, "POST", "/api/portfolios", '{"name":"x","base_currency":"ILS","extra":Infinity}')
    assert r.status_code == 422
    r = _raw(c, "POST", "/api/alerts", '{"symbol":"AAPL","op":"above","price":Infinity}')
    assert r.status_code == 422
    # and finite JSON is unaffected
    r = _raw(c, "POST", f"/api/portfolios/{pid}/holdings", '{"symbol":"AAPL","quantity":1.5e1}')
    assert r.status_code == 201, r.text


def test_proposed_change_bounds_and_currency(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    d = _post_rows(c, pid, [ROW]).json()
    url = f"/api/imports/{d['id']}"
    change = {
        "row_index": 0,
        "symbol": "AAPL",
        "type": "buy",
        "quantity": 5,
        "amount": 100.0,
        "currency": "USD",
    }
    assert c.patch(url, json={"proposed_changes": [change]}).status_code == 200
    for bad in (
        {"currency": "EUR"},
        {"currency": "U" * 100_000},
        {"quantity": 1e308},
        {"amount": 1e308},
        {"row_index": -5},
        {"symbol": "x" * 300},
    ):
        assert c.patch(url, json={"proposed_changes": [{**change, **bad}]}).status_code == 422, bad
    assert _raw(c, "PATCH", url, '{"proposed_changes":[{**}]}').status_code == 422
    raw = '{"proposed_changes":[{"row_index":0,"symbol":"AAPL","type":"buy","quantity":1,"amount":Infinity,"currency":"USD"}]}'
    assert _raw(c, "PATCH", url, raw).status_code == 422


def test_patch_rows_are_capped_at_200(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    d = _post_rows(c, pid, [ROW]).json()
    url = f"/api/imports/{d['id']}"
    assert c.patch(url, json={"rows": [ROW] * 200}).status_code == 200
    assert c.patch(url, json={"rows": [ROW] * 201}).status_code == 422


def test_patch_route_is_rate_limited(
    pc: tuple[TestClient, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMPORT_EDIT_RATE_LIMIT_PER_HOUR", "3")
    get_settings.cache_clear()
    c, pid = pc
    d = _post_rows(c, pid, [ROW]).json()
    url = f"/api/imports/{d['id']}"
    codes = [c.patch(url, json={"rows": [ROW]}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_names_are_capped_and_digit_runs_masked_server_side(pc: tuple[TestClient, int]) -> None:
    c, pid = pc
    name = "Apple Inc 123456789 account 0012345 and 12345 " + "x" * 400
    out = _post_rows(c, pid, [{**ROW, "symbol": None, "name": name}]).json()["rows"][0]
    assert len(out["name"]) <= 200
    assert "123456789" not in out["name"] and "0012345" not in out["name"]
    assert "12345" in out["name"]  # five digits are not an ID
    # the same rule on the patch path
    d = _post_rows(c, pid, [ROW]).json()
    got = c.patch(f"/api/imports/{d['id']}", json={"rows": [{**ROW, "name": "Acct 9876543210"}]})
    assert "9876543210" not in got.json()["rows"][0]["name"]


def test_usd_currency_with_ils_unit_cannot_be_stored_as_an_ils_cost(
    pc: tuple[TestClient, int],
) -> None:
    """The on-device path used to confirm `USD` + unit `ILS` as an ILS cost, flag-free."""
    c, pid = pc
    bad = {**ROW, "cost": 150.0, "currency": "USD", "unit": "ILS"}
    assert _post_rows(c, pid, [bad]).status_code == 422
    d = _post_rows(c, pid, [{**ROW, "cost": 150.0}]).json()
    assert c.post(f"/api/imports/{d['id']}/confirm").status_code == 200
    with new_session() as db:
        h = db.exec(select(Holding)).one()
        assert (h.avg_cost, h.cost_currency) == (150.0, "USD")


def test_confirm_and_match_agree_on_the_row_currency() -> None:
    from app.importer.parse import row_currency

    assert row_currency(ParsedRow(currency="ILS", unit="agorot")) == "ILS"
    assert row_currency(ParsedRow(currency="USD", unit="USD")) == "USD"
    with pytest.raises(ValueError):
        ParsedRow(currency="USD", unit="agorot")
