from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import new_session
from app.models import (
    Holding,
    HoldingsSnapshot,
    Portfolio,
    PortfolioSnapshot,
    PriceQuote,
    Transaction,
)
from app.providers.base import OcrResult, OcrUnavailableError
from app.providers.registry import Providers, set_providers
from app.timeutil import local_today, utcnow
from tests.conftest import FakeHistory, FakeOcr, FakeQuotes, png_bytes
from tests.fixtures.series import uptrend

SignupFn = Callable[..., TestClient]
OCR_FIRST = (
    "שם נייר כמות שער (אג') שווי עלות\nטבע 1,000 6,500 65,000 5,800\nלאומי 500 3,200 16,000\n"
)
OCR_SECOND = "שם נייר כמות שער (אג') שווי\nטבע 1,200 6,500 78,000\nלאומי 500 3,200 16,000\n"


def set_fx(rate: float) -> None:
    with new_session() as db:
        db.merge(PriceQuote(symbol="ILS=X", price=rate, currency="ILS", as_of=utcnow()))
        db.commit()


def make_portfolio(c: TestClient, name: str = "main") -> int:
    r = c.post("/api/portfolios", json={"name": name, "base_currency": "ILS"})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def shift_tracking_back(pid: int, days: int, value_ils: float) -> None:
    """Pretend the user started `days` ago: move the start date and rebuild flat snapshots."""
    with new_session() as db:
        p = db.get(Portfolio, pid)
        assert p is not None
        today = local_today()
        for s in db.exec(
            select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pid)
        ).all():
            db.delete(s)
        p.tracking_started_at = today - timedelta(days=days)
        db.add(p)
        db.flush()
        for i in range(days, 0, -1):
            db.add(
                PortfolioSnapshot(
                    portfolio_id=pid,
                    date=today - timedelta(days=i),
                    value_ils=value_ils,
                    value_usd=value_ils / 3.5,
                )
            )
        db.commit()


def test_since_start_is_baseline_not_broker_cost_basis(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    set_fx(3.5)
    quotes.set("AAPL", 200.0, "USD", 1.0)
    c = signup()
    pid = make_portfolio(c)
    s0 = c.get(f"/api/portfolios/{pid}/summary").json()
    assert s0["since_start_date"] is None and s0["since_start_pnl"]["ils"] == 0

    # first manual add starts tracking. Broker cost basis (100) is far below the market (200).
    r = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 10, "avg_cost": 100}
    )
    assert r.status_code == 201, r.text
    holding = r.json()
    assert holding["pnl"]["ils"] == pytest.approx(3500.0)  # cost-basis P&L is shown per holding
    assert holding["pnl"]["pct"] == pytest.approx(100.0)

    today = local_today().isoformat()
    s1 = c.get(f"/api/portfolios/{pid}/summary").json()
    assert s1["since_start_date"] == today
    assert s1["value"]["ils"] == pytest.approx(7000.0)
    assert s1["since_start_pnl"]["ils"] == 0.0 and s1["since_start_pnl"]["pct"] == 0.0
    with new_session() as db:
        snap = db.exec(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pid)).one()
        assert snap.value_ils == pytest.approx(7000.0)  # baseline = value that day, not cost 3500
        p = db.get(Portfolio, pid)
        assert p is not None and p.tracking_started_at == local_today()

    # a second add does not move the start date
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "MSFT", "quantity": 1, "avg_cost": 1})
    assert c.get(f"/api/portfolios/{pid}").json()["tracking_started_at"] == today


