"""The screener's rules applied to as-of data, as a pure function (no database, no live calls).

It reuses `compute_scorecard`, `compute_exit_levels`, `annualised_volatility_pct` and the screener's
thresholds from `Settings`. What differs from `scoring/screener.py` is only where the inputs come
from: bars cut at the decision day (`HistoryStore.asof`) instead of the caches, and the book is the
simulated portfolio. The gates are the same, in the same order: score, confidence, volatility cap,
size caps, levels, minimum reward-to-risk, size shrunk to the stop (the stop is never tightened).

The score is the deterministic technical + patterns score only (the other four signals have no
history here), so `confidence` is at most the share of the weights those two hold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.config import Settings
from app.models import PriceQuote, Security
from app.providers.fx_provider import SUPPORTED_CURRENCIES, to_ils
from app.scoring.exit_levels import compute_exit_levels
from app.scoring.risk import RiskFilter
from app.scoring.scorecard import compute_scorecard
from app.scoring.screener import _floor_qty, _valued, annualised_volatility_pct
from app.timeutil import as_utc

# (symbol, day) -> (available, total score, confidence). A score depends on the bars and the
# weights only, so runs of different profiles can share it. Only the three numbers are kept: the
# full score card carries explanations that would fill the memory over thousands of days.
ScoreCache = dict[tuple[str, pd.Timestamp], tuple[bool, float, float]]


class _Frame:
    """A `HistoryProvider` that hands compute_scorecard the as-of frame it was given."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        return self.df


@dataclass
class Book:
    """What the simulated portfolio holds on the decision day, in ILS (pending buys included)."""

    total: float
    by_symbol: dict[str, float] = field(default_factory=dict)
    sector: dict[str, float] = field(default_factory=dict)
    country: dict[str, float] = field(default_factory=dict)
    by_group: dict[str, float] = field(default_factory=dict)

    def add(self, sec: Security, value_ils: float) -> None:
        self.by_symbol[sec.symbol] = self.by_symbol.get(sec.symbol, 0.0) + value_ils
        self.sector[sec.sector] = self.sector.get(sec.sector, 0.0) + value_ils
        self.country[sec.country] = self.country.get(sec.country, 0.0) + value_ils
        if sec.dual_listing_group:
            g = sec.dual_listing_group
            self.by_group[g] = self.by_group.get(g, 0.0) + value_ils

    def existing(self, sec: Security) -> float:
        own = self.by_symbol.get(sec.symbol, 0.0)
        if sec.dual_listing_group:
            return max(own, self.by_group.get(sec.dual_listing_group, 0.0))
        return own


@dataclass
class BtCandidate:
    sec: Security
    day: pd.Timestamp
    price: float  # the as-of close, in the security's currency
    score: float
    confidence: float
    rank_score: float
    quantity: float
    stop: float
    take_profit: float
    best_rr: float
    stop_type: str


def _room(existing: float, total: float, cap_pct: float) -> float:
    """ILS that may still be added to a bucket worth `existing` when the portfolio is worth
    `total` and the buy comes out of cash (the total does not grow)."""
    return max(0.0, cap_pct / 100.0 * total - existing)


def _bonus(sec: Security, book: Book, risk: RiskFilter, s: Settings) -> float:
    if book.total <= 0:
        return 0.0
    ratios: list[float] = []
    if sec.sector.lower() not in {x.lower() for x in s.diversified_sectors}:
        ratios.append(
            1.0
            - min(1.0, book.sector.get(sec.sector, 0.0) / book.total * 100.0 / risk.max_sector_pct)
        )
    if sec.country.lower() not in {x.lower() for x in s.non_country_labels}:
        ratios.append(
            1.0
            - min(
                1.0, book.country.get(sec.country, 0.0) / book.total * 100.0 / risk.max_country_pct
            )
        )
    return s.screener_diversification_bonus * sum(ratios) / len(ratios) if ratios else 0.0


