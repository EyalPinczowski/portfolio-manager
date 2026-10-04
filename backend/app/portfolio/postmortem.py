"""Portfolio post-mortem: why the return differs from what the user expected.

Pure functions over snapshots, transactions, holdings and stored price history. No database, no
network, and no LLM: the text is built from templates, and no LLM provider may be imported here
(`tests/test_postmortem.py` checks the import list). If a model ever phrases this report it may
receive only `PublicFacts`, never the portfolio's numbers.

Method
------
* Return: the daily-chained time-weighted return of `performance.py` (deposits and withdrawals are
  flows, never profit) over the period. The baseline is the last snapshot on or before `start`.
* Reference for the gap: the user's expectation, scaled to the elapsed days by compounding (a
  12-month expectation of 8% is 1.08 ** (days / 365.25 ) - 1 after `days`), and the benchmark that
  matches the holdings' markets (^GSPC for US/crypto, ^TA125.TA for TASE), the US index converted to
  shekels with the USD/ILS history so both sides are in the same currency.
* Per holding: average-cost lots in the holding's own currency, split into realized and unrealized
  profit, and each of those into the local-currency result (valued at the entry rate) and the FX
  effect (value at the later rate minus value at the entry rate). The two always add up to the
  shekel profit. A position held at the start enters at that day's stored close.
* Gap attribution (percentage points of the portfolio's capital): per holding, its local-currency
  result minus the reference return on its share of capital; plus the FX effect. What is left
  (`residual`: time-weighting versus money-weighting, holdings without a usable price, rounding) is
  shown as its own line, never hidden: `attributed + residual == gap` by construction.
* Nothing is invented: a holding with no usable stored close (missing, or older than
  `postmortem_max_close_age_days`), or whose live price is a cost/stale price, is excluded with a
  flag, and its profit lands in the residual.

Wording is descriptive (what happened), never a verdict or advice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.config import DISCLAIMER, Settings
from app.portfolio.performance import DayPoint, benchmark_pct, period_result
from app.signals.base import Explanation, ExplanationSource

EPS = 1e-9
DAYS_PER_MONTH = 30.4375
DAYS_PER_YEAR = 365.25

Status = Literal[
    "ok", "not_recorded", "not_enough_data", "needs_expectation", "no_activity", "no_foreign_assets"
]
Kind = Literal[
    "performance",
    "contribution",
    "timing",
    "concentration",
    "fx",
    "cash",
    "costs",
    "stops",
    "flows",
]
TxKind = Literal["purchase", "sale", "deposit", "withdrawal"]


# ---------------------------------------------------------------- inputs
@dataclass(frozen=True)
class TxIn:
    date: date
    kind: TxKind
    symbol: str | None = None
    quantity: float | None = None
    price: float | None = None  # in `currency`, normalised (TASE in shekels, never agorot)
    amount: float = 0.0
    currency: str = "ILS"
    fx_to_ils: float = 1.0
    fee_ils: float | None = None  # None: fees are not recorded


@dataclass(frozen=True)
class SecInfo:
    symbol: str
    name: str
    market: str  # US | TASE | CRYPTO
    currency: str  # ILS | USD (normalised)
    sector: str = "Unknown"
    country: str = "Unknown"


@dataclass(frozen=True)
class HoldingIn:
    symbol: str
    quantity: float
    price: float | None  # live price in the holding's currency
    price_fresh: bool  # False: stale, screenshot or cost price


@dataclass
class PostmortemInput:
    start: date
    end: date
    today: date
    points: Sequence[DayPoint]
    txs: Sequence[TxIn]
    holdings: Sequence[HoldingIn]
    securities: dict[str, SecInfo]
    closes: dict[str, pd.Series | None]  # stored daily closes per symbol, in its own currency
    fx_closes: pd.Series | None  # USD/ILS (ILS per dollar)
    benchmark_closes: dict[str, pd.Series | None]  # keyed by the benchmark symbol
    expected_return_pct: float | None = None
    expected_return_horizon_months: int | None = None
    max_sector_pct: float | None = None
    max_country_pct: float | None = None
    stops: dict[str, float] = field(default_factory=dict)  # symbol -> saved stop (own currency)


# ---------------------------------------------------------------- outputs
class _Out(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class EvidenceRow(_Out):
    label: str
    value: float | str | None = None
    unit: str | None = None
    note: str | None = None


class Finding(_Out):
    kind: Kind
    status: Status
    headline: str
    amount_ils: float | None = None
    amount_pct: float | None = None  # of the period's capital, or the stated quantity
    gap_contribution_pp: float | None = None  # sum of this finding's lines in the primary gap
    evidence: list[EvidenceRow] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    explanation: Explanation


class GapItem(_Out):
    key: str
    label: str
    symbol: str | None = None
    pp: float  # percentage points of the portfolio's capital
    amount_ils: float


class GapBreakdown(_Out):
    reference: Literal["expectation", "benchmark"]
    status: Literal["ok", "needs_expectation", "not_enough_data"]
    reference_pct: float | None = None
    portfolio_pct: float | None = None
    gap_pp: float | None = None  # portfolio minus reference
    items: list[GapItem] = Field(default_factory=list)  # ranked by size
    attributed_pp: float | None = None
    residual_pp: float | None = None  # gap minus attributed: shown, never hidden
    residual_ils: float | None = None
    reconciles: bool | None = None  # attributed + residual == gap (to 1e-6)
    note: str = ""


class BenchmarkOut(_Out):
    symbol: str
    weight_pct: float  # share of the holdings (by value) this benchmark stands for
    local_pct: float | None = None
    ils_pct: float | None = None
    flags: list[str] = Field(default_factory=list)


class ExpectationOut(_Out):
    status: Literal["ok", "needs_expectation"]
    expected_return_pct: float | None = None
    horizon_months: int | None = None
    expected_for_period_pct: float | None = None
    gap_pp: float | None = None


class HistoryOut(_Out):
    days_available: int
    days_required: int
    days_remaining: int


class HoldingResult(_Out):
    symbol: str
    name: str
    currency: str
    realized_ils: float
    unrealized_ils: float
    local_ils: float  # local-currency result at entry rates
    fx_ils: float
    total_ils: float
    pct_of_capital: float
    still_held: bool


class Excluded(_Out):
    symbol: str
    reason: str


class PostmortemOut(_Out):
    status: Literal["ok", "not_enough_history", "not_enough_data"]
    start: date
    end: date
    days: int
    history: HistoryOut | None = None
    twr_pct: float | None = None
    pnl_ils: float | None = None
    annualised_pct: float | None = None
    annualised_is_extrapolated: bool = False
    capital_ils: float | None = None
    expectation: ExpectationOut
    benchmarks: list[BenchmarkOut] = Field(default_factory=list)
    gap_vs_expectation: GapBreakdown | None = None
    gap_vs_benchmark: GapBreakdown | None = None
    holdings: list[HoldingResult] = Field(default_factory=list)
    top_contributors: list[str] = Field(default_factory=list)
    top_detractors: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    excluded: list[Excluded] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    summary: str = ""
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------- series helpers
def _norm(s: pd.Series | None) -> pd.Series | None:
    if s is None or s.empty:
        return None
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out = pd.Series(s.to_numpy(dtype="float64"), index=idx.normalize()).sort_index()
    out = out[~out.index.duplicated(keep="last")].dropna()
    return out if not out.empty else None


def _close_at(s: pd.Series | None, d: date, max_age_days: int) -> float | None:
    """The close on `d` or the last one before it, only if it is at most `max_age_days` old."""
    if s is None:
        return None
    part = s.loc[: pd.Timestamp(d)]
    if part.empty:
        return None
    if (pd.Timestamp(d) - part.index[-1]).days > max_age_days:
        return None
    v = float(part.iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def _fx(currency: str, fx: pd.Series | None, d: date, max_age: int) -> float | None:
    if currency == "ILS":
        return 1.0
    if currency == "USD":
        return _close_at(fx, d, max_age)
    return None


def _clean(v: float, nd: int = 4) -> float:
    return round(v, nd) if math.isfinite(v) else 0.0


def _ils(x: float) -> str:
    return f"₪{x:,.0f}"


def _pp(x: float) -> str:
    return f"{x:+.2f}"


def _expl(
    summary: str,
    inputs: dict[str, float | str],
    rules: list[str],
    end: date,
    sources: list[str],
    caveats: list[str] | None = None,
) -> Explanation:
    as_of = datetime(end.year, end.month, end.day)
    return Explanation(
        summary=summary[:2000],
        inputs={k: (v if isinstance(v, str) else _clean(v, 6)) for k, v in inputs.items()},
        rules_applied=rules,
        as_of=as_of,
        invalidation_risks=caveats or [],
        sources=[ExplanationSource(name=s, as_of=as_of) for s in sources],
    )


# ---------------------------------------------------------------- expectation
def expected_for_period(pct: float, months: int, days: int) -> float:
    """The expectation scaled to the elapsed days by compounding (never a straight proration)."""
    growth = 1.0 + pct / 100.0
    if growth <= 0:
        return -100.0
    return float((growth ** (days / (months * DAYS_PER_MONTH)) - 1.0) * 100.0)


def annualise(twr_pct: float, days: int) -> float | None:
    growth = 1.0 + twr_pct / 100.0
    if days <= 0 or growth <= 0:
        return None
    try:
        return float((growth ** (DAYS_PER_YEAR / days) - 1.0) * 100.0)
    except OverflowError:
        return None


# ---------------------------------------------------------------- per-holding profit
@dataclass
class _Lots:
    qty: float = 0.0
    cost_local: float = 0.0
    cost_ils: float = 0.0
    realized_local: float = 0.0
    realized_fx: float = 0.0
    capital_ils: float = 0.0  # what was put in: baseline value plus purchases


def _tx_price(t: TxIn) -> float | None:
    if t.price is not None and t.price > 0:
        return t.price
    if t.quantity and t.quantity > 0 and t.amount > 0:
        return t.amount / t.quantity
    return None


def _trades(inp: PostmortemInput, symbol: str) -> list[TxIn]:
    return sorted(
        (
            t
            for t in inp.txs
            if t.symbol == symbol
            and t.kind in ("purchase", "sale")
            and t.quantity
            and t.quantity > 0
            and _tx_price(t) is not None
        ),
        key=lambda t: t.date,
    )


def _qty_at(inp: PostmortemInput, symbol: str, d: date, qty_now: float) -> float:
    """Quantity at the end of day `d`: today's quantity minus the net of later trades."""
    later = sum(
        (t.quantity or 0.0) * (1.0 if t.kind == "purchase" else -1.0)
        for t in _trades(inp, symbol)
        if t.date > d
    )
    return qty_now - later


