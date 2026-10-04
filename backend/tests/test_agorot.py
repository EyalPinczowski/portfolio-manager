"""Agorot rule: normalise by Yahoo's `currency` field, never by the .TA suffix."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session

from app.models import Holding, Portfolio, PriceQuote, Security, User
from app.portfolio.valuation import value_portfolio
from app.providers.base import normalize_currency, normalize_history, normalize_price
from app.providers.yfinance_provider import YFinanceProvider, build_quote
from app.timeutil import utcnow
from tests.fixtures.series import make_ohlcv


def test_tase_stock_in_ila_is_divided_by_100() -> None:
    assert normalize_price(6500.0, "ILA") == (65.0, "ILS")
    assert normalize_price(6500.0, "ila") == (65.0, "ILS")
    q = build_quote("TEVA.TA", 6500.0, "ILA", 1.5, utcnow())
    assert (q.price, q.currency) == (65.0, "ILS")


def test_etf_in_usd_or_ils_is_not_divided() -> None:
    assert normalize_price(512.34, "USD") == (512.34, "USD")  # SPY
    # A TASE ETF that Yahoo reports in ILS must NOT be divided even though it ends with .TA
    q = build_quote("XYZ-ETF.TA", 187.5, "ILS", 0.2, utcnow())
    assert (q.price, q.currency) == (187.5, "ILS")


def test_index_in_points_is_never_divided() -> None:
    for cur in (None, "", "ILS"):
        price, _ = normalize_price(2450.0, cur)
        assert price == 2450.0, cur
    q = build_quote("^TA125.TA", 2450.0, None, 0.4, utcnow())
    assert q.price == 2450.0 and q.currency == ""
    assert build_quote("^GSPC", 5800.0, None, 0.1, utcnow()).price == 5800.0


def test_decision_ignores_suffix_in_both_directions() -> None:
    # ILA without the .TA suffix is still agorot; .TA with ILS is not
    assert normalize_price(1000.0, "ILA")[0] == 10.0
    assert normalize_price(1000.0, "ILS")[0] == 1000.0
    assert normalize_currency("ILA") == "ILS"
    assert normalize_currency(None) is None


def test_history_normalisation_scales_ohlc_but_not_volume() -> None:
    df = make_ohlcv([6400.0, 6500.0, 6600.0])
    out = normalize_history(df, "ILA")
    assert out["Close"].tolist() == pytest.approx([64.0, 65.0, 66.0])
    assert out["High"].iloc[0] == pytest.approx(df["High"].iloc[0] / 100)
    assert out["Volume"].tolist() == df["Volume"].tolist()
    assert normalize_history(df, "ILS") is df
    assert normalize_history(df, None) is df


def test_provider_quote_path_uses_reported_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = YFinanceProvider()
    currencies = {"TEVA.TA": "ILA", "^TA125.TA": None, "SPY": "USD"}
    monkeypatch.setattr(prov, "raw_currency", lambda s: currencies[s])
    frame = pd.DataFrame({"Close": [6400.0, 6500.0]})
    teva = prov._quote_from_frame("TEVA.TA", frame, utcnow())
    assert teva is not None
    assert teva.price == 65.0 and teva.currency == "ILS"
    assert teva.change_pct == pytest.approx((6500 / 6400 - 1) * 100)
    idx = prov._quote_from_frame("^TA125.TA", pd.DataFrame({"Close": [2400.0, 2450.0]}), utcnow())
    assert idx is not None and idx.price == 2450.0
    spy = prov._quote_from_frame("SPY", pd.DataFrame({"Close": [500.0, 505.0]}), utcnow())
    assert spy is not None and spy.price == 505.0 and spy.currency == "USD"


def test_valuation_values_ila_holding_in_shekels(db: Session) -> None:
    user = User(email="a@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p")
    db.add(p)
    db.commit()
    db.add(
        Holding(
            portfolio_id=p.id or 0,
            symbol="TEVA.TA",
            quantity=100,
            avg_cost=60.0,
            cost_currency="ILS",
        )
    )
    quote = build_quote("TEVA.TA", 6500.0, "ILA", 0.0, utcnow())
    db.add(PriceQuote(symbol=quote.symbol, price=quote.price, currency=quote.currency))
    db.commit()
    sec = db.get(Security, "TEVA.TA")
    assert sec is not None and sec.currency == "ILS"
    val = value_portfolio(db, p)
    assert val.holdings[0].value_ils == pytest.approx(6500.0)  # 100 x 65 ILS, not 650000
    assert val.holdings[0].pnl_ils == pytest.approx(500.0)
