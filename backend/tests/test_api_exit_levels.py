"""GET /api/holdings/{id}/exit-levels and POST /api/portfolios/{id}/exit-review (fixtures only)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.verdict_words import verdict_words_in_text
from tests.conftest import FakeHistory, FakeQuotes
from tests.test_exit_levels import frame

SignupFn = Callable[..., TestClient]


@pytest.fixture
def setup(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory
) -> tuple[TestClient, int, dict[str, int]]:
    df = frame()
    price = float(df["Close"].iloc[-1])
    history.frames["AAPL"] = df
    history.frames["MSFT"] = df
    quotes.set("AAPL", price, "USD")
    quotes.set("MSFT", price, "USD")
    c = signup("a@mail.com")
    pid = c.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    ids: dict[str, int] = {}
    for sym, extra in (("AAPL", {"horizon": "1m"}), ("MSFT", {})):
        r = c.post(
            f"/api/portfolios/{pid}/holdings",
            json={"symbol": sym, "quantity": 10, "avg_cost": price - 20, **extra},
        )
        assert r.status_code == 201, r.text
        ids[sym] = r.json()["id"]
    return c, pid, ids


def test_a_holding_without_a_horizon_answers_needs_horizon(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, _, ids = setup
    r = c.get(f"/api/holdings/{ids['MSFT']}/exit-levels")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "needs_horizon" and body["stop"] is None
    # an explicit what-if horizon computes levels but never fills the holding's horizon in
    what_if = c.get(f"/api/holdings/{ids['MSFT']}/exit-levels", params={"horizon": "3m"}).json()
    assert what_if["status"] == "levels" and what_if["horizon"] == "3m"
    again = c.get(f"/api/holdings/{ids['MSFT']}/exit-levels").json()
    assert again["status"] == "needs_horizon"


def test_levels_for_a_holding_with_a_horizon_carry_reasons_and_explanations(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, _, ids = setup
    body = c.get(f"/api/holdings/{ids['AAPL']}/exit-levels").json()
    assert body["status"] == "levels" and body["horizon"] == "1m"
    stop = body["stop"]
    assert 0 < stop["price"] < body["price"] and stop["reason"]
    assert stop["explanation"]["summary"] and stop["explanation"]["sources"]
    assert body["disclaimer"] == "Not financial advice."
    assert body["size_guidance"]["keep_fraction"] <= 1.0
    for text in (body["reason"], stop["reason"], body["explanation"]["summary"]):
        assert verdict_words_in_text(text) == []


def test_risk_preset_is_validated_and_changes_the_result(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, _, ids = setup
    url = f"/api/holdings/{ids['AAPL']}/exit-levels"
    assert c.get(url, params={"risk": "nope"}).status_code == 422
    assert c.get(url, params={"horizon": "2y"}).status_code == 422
    assert c.get(url, params={"prior_stop": "-1"}).status_code == 422
    assert c.get(url, params={"prior_stop": "nan"}).status_code == 422
    cons = c.get(url, params={"risk": "very_conservative"}).json()
    aggr = c.get(url, params={"risk": "very_aggressive"}).json()
    assert cons["risk_preset"] == "very_conservative" and aggr["risk_preset"] == "very_aggressive"
    assert aggr["stop"]["price"] < cons["stop"]["price"]


def test_a_prior_stop_is_never_lowered(setup: tuple[TestClient, int, dict[str, int]]) -> None:
    c, _, ids = setup
    url = f"/api/holdings/{ids['AAPL']}/exit-levels"
    base = c.get(url).json()
    saved = base["stop"]["price"] + 2.0
    kept = c.get(url, params={"prior_stop": saved}).json()
    assert kept["stop"]["price"] == pytest.approx(saved)
    low = c.get(url, params={"prior_stop": base["stop"]["price"] - 5}).json()
    assert low["stop"]["price"] == base["stop"]["price"]


def test_analyst_targets_are_an_optional_query_input(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, _, ids = setup
    url = f"/api/holdings/{ids['AAPL']}/exit-levels"
    price = c.get(url, params={"horizon": "3m"}).json()["price"]
    with_t = c.get(url, params={"horizon": "3m", "analyst_mean": price * 1.5}).json()
    assert any(t["source"] == "analyst_mean_target" for t in with_t["take_profits"])


def test_no_history_or_no_price_answers_no_levels_not_an_error(
    setup: tuple[TestClient, int, dict[str, int]], history: FakeHistory, quotes: FakeQuotes
) -> None:
    c, pid, ids = setup
    history.frames.pop("AAPL")
    body = c.get(f"/api/holdings/{ids['AAPL']}/exit-levels").json()
    assert body["status"] == "no_levels" and body["reason_code"] == "no_history"
    # a holding that has no market price at all (cost placeholder)
    r = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "NVDA", "quantity": 1, "avg_cost": 5, "horizon": "1m"},
    )
    assert r.status_code == 201, r.text
    body = c.get(f"/api/holdings/{r.json()['id']}/exit-levels").json()
    assert body["status"] == "no_levels" and body["reason_code"] == "stale_price"
    assert body["stop"] is None and body["take_profits"] == []


def test_review_returns_rows_and_portfolio_totals(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, pid, _ = setup
    r = c.post(f"/api/portfolios/{pid}/exit-review", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {x["symbol"]: x for x in body["rows"]}
    assert body["rows"][0]["symbol"] == "MSFT" and rows["MSFT"]["status"] == "needs_horizon"
    assert rows["AAPL"]["status"] == "levels" and rows["AAPL"]["risk_ils"] > 0
    t = body["totals"]
    assert t["total_risk_ils"] == pytest.approx(rows["AAPL"]["risk_ils"])
    assert (
        t["total_risk_usd"] == pytest.approx(t["total_risk_ils"] / 3.6, rel=0.05, abs=0.1)
        or t["total_risk_usd"] > 0
    )
    assert [x["symbol"] for x in t["top_contributors"]] == ["AAPL"]
    assert [x["symbol"] for x in t["positions_without_stop"]] == ["MSFT"]
    assert t["positions_without_stop"][0]["reason_code"] == "needs_horizon"
    assert 0 < t["total_risk_pct_of_portfolio"] < 100 and t["limit_pct"] > 0
    assert rows["AAPL"]["levels"]["explanation"]["summary"]


def test_review_what_if_horizon_covers_every_holding_and_saves_nothing(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, pid, _ = setup
    body = c.post(
        f"/api/portfolios/{pid}/exit-review", json={"horizon": "3m", "risk": "balanced"}
    ).json()
    assert {x["status"] for x in body["rows"]} == {"levels"}
    assert {x["horizon_source"] for x in body["rows"]} == {"override"}
    assert body["totals"]["positions_without_stop"] == []
    holdings = c.get(f"/api/portfolios/{pid}/holdings").json()
    assert {h["symbol"]: h["horizon"] for h in holdings} == {"AAPL": "1m", "MSFT": None}


def test_review_prior_stops_flag_tight_and_wide_stops(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, pid, _ = setup
    price = c.get(f"/api/portfolios/{pid}/holdings").json()[0]["price"]
    body = c.post(
        f"/api/portfolios/{pid}/exit-review",
        json={"horizon": "1m", "prior_stops": {"AAPL": price - 0.1, "MSFT": price * 0.2}},
    ).json()
    assert body["totals"]["stops_too_tight"] == ["AAPL"]
    assert body["totals"]["stops_too_wide"] == ["MSFT"]


@pytest.mark.parametrize(
    "payload",
    [
        {"surprise": 1},
        {"horizon": "2y"},
        {"risk": "yolo"},
        {"prior_stops": {"AAPL": -1}},
        {"prior_stops": {"AAPL": 0}},
        {"prior_stops": {"not a symbol!": 5}},
        {"analyst_targets": {"AAPL": {"mean": 5, "extra": 1}}},
        {"horizon": None, "risk": 5},
    ],
)
def test_review_body_is_strict(
    setup: tuple[TestClient, int, dict[str, int]], payload: dict[str, object]
) -> None:
    c, pid, _ = setup
    assert c.post(f"/api/portfolios/{pid}/exit-review", json=payload).status_code == 422


def test_review_rejects_non_finite_numbers(setup: tuple[TestClient, int, dict[str, int]]) -> None:
    c, pid, _ = setup
    r = c.post(
        f"/api/portfolios/{pid}/exit-review",
        content='{"prior_stops": {"AAPL": NaN}}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_the_per_holding_risk_override_wins_over_the_portfolio_filter(
    setup: tuple[TestClient, int, dict[str, int]],
) -> None:
    c, pid, ids = setup
    url = f"/api/holdings/{ids['AAPL']}/exit-levels"
    assert c.get(url).json()["risk_preset"] == "balanced_aggressive"
    r = c.patch(
        f"/api/portfolios/{pid}/holdings/{ids['AAPL']}",
        json={"risk_override": {"preset": "conservative"}},
    )
    assert r.status_code == 200, r.text
    assert c.get(url).json()["risk_preset"] == "conservative"
    assert c.get(url, params={"risk": "aggressive"}).json()["risk_preset"] == "aggressive"