def _in_period(inp: PostmortemInput, t: TxIn, start: date) -> bool:
    return start < t.date <= inp.end


def _holding_pnl(
    inp: PostmortemInput, symbol: str, start: date, s: Settings
) -> tuple[_Lots, float, float, float, float] | str:
    """Lots, end value (ils), unrealized local, unrealized fx, qty_end; or the reason it is excluded."""
    sec = inp.securities.get(symbol)
    if sec is None:
        return "unknown_security"
    if sec.currency not in ("ILS", "USD"):
        return "unsupported_currency"
    age = s.postmortem_max_close_age_days
    qty_now = next((h.quantity for h in inp.holdings if h.symbol == symbol), 0.0)
    live = next((h for h in inp.holdings if h.symbol == symbol), None)
    qty0 = max(_qty_at(inp, symbol, start, qty_now), 0.0)
    closes = inp.closes.get(symbol)
    lots = _Lots()
    if qty0 > EPS:
        p0 = _close_at(closes, start, age)
        fx0 = _fx(sec.currency, inp.fx_closes, start, age)
        if p0 is None or fx0 is None:
            return "no_start_price"
        lots.qty, lots.cost_local, lots.cost_ils = qty0, qty0 * p0, qty0 * p0 * fx0
        lots.capital_ils = lots.cost_ils
    for t in _trades(inp, symbol):
        if not _in_period(inp, t, start):
            continue
        if t.currency != sec.currency:
            return "currency_mismatch"
        q, p = t.quantity or 0.0, _tx_price(t) or 0.0
        if t.kind == "purchase":
            lots.qty += q
            lots.cost_local += q * p
            lots.cost_ils += q * p * t.fx_to_ils
            lots.capital_ils += q * p * t.fx_to_ils
        else:
            if q > lots.qty + 1e-6 or lots.qty <= EPS:
                return "inconsistent_quantities"
            avg_local = lots.cost_local / lots.qty
            avg_ils = lots.cost_ils / lots.qty
            avg_fx = lots.cost_ils / lots.cost_local if lots.cost_local > EPS else t.fx_to_ils
            lots.realized_local += q * (p - avg_local) * avg_fx
            lots.realized_fx += q * p * (t.fx_to_ils - avg_fx)
            lots.qty -= q
            lots.cost_local -= q * avg_local
            lots.cost_ils -= q * avg_ils
    if lots.qty <= EPS:
        return lots, 0.0, 0.0, 0.0, 0.0
    # end price: the live price when the period ends today and it is a real, fresh one
    p_end: float | None = None
    if inp.end >= inp.today and live is not None and live.price_fresh and live.price:
        p_end = live.price
    if p_end is None:
        p_end = _close_at(closes, inp.end, age)
    fx_end = _fx(sec.currency, inp.fx_closes, inp.end, age)
    if p_end is None or fx_end is None:
        return "stale_price" if live is not None and not live.price_fresh else "no_end_price"
    avg_fx = lots.cost_ils / lots.cost_local if lots.cost_local > EPS else fx_end
    value_local = lots.qty * p_end
    unreal_local = (value_local - lots.cost_local) * avg_fx
    unreal_fx = value_local * (fx_end - avg_fx)
    return lots, value_local * fx_end, unreal_local, unreal_fx, lots.qty