def test_since_start_pnl_after_time_passes_ignores_earlier_gains_and_deposits(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    set_fx(3.5)
    quotes.set("AAPL", 200.0, "USD", 1.0)
    quotes.set("MSFT", 400.0, "USD", 0.0)
    c = signup()
    pid = make_portfolio(c)
    c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 10, "avg_cost": 100}
    )
    shift_tracking_back(pid, days=3, value_ils=7000.0)  # baseline 7000 three days ago
    with new_session() as db:
        db.merge(
            PriceQuote(symbol="AAPL", price=220.0, currency="USD", change_pct=0.0, as_of=utcnow())
        )
        db.commit()
    s = c.get(f"/api/portfolios/{pid}/summary").json()
    assert s["since_start_date"] == (local_today() - timedelta(days=3)).isoformat()
    assert s["since_start_pnl"]["ils"] == pytest.approx(700.0)  # 10 x 20 x 3.5 since the baseline
    assert s["since_start_pnl"]["pct"] == pytest.approx(10.0)
    assert s["since_start_series"][0]["pct"] == 0.0
    assert s["since_start_series"][-1]["pct"] == pytest.approx(10.0)
    assert s["since_start_series"][-1]["sp500_pct"] is None  # no benchmark history in the fixture

    # adding MSFT now is a deposit (flow), not profit
    r = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "MSFT", "quantity": 5, "avg_cost": 300}
    )
    assert r.status_code == 201
    with new_session() as db:
        tx = db.exec(select(Transaction).where(Transaction.portfolio_id == pid)).one()
        assert tx.type == "buy" and tx.amount == pytest.approx(2000.0) and tx.currency == "USD"
        assert tx.fx_to_ils == pytest.approx(3.5) and tx.inferred is False
    s2 = c.get(f"/api/portfolios/{pid}/summary").json()
    assert s2["value"]["ils"] == pytest.approx(7700.0 + 7000.0)
    assert s2["since_start_pnl"]["ils"] == pytest.approx(700.0)
    # flows are at the start of the day: the 7,000 deposited earned 0 that day, so the 10% gain on
    # the original 7,000 is diluted to 700 / 14,000 = 5% for the day (and never counted as profit)
    assert s2["since_start_pnl"]["pct"] == pytest.approx(5.0)
    assert s2["week_pnl"]["ils"] <= 700.0 + 1e-6
    assert s2["weekly_bars"] and s2["monthly_bars"]
    assert {"week_start", "pnl_ils", "pct"} <= set(s2["weekly_bars"][0])


