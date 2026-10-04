"""Screener: ranked candidates for new money, from the cached universe scores (no live calls).

The caller gives amount, horizon, risk preset and markets/asset types (no defaults). Every
universe symbol is then checked against the user's limits and either becomes a `Candidate` (with
size, entry, stop, take-profits from `exit_levels.py` and a typed `Explanation`) or lands in
`skipped` with a reason code and a plain sentence.

Rules (CLAUDE.md):
- Reads only caches: `SignalCache` (score card and bars), `PriceQuote`, `Security`. A request never
  fetches anything. No fresh quote -> no levels -> the symbol is dropped with the reason.
- Diversification-aware: a purchase that would push a sector or country over the preset's cap is
  shrunk to fit; when not even one unit fits, the symbol is skipped. Sectors and countries that
  are unrankable ("Diversified", "Global" ...) are not capped, as in the X-ray. Sectors and countries
  the portfolio is light in rank higher.
- The stop is never tightened to fit the filter: the size shrinks instead (exit_levels guidance).
- Candidates are neutral: score and confidence only. A buy/sell-style verdict can only come from
  `LaunchGate.release()`; this module never builds one, so the answer has no verdict field.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.config import DISCLAIMER, Settings, get_settings
from app.models import Holding, Portfolio, PriceQuote, Security, SignalCache
from app.portfolio.valuation import PortfolioValuation, ValuedHolding, value_portfolio
from app.portfolio.xray import _position_override
from app.providers.fx_provider import SUPPORTED_CURRENCIES, to_ils
from app.scoring.exit_levels import ExitLevel, compute_exit_levels
from app.scoring.risk import RiskFilter, resolve_risk_filter
from app.scoring.universe import BARS_PREFIX, bars_frame, universe_securities
from app.signals.base import (
    MAX_ITEMS,
    ChartAnnotation,
    Explanation,
    ExplanationSource,
    SignalContribution,
    as_float_inputs,
)
from app.timeutil import as_utc, utcnow

NOT_VALIDATED_NOTICE = (
    "Scores are not yet validated by a backtest and paper trading. These are neutral candidates "
    "for you to research, not instructions to trade. Not financial advice."
)

SkipCode = Literal[
    "excluded_by_user",
    "no_score",
    "low_score",
    "low_confidence",
    "stale_score",
    "volatility_cap",
    "no_quote",
    "stale_price",
    "no_levels",
    "min_rr",
    "position_cap",
    "sector_cap",
    "country_cap",
    "size_too_small",
    "duplicate_listing",
    "ranked_lower",
]


# ---------------------------------------------------------------- output schemas
class SkippedItem(BaseModel):
    symbol: str
    name_en: str
    code: SkipCode
    reason: str


class SizeOut(BaseModel):
    quantity: float
    cost_native: float
    currency: str
    cost_ils: float
    cost_usd: float
    pct_of_amount: float
    position_pct_after: float | None = None  # this symbol's share of the portfolio after buying
    sector_pct_after: float | None = None
    country_pct_after: float | None = None
    risk_ils: float  # loss at the stop
    risk_pct_of_portfolio: float
    limited_by: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    rank: int
    symbol: str
    name_en: str
    name_he: str
    market: str
    asset_type: str
    sector: str
    country: str
    currency: str
    score: float  # cached combined score in [-100, 100]
    confidence: float  # in [0, 1]
    rank_score: float  # score x confidence + the diversification bonus
    diversification_bonus: float
    score_as_of: datetime
    price: float
    price_as_of: datetime | None = None
    entry: float
    stop: ExitLevel
    take_profits: list[ExitLevel]
    best_rr: float
    annualised_volatility_pct: float
    size: SizeOut
    reasons: list[str]
    explanation: Explanation


class CandidatesOut(BaseModel):
    portfolio_id: int
    generated_at: datetime
    amount: float
    currency: str
    amount_ils: float
    horizon: str
    risk_preset: str
    markets: list[str]
    asset_types: list[str]
    universe_size: int  # symbols that matched the markets / asset types
    candidates: list[Candidate]
    skipped: list[SkippedItem]
    launch_gate_open: bool
    launch_gate_reasons: list[str]
    notice: str = NOT_VALIDATED_NOTICE
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------- helpers
@dataclass
class _Skip(Exception):
    code: SkipCode
    reason: str


def _cut(text: str, n: int = 480) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _headroom(existing: float, total: float, cap_pct: float) -> float:
    """The most ILS that can be added to a bucket worth `existing` (portfolio `total`) so that
    (existing + s) / (total + s) stays at or under `cap_pct`. 0 when it is already over."""
    c = cap_pct / 100.0
    if c >= 1.0:
        return math.inf
    return max(0.0, (c * total - existing) / (1.0 - c))


def annualised_volatility_pct(closes: np.ndarray, lookback: int, periods: int) -> float | None:
    tail = closes[-(lookback + 1) :]
    tail = tail[np.isfinite(tail) & (tail > 0)]
    if len(tail) < 11:
        return None
    rets = np.diff(np.log(tail))
    return float(np.std(rets, ddof=1) * math.sqrt(periods) * 100.0)


def _floor_qty(qty: float, asset_type: str) -> float:
    if asset_type == "crypto":
        return math.floor(qty * 1e6) / 1e6
    return float(math.floor(qty))


def _contrib(payload: dict[str, object]) -> Explanation | None:
    raw = payload.get("explanation")
    try:
        return Explanation.model_validate(raw)
    except Exception:
        return None


@dataclass
class _Book:
    """What the portfolio holds now, in ILS."""

    total: float
    sector: dict[str, float]
    country: dict[str, float]
    by_symbol: dict[str, float]
    by_group: dict[str, float]
    holdings: dict[str, Holding]

    def existing(self, sec: Security) -> float:
        own = self.by_symbol.get(sec.symbol, 0.0)
        if sec.dual_listing_group:
            sibling = self.by_group.get(sec.dual_listing_group, 0.0)
            return max(own, sibling)  # the same company: never double count
        return own


def _book(val: PortfolioValuation) -> _Book:
    sector: dict[str, float] = defaultdict(float)
    country: dict[str, float] = defaultdict(float)
    by_symbol: dict[str, float] = defaultdict(float)
    by_group: dict[str, float] = defaultdict(float)
    holdings: dict[str, Holding] = {}
    for v in val.holdings:
        sector[v.security.sector] += v.value_ils
        country[v.security.country] += v.value_ils
        by_symbol[v.security.symbol] += v.value_ils
        if v.security.dual_listing_group:
            by_group[v.security.dual_listing_group] += v.value_ils
        holdings[v.security.symbol] = v.holding
    return _Book(
        val.total_ils, dict(sector), dict(country), dict(by_symbol), dict(by_group), holdings
    )


def _valued(
    sec: Security, quote: PriceQuote, usd_ils: float, quantity: float, portfolio_id: int
) -> ValuedHolding:
    """A not-yet-owned security shaped as a valued holding, so the exit-levels engine can run."""
    native = quote.price * quantity
    v_ils = to_ils(native, quote.currency, usd_ils)
    return ValuedHolding(
        holding=Holding(
            portfolio_id=portfolio_id,
            symbol=sec.symbol,
            quantity=quantity,
            avg_cost=None,
            cost_currency=quote.currency,
        ),
        security=sec,
        price=quote.price,
        currency=quote.currency,
        stale=False,
        day_change_pct=quote.change_pct or 0.0,
        value_native=native,
        value_ils=v_ils,
        value_usd=v_ils / usd_ils if usd_ils else 0.0,
        day_pnl_ils=0.0,
        pnl_ils=None,
        pnl_usd=None,
        pnl_pct=None,
        as_of=quote.as_of,
        price_source="quote",
        usd_ils=usd_ils,
        quote_source=quote.source,
        price_basis=quote.basis,
        quote_flag=quote.flag,
    )


# ---------------------------------------------------------------- the screener
def screen(
    db: Session,
    portfolio: Portfolio,
    *,
    amount: float,
    currency: str,
    horizon: str,
    risk_preset: str,
    markets: list[str],
    asset_types: list[str],
    exclude_symbols: list[str] | None = None,
    gate_open: bool = False,
    gate_reasons: list[str] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> CandidatesOut:
    s = settings or get_settings()
    assert portfolio.id is not None
    at = as_utc(now or utcnow())
    risk = resolve_risk_filter({"preset": risk_preset}, s)
    val = value_portfolio(db, portfolio, s)
    usd_ils = val.usd_ils
    amount_ils = to_ils(amount, currency, usd_ils)
    book = _book(val)
    blocked = {x.upper() for x in exclude_symbols or []}
    sector_skip = {x.lower() for x in s.diversified_sectors}
    country_skip = {x.lower() for x in s.non_country_labels}
    vol_cap = s.screener_max_volatility_pct.get(risk.preset or risk_preset)

    in_scope = [
        sec
        for sec in universe_securities(db, s)
        if sec.market in markets and sec.asset_type in asset_types
    ]
    built: list[Candidate] = []
    skipped: list[SkippedItem] = []

    for sec in in_scope:
        try:
            built.append(
                _evaluate(
                    db,
                    sec,
                    portfolio,
                    book,
                    risk,
                    vol_cap,
                    amount_ils=amount_ils,
                    usd_ils=usd_ils,
                    horizon=horizon,
                    blocked=blocked,
                    sector_skip=sector_skip,
                    country_skip=country_skip,
                    at=at,
                    s=s,
                )
            )
        except _Skip as sk:
            skipped.append(
                SkippedItem(symbol=sec.symbol, name_en=sec.name_en, code=sk.code, reason=sk.reason)
            )

    built.sort(key=lambda c: (-c.rank_score, c.symbol))
    kept: list[Candidate] = []
    groups: dict[str, str] = {}
    secs = {x.symbol: x for x in in_scope}
    for c in built:
        grp = secs[c.symbol].dual_listing_group
        if grp and grp in groups:
            skipped.append(
                SkippedItem(
                    symbol=c.symbol,
                    name_en=c.name_en,
                    code="duplicate_listing",
                    reason=f"Same company as {groups[grp]}, which ranks higher; it is never counted twice.",
                )
            )
            continue
        if len(kept) >= s.screener_top_n:
            skipped.append(
                SkippedItem(
                    symbol=c.symbol,
                    name_en=c.name_en,
                    code="ranked_lower",
                    reason=f"Passed every filter but ranks below the top {s.screener_top_n}.",
                )
            )
            continue
        if grp:
            groups[grp] = c.symbol
        kept.append(c.model_copy(update={"rank": len(kept) + 1}))

    return CandidatesOut(
        portfolio_id=portfolio.id,
        generated_at=at,
        amount=amount,
        currency=currency,
        amount_ils=round(amount_ils, 2),
        horizon=horizon,
        risk_preset=risk.preset or risk_preset,
        markets=list(markets),
        asset_types=list(asset_types),
        universe_size=len(in_scope),
        candidates=kept,
        skipped=skipped[: s.screener_max_skipped],
        launch_gate_open=gate_open,
        launch_gate_reasons=list(gate_reasons or []),
    )


def _evaluate(
    db: Session,
    sec: Security,
    portfolio: Portfolio,
    book: _Book,
    risk: RiskFilter,
    vol_cap: float | None,
    *,
    amount_ils: float,
    usd_ils: float,
    horizon: str,
    blocked: set[str],
    sector_skip: set[str],
    country_skip: set[str],
    at: datetime,
    s: Settings,
) -> Candidate:
    sym = sec.symbol
    if sym in blocked:
        raise _Skip("excluded_by_user", "You excluded this symbol.")

    # ---- cached score
    row = db.get(SignalCache, sym)
    payload = row.payload if row is not None else {}
    if row is None or not payload.get("available"):
        raise _Skip("no_score", "No cached score yet: none of the signals has data for it.")
    age_h = (at - as_utc(row.computed_at)).total_seconds() / 3600.0
    if age_h > s.screener_max_score_age_hours:
        raise _Skip(
            "stale_score",
            f"The cached score is {age_h:.0f} h old (limit {s.screener_max_score_age_hours} h).",
        )
    score = float(payload.get("total", 0.0))
    confidence = float(payload.get("confidence", 0.0))
    if confidence < s.screener_min_confidence:
        raise _Skip(
            "low_confidence",
            f"Only {confidence:.0%} of the signal weight had data (minimum {s.screener_min_confidence:.0%}).",
        )
    if score < s.screener_min_score:
        raise _Skip(
            "low_score",
            f"Score {score:+.0f} is below the screening minimum {s.screener_min_score:+.0f}.",
        )

    # ---- bars and volatility
    bars_row = db.get(SignalCache, BARS_PREFIX + sym)
    df = bars_frame(bars_row.payload if bars_row is not None else None)
    vol: float | None = None
    if df is not None:
        periods = 365 if sec.asset_type == "crypto" or sec.market == "CRYPTO" else 252
        vol = annualised_volatility_pct(
            df["Close"].to_numpy(dtype=float), s.screener_vol_lookback_days, periods
        )
    if vol is not None and vol_cap is not None and vol > vol_cap:
        raise _Skip(
            "volatility_cap",
            f"Annualised volatility {vol:.0f}% is above the {vol_cap:.0f}% cap of the {risk.preset} preset.",
        )

    # ---- quote
    quote = db.get(PriceQuote, sym)
    if (
        quote is None
        or quote.price <= 0
        or quote.currency.strip().upper() not in SUPPORTED_CURRENCIES
    ):
        raise _Skip("no_quote", "No cached market price for this symbol.")
    price_ils = to_ils(quote.price, quote.currency, usd_ils)

    # ---- size allowed by the amount and the caps
    total = book.total
    existing = book.existing(sec)
    held = book.holdings.get(sym)
    pos_override = _position_override(held.risk_override) if held is not None else None
    pos_cap = pos_override if pos_override is not None else risk.max_position_pct
    limits: list[tuple[float, SkipCode, str]] = [(amount_ils, "size_too_small", "your amount")]
    if total > 0:
        limits.append(
            (
                _headroom(existing, total, pos_cap),
                "position_cap",
                f"single-position limit {pos_cap:g}%",
            )
        )
        if sec.sector.lower() not in sector_skip:
            limits.append(
                (
                    _headroom(book.sector.get(sec.sector, 0.0), total, risk.max_sector_pct),
                    "sector_cap",
                    f"{sec.sector} sector limit {risk.max_sector_pct:g}%",
                )
            )
        if sec.country.lower() not in country_skip:
            limits.append(
                (
                    _headroom(book.country.get(sec.country, 0.0), total, risk.max_country_pct),
                    "country_cap",
                    f"{sec.country} country limit {risk.max_country_pct:g}%",
                )
            )
    spend, bind_code, bind_text = min(limits, key=lambda x: x[0])
    qty0 = _floor_qty(spend / price_ils, sec.asset_type) if price_ils > 0 else 0.0
    if qty0 <= 0:
        if bind_code == "size_too_small":
            raise _Skip(
                "size_too_small", f"Your amount does not cover one unit ({price_ils:,.2f} ILS)."
            )
        raise _Skip(
            bind_code,
            f"Buying even one unit would break your {bind_text}"
            f" (room for {max(spend, 0.0):,.0f} ILS, one unit costs {price_ils:,.2f} ILS).",
        )

    # ---- levels (the engine refuses anything but a fresh live price)
    base_ils = total + amount_ils
    v = _valued(sec, quote, usd_ils, qty0, portfolio.id or 0)
    res = compute_exit_levels(
        v, horizon, risk, df, portfolio_value_ils=base_ils, now=at, settings=s
    )
    if res.status != "levels":
        code: SkipCode = "stale_price" if res.reason_code == "stale_price" else "no_levels"
        raise _Skip(code, res.reason)
    stop = res.effective_stop
    if stop is None or res.price is None or stop.price >= res.price:
        raise _Skip("no_levels", "No usable stop below the price.")
    rrs = [t.rr for t in res.take_profits if t.rr is not None]
    best_rr = max(rrs) if rrs else 0.0
    if best_rr < risk.min_rr:
        raise _Skip(
            "min_rr",
            f"Best reward-to-risk is {best_rr:.1f}, below your minimum {risk.min_rr:g}.",
        )

    # ---- final size: the stop stays where it is, the size shrinks
    guide = res.size_guidance
    keep = guide.keep_fraction if guide is not None else 1.0
    qty = _floor_qty(qty0 * keep, sec.asset_type)
    if qty <= 0:
        raise _Skip(
            "size_too_small",
            "Within your risk limits not even one unit fits at this stop distance.",
        )
    cost_native = qty * quote.price
    cost_ils = qty * price_ils
    limited: list[str] = []
    if cost_ils < amount_ils - price_ils:
        limited.append(
            f"limited by your {bind_text}" if bind_code != "size_too_small" else "whole units only"
        )
    if guide is not None and guide.needed:
        limited.extend(guide.rules)
    gap_ils = to_ils(max(0.0, res.price - stop.price) * qty, quote.currency, usd_ils)
    after_total = total + cost_ils
    pct_after = (lambda x: round(x / after_total * 100.0, 2)) if total > 0 else (lambda x: None)
    size = SizeOut(
        quantity=qty,
        cost_native=round(cost_native, 4),
        currency=quote.currency,
        cost_ils=round(cost_ils, 2),
        cost_usd=round(cost_ils / usd_ils if usd_ils else 0.0, 2),
        pct_of_amount=round(cost_ils / amount_ils * 100.0, 2) if amount_ils else 0.0,
        position_pct_after=pct_after(existing + cost_ils),
        sector_pct_after=pct_after(book.sector.get(sec.sector, 0.0) + cost_ils),
        country_pct_after=pct_after(book.country.get(sec.country, 0.0) + cost_ils),
        risk_ils=round(gap_ils, 2),
        risk_pct_of_portfolio=round(gap_ils / base_ils * 100.0, 4) if base_ils else 0.0,
        limited_by=[_cut(x) for x in limited][:MAX_ITEMS],
    )

    # ---- rank: score x confidence plus a bonus for sectors / countries the portfolio is light in
    ratios: list[float] = []
    if total > 0:
        if sec.sector.lower() not in sector_skip:
            ratios.append(
                1.0
                - min(1.0, book.sector.get(sec.sector, 0.0) / total * 100.0 / risk.max_sector_pct)
            )
        if sec.country.lower() not in country_skip:
            ratios.append(
                1.0
                - min(
                    1.0, book.country.get(sec.country, 0.0) / total * 100.0 / risk.max_country_pct
                )
            )
    bonus = (
        round(s.screener_diversification_bonus * (sum(ratios) / len(ratios)), 2) if ratios else 0.0
    )
    rank_score = round(score * confidence + bonus, 2)

    reasons = [
        f"Cached score {score:+.0f} with {confidence:.0%} of the signal weight backed by data.",
        f"Stop {stop.price:.4g} ({stop.distance_pct:+.1f}%), best reward-to-risk {best_rr:.1f} "
        f"(your minimum {risk.min_rr:g}).",
    ]
    if vol is not None:
        reasons.append(f"Annualised volatility {vol:.0f}% (cap {vol_cap:.0f}% for {risk.preset}).")
    if ratios:
        reasons.append(
            f"Light in your portfolio: {sec.sector} {book.sector.get(sec.sector, 0.0) / total * 100:.0f}%, "
            f"{sec.country} {book.country.get(sec.country, 0.0) / total * 100:.0f}% "
            f"(diversification bonus {bonus:+.1f})."
        )

    explanation = _explain(
        payload,
        res.explanation.annotations,
        sec,
        row.computed_at,
        quote,
        score,
        confidence,
        rank_score,
        bonus,
        best_rr,
        vol,
        risk,
        size,
        stop,
        reasons,
        at,
    )
    return Candidate(
        rank=0,
        symbol=sym,
        name_en=sec.name_en,
        name_he=sec.name_he,
        market=sec.market,
        asset_type=sec.asset_type,
        sector=sec.sector,
        country=sec.country,
        currency=quote.currency,
        score=score,
        confidence=confidence,
        rank_score=rank_score,
        diversification_bonus=bonus,
        score_as_of=as_utc(row.computed_at),
        price=res.price or quote.price,
        price_as_of=res.price_as_of,
        entry=res.price or quote.price,
        stop=stop,
        take_profits=res.take_profits,
        best_rr=round(best_rr, 2),
        annualised_volatility_pct=round(vol, 1) if vol is not None else 0.0,
        size=size,
        reasons=[_cut(r) for r in reasons],
        explanation=explanation,
    )


def _explain(
    payload: dict[str, object],
    level_annotations: list[ChartAnnotation],
    sec: Security,
    scored_at: datetime,
    quote: PriceQuote,
    score: float,
    confidence: float,
    rank_score: float,
    bonus: float,
    best_rr: float,
    vol: float | None,
    risk: RiskFilter,
    size: SizeOut,
    stop: ExitLevel,
    reasons: list[str],
    at: datetime,
) -> Explanation:
    cached = _contrib(payload)
    contributions: list[SignalContribution] = cached.contributions if cached else []
    invalidation = list(cached.invalidation_risks) if cached else []
    sources: list[ExplanationSource] = list(cached.sources) if cached else []
    sources.append(
        ExplanationSource(
            name="Cached universe score",
            as_of=as_utc(scored_at),
            detail="Refreshed by the background universe job, not at request time.",
        )
    )
    sources.append(
        ExplanationSource(
            name="Cached quote",
            as_of=as_utc(quote.as_of),
            detail=f"{quote.source}; the entry price.",
        )
    )
    return Explanation(
        summary=_cut(
            f"{sec.symbol}: score {score:+.0f}, confidence {confidence:.0%}. {reasons[1]} "
            f"Size {size.quantity:g} unit(s), about {size.cost_ils:,.0f} ILS, "
            f"losing {size.risk_ils:,.0f} ILS if the stop is hit. A candidate to research, not an instruction.",
            1900,
        ),
        inputs=as_float_inputs(
            {
                "score": score,
                "confidence": confidence,
                "rank_score": rank_score,
                "diversification_bonus": bonus,
                "best_rr": best_rr,
                "volatility_pct": vol,
                "entry": quote.price,
                "stop": stop.price,
            }
        ),
        rules_applied=[
            "Rank = score x confidence + diversification bonus (sectors/countries the portfolio is light in).",
            "Only cached scores, bars and quotes are used; nothing is fetched while you wait.",
            "Levels come from the exit-levels engine on a fresh market price only.",
        ],
        as_of=as_utc(scored_at),
        contributions=contributions[:MAX_ITEMS],
        annotations=[*(cached.annotations if cached else []), *level_annotations][: MAX_ITEMS * 2],
        risk_rules_applied=[
            _cut(x)
            for x in [
                f"preset {risk.preset}: position {risk.max_position_pct:g}%, sector {risk.max_sector_pct:g}%, "
                f"country {risk.max_country_pct:g}%",
                f"risk per trade {risk.max_portfolio_risk_per_trade_pct:g}% of the portfolio, min reward-to-risk {risk.min_rr:g}",
                *size.limited_by,
            ]
        ][:MAX_ITEMS],
        invalidation_risks=[
            *invalidation,
            "Scores are not backtested yet and can be wrong; a gap can pass the stop.",
        ][:MAX_ITEMS],
        sources=sources[:MAX_ITEMS],
    )