# ---------------------------------------------------------------- timing
def _timing_finding(inp: PostmortemInput, start: date, s: Settings) -> Finding:
    window, fwd_days = s.postmortem_timing_window_days, s.postmortem_forward_days
    rows: list[tuple[float, EvidenceRow]] = []
    purchases = near_high = sales = sales_then_up = 0
    purchase_fwd: list[float] = []
    effect_ils = 0.0
    flags: list[str] = []
    for t in sorted(inp.txs, key=lambda x: x.date):
        if t.kind not in ("purchase", "sale") or t.symbol is None or not _in_period(inp, t, start):
            continue
        closes = _norm(inp.closes.get(t.symbol))
        price = _tx_price(t)
        if closes is None or price is None or not t.quantity:
            flags.append(f"no_history:{t.symbol}")
            continue
        before = closes.loc[pd.Timestamp(t.date - timedelta(days=window)) : pd.Timestamp(t.date)]
        after = closes.loc[pd.Timestamp(t.date) + timedelta(days=1) :]
        fwd_close: float | None = None
        if not after.empty:
            actual = (after.index[-1].date() - t.date).days
            target = after.loc[: pd.Timestamp(t.date + timedelta(days=fwd_days))]
            if actual >= min(5, fwd_days) and not target.empty:
                fwd_close = float(target.iloc[-1])
        fwd_pct = (fwd_close / price - 1.0) * 100.0 if fwd_close else None
        note_parts: list[str] = []
        if t.kind == "purchase":
            purchases += 1
            if len(before) >= 10:
                top = float(before.max())
                ratio = price / top * 100.0
                note_parts.append(f"{ratio:.0f}% of the {window}-day high")
                near_high += int(ratio >= s.postmortem_near_high_pct)
            if fwd_pct is not None:
                purchase_fwd.append(fwd_pct)
        else:
            sales += 1
            if fwd_pct is not None and fwd_pct >= s.postmortem_material_move_pct:
                sales_then_up += 1
        if fwd_close is not None:
            change = t.quantity * (fwd_close - price) * t.fx_to_ils
            effect_ils += change if t.kind == "purchase" else -change
        label = f"{t.date.isoformat()} {'purchase' if t.kind == 'purchase' else 'sale'} {t.symbol}"
        rows.append(
            (
                abs(fwd_pct or 0.0),
                EvidenceRow(
                    label=label,
                    value=_clean(fwd_pct, 2) if fwd_pct is not None else None,
                    unit=f"% price change in the {fwd_days} days after",
                    note="; ".join(note_parts) or None,
                ),
            )
        )
    inputs: dict[str, float | str] = {
        "window_days": window,
        "forward_days": fwd_days,
        "near_high_pct": s.postmortem_near_high_pct,
        "material_move_pct": s.postmortem_material_move_pct,
    }
    rules = [
        f"a purchase is 'near a local high' at {s.postmortem_near_high_pct:g}% or more of the "
        f"{window}-day high",
        f"a sale counts as 'followed by gains' if the price rose {s.postmortem_material_move_pct:g}% "
        f"or more in the next {fwd_days} days",
        "stored price history only; amounts are estimates of the value change of the traded shares",
    ]
    if purchases + sales == 0:
        return Finding(
            kind="timing",
            status="no_activity",
            headline="No purchases or sales were recorded in this period.",
            explanation=_expl("No trades in the period.", inputs, rules, inp.end, ["transactions"]),
            flags=flags,
        )
    avg_fwd = sum(purchase_fwd) / len(purchase_fwd) if purchase_fwd else None
    parts = [
        f"{purchases} purchase(s), {near_high} of them at {s.postmortem_near_high_pct:g}% or more "
        f"of the {window}-day high",
        f"{sales} sale(s), {sales_then_up} followed by a rise of {s.postmortem_material_move_pct:g}% "
        f"or more within {fwd_days} days",
    ]
    if avg_fwd is not None:
        parts.append(f"average price change after purchases {avg_fwd:+.1f}%")
    headline = "; ".join(parts) + "."
    inputs.update(
        {
            "purchases": purchases,
            "purchases_near_high": near_high,
            "sales": sales,
            "sales_then_gains": sales_then_up,
            "estimated_effect_ils": effect_ils,
        }
    )
    rows.sort(key=lambda r: r[0], reverse=True)
    return Finding(
        kind="timing",
        status="ok",
        headline=headline,
        amount_ils=_clean(effect_ils, 2),
        evidence=[r for _, r in rows[:12]],
        flags=sorted(set(flags)),
        explanation=_expl(
            headline,
            inputs,
            rules,
            inp.end,
            ["transactions", "stored price history"],
            ["The estimate overlaps the per-holding results; it is not an extra gain or loss."],
        ),
    )


