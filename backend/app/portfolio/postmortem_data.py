"""Load a portfolio's snapshots, transactions, holdings and stored history for the post-mortem.

Reads only the caller's portfolio (the route resolves it with `get_portfolio`). History comes from
the cached `HistoryProvider`; nothing here calls an LLM or sends personal data anywhere.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlmodel import Session, select

from app.config import Settings
from app.models import Portfolio, Security, Transaction
from app.portfolio.freshness import price_is_fresh
from app.portfolio.postmortem import (
    HoldingIn,
    PostmortemInput,
    PostmortemOut,
    SecInfo,
    TxIn,
    build_postmortem,
)
from app.portfolio.summary import portfolio_points
from app.portfolio.valuation import value_portfolio
from app.providers.base import HistoryProvider
from app.scoring.risk import resolve_risk_filter

_KIND = {"buy": "purchase", "sell": "sale", "deposit": "deposit", "withdrawal": "withdrawal"}


def _closes(history: HistoryProvider | None, symbol: str, days: int) -> pd.Series | None:
    if history is None:
        return None
    try:
        df = history.get_history(symbol, days)
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    return df["Close"]


def post_mortem_for_portfolio(
    db: Session,
    portfolio: Portfolio,
    history: HistoryProvider | None,
    settings: Settings,
    start: date | None,
    end: date | None,
    today: date,
) -> PostmortemOut:
    assert portfolio.id is not None
    tracking = portfolio.tracking_started_at
    first = max(start, tracking) if start and tracking else (start or tracking or today)
    valuation = value_portfolio(db, portfolio, settings)
    points = portfolio_points(db, portfolio, valuation, today)
    txs = [
        t
        for t in db.exec(select(Transaction).where(Transaction.portfolio_id == portfolio.id)).all()
        if t.type in _KIND
    ]
    tx_in = [
        TxIn(
            date=t.date,
            kind=_KIND[t.type],  # type: ignore[arg-type]
            symbol=t.symbol,
            quantity=t.quantity,
            price=t.price,
            amount=t.amount,
            currency=t.currency,
            fx_to_ils=t.fx_to_ils,
        )
        for t in txs
    ]
    symbols = sorted(
        {v.holding.symbol for v in valuation.holdings} | {t.symbol for t in txs if t.symbol}
    )
    secs = {
        s.symbol: SecInfo(
            symbol=s.symbol,
            name=s.name_en,
            market=s.market,
            currency=s.currency,
            sector=s.sector,
            country=s.country,
        )
        for s in db.exec(select(Security).where(Security.symbol.in_(symbols))).all()  # type: ignore[attr-defined]
    }
    days = (today - first).days + settings.postmortem_timing_window_days + 15
    benches = [settings.benchmark_sp500, settings.benchmark_ta125]
    limits = resolve_risk_filter(portfolio.risk_filter, settings)
    inp = PostmortemInput(
        start=first,
        end=min(end or today, today),
        today=today,
        points=points,
        txs=tx_in,
        holdings=[
            HoldingIn(
                symbol=v.holding.symbol,
                quantity=v.holding.quantity,
                price=v.price,
                price_fresh=price_is_fresh(v) and v.performance_priced,
            )
            for v in valuation.holdings
        ],
        securities=secs,
        closes={s: _closes(history, s, days) for s in symbols},
        fx_closes=_closes(history, settings.fx_symbol, days),
        benchmark_closes={b: _closes(history, b, days) for b in benches},
        expected_return_pct=portfolio.expected_return_pct,
        expected_return_horizon_months=portfolio.expected_return_horizon_months,
        max_sector_pct=limits.max_sector_pct,
        max_country_pct=limits.max_country_pct,
    )
    return build_postmortem(inp, settings)
