"""GET /portfolios/{id}/post-mortem: user-scoped, strict, template text only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pandas as pd
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import new_session
from app.models import Portfolio, PortfolioSnapshot
from app.timeutil import local_today
from tests.conftest import FakeHistory, FakeQuotes

SignupFn = Callable[..., TestClient]


def _frame(first: float, last: float, days: int) -> pd.DataFrame:
    today = local_today()
    idx = pd.date_range(today - timedelta(days=days), today, freq="D")
    vals = [first + (last - first) * i / (len(idx) - 1) for i in range(len(idx))]
    return pd.DataFrame({"Close": vals}, index=idx)


def test_post_mortem_for_a_new_portfolio_needs_more_history(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    quotes.set("AAPL", 200.0, "USD")
    c = signup("a@mail.com")
    pid = c.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 3})
    r = c.get(f"/api/portfolios/{pid}/post-mortem")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_enough_history"
    assert body["history"]["days_remaining"] == get_settings().postmortem_min_days
    assert body["twr_pct"] is None and body["findings"] == []
    assert body["expectation"]["status"] == "needs_expectation"


def test_post_mortem_with_history_and_an_expectation(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory
) -> None:
    quotes.set("AAPL", 200.0, "USD")
    history.frames["AAPL"] = _frame(100, 200, 90)
    history.frames[get_settings().fx_symbol] = _frame(3.5, 3.5, 90)
    history.frames[get_settings().benchmark_sp500] = _frame(5000, 5500, 90)
    c = signup("a@mail.com")
    pid = c.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 3})
    today = local_today()
    start = today - timedelta(days=60)
    with new_session() as db:
        p = db.exec(select(Portfolio)).one()
        p.tracking_started_at = start
        db.add(p)
        for s in db.exec(select(PortfolioSnapshot)).all():
            db.delete(s)
        db.flush()
        db.add(PortfolioSnapshot(portfolio_id=pid, date=start, value_ils=1500, value_usd=428))
        db.add(PortfolioSnapshot(portfolio_id=pid, date=today, value_ils=2100, value_usd=600))
        db.commit()
    c.patch(
        f"/api/portfolios/{pid}",
        json={"expected_return_pct": 10, "expected_return_horizon_months": 12},
    )
    body = c.get(f"/api/portfolios/{pid}/post-mortem").json()
    assert body["status"] == "ok" and body["days"] == 60
    assert body["expectation"]["status"] == "ok"
    gap = body["gap_vs_expectation"]
    assert gap["status"] == "ok" and gap["reconciles"] is True
    assert abs(gap["attributed_pp"] + gap["residual_pp"] - gap["gap_pp"]) < 1e-4
    assert {f["kind"] for f in body["findings"]} >= {"performance", "contribution", "fx", "timing"}
    assert all(f["explanation"]["summary"] for f in body["findings"])
    # explicit period: start must be before end
    assert c.get(f"/api/portfolios/{pid}/post-mortem?start={today}&end={start}").status_code == 422
    assert c.get(f"/api/portfolios/{pid}/post-mortem?start=nonsense").status_code == 422
    ok = c.get(f"/api/portfolios/{pid}/post-mortem?start={start}&end={today}")
    assert ok.status_code == 200 and ok.json()["start"] == start.isoformat()