# ---------------------------------------------------------------- concentration
def _sample_dates(start: date, end: date) -> list[date]:
    out = [start]
    d = start + timedelta(days=7)
    while d < end:
        out.append(d)
        d += timedelta(days=7)
    if end != start:
        out.append(end)
    return out


def _values_at(
    inp: PostmortemInput, d: date, symbols: list[str], s: Settings
) -> tuple[dict[str, float], int]:
    out: dict[str, float] = {}
    missing = 0
    for sym in symbols:
        sec = inp.securities.get(sym)
        qty_now = next((h.quantity for h in inp.holdings if h.symbol == sym), 0.0)
        qty = _qty_at(inp, sym, d, qty_now)
        if qty <= EPS:
            continue
        age = s.postmortem_max_close_age_days
        p = _close_at(inp.closes.get(sym), d, age)
        fx = _fx(sec.currency, inp.fx_closes, d, age) if sec else None
        if p is None or fx is None:
            missing += 1
            continue
        out[sym] = qty * p * fx
    return out, missing


def _concentration_finding(inp: PostmortemInput, start: date, s: Settings) -> Finding:
    symbols = sorted(
        {h.symbol for h in inp.holdings}
        | {t.symbol for t in inp.txs if t.symbol and t.kind in ("purchase", "sale")}
    )
    series: list[tuple[date, dict[str, float]]] = []
    missing_total = 0
    for d in _sample_dates(start, inp.end):
        vals, miss = _values_at(inp, d, symbols, s)
        missing_total += miss
        if vals:
            series.append((d, vals))
    inputs: dict[str, float | str] = {}
    rules = [
        "weights are quantity x stored close x USD/ILS on weekly sample dates",
        "only holdings with a usable stored close are counted",
    ]
    if not series:
        return Finding(
            kind="concentration",
            status="not_enough_data",
            headline="No weights could be rebuilt from the stored prices.",
            explanation=_expl(
                "No usable prices.", inputs, rules, inp.end, ["stored price history"]
            ),
        )
    peak_share, peak_sym, peak_day = 0.0, "", series[0][0]
    for d, vals in series:
        tot = sum(vals.values())
        sym, v = max(vals.items(), key=lambda kv: kv[1])
        if tot > 0 and v / tot * 100.0 > peak_share:
            peak_share, peak_sym, peak_day = v / tot * 100.0, sym, d
    first, last = series[0], series[-1]

    def group(vals: dict[str, float], attr: Literal["sector", "country"]) -> dict[str, float]:
        tot = sum(vals.values())
        acc: dict[str, float] = {}
        for sym, v in vals.items():
            sec = inp.securities.get(sym)
            key = getattr(sec, attr, "Unknown") if sec else "Unknown"
            acc[key] = acc.get(key, 0.0) + v / tot * 100.0
        return acc

    rows: list[EvidenceRow] = []
    tot_last = sum(last[1].values())
    top_end = sorted(last[1].items(), key=lambda kv: kv[1], reverse=True)[:5]
    for sym, v in top_end:
        rows.append(
            EvidenceRow(
                label=f"weight at {last[0]} {sym}", value=_clean(v / tot_last * 100, 2), unit="%"
            )
        )
    rows.append(
        EvidenceRow(
            label="largest position share in the period",
            value=_clean(peak_share, 2),
            unit="%",
            note=f"{peak_sym} on {peak_day.isoformat()}",
        )
    )
    flags: list[str] = []
    for attr, cap in (("sector", inp.max_sector_pct), ("country", inp.max_country_pct)):
        g0, g1 = group(first[1], attr), group(last[1], attr)  # type: ignore[arg-type]
        for key in sorted(set(g0) | set(g1), key=lambda k: g1.get(k, 0.0), reverse=True)[:6]:
            note = None
            if cap is not None and g1.get(key, 0.0) > cap + EPS:
                note = f"above the {cap:g}% cap at the end"
                flags.append(f"{attr}_over_cap:{key}")
            rows.append(
                EvidenceRow(
                    label=f"{attr} {key}: {first[0]} to {last[0]}",
                    value=_clean(g1.get(key, 0.0) - g0.get(key, 0.0), 2),
                    unit="percentage points change",
                    note=note,
                )
            )
    if missing_total:
        flags.append("some_holdings_without_price_on_sample_dates")
    inputs.update(
        {
            "largest_share_pct": peak_share,
            "samples": len(series),
            "max_sector_pct": inp.max_sector_pct if inp.max_sector_pct is not None else "not set",
            "max_country_pct": inp.max_country_pct
            if inp.max_country_pct is not None
            else "not set",
        }
    )
    headline = (
        f"The largest position reached {peak_share:.1f}% of the priced holdings ({peak_sym}, "
        f"{peak_day.isoformat()}); at the end it was {top_end[0][1] / tot_last * 100:.1f}% ({top_end[0][0]})."
    )
    return Finding(
        kind="concentration",
        status="ok",
        headline=headline,
        evidence=rows,
        flags=flags,
        explanation=_expl(
            headline, inputs, rules, inp.end, ["stored price history", "transactions"]
        ),
    )