def test_summary_shape_and_combined_starts_at_earliest_start(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    set_fx(3.5)
    quotes.set("AAPL", 200.0, "USD", 2.0)
    quotes.set("TEVA.TA", 65.0, "ILS", 0.0)  # providers hand over normalised prices
    c = signup()
    p1, p2 = make_portfolio(c, "one"), make_portfolio(c, "two")
    c.post(
        f"/api/portfolios/{p1}/holdings", json={"symbol": "AAPL", "quantity": 10, "avg_cost": 190}
    )
    c.post(
        f"/api/portfolios/{p2}/holdings",
        json={"symbol": "TEVA.TA", "quantity": 100, "avg_cost": 60},
    )
    shift_tracking_back(p1, 5, 7000.0)
    shift_tracking_back(p2, 2, 6500.0)
    one = c.get(f"/api/portfolios/{p1}/summary").json()
    assert set(one) == {
        "value", "day_pnl", "week_pnl", "month_pnl", "since_start_pnl", "since_start_date",
        "weekly_bars", "monthly_bars", "since_start_series", "as_of", "markets",
            "week_start", "fx_stale",
    }  # fmt: skip
    assert set(one["value"]) == {"ils", "usd"} and set(one["day_pnl"]) == {"ils", "usd", "pct"}
    assert (
        set(one["markets"]) == {"US", "TASE", "CRYPTO"} and one["markets"]["CRYPTO"]["open"] is True
    )
    assert one["as_of"].endswith("Z") or "+00:00" in one["as_of"]
    assert one["day_pnl"]["ils"] > 0  # +2% day change on AAPL
    comb = c.get("/api/portfolios/combined/summary").json()
    assert comb["value"]["ils"] == pytest.approx(7000 + 6500)
    assert comb["since_start_date"] == (local_today() - timedelta(days=5)).isoformat()
    # the second portfolio joined 2 days ago as a deposit, so it creates no combined profit
    assert comb["since_start_pnl"]["ils"] == pytest.approx(0.0, abs=1e-6)


def test_holdings_list_contract_and_score_card(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory
) -> None:
    set_fx(3.5)
    quotes.set("AAPL", 200.0, "USD", 1.5)
    history.frames["AAPL"] = uptrend()
    c = signup()
    pid = make_portfolio(c)
    c.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "AAPL", "quantity": 10, "avg_cost": 150, "horizon": "3m"},
    )
    # the first response may predate the background score computation; the second sees the cache
    c.get(f"/api/portfolios/{pid}/holdings")
    rows = c.get(f"/api/portfolios/{pid}/holdings").json()
    assert len(rows) == 1
    h = rows[0]
    assert set(h) >= {
        "id", "symbol", "name_en", "name_he", "asset_type", "market", "quantity", "price", "currency",
        "day_change_pct", "value_ils", "pnl", "weight_pct", "horizon", "stop_tp_status", "score_card",
    }  # fmt: skip
    assert h["symbol"] == "AAPL" and h["name_en"] == "Apple" and h["market"] == "US"
    assert h["weight_pct"] == pytest.approx(100.0) and h["value_ils"] == pytest.approx(7000.0)
    assert h["horizon"] == "3m" and h["stop_tp_status"] == "missing"
    card = h["score_card"]
    assert set(card) == {"total", "technical", "patterns", "confidence"}
    assert card["confidence"] > 0 and card["technical"] > -100

    detail = c.get(f"/api/holdings/{h['id']}/scorecard").json()
    assert detail["validated"] is False and detail["symbol"] == "AAPL"
    names = [s["name"] for s in detail["signals"]]
    assert names == ["technical", "patterns", "fundamentals", "analysts", "geo_news", "sentiment"]
    by = {s["name"]: s for s in detail["signals"]}
    for missing in ("fundamentals", "analysts", "geo_news", "sentiment"):
        assert by[missing]["confidence"] == 0 and by[missing]["weight"] == 0
        assert by[missing]["reasons"]
    assert by["technical"]["weight"] + by["patterns"]["weight"] == pytest.approx(100.0, abs=0.01)
    assert by["technical"]["explanation"]["rules_applied"] and by["technical"]["reasons"]
    assert detail["explanation"]["summary"] and "disclaimer" in detail
    assert detail["explanation"]["version"] == 1 and by["technical"]["explanation"]["sources"]
    assert detail["explanation"]["contributions"] and detail["explanation"]["as_of"]
    text = str(detail).lower()
    assert "verdict" not in {k.lower() for k in detail} and "recommendation" not in {
        k.lower() for k in detail
    }
    assert "buy" not in {k.lower() for k in detail}
    assert "no buy/sell verdict" in text  # the gate is explained to the user


def test_holding_without_history_has_zero_confidence_card(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    quotes.set("AAPL", 200.0, "USD")
    c = signup()
    pid = make_portfolio(c)
    hid = c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 1}).json()[
        "id"
    ]
    card = c.get(f"/api/portfolios/{pid}/holdings").json()[0]["score_card"]
    assert card["confidence"] == 0 and card["total"] == 0
    detail = c.get(f"/api/holdings/{hid}/scorecard").json()
    assert detail["available"] is False and detail["confidence"] == 0


