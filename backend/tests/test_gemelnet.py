"""GemelNet provider, fund returns, fund holdings and the fund API (fixtures only, no network).

The fixtures in `tests/fixtures/gemelnet/` are hand-built in the CKAN `datastore_search` shape with
the column names in `Settings.gemelnet_fields`. Those names and the resource id are UNVERIFIED
against the live dataset (the sandbox cannot reach data.gov.il); a live check belongs behind
`@pytest.mark.live`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import new_session
from app.funds import compute_returns, data_is_stale, fund_id_of, fund_symbol, is_fund_symbol
from app.models import FundHolding, Holding, Transaction
from app.providers.base import FundProvider, FundSeries
from app.providers.gemelnet import GemelNetProvider, parse_period
from app.providers.registry import Providers, set_providers
from app.timeutil import local_today
from tests.conftest import FakeHistory, FakeQuotes

FIX = Path(__file__).parent / "fixtures" / "gemelnet"
RID = "00000000-0000-0000-0000-000000000001"
SignupFn = Callable[..., TestClient]


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text(encoding="utf-8"))
    return data


class Upstream:
    """A MockTransport that answers like data.gov.il, from the fixtures, and counts its calls."""

    def __init__(self, status: int = 200) -> None:
        self.calls: list[dict[str, str]] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        if self.status != 200:
            return httpx.Response(self.status, json={"success": False})
        assert request.url.path.endswith("/datastore_search")
        assert params["resource_id"] == RID
        filters = json.loads(params["filters"]) if "filters" in params else {}
        if "FUND_CLASSIFICATION" in filters:
            return httpx.Response(200, json=load("category_peers_2026_09.json"))
        fid = filters.get("FUND_ID")
        if fid == "1001":
            return httpx.Response(200, json=load("fund_1001_series.json"))
        if fid == "2002":
            return httpx.Response(200, json=load("fund_2002_series.json"))
        if fid is not None:
            return httpx.Response(200, json=load("empty.json"))
        if params.get("q") == "boom":
            return httpx.Response(200, json=load("error.json"))
        if params.get("q") == "nothing":
            return httpx.Response(200, json=load("empty.json"))
        return httpx.Response(200, json=load("search_example.json"))


def make_provider(
    upstream: Upstream | None = None, *, configured: bool = True, per_minute: int | None = None
) -> tuple[GemelNetProvider, Upstream]:
    up = upstream or Upstream()
    settings = Settings(
        gemelnet_resource_ids={"monthly_returns": RID} if configured else {},
    )
    if per_minute is not None:
        limits = dict(settings.quote_source_limits)
        limits["gemelnet"] = limits["gemelnet"].model_copy(
            update={"max_calls_per_minute": per_minute}
        )
        settings = settings.model_copy(update={"quote_source_limits": limits})
    client = httpx.Client(transport=httpx.MockTransport(up))
    return GemelNetProvider(settings, client=client), up


def compound(yields: list[float]) -> float:
    return round((math.prod(1 + y / 100 for y in yields) - 1) * 100, 4)


# ---------------------------------------------------------------- symbols and parsing
def test_fund_symbol_helpers() -> None:
    assert (
        is_fund_symbol("GEMEL-1001") and not is_fund_symbol("GEMEL-") and not is_fund_symbol("AAPL")
    )
    assert not is_fund_symbol("GEMEL-1001.TA") and not is_fund_symbol("GEMEL-12345678901")
    assert fund_id_of("gemel-1001") == "1001" and fund_symbol("1001") == "GEMEL-1001"


@pytest.mark.parametrize(
    ("raw", "want"),
    [(202609, "2026-09"), ("202609", "2026-09"), ("2026-09", "2026-09"), ("2026-09-30T00:00:00", "2026-09"),
     ("09/2026", "2026-09"), (202613, None), ("junk", None), (None, None), (True, None)],
)  # fmt: skip
def test_parse_period(raw: object, want: str | None) -> None:
    assert parse_period(raw) == want


# ---------------------------------------------------------------- provider
def test_get_fund_parses_series_ascending_and_labels_source() -> None:
    p, _ = make_provider()
    f = p.get_fund("1001")
    assert f.value is not None and f.source == "gemelnet" and f.as_of is not None
    s = f.value
    assert s.info.name.startswith("קרן השתלמות") and s.info.classification == "קרנות השתלמות"
    assert (
        len(s.months) == 40 and s.months[0].period == "2023-06" and s.months[-1].period == "2026-09"
    )
    assert [m.period for m in s.months] == sorted(m.period for m in s.months)
    assert f.as_of.date() == date(2026, 9, 30)  # end of the latest reporting month
    assert s.months[-1].total_assets == 12345.6 and s.months[-1].management_fee_pct == 0.62


def test_category_average_needs_enough_peers() -> None:
    p, _ = make_provider()
    s = p.get_fund("1001").value
    assert s is not None
    assert s.category_period == "2026-09" and s.category_peer_count == 5
    assert s.category_avg_monthly_return_pct == pytest.approx(0.6)
    # fewer peers than the configured minimum: no average, never a made-up one
    settings = Settings(
        gemelnet_resource_ids={"monthly_returns": RID}, gemelnet_category_min_peers=9
    )
    p2 = GemelNetProvider(settings, client=httpx.Client(transport=httpx.MockTransport(Upstream())))
    s2 = p2.get_fund("1001").value
    assert (
        s2 is not None
        and s2.category_avg_monthly_return_pct is None
        and s2.category_peer_count == 0
    )


def test_returns_compound_the_monthly_series() -> None:
    p, _ = make_provider()
    s = p.get_fund("1001").value
    assert s is not None
    ys = load("fund_1001_expected.json")["yields"]
    r = {x.horizon: x for x in compute_returns(s)}
    assert r["1m"].return_pct == ys[-1]
    assert r["3m"].return_pct == compound(ys[-3:])
    assert r["1y"].return_pct == compound(ys[-12:]) and r["1y"].annualised_pct == r["1y"].return_pct
    assert r["3y"].return_pct == compound(ys[-36:])
    assert r["3y"].annualised_pct == pytest.approx(
        ((1 + r["3y"].return_pct / 100) ** (1 / 3) - 1) * 100, abs=1e-3
    )  # type: ignore[operator]
    assert r["1m"].category_avg_pct == pytest.approx(0.6) and r[
        "1m"
    ].vs_category_pts == pytest.approx(0.4)
    assert r["3m"].category_avg_pct is None  # the dataset gives peers per month: 1m only


def test_gaps_are_flagged_never_filled_with_zero() -> None:
    p, _ = make_provider()
    s = p.get_fund("2002").value
    assert s is not None and "2026-02" not in {m.period for m in s.months}
    r = {x.horizon: x for x in compute_returns(s)}
    assert r["1m"].return_pct == 0.3
    assert r["3m"].return_pct == compound([0.3, 0.3, 0.3])
    # 2026-05 has an empty value: any window that contains it is a gap, not 0
    s_gap = FundSeries.model_validate(s.model_dump())
    window = {m.period: m.monthly_return_pct for m in s_gap.months[-5:]}
    assert window["2026-05"] is None
    assert r["1y"].return_pct is None and r["1y"].missing_reason == "gap_in_series"
    assert r["3y"].return_pct is None and r["3y"].missing_reason == "not_enough_months"


def test_unknown_fund_empty_error_and_bad_id_are_honest_gaps() -> None:
    p, up = make_provider()
    assert p.get_fund("999999").missing_reason == "not_found"
    assert (
        p.get_fund("12a").missing_reason == "not_found" and len(up.calls) == 1
    )  # no call for junk
    assert p.search_funds("nothing").missing_reason == "not_found"
    assert p.search_funds("boom").missing_reason == "unavailable"  # success: false
    assert p.search_funds("x").missing_reason == "not_found"  # shorter than the minimum


def test_not_configured_means_unavailable_without_any_call() -> None:
    p, up = make_provider(configured=False)
    assert p.get_fund("1001").missing_reason == "unavailable"
    assert p.search_funds("קרן").missing_reason == "unavailable"
    assert up.calls == []


def test_search_dedupes_funds_and_uses_filter_for_a_number() -> None:
    p, up = make_provider()
    f = p.search_funds("קרן השתלמות")
    assert f.value is not None and [x.fund_id for x in f.value] == ["1001", "1002"]
    assert "q" in up.calls[0] and "filters" not in up.calls[0]
    p.search_funds("1001")
    assert json.loads(up.calls[1]["filters"]) == {"FUND_ID": "1001"}


def test_cache_and_rate_limit_and_breaker() -> None:
    p, up = make_provider()
    p.get_fund("1001")
    n = len(up.calls)
    p.get_fund("1001")
    assert len(up.calls) == n  # TTL cache: no second round trip
    # a 429 trips the breaker: later calls make no request and say rate_limited
    p2, up2 = make_provider(Upstream(status=429))
    assert p2.get_fund("1001").missing_reason == "rate_limited"
    assert p2.get_fund("1002").missing_reason == "rate_limited" and len(up2.calls) == 1
    # a 500 is just unavailable
    p3, _ = make_provider(Upstream(status=500))
    assert p3.search_funds("קרן").missing_reason == "unavailable"
    # our own per-minute budget
    p4, up4 = make_provider(per_minute=1)
    p4.search_funds("aa")
    assert p4.search_funds("bb").missing_reason == "rate_limited" and len(up4.calls) == 1


def test_provider_satisfies_the_protocol() -> None:
    p, _ = make_provider()
    assert isinstance(p, FundProvider)


def test_stale_monthly_data_is_flagged() -> None:
    p, _ = make_provider()
    s = p.get_fund("1001").value
    assert s is not None
    cfg = Settings()
    assert not data_is_stale(s, date(2026, 10, 20), cfg)
    assert data_is_stale(s, date(2027, 2, 1), cfg)


# ---------------------------------------------------------------- API
@pytest.fixture
def funds_client(signup: SignupFn, providers: Providers) -> tuple[SignupFn, GemelNetProvider]:
    p, _ = make_provider()
    set_providers(
        Providers(quotes=providers.quotes, history=providers.history, funds=p)  # type: ignore[arg-type]
    )
    return signup, p


def test_search_and_detail_endpoints(funds_client: tuple[SignupFn, GemelNetProvider]) -> None:
    c = funds_client[0]("f@mail.com")
    r = c.get("/api/funds/search", params={"q": "קרן"})
    assert r.status_code == 200
    body = r.json()
    assert body["data_status"] == "ok" and body["source"] == "gemelnet"
    assert [x["symbol"] for x in body["results"]] == ["GEMEL-1001", "GEMEL-1002"]
    assert "GemelNet" in body["credit"] and "non-commercial" in body["credit"]
    none = c.get("/api/funds/search", params={"q": "nothing"}).json()
    assert none["data_status"] == "no_data" and none["results"] == []
    d = c.get("/api/funds/1001")
    assert d.status_code == 200
    j = d.json()
    assert (
        j["symbol"] == "GEMEL-1001"
        and j["latest_period"] == "2026-09"
        and j["price_available"] is False
    )
    assert {x["horizon"] for x in j["returns"]} == {"1m", "3m", "1y", "3y"}
    assert (
        j["returns"][0]["vs_category_pts"] == pytest.approx(0.4) and j["category_peer_count"] == 5
    )
    assert len(j["monthly_series"]) == 40 and j["total_assets"] == 12345.6
    assert c.get("/api/funds/777777").status_code == 404
    assert c.get("/api/funds/abc").status_code == 422
    assert c.get("/api/funds/search").status_code == 422


def test_fund_endpoints_need_login_and_report_unavailable(client: TestClient) -> None:
    assert client.get("/api/funds/search?q=abc").status_code == 401
    assert client.get("/api/funds/1001").status_code == 401


def test_detail_when_dataset_is_down(signup: SignupFn, providers: Providers) -> None:
    p, _ = make_provider(configured=False)
    set_providers(Providers(quotes=providers.quotes, history=providers.history, funds=p))  # type: ignore[arg-type]
    c = signup("down@mail.com")
    assert c.get("/api/funds/1001").status_code == 503
    assert c.get("/api/funds/search?q=abc").json()["data_status"] == "unavailable"
    p2, _ = make_provider(Upstream(status=429))
    set_providers(Providers(quotes=providers.quotes, history=providers.history, funds=p2))  # type: ignore[arg-type]
    assert c.get("/api/funds/1001").status_code == 429


def test_search_is_rate_limited_per_user(
    funds_client: tuple[SignupFn, GemelNetProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FUND_SEARCH_RATE_LIMIT_PER_HOUR", "2")
    from app.config import get_settings

    get_settings.cache_clear()
    c = funds_client[0]("rl@mail.com")
    assert c.get("/api/funds/search?q=קרן").status_code == 200
    assert c.get("/api/funds/search?q=קרן").status_code == 200
    assert c.get("/api/funds/search?q=קרן").status_code == 429


# ---------------------------------------------------------------- fund holdings
def _portfolio(c: TestClient) -> int:
    return int(c.post("/api/portfolios", json={"name": "P", "base_currency": "ILS"}).json()["id"])


def test_add_fund_with_manual_value_is_valued_in_ils_and_flagged(
    funds_client: tuple[SignupFn, GemelNetProvider], quotes: FakeQuotes
) -> None:
    c = funds_client[0]("h@mail.com")
    pid = _portfolio(c)
    r = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={
            "symbol": "GEMEL-1001",
            "quantity": 4,
            "manual_value_ils": 25000.0,
            "avg_cost": 5000.0,
        },
    )
    assert r.status_code == 201, r.text
    h = r.json()
    assert h["asset_type"] == "fund" and h["market"] == "TASE" and h["currency"] == "ILS"
    assert h["value_ils"] == 25000.0 and h["price"] == 6250.0  # no agorot, no /100 anywhere
    assert h["price_stale"] is True and h["price_is_fresh"] is False and h["price_source"] is None
    assert h["stop_tp_status"] == "no_levels"
    assert h["name_en"].startswith("קרן השתלמות")  # the dataset's public name
    f = h["fund"]
    assert f["fund_id"] == "1001" and f["value_basis"] == "manual_value"
    assert f["track"] == "קרנות השתלמות" and f["manual_value_as_of"] == local_today().isoformat()
    assert "monthly_data_only" in f["flags"] and "no_price_manual_value" in f["flags"]
    assert h["pnl"]["ils"] == 5000.0
    assert not [
        s for call in quotes.calls for s in call if s.startswith("GEMEL")
    ]  # Yahoo never asked
    summary = c.get(f"/api/portfolios/{pid}/summary").json()
    assert summary["value"]["ils"] == 25000.0
    with new_session() as db:  # a fund never leaves a marker waiting for a market price
        assert [t for t in db.query(Transaction) if t.type == "pending_buy"] == []
        assert db.get(FundHolding, h["id"]) is not None


def test_fund_without_value_says_so_and_manual_entry_can_follow(
    funds_client: tuple[SignupFn, GemelNetProvider],
) -> None:
    c = funds_client[0]("nv@mail.com")
    pid = _portfolio(c)
    h = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "GEMEL-2002", "quantity": 1}
    ).json()
    assert (
        h["value_ils"] == 0.0
        and h["fund"]["value_basis"] == "no_value"
        and "no_value" in h["fund"]["flags"]
    )
    assert h["price_stale"] is True and h["currency"] == "ILS"
    with_cost = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "GEMEL-3003", "quantity": 1, "avg_cost": 900},
    ).json()
    assert with_cost["fund"]["value_basis"] == "cost_only" and with_cost["value_ils"] == 900.0
    p = c.patch(
        f"/api/portfolios/{pid}/holdings/{h['id']}",
        json={"manual_value_ils": 1234.5, "manual_value_as_of": "2026-01-31", "track": "S&P 500"},
    )
    assert p.status_code == 200, p.text
    out = p.json()
    assert out["value_ils"] == 1234.5 and out["fund"]["manual_value_as_of"] == "2026-01-31"
    assert out["fund"]["track"] == "S&P 500" and out["fund"]["value_basis"] == "manual_value"
    cleared = c.patch(
        f"/api/portfolios/{pid}/holdings/{h['id']}", json={"manual_value_ils": None}
    ).json()
    assert cleared["value_ils"] == 0.0 and cleared["fund"]["value_basis"] == "no_value"


def test_manual_fund_entry_works_when_the_dataset_is_down(
    signup: SignupFn, providers: Providers
) -> None:
    p, _ = make_provider(configured=False)
    set_providers(Providers(quotes=providers.quotes, history=providers.history, funds=p))  # type: ignore[arg-type]
    c = signup("manual@mail.com")
    pid = _portfolio(c)
    r = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={
            "symbol": "GEMEL-555",
            "quantity": 1,
            "manual_value_ils": 100.0,
            "fund_name": "My fund",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["name_en"] == "My fund" and r.json()["fund"]["track"] is None


def test_fund_only_fields_and_currency_are_validated(
    funds_client: tuple[SignupFn, GemelNetProvider],
) -> None:
    c = funds_client[0]("v@mail.com")
    pid = _portfolio(c)
    base = f"/api/portfolios/{pid}/holdings"
    assert (
        c.post(base, json={"symbol": "AAPL", "quantity": 1, "manual_value_ils": 5}).status_code
        == 422
    )
    assert (
        c.post(base, json={"symbol": "GEMEL-1", "quantity": 1, "cost_currency": "USD"}).status_code
        == 422
    )
    future = (local_today() + timedelta(days=2)).isoformat()
    assert (
        c.post(
            base,
            json={
                "symbol": "GEMEL-1",
                "quantity": 1,
                "manual_value_ils": 5,
                "manual_value_as_of": future,
            },
        ).status_code
        == 422
    )
    assert (
        c.post(base, json={"symbol": "GEMEL-1", "quantity": 1, "manual_value_ils": -5}).status_code
        == 422
    )
    raw = b'{"symbol": "GEMEL-1", "quantity": 1, "manual_value_ils": NaN}'
    r = c.post(base, content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)
    aapl = c.post(base, json={"symbol": "AAPL", "quantity": 1}).json()
    assert c.patch(f"{base}/{aapl['id']}", json={"track": "x"}).status_code == 422
    assert c.patch(f"{base}/{aapl['id']}", json={"manual_value_ils": 3}).status_code == 422
    fund = c.post(base, json={"symbol": "GEMEL-9", "quantity": 1}).json()
    assert (
        c.patch(f"{base}/{fund['id']}", json={"manual_value_as_of": "2026-01-01"}).status_code
        == 422
    )
    assert c.patch(f"{base}/{fund['id']}", json={"cost_currency": "USD"}).status_code == 422
    assert c.post(base, json={"symbol": "GEMEL-9", "quantity": 1}).status_code == 409


def test_funds_get_no_exit_levels(
    funds_client: tuple[SignupFn, GemelNetProvider], quotes: FakeQuotes, history: FakeHistory
) -> None:
    c = funds_client[0]("x@mail.com")
    pid = _portfolio(c)
    h = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "GEMEL-1001", "quantity": 1, "manual_value_ils": 1000, "horizon": "1m"},
    ).json()
    r = c.get(f"/api/holdings/{h['id']}/exit-levels")  # no horizon given: the holding's own is used
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "no_levels" and j["reason_code"] == "fund_no_levels"
    assert j["stop"] is None and j["take_profits"] == [] and "fund" in j["reason"].lower()
    nohz = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "GEMEL-2", "quantity": 1}
    ).json()
    j2 = c.get(f"/api/holdings/{nohz['id']}/exit-levels").json()
    assert (
        j2["status"] == "no_levels" and j2["reason_code"] == "fund_no_levels"
    )  # never "needs_horizon"
    rev = c.post(f"/api/portfolios/{pid}/exit-review", json={}).json()
    rows = {row["symbol"]: row for row in rev["rows"]}
    assert rows["GEMEL-1001"]["status"] == "no_levels" and rows["GEMEL-1001"]["stop_price"] is None
    assert {m["symbol"] for m in rev["totals"]["positions_without_stop"]} >= {
        "GEMEL-1001",
        "GEMEL-2",
    }


def test_fund_stays_out_of_performance_but_in_the_total(
    funds_client: tuple[SignupFn, GemelNetProvider], quotes: FakeQuotes
) -> None:
    quotes.set("AAPL", 200.0, "USD")
    c = funds_client[0]("perf@mail.com")
    pid = _portfolio(c)
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 1})
    c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "GEMEL-1001", "quantity": 1, "manual_value_ils": 10000},
    )
    from app.config import get_settings
    from app.models import Portfolio
    from app.portfolio.valuation import value_portfolio

    with new_session() as db:
        val = value_portfolio(db, db.get(Portfolio, pid), get_settings())  # type: ignore[arg-type]
        by = {v.holding.symbol: v for v in val.holdings}
        assert (
            by["GEMEL-1001"].performance_priced is False
            and by["GEMEL-1001"].price_source == "manual"
        )
        assert by["AAPL"].performance_priced is True
        assert val.total_ils == pytest.approx(by["AAPL"].value_ils + 10000.0)
        assert val.performance_total_ils == pytest.approx(by["AAPL"].value_ils)


def test_deleting_the_holding_removes_the_fund_row(
    funds_client: tuple[SignupFn, GemelNetProvider],
) -> None:
    c = funds_client[0]("d@mail.com")
    pid = _portfolio(c)
    h = c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "GEMEL-1001", "quantity": 1, "manual_value_ils": 5},
    ).json()
    assert c.delete(f"/api/portfolios/{pid}/holdings/{h['id']}").status_code == 204
    with new_session() as db:
        assert db.get(FundHolding, h["id"]) is None and db.query(Holding).count() == 0


def test_export_includes_fund_rows(funds_client: tuple[SignupFn, GemelNetProvider]) -> None:
    c = funds_client[0]("e@mail.com")
    pid = _portfolio(c)
    c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "GEMEL-1001", "quantity": 1, "manual_value_ils": 5},
    )
    out = c.post("/api/me/export", json={"password": "correct horse battery"}).json()
    assert out["portfolios"][0]["fund_holdings"][0]["fund_id"] == "1001"