# ---------------------------------------------------------------- main
def _benchmarks(
    inp: PostmortemInput, start: date, end_values: dict[str, float], s: Settings
) -> tuple[list[BenchmarkOut], float | None]:
    """Benchmarks weighted by where the holdings are; the blended shekel return (or None)."""
    by_bench: dict[str, float] = {}
    for sym, v in end_values.items():
        sec = inp.securities.get(sym)
        if sec is None:
            continue
        key = s.benchmark_ta125 if sec.market == "TASE" else s.benchmark_sp500
        by_bench[key] = by_bench.get(key, 0.0) + v
    total = sum(by_bench.values())
    if total <= 0:
        return [], None
    outs: list[BenchmarkOut] = []
    blend, wsum = 0.0, 0.0
    age = s.postmortem_max_close_age_days
    for sym, v in sorted(by_bench.items()):
        flags: list[str] = []
        local = benchmark_pct(_norm(inp.benchmark_closes.get(sym)), start, inp.end)
        ils = local
        if local is not None and sym == s.benchmark_sp500:
            f0, f1 = _fx("USD", inp.fx_closes, start, age), _fx("USD", inp.fx_closes, inp.end, age)
            if f0 and f1:
                ils = ((1 + local / 100.0) * (f1 / f0) - 1.0) * 100.0
            else:
                flags.append("not_fx_adjusted")
        if local is None:
            flags.append("no_history")
        outs.append(
            BenchmarkOut(
                symbol=sym,
                weight_pct=_clean(v / total * 100.0, 2),
                local_pct=_clean(local, 4) if local is not None else None,
                ils_pct=_clean(ils, 4) if ils is not None else None,
                flags=flags,
            )
        )
        if ils is not None:
            blend += ils * v / total
            wsum += v / total
    return outs, (blend / wsum if wsum > EPS else None)


def _gap(
    reference: Literal["expectation", "benchmark"],
    ref_pct: float | None,
    twr_pct: float,
    holdings: list[tuple[str, float, float]],  # (symbol, local pp, capital share 0..1)
    fx_pp: float,
    capital: float,
    note_missing: str = "",
) -> GapBreakdown:
    if ref_pct is None:
        return GapBreakdown(
            reference=reference,
            status="needs_expectation" if reference == "expectation" else "not_enough_data",
            note=note_missing,
        )
    gap = twr_pct - ref_pct
    items = [
        GapItem(
            key=f"holding:{sym}",
            label=f"{sym}: local-currency result minus the reference on its share of capital",
            symbol=sym,
            pp=_clean(pp - ref_pct * share, 6),
            amount_ils=_clean((pp - ref_pct * share) / 100.0 * capital, 2),
        )
        for sym, pp, share in holdings
    ]
    items.append(
        GapItem(
            key="fx",
            label="USD/ILS movement on foreign holdings",
            pp=_clean(fx_pp, 6),
            amount_ils=_clean(fx_pp / 100.0 * capital, 2),
        )
    )
    items.sort(key=lambda i: abs(i.pp), reverse=True)
    attributed = sum(i.pp for i in items)
    residual = gap - attributed
    return GapBreakdown(
        reference=reference,
        status="ok",
        reference_pct=_clean(ref_pct, 6),
        portfolio_pct=_clean(twr_pct, 6),
        gap_pp=_clean(gap, 6),
        items=items,
        attributed_pp=_clean(attributed, 6),
        residual_pp=_clean(residual, 6),
        residual_ils=_clean(residual / 100.0 * capital, 2),
        reconciles=abs(attributed + residual - gap) < 1e-6,
        note=(
            "The residual is what the lines above do not explain: time-weighting versus money-"
            "weighting of deposits and withdrawals, holdings without a usable price, rounding."
        ),
    )


