"""Toggleable X-ray rules: defaults, switching, overrides within bounds, strict bodies, scoping,
export and cascade. Informational only: nothing is ever blocked."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import new_session
from tests.conftest import FakeQuotes
from tests.test_api_portfolio import make_portfolio, set_fx

SignupFn = Callable[..., TestClient]
PW = "correct horse battery"


def _setup(
    signup: SignupFn, quotes: FakeQuotes, email: str = "alice@mail.com"
) -> tuple[TestClient, int]:
    set_fx(3.5)
    quotes.set("NVDA", 100.0, "USD", 3.0)
    quotes.set("TEVA.TA", 65.0, "ILS", -1.0)
    c = signup(email)
    pid = make_portfolio(c)
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "NVDA", "quantity": 100})
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "TEVA.TA", "quantity": 10})
    return c, pid


def _rules(c: TestClient, pid: int) -> dict[str, Any]:
    return {r["rule"]: r for r in c.get(f"/api/portfolios/{pid}/xray").json()["rules"]}


def test_defaults_all_on_with_thresholds_from_the_risk_filter(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c, pid = _setup(signup, quotes)
    c.patch(f"/api/portfolios/{pid}", json={"risk_filter": {"preset": "conservative"}})
    g = c.get(f"/api/portfolios/{pid}/xray-rules").json()["rules"]
    assert [r["rule"] for r in g] == ["concentration", "currency", "country_home", "sector"]
    assert all(r["enabled"] and r["override_pct"] is None for r in g)
    by = {r["rule"]: r for r in g}
    assert (by["concentration"]["threshold_pct"], by["concentration"]["threshold_source"]) == (
        8,
        "risk_filter",
    )
    assert by["sector"]["threshold_pct"] == 25 and by["country_home"]["threshold_pct"] == 65
    assert by["currency"]["threshold_pct"] is None
    assert by["currency"]["threshold_source"] == "none"
    assert by["currency"]["default_threshold_pct"] is None
    res = _rules(c, pid)
    assert {k: v["state"] for k, v in res.items()} == {
        "concentration": "breach", "currency": "ok", "country_home": "breach",
        "sector": "breach",
    }  # fmt: skip
    ex = res["concentration"]["explanation"]
    assert ex["summary"] and ex["rules_applied"] and ex["sources"] and ex["invalidation_risks"]
    assert res["concentration"]["items"][0]["name"] == "NVDA"
    # informational only: the same request still works and nothing else changed
    assert c.get(f"/api/portfolios/{pid}/holdings").status_code == 200


def test_switch_off_and_back_on_and_override(signup: SignupFn, quotes: FakeQuotes) -> None:
    c, pid = _setup(signup, quotes)
    url = f"/api/portfolios/{pid}/xray-rules"
    r = c.patch(url, json={"rules": [{"rule": "sector", "enabled": False}]})
    assert r.status_code == 200
    assert _rules(c, pid)["sector"]["state"] == "off"
    assert _rules(c, pid)["sector"]["value_pct"] is None
    assert "switched off" in _rules(c, pid)["sector"]["explanation"]["summary"]
    # override: a looser concentration limit makes the rule ok
    c.patch(url, json={"rules": [{"rule": "concentration", "threshold_pct": 99}]})
    row = _rules(c, pid)["concentration"]
    assert row["state"] == "ok" and row["threshold_source"] == "override"
    # clearing the override and re-enabling removes the stored rows
    c.patch(url, json={"rules": [{"rule": "concentration", "threshold_pct": None},
                                 {"rule": "sector", "enabled": True}]})  # fmt: skip
    with new_session() as db:
        assert db.connection().execute(text("SELECT count(*) FROM xray_rule_setting")).scalar() == 0
    assert _rules(c, pid)["concentration"]["threshold_source"] == "risk_filter"


def test_bounds_and_strict_bodies(signup: SignupFn, quotes: FakeQuotes) -> None:
    c, pid = _setup(signup, quotes)
    url = f"/api/portfolios/{pid}/xray-rules"
    low = c.patch(url, json={"rules": [{"rule": "currency", "threshold_pct": 5}]})
    assert low.status_code == 422 and low.json()["code"] == "threshold_out_of_bounds"
    assert low.json()["min_pct"] == 10
    for body in (
        {"rules": [{"rule": "bogus", "enabled": True}]},
        {"rules": [{"rule": "sector"}]},
        {"rules": [{"rule": "sector", "enabled": "yes"}]},
        {"rules": [{"rule": "sector", "enabled": None}]},
        {"rules": [{"rule": "sector", "enabled": True, "extra": 1}]},
        {"rules": [{"rule": "sector", "threshold_pct": 0}]},
        {"rules": [{"rule": "sector", "threshold_pct": 101}]},
        {"rules": [{"rule": "sector", "threshold_pct": True}]},
        {"rules": [{"rule": "sector", "enabled": True}, {"rule": "sector", "enabled": False}]},
        {"rules": []},
        {"rules": [{"rule": "sector", "enabled": True}], "other": 1},
        {},
    ):
        assert c.patch(url, json=body).status_code == 422, body
    # a rejected patch changes nothing (good item + bad bound)
    bad = c.patch(url, json={"rules": [{"rule": "sector", "enabled": False},
                                       {"rule": "currency", "threshold_pct": 5}]})  # fmt: skip
    assert bad.status_code == 422
    assert _rules(c, pid)["sector"]["state"] != "off"


def test_per_holding_limit_still_wins_for_concentration(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c, pid = _setup(signup, quotes)
    c.patch(
        f"/api/portfolios/{pid}/xray-rules",
        json={"rules": [{"rule": "concentration", "threshold_pct": 20}]},
    )
    assert _rules(c, pid)["concentration"]["state"] == "breach"  # NVDA is ~98%
    nvda = next(h for h in c.get(f"/api/portfolios/{pid}/holdings").json() if h["symbol"] == "NVDA")
    r = c.patch(
        f"/api/portfolios/{pid}/holdings/{nvda['id']}",
        json={"risk_override": {"max_position_pct": 100}},
    )
    assert r.status_code == 200, r.text
    assert _rules(c, pid)["concentration"]["state"] == "ok"  # the per-holding limit wins


def test_scoping_export_and_cascade(signup: SignupFn, quotes: FakeQuotes) -> None:
    a, pid = _setup(signup, quotes)
    b = signup("bob@mail.com")
    a.patch(
        f"/api/portfolios/{pid}/xray-rules", json={"rules": [{"rule": "sector", "enabled": False}]}
    )
    assert b.get(f"/api/portfolios/{pid}/xray-rules").status_code == 404
    assert b.patch(
        f"/api/portfolios/{pid}/xray-rules", json={"rules": [{"rule": "sector", "enabled": False}]}
    ).status_code == 404  # fmt: skip
    mine = a.post("/api/me/export", json={"password": PW}).json()
    assert [(x["rule"], x["enabled"]) for x in mine["portfolios"][0]["xray_rules"]] == [
        ("sector", False)
    ]
    assert a.delete(f"/api/portfolios/{pid}").status_code == 204
    with new_session() as db:  # raw count: the database cascaded
        assert db.connection().execute(text("SELECT count(*) FROM xray_rule_setting")).scalar() == 0


def test_currency_has_no_default_limit_override_breaches_and_clears(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    set_fx(3.5)
    quotes.set("TEVA.TA", 65.0, "ILS", -1.0)
    c = signup("carol@mail.com")
    pid = make_portfolio(c)
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "TEVA.TA", "quantity": 10})
    url = f"/api/portfolios/{pid}/xray-rules"
    cur = _rules(c, pid)["currency"]  # all ILS: still no breach by default
    assert cur["state"] == "ok" and cur["threshold_pct"] is None
    assert [i["name"] for i in cur["items"]] == ["ILS"] and cur["items"][0]["value_pct"] > 99
    assert "no limit" in cur["explanation"]["summary"]
    r = c.patch(url, json={"rules": [{"rule": "currency", "threshold_pct": 50}]})
    assert r.status_code == 200
    cur = _rules(c, pid)["currency"]
    assert cur["state"] == "breach" and cur["threshold_source"] == "override"
    got = {x["rule"]: x for x in c.get(url).json()["rules"]}["currency"]
    assert got["threshold_pct"] == 50 and got["override_pct"] == 50
    c.patch(url, json={"rules": [{"rule": "currency", "threshold_pct": None}]})
    cur = _rules(c, pid)["currency"]
    assert cur["state"] == "ok" and cur["threshold_pct"] is None
    assert cur["threshold_source"] == "none"