def evaluate_asof(
    sec: Security,
    df: pd.DataFrame,
    day: pd.Timestamp,
    *,
    risk: RiskFilter,
    horizon: str,
    book: Book,
    cash_ils: float,
    usd_ils: float,
    settings: Settings,
    score_cache: ScoreCache | None = None,
) -> BtCandidate | None:
    """One security on one decision day. None when it fails any gate. `df` must already end at `day`."""
    s = settings
    if df.empty or pd.Timestamp(df.index[-1]) != day:
        return None  # did not trade that day: no price to decide on
    key = (sec.symbol, day)
    cached = score_cache.get(key) if score_cache is not None else None
    if cached is None:
        card = compute_scorecard(sec.symbol, _Frame(df), s)
        cached = (bool(card.get("available")), float(card["total"]), float(card["confidence"]))
        if score_cache is not None:
            score_cache[key] = cached
    available, score, confidence = cached
    if not available:
        return None
    if confidence < s.screener_min_confidence or score < s.screener_min_score:
        return None

    price = float(df["Close"].iloc[-1])
    currency = sec.currency.strip().upper()
    if price <= 0 or currency not in SUPPORTED_CURRENCIES:
        return None
    periods = 365 if sec.asset_type == "crypto" or sec.market == "CRYPTO" else 252
    vol = annualised_volatility_pct(
        df["Close"].to_numpy(dtype=float), s.screener_vol_lookback_days, periods
    )
    vol_cap = s.screener_max_volatility_pct.get(risk.preset or "")
    if vol is not None and vol_cap is not None and vol > vol_cap:
        return None

    price_ils = to_ils(price, currency, usd_ils)
    total = book.total
    limits = [cash_ils]
    if total > 0:
        limits.append(_room(book.existing(sec), total, risk.max_position_pct))
        if sec.sector.lower() not in {x.lower() for x in s.diversified_sectors}:
            limits.append(_room(book.sector.get(sec.sector, 0.0), total, risk.max_sector_pct))
        if sec.country.lower() not in {x.lower() for x in s.non_country_labels}:
            limits.append(_room(book.country.get(sec.country, 0.0), total, risk.max_country_pct))
    spend = min(limits)
    cost_factor = 1.0 + (s.backtest_commission_bps + s.backtest_slippage_bps) / 10_000.0
    qty0 = _floor_qty(spend / (price_ils * cost_factor), sec.asset_type)
    if qty0 <= 0:
        return None

    at = as_utc(datetime(day.year, day.month, day.day, 23, 0))
    quote = PriceQuote(
        symbol=sec.symbol, price=price, currency=currency, as_of=at, source="backtest"
    )
    valued = _valued(sec, quote, usd_ils, qty0, 0)
    res = compute_exit_levels(
        valued, horizon, risk, df, portfolio_value_ils=total, now=at, settings=s
    )
    if res.status != "levels":
        return None
    stop = res.effective_stop
    if stop is None or res.price is None or stop.price >= res.price:
        return None
    rrs = [t.rr for t in res.take_profits if t.rr is not None]
    best_rr = max(rrs) if rrs else 0.0
    if best_rr < risk.min_rr:
        return None
    guide = res.size_guidance
    keep = guide.keep_fraction if guide is not None else 1.0
    qty = _floor_qty(qty0 * keep, sec.asset_type)
    if qty <= 0:
        return None
    # the nearest take-profit that already meets the minimum reward-to-risk
    ok = sorted(t.price for t in res.take_profits if t.rr is not None and t.rr >= risk.min_rr)
    if not ok or not math.isfinite(ok[0]):
        return None
    rank_score = score * confidence + _bonus(sec, book, risk, s)
    return BtCandidate(
        sec=sec,
        day=day,
        price=price,
        score=score,
        confidence=confidence,
        rank_score=round(rank_score, 2),
        quantity=qty,
        stop=stop.price,
        take_profit=ok[0],
        best_rr=best_rr,
        stop_type=risk.stop_type,
    )