def build_postmortem(inp: PostmortemInput, s: Settings) -> PostmortemOut:
    pts = sorted(inp.points, key=lambda p: p.date)
    end = min(inp.end, inp.today)
    baseline = [p for p in pts if p.date <= inp.start]
    in_range = [p for p in pts if inp.start < p.date <= end]
    exp_empty = ExpectationOut(
        status=(
            "ok"
            if inp.expected_return_pct is not None and inp.expected_return_horizon_months
            else "needs_expectation"
        ),
        expected_return_pct=inp.expected_return_pct,
        horizon_months=inp.expected_return_horizon_months,
    )
    if not baseline and in_range:  # the series starts inside the requested period
        baseline, in_range = [in_range[0]], in_range[1:]
    if not baseline:
        return PostmortemOut(
            status="not_enough_history",
            start=inp.start,
            end=end,
            days=0,
            history=HistoryOut(
                days_available=0,
                days_required=s.postmortem_min_days,
                days_remaining=s.postmortem_min_days,
            ),
            expectation=exp_empty,
            summary=f"No history yet; at least {s.postmortem_min_days} days are needed.",
        )
    base = baseline[-1]
    start = base.date
    series = [base, *in_range]
    last_date = series[-1].date
    days = (last_date - start).days
    if days < s.postmortem_min_days:
        remaining = s.postmortem_min_days - days
        return PostmortemOut(
            status="not_enough_history",
            start=start,
            end=last_date,
            days=days,
            history=HistoryOut(
                days_available=days, days_required=s.postmortem_min_days, days_remaining=remaining
            ),
            expectation=exp_empty,
            summary=(
                f"{days} day(s) of history; {remaining} more day(s) are needed before a "
                "post-mortem can be built. No figures are shown until then."
            ),
        )
    inp = replace(inp, start=start, end=last_date)  # normalised period
    res = period_result(series, None)
    twr, pnl = res.pct, res.pnl_ils
    capital = base.value_ils + sum(max(p.net_flow_ils, 0.0) for p in in_range)
    ann = annualise(twr, days)
    flags: list[str] = []
    if capital <= 0:
        return PostmortemOut(
            status="not_enough_data",
            start=start,
            end=last_date,
            days=days,
            twr_pct=twr,
            pnl_ils=pnl,
            expectation=exp_empty,
            summary="The portfolio had no value in the period, so no shares of capital exist.",
        )

    # ---- per holding
    symbols = sorted(
        {h.symbol for h in inp.holdings}
        | {t.symbol for t in inp.txs if t.symbol and t.kind in ("purchase", "sale")}
    )
    results: list[HoldingResult] = []
    shares: list[tuple[str, float, float]] = []
    excluded: list[Excluded] = []
    end_values: dict[str, float] = {}
    fx_total = local_total = realized_total = unrealized_total = 0.0
    for sym in symbols:
        r = _holding_pnl(inp, sym, start, s)
        if isinstance(r, str):
            excluded.append(Excluded(symbol=sym, reason=r))
            continue
        lots, end_value, un_local, un_fx, qty_end = r
        if lots.capital_ils <= 0 and lots.realized_local == 0 and lots.realized_fx == 0:
            continue
        realized = lots.realized_local + lots.realized_fx
        unrealized = un_local + un_fx
        local, fx = lots.realized_local + un_local, lots.realized_fx + un_fx
        sec = inp.securities[sym]
        results.append(
            HoldingResult(
                symbol=sym,
                name=sec.name,
                currency=sec.currency,
                realized_ils=_clean(realized, 2),
                unrealized_ils=_clean(unrealized, 2),
                local_ils=_clean(local, 2),
                fx_ils=_clean(fx, 2),
                total_ils=_clean(realized + unrealized, 2),
                pct_of_capital=_clean((realized + unrealized) / capital * 100.0, 4),
                still_held=qty_end > EPS,
            )
        )
        shares.append((sym, local / capital * 100.0, lots.capital_ils / capital))
        fx_total += fx
        local_total += local
        realized_total += realized
        unrealized_total += unrealized
        if end_value > 0:
            end_values[sym] = end_value
    fx_pp = fx_total / capital * 100.0
    results.sort(key=lambda h: h.total_ils, reverse=True)
    n = s.postmortem_top_n
    top_c = [h.symbol for h in results if h.total_ils > 0][:n]
    top_d = [h.symbol for h in reversed(results) if h.total_ils < 0][:n]
    if excluded:
        flags.append("some_holdings_excluded_see_excluded")

    # ---- references
    exp_pct: float | None = None
    if exp_empty.status == "ok":
        assert inp.expected_return_pct is not None and inp.expected_return_horizon_months
        exp_pct = expected_for_period(
            inp.expected_return_pct, inp.expected_return_horizon_months, days
        )
    expectation = ExpectationOut(
        status=exp_empty.status,
        expected_return_pct=inp.expected_return_pct,
        horizon_months=inp.expected_return_horizon_months,
        expected_for_period_pct=_clean(exp_pct, 4) if exp_pct is not None else None,
        gap_pp=_clean(twr - exp_pct, 4) if exp_pct is not None else None,
    )
    bench_values = end_values or {h.symbol: 1.0 for h in inp.holdings if h.symbol in inp.securities}
    benchmarks, bench_pct = _benchmarks(inp, start, bench_values, s)
    g_exp = _gap("expectation", exp_pct, twr, shares, fx_pp, capital)
    g_bench = _gap(
        "benchmark",
        bench_pct,
        twr,
        shares,
        fx_pp,
        capital,
        "No benchmark history is stored for these holdings." if bench_pct is None else "",
    )
    primary = g_exp if g_exp.status == "ok" else g_bench

    # ---- findings
    findings: list[Finding] = []
    rules_core = [
        "time-weighted return: deposits and withdrawals are flows, never profit",
        "gap = portfolio return minus the reference; lines + residual = gap",
    ]
    ann_txt = f", about {ann:.1f}% a year if the pace held" if ann is not None else ""
    parts = [f"Time-weighted return {twr:.2f}% over {days} days ({_ils(pnl)} profit)" + ann_txt]
    if exp_pct is not None:
        parts.append(
            f"expectation scaled to the period {exp_pct:.2f}% (gap {_pp(twr - exp_pct)} pp)"
        )
    if bench_pct is not None:
        parts.append(
            f"matching benchmark in shekels {bench_pct:.2f}% (gap {_pp(twr - bench_pct)} pp)"
        )
    perf_head = "; ".join(parts) + "."
    perf_rows = [
        EvidenceRow(label="time-weighted return", value=_clean(twr, 4), unit="%"),
        EvidenceRow(label="profit, flows excluded", value=_clean(pnl, 2), unit="ILS"),
        EvidenceRow(
            label="capital put in (start value + inflows)", value=_clean(capital, 2), unit="ILS"
        ),
    ]
    for b in benchmarks:
        perf_rows.append(
            EvidenceRow(
                label=f"{b.symbol} ({b.weight_pct:.0f}% of holdings)",
                value=b.ils_pct,
                unit="% in ILS",
                note=f"local {b.local_pct}%" if b.local_pct is not None else "no history",
            )
        )
    findings.append(
        Finding(
            kind="performance",
            status="ok",
            headline=perf_head,
            amount_ils=_clean(pnl, 2),
            amount_pct=_clean(twr, 4),
            evidence=perf_rows,
            flags=[] if exp_pct is not None else ["needs_expectation"],
            explanation=_expl(
                perf_head,
                {"twr_pct": twr, "pnl_ils": pnl, "days": days, "capital_ils": capital},
                [
                    *rules_core,
                    "expectation scaled by compounding over the elapsed days",
                    "US index converted to shekels with the stored USD/ILS history",
                ],
                last_date,
                ["portfolio snapshots", "stored benchmark history"],
                ["Annualised figures are extrapolated when the period is under a year."]
                if days < 365
                else [],
            ),
        )
    )
    holding_items = [i for i in primary.items if i.symbol]
    contrib_rows = [
        EvidenceRow(
            label=f"{h.symbol}",
            value=h.total_ils,
            unit="ILS",
            note=(
                f"realized {_ils(h.realized_ils)}, unrealized {_ils(h.unrealized_ils)}, "
                f"{h.pct_of_capital:+.2f}% of capital"
            ),
        )
        for h in results
    ]
    c_head = (
        f"Realized {_ils(realized_total)} and unrealized {_ils(unrealized_total)} across "
        f"{len(results)} holding(s); largest positive: {', '.join(top_c) or 'none'}; "
        f"largest negative: {', '.join(top_d) or 'none'}."
    )
    findings.append(
        Finding(
            kind="contribution",
            status="ok" if results else "not_enough_data",
            headline=c_head,
            amount_ils=_clean(realized_total + unrealized_total, 2),
            amount_pct=_clean((realized_total + unrealized_total) / capital * 100.0, 4),
            gap_contribution_pp=(
                _clean(sum(i.pp for i in holding_items), 6) if primary.status == "ok" else None
            ),
            evidence=contrib_rows,
            flags=[f"excluded:{e.symbol}:{e.reason}" for e in excluded],
            explanation=_expl(
                c_head,
                {"realized_ils": realized_total, "unrealized_ils": unrealized_total},
                [
                    "average-cost lots in each holding's own currency",
                    "a position held at the start enters at its stored close that day",
                    "holdings without a usable stored or fresh live price are excluded and flagged",
                ],
                last_date,
                ["transactions", "stored price history", "portfolio snapshots"],
                ["Excluded holdings' profit is part of the residual."] if excluded else [],
            ),
        )
    )
    findings.append(_timing_finding(inp, start, s))
    findings.append(_concentration_finding(inp, start, s))

    has_foreign = any(h.currency != "ILS" for h in results)
    usd0, usd1 = (
        _fx("USD", inp.fx_closes, d, s.postmortem_max_close_age_days) for d in (start, last_date)
    )
    fx_move = (usd1 / usd0 - 1.0) * 100.0 if usd0 and usd1 else None
    fx_head = (
        f"Currency movement on foreign holdings: {_ils(fx_total)} ({_pp(fx_pp)} pp of capital); "
        f"local-currency result valued at entry rates: {_ils(local_total)}."
        + (f" USD/ILS moved {fx_move:+.2f}% over the period." if fx_move is not None else "")
    )
    findings.append(
        Finding(
            kind="fx",
            status="ok" if has_foreign else "no_foreign_assets",
            headline=fx_head if has_foreign else "No foreign-currency holdings in the period.",
            amount_ils=_clean(fx_total, 2),
            amount_pct=_clean(fx_pp, 4),
            gap_contribution_pp=_clean(fx_pp, 6) if primary.status == "ok" else None,
            evidence=[
                EvidenceRow(label="FX effect", value=_clean(fx_total, 2), unit="ILS"),
                EvidenceRow(
                    label="local-currency result", value=_clean(local_total, 2), unit="ILS"
                ),
                EvidenceRow(
                    label="USD/ILS change",
                    value=_clean(fx_move, 4) if fx_move is not None else None,
                    unit="%",
                ),
            ],
            explanation=_expl(
                fx_head,
                {"fx_ils": fx_total, "local_ils": local_total},
                [
                    "FX effect = value at the later rate minus value at the entry rate",
                    "local-currency result = price change valued at the entry rate",
                    "the two add up to each holding's shekel profit",
                ],
                last_date,
                ["transactions (FX on trade date)", "stored USD/ILS history"],
            ),
        )
    )
    findings.append(
        Finding(
            kind="cash",
            status="not_recorded",
            headline="Cash balances are not recorded, so the share of cash in the portfolio is unknown.",
            explanation=_expl(
                "Cash is not stored in snapshots.",
                {},
                ["no cash figure is invented"],
                last_date,
                ["portfolio snapshots"],
            ),
        )
    )
    fees = [t.fee_ils for t in inp.txs if t.fee_ils is not None and _in_period(inp, t, start)]
    if fees:
        fee_total = sum(fees)
        findings.append(
            Finding(
                kind="costs",
                status="ok",
                headline=f"Recorded fees in the period: {_ils(fee_total)}.",
                amount_ils=_clean(fee_total, 2),
                amount_pct=_clean(fee_total / capital * 100.0, 4),
                explanation=_expl(
                    f"Recorded fees {_ils(fee_total)}.",
                    {"fees_ils": fee_total},
                    ["sum of fees stored on transactions"],
                    last_date,
                    ["transactions"],
                ),
            )
        )
    else:
        findings.append(
            Finding(
                kind="costs",
                status="not_recorded",
                headline="Fees and commissions are not recorded on transactions.",
                explanation=_expl(
                    "No fee data.", {}, ["no cost is estimated"], last_date, ["transactions"]
                ),
            )
        )
    findings.append(_stops_finding(inp, start, last_date, s))
    net_flow = sum(p.net_flow_ils for p in in_range)
    dep = sum(
        t.amount * t.fx_to_ils for t in inp.txs if t.kind == "deposit" and _in_period(inp, t, start)
    )
    wd = sum(
        t.amount * t.fx_to_ils
        for t in inp.txs
        if t.kind == "withdrawal" and _in_period(inp, t, start)
    )
    fl_head = (
        f"Net flows of {_ils(net_flow)} (deposits {_ils(dep)}, withdrawals {_ils(wd)}, the rest "
        "positions added or removed) are not counted as profit."
    )
    findings.append(
        Finding(
            kind="flows",
            status="ok",
            headline=fl_head,
            amount_ils=_clean(net_flow, 2),
            evidence=[
                EvidenceRow(label="deposits", value=_clean(dep, 2), unit="ILS"),
                EvidenceRow(label="withdrawals", value=_clean(wd, 2), unit="ILS"),
                EvidenceRow(
                    label="value change minus flows = profit", value=_clean(pnl, 2), unit="ILS"
                ),
            ],
            explanation=_expl(
                fl_head,
                {"net_flow_ils": net_flow, "profit_ils": pnl},
                ["profit = end value - start value - net flows"],
                last_date,
                ["portfolio snapshots", "transactions"],
            ),
        )
    )

    summary = perf_head
    if primary.status == "ok" and primary.items:
        top = primary.items[0]
        summary += (
            f" Largest line in the gap against the {primary.reference}: {top.label} "
            f"({_pp(top.pp)} pp); residual {_pp(primary.residual_pp or 0.0)} pp."
        )
    elif exp_empty.status == "needs_expectation":
        summary += " No expectation is set (needs_expectation)."
    return PostmortemOut(
        status="ok",
        start=start,
        end=last_date,
        days=days,
        twr_pct=_clean(twr, 4),
        pnl_ils=_clean(pnl, 2),
        annualised_pct=_clean(ann, 4) if ann is not None else None,
        annualised_is_extrapolated=days < 365,
        capital_ils=_clean(capital, 2),
        expectation=expectation,
        benchmarks=benchmarks,
        gap_vs_expectation=g_exp,
        gap_vs_benchmark=g_bench,
        holdings=results,
        top_contributors=top_c,
        top_detractors=top_d,
        findings=findings,
        excluded=excluded,
        flags=flags,
        summary=summary,
    )