def test_patch_holding_horizon_quantity_and_delete(signup: SignupFn, quotes: FakeQuotes) -> None:
    set_fx(3.5)
    quotes.set("AAPL", 200.0, "USD")
    c = signup()
    pid = make_portfolio(c)
    h = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 10, "avg_cost": 100}
    ).json()
    assert h["stop_tp_status"] == "needs_horizon" and h["horizon"] is None
    up = c.patch(f"/api/portfolios/{pid}/holdings/{h['id']}", json={"horizon": "6m"}).json()
    assert up["horizon"] == "6m" and up["stop_tp_status"] == "missing"
    cleared = c.patch(f"/api/portfolios/{pid}/holdings/{h['id']}", json={"horizon": None}).json()
    assert cleared["horizon"] is None
    assert (
        c.patch(f"/api/portfolios/{pid}/holdings/{h['id']}", json={"horizon": "2d"}).status_code
        == 422
    )
    assert (
        c.post(
            f"/api/portfolios/{pid}/holdings", json={"symbol": "aapl", "quantity": 1}
        ).status_code
        == 409
    )
    assert c.delete(f"/api/portfolios/{pid}/holdings/{h['id']}").status_code == 204
    assert c.get(f"/api/portfolios/{pid}/holdings").json() == []


def test_risk_presets_filter_and_xray(signup: SignupFn, quotes: FakeQuotes) -> None:
    set_fx(3.5)
    quotes.set("NVDA", 100.0, "USD", 3.0)
    quotes.set("TEVA.TA", 65.0, "ILS", -1.0)
    c = signup()
    presets = c.get("/api/risk/presets").json()
    assert [p["name"] for p in presets] == [
        "very_conservative", "conservative", "balanced", "balanced_aggressive", "aggressive", "very_aggressive",
    ]  # fmt: skip
    assert {"max_position_pct", "max_sector_pct", "max_country_pct", "max_loss_per_position_pct",
            "max_portfolio_risk_per_trade_pct", "max_total_portfolio_risk_pct", "min_rr", "stop_type",
            "drawdown_defensive_pct"} <= set(presets[0])  # fmt: skip
    pid = make_portfolio(c)
    assert c.get(f"/api/portfolios/{pid}").json()["risk_filter"]["preset"] == "balanced_aggressive"
    c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "NVDA", "quantity": 100}
    )  # 10,000 USD
    c.post(f"/api/portfolios/{pid}/holdings", json={"symbol": "TEVA.TA", "quantity": 10})  # 650 ILS
    assert (
        c.patch(f"/api/portfolios/{pid}", json={"risk_filter": {"preset": "bogus"}}).status_code
        == 422
    )
    up = c.patch(
        f"/api/portfolios/{pid}",
        json={"risk_filter": {"preset": "conservative", "max_position_pct": 50}},
    )
    assert up.json()["risk_filter"]["preset"] == "conservative"
    assert up.json()["risk_filter"]["max_position_pct"] == 50
    xr = c.get(f"/api/portfolios/{pid}/xray").json()
    assert set(xr) == {
        "concentration",
        "currency_exposure",
        "country_exposure",
        "sector_exposure",
        "home_bias",
        "breaches",
    }
    assert xr["concentration"][0]["symbol"] == "NVDA"
    cur = {i["name"]: i["weight_pct"] for i in xr["currency_exposure"]}
    assert cur["USD"] > 90 and cur["ILS"] < 10
    assert {i["name"] for i in xr["sector_exposure"]} == {"Technology", "Healthcare"}
    assert xr["home_bias"]["israel_pct"] == pytest.approx(cur["ILS"], abs=0.1)
    rules = {b["rule"] for b in xr["breaches"]}
    assert {"max_position_pct", "max_sector_pct", "max_country_pct"} <= rules
    pos = next(b for b in xr["breaches"] if b["rule"] == "max_position_pct")
    assert pos["symbol"] == "NVDA" and pos["limit"] == 50 and "50%" in pos["why"]
    for b in xr["breaches"]:
        assert b["why"] and b["value"] > b["limit"]
    heat = c.get(f"/api/portfolios/{pid}/heatmap").json()
    assert {h["symbol"] for h in heat} == {"NVDA", "TEVA.TA"}
    assert set(heat[0]) == {"symbol", "sector", "weight_pct", "day_change_pct"}
    assert heat[0]["symbol"] == "NVDA" and heat[0]["day_change_pct"] == pytest.approx(3.0)


