"""The user-set expected return: nullable, never defaulted, strict bounds, exported."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

SignupFn = Callable[..., TestClient]


def _pid(c: TestClient) -> int:
    return int(c.post("/api/portfolios", json={"name": "P", "base_currency": "ILS"}).json()["id"])


def test_expectation_is_null_until_the_user_sets_it(signup: SignupFn) -> None:
    c = signup("a@mail.com")
    pid = _pid(c)
    out = c.get(f"/api/portfolios/{pid}").json()
    assert out["expected_return_pct"] is None and out["expected_return_horizon_months"] is None
    r = c.patch(
        f"/api/portfolios/{pid}",
        json={"expected_return_pct": 8.5, "expected_return_horizon_months": 12},
    )
    assert r.status_code == 200
    assert (r.json()["expected_return_pct"], r.json()["expected_return_horizon_months"]) == (
        8.5,
        12,
    )
    assert (
        c.patch(f"/api/portfolios/{pid}", json={"name": "Q"}).json()["expected_return_pct"] == 8.5
    )
    cleared = c.patch(
        f"/api/portfolios/{pid}",
        json={"expected_return_pct": None, "expected_return_horizon_months": None},
    ).json()
    assert cleared["expected_return_pct"] is None
    assert cleared["expected_return_horizon_months"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"expected_return_pct": 8.5},  # a pair, never one half
        {"expected_return_horizon_months": 12},
        {"expected_return_pct": 8.5, "expected_return_horizon_months": None},
        {"expected_return_pct": 501, "expected_return_horizon_months": 12},
        {"expected_return_pct": -100.5, "expected_return_horizon_months": 12},
        {"expected_return_pct": 5, "expected_return_horizon_months": 0},
        {"expected_return_pct": 5, "expected_return_horizon_months": 121},
        {"expected_return_pct": 5, "expected_return_horizon_months": 1.5},
        {"expected_return_pct": "5", "expected_return_horizon_months": 12},
        {"expected_return_pct": True, "expected_return_horizon_months": 12},
    ],
)
def test_expectation_bounds_are_strict(signup: SignupFn, body: dict[str, Any]) -> None:
    c = signup("a@mail.com")
    pid = _pid(c)
    assert c.patch(f"/api/portfolios/{pid}", json=body).status_code == 422
    assert c.get(f"/api/portfolios/{pid}").json()["expected_return_pct"] is None


def test_expectation_is_in_the_export(signup: SignupFn) -> None:
    c = signup("a@mail.com")
    pid = _pid(c)
    c.patch(
        f"/api/portfolios/{pid}",
        json={"expected_return_pct": 10, "expected_return_horizon_months": 6},
    )
    mine = c.post("/api/me/export", json={"password": "correct horse battery"}).json()
    p = mine["portfolios"][0]
    assert (p["expected_return_pct"], p["expected_return_horizon_months"]) == (10, 6)