def _stops_finding(inp: PostmortemInput, start: date, end: date, s: Settings) -> Finding:
    if not inp.stops:
        return Finding(
            kind="stops",
            status="not_recorded",
            headline="No stop levels are saved, so stops cannot be checked against the period.",
            explanation=_expl(
                "No saved stops.", {}, ["only saved stops are checked"], end, ["saved stops"]
            ),
        )
    rows: list[EvidenceRow] = []
    for sym, stop in sorted(inp.stops.items()):
        closes = _norm(inp.closes.get(sym))
        if closes is None:
            continue
        part = closes.loc[pd.Timestamp(start) + timedelta(days=1) : pd.Timestamp(end)]
        hit = part[part <= stop]
        rows.append(
            EvidenceRow(
                label=sym,
                value=float(hit.iloc[0]) if not hit.empty else None,
                unit="first close at or below the stop",
                note=hit.index[0].date().isoformat() if not hit.empty else "not reached",
            )
        )
    hit_n = sum(1 for r in rows if r.value is not None)
    head = f"{hit_n} of {len(rows)} saved stop level(s) were reached by a close in the period."
    return Finding(
        kind="stops",
        status="ok",
        headline=head,
        evidence=rows,
        explanation=_expl(
            head,
            {"stops_checked": len(rows), "stops_reached": hit_n},
            ["a stop is reached when a stored daily close is at or below it"],
            end,
            ["saved stops", "stored price history"],
            ["Intraday moves are not in daily closes."],
        ),
    )


__all__ = [
    "EvidenceRow",
    "Finding",
    "GapBreakdown",
    "GapItem",
    "HoldingIn",
    "PostmortemInput",
    "PostmortemOut",
    "SecInfo",
    "TxIn",
    "annualise",
    "build_postmortem",
    "expected_for_period",
]