def test_securities_search_hebrew_english_symbol(signup: SignupFn) -> None:
    c = signup()

    def syms(q: str) -> list[str]:
        return [h["symbol"] for h in c.get("/api/securities/search", params={"q": q}).json()]

    assert "TEVA.TA" in syms("טבע")
    assert "LUMI.TA" in syms("לאומי")
    assert syms("NVD")[0] == "NVDA"
    assert "MSFT" in syms("micro")
    assert "BTC-USD" in syms("bitcoin")
    assert syms("") == []
    hit = c.get("/api/securities/search", params={"q": "TEVA"}).json()[0]
    assert set(hit) == {"symbol", "name_en", "name_he", "market"}


# ---------------------------------------------------------------- screenshot import
def upload(c: TestClient, pid: int) -> Any:
    return c.post(
        f"/api/portfolios/{pid}/imports", content=png_bytes(), headers={"Content-Type": "image/png"}
    )


def test_import_requires_consent_and_valid_image(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    ocr_text["text"] = OCR_FIRST
    c = signup()
    pid = make_portfolio(c)
    assert upload(c, pid).status_code == 403
    c.post("/api/auth/consent/ocr")
    bad = c.post(
        f"/api/portfolios/{pid}/imports",
        content=b"not an image",
        headers={"Content-Type": "image/png"},
    )
    assert bad.status_code == 415  # the magic bytes decide, not the header
    corrupt = c.post(
        f"/api/portfolios/{pid}/imports",
        content=png_bytes()[:40],  # a PNG header, then nothing
        headers={"Content-Type": "image/png"},
    )
    assert corrupt.status_code == 400
    ocr_text["text"] = "nothing useful here"
    assert upload(c, pid).status_code == 422


def test_import_without_ocr_engine_is_a_clean_503(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory
) -> None:
    class Broken(FakeOcr):
        def extract(self, image_bytes: bytes) -> OcrResult:
            raise OcrUnavailableError("Tesseract is not installed")

    def broken() -> FakeOcr:
        return Broken("")

    set_providers(Providers(quotes=quotes, history=history, ocr_factory=broken))
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    r = upload(c, pid)
    assert r.status_code == 503 and "Tesseract" in r.json()["detail"]


def test_import_end_to_end_with_tracking_and_diff(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    set_fx(3.5)
    ocr_text["text"] = OCR_FIRST
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    draft = upload(c, pid).json()
    assert draft["status"] == "draft" and draft["portfolio_id"] == pid
    assert draft["proposed_changes"] == []  # first import is the baseline
    rows = draft["rows"]
    assert [r["symbol"] for r in rows] == ["TEVA.TA", "LUMI.TA"]
    assert rows[0]["unit"] == "agorot" and rows[0]["flags"] == [] and rows[0]["matched_name"]
    assert set(rows[0]) >= {
        "index",
        "name",
        "symbol",
        "quantity",
        "price",
        "value",
        "cost",
        "currency",
        "unit",
        "flags",
    }
    assert c.get(f"/api/imports/{draft['id']}").json() == draft

    # nothing is written until confirm
    assert c.get(f"/api/portfolios/{pid}/holdings").json() == []
    conf = c.post(f"/api/imports/{draft['id']}/confirm")
    assert conf.status_code == 200 and conf.json()["status"] == "confirmed"
    assert c.post(f"/api/imports/{draft['id']}/confirm").status_code == 409
    assert c.patch(f"/api/imports/{draft['id']}", json={"rows": []}).status_code == 409

    holdings = {h["symbol"]: h for h in c.get(f"/api/portfolios/{pid}/holdings").json()}
    teva = holdings["TEVA.TA"]
    assert (
        teva["quantity"] == 1000
        and teva["price"] == pytest.approx(65.0)
        and teva["currency"] == "ILS"
    )
    assert teva["value_ils"] == pytest.approx(65000.0)  # agorot normalised
    assert teva["pnl"]["ils"] == pytest.approx(65000 - 58000)  # avg cost 5,800 agorot = 58 ILS
    with new_session() as db:
        p = db.get(Portfolio, pid)
        assert p is not None and p.tracking_started_at == local_today()
        h = db.exec(select(Holding).where(Holding.symbol == "TEVA.TA")).one()
        assert h.avg_cost == pytest.approx(58.0) and h.cost_currency == "ILS"
        assert len(db.exec(select(HoldingsSnapshot)).all()) == 1
        assert db.exec(select(Transaction)).all() == []  # baseline import creates no transactions
        snap = db.exec(select(PortfolioSnapshot)).one()
        assert snap.value_ils == pytest.approx(65000 + 16000)

    # second import: +200 TEVA shares -> proposed buy, confirmed as an inferred transaction
    ocr_text["text"] = OCR_SECOND
    d2 = upload(c, pid).json()
    (change,) = d2["proposed_changes"]
    assert change["type"] == "buy" and change["symbol"] == "TEVA.TA" and change["quantity"] == 200
    assert change["amount"] == pytest.approx(13000.0) and change["currency"] == "ILS"
    # the user can reclassify the change (e.g. shares transferred in kind = deposit)
    change["type"] = "deposit"
    patched = c.patch(f"/api/imports/{d2['id']}", json={"proposed_changes": [change]}).json()
    assert patched["proposed_changes"][0]["type"] == "deposit"
    assert c.post(f"/api/imports/{d2['id']}/confirm").status_code == 200
    with new_session() as db:
        tx = db.exec(select(Transaction)).one()
        assert tx.type == "deposit" and tx.inferred is True and tx.amount == pytest.approx(13000.0)
        assert len(db.exec(select(HoldingsSnapshot)).all()) == 2
        p = db.get(Portfolio, pid)
        assert p is not None and p.tracking_started_at == local_today()


def test_import_unmatched_rows_block_confirm_until_fixed(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    ocr_text["text"] = "טבע 1,000 6,500 65,000\nxqzvw qrstu 10 5 50\n"
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    d = upload(c, pid).json()
    assert d["rows"][1]["symbol"] is None and "unmatched" in d["rows"][1]["flags"]
    blocked = c.post(f"/api/imports/{d['id']}/confirm")
    assert blocked.status_code == 422 and "not matched" in blocked.json()["detail"]
    # fix the row by hand (the review table sends the edited rows back)
    d["rows"][1]["symbol"] = "AAPL"
    d["rows"][1]["currency"], d["rows"][1]["unit"] = "USD", "USD"
    fixed = c.patch(f"/api/imports/{d['id']}", json={"rows": d["rows"]}).json()
    assert fixed["rows"][1]["symbol"] == "AAPL" and "unmatched" not in fixed["rows"][1]["flags"]
    assert c.post(f"/api/imports/{d['id']}/confirm").status_code == 200


def test_import_flags_inconsistent_rows(signup: SignupFn, ocr_text: dict[str, str]) -> None:
    ocr_text["text"] = "טבע 1,000 6,500 99,000\n"
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    row = upload(c, pid).json()["rows"][0]
    assert "value_mismatch" in row["flags"] and row["symbol"] == "TEVA.TA"


def test_no_image_is_persisted(signup: SignupFn, ocr_text: dict[str, str], tmp_path: Any) -> None:
    ocr_text["text"] = OCR_FIRST
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    d = upload(c, pid).json()
    assert "image" not in str(d).lower() and "base64" not in str(d).lower()
    with new_session() as db:
        from app.models import ImportDraft

        draft = db.exec(select(ImportDraft)).one()
        assert all(
            isinstance(v, str | int | float | list | dict | type(None))
            for v in draft.rows[0].values()
        )
        assert len(str(draft.rows)) < 5000  # a few rows of text, not image data


def test_weak_name_match_returns_candidates_and_blocks_confirm(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    ocr_text["text"] = "Microstrategy 10 $120.00 $1,200.00\n"
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    d = upload(c, pid).json()
    row = d["rows"][0]
    assert row["symbol"] is None and "low_confidence_match" in row["flags"]
    assert row["candidates"][0]["symbol"] == "MSFT"
    assert c.post(f"/api/imports/{d['id']}/confirm").status_code == 422
    row["symbol"] = row["candidates"][0]["symbol"]  # the user picks the suggestion
    fixed = c.patch(f"/api/imports/{d['id']}", json={"rows": d["rows"]}).json()["rows"][0]
    assert fixed["symbol"] == "MSFT" and fixed["candidates"] == []
    assert "low_confidence_match" not in fixed["flags"]
    assert c.post(f"/api/imports/{d['id']}/confirm").status_code == 200


def test_currency_changed_flag_blocks_confirm_until_the_user_confirms(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    ocr_text["text"] = "אפל 10 700 7,000 ₪\n"  # a US stock shown in shekels by the broker
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    d = upload(c, pid).json()
    row = d["rows"][0]
    assert row["symbol"] == "AAPL" and "currency_changed" in row["flags"]
    assert row["currency"] == "ILS"  # not silently relabelled USD (that would be x3.6)
    blocked = c.post(f"/api/imports/{d['id']}/confirm")
    assert blocked.status_code == 422 and "currency" in blocked.json()["detail"]
    row["flags"] = [f for f in row["flags"] if f != "currency_changed"]  # user confirms
    assert c.patch(f"/api/imports/{d['id']}", json={"rows": d["rows"]}).status_code == 200
    assert c.post(f"/api/imports/{d['id']}/confirm").status_code == 200
    with new_session() as db:
        h = db.exec(select(Holding).where(Holding.symbol == "AAPL")).one()
        assert h.cost_currency == "ILS" or h.avg_cost is None


def test_screenshot_prices_never_become_global_quotes(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    set_fx(3.5)
    ocr_text["text"] = OCR_FIRST
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    d = upload(c, pid).json()
    assert c.post(f"/api/imports/{d['id']}/confirm").status_code == 200
    with new_session() as db:
        assert db.get(PriceQuote, "TEVA.TA") is None  # a screenshot is not a live market quote
        assert db.get(PriceQuote, "LUMI.TA") is None
    h = {x["symbol"]: x for x in c.get(f"/api/portfolios/{pid}/holdings").json()}["TEVA.TA"]
    assert h["price_stale"] is True and h["price"] == pytest.approx(65.0)
    assert h["value_ils"] == pytest.approx(65000.0)
    # another user holding the same symbol does not inherit this user's screenshot price
    other = signup("bob@mail.com")
    pid2 = make_portfolio(other)
    other.post(f"/api/portfolios/{pid2}/holdings", json={"symbol": "TEVA.TA", "quantity": 1})
    h2 = other.get(f"/api/portfolios/{pid2}/holdings").json()[0]
    assert h2["price_stale"] is True and h2["price"] != pytest.approx(65.0)


def test_live_quote_replaces_the_stale_screenshot_price(
    signup: SignupFn, ocr_text: dict[str, str], quotes: FakeQuotes
) -> None:
    set_fx(3.5)
    ocr_text["text"] = OCR_FIRST
    c = signup()
    pid = make_portfolio(c)
    c.post("/api/auth/consent/ocr")
    d = upload(c, pid).json()
    c.post(f"/api/imports/{d['id']}/confirm")
    with new_session() as db:
        db.merge(PriceQuote(symbol="TEVA.TA", price=70.0, currency="ILS", as_of=utcnow()))
        db.commit()
    h = {x["symbol"]: x for x in c.get(f"/api/portfolios/{pid}/holdings").json()}["TEVA.TA"]
    assert h["price"] == 70.0 and h["price_stale"] is False
