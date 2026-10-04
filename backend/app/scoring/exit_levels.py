"""Exit levels: stop-loss and take-profit levels for one holding, from a horizon and a risk filter.

The numbers per horizon (chart, ATR multiple, moving average, take-profit sources) are the README
table, kept in `Settings.horizon_table`; every other tunable is an `exit_levels_*` setting.

Rules (CLAUDE.md):
- There is no default horizon. `horizon=None` answers `needs_horizon` and computes nothing.
- The price is `exit_level_price()`: a stale, cost, screenshot or last-close price answers
  `no_levels` with the reason, never a guess.
- A stop is never quietly tightened to fit the risk filter. When the chart stop is further away
  than the filter allows, the stop stays where it is and `size_guidance` says how many shares fit.
- A stop only moves up: a saved stop (`StopState`) is never lowered, and a trailing stop is a
  ratchet over the highest stop so far.
- Every level carries a one-line reason and a typed `Explanation`.

Pure code: no provider is called here (the caller passes the history) and no verdict word is used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config import DISCLAIMER, HorizonSpec, ScaleOutPlanSpec, Settings, get_settings
from app.portfolio.freshness import StalePriceError, exit_level_price
from app.portfolio.valuation import ValuedHolding
from app.providers.fx_provider import to_ils
from app.scoring.risk import RiskFilter
from app.signals import indicators as ind
from app.signals.base import (
    ChartAnnotation,
    Explanation,
    ExplanationSource,
    as_float_inputs,
    price_history_source,
)
from app.signals.patterns import cluster_levels, find_pivots
from app.timeutil import as_utc

Reason = Annotated[str, StringConstraints(max_length=500)]
FINITE = ConfigDict(allow_inf_nan=False)

LevelKind = Literal["stop", "trailing_stop", "breakeven", "take_profit"]
StopSource = Literal["atr", "structure", "moving_average", "max_loss", "saved_stop", "trailing"]
ResultStatus = Literal["levels", "needs_horizon", "no_levels"]
ReasonCode = Literal[
    "needs_horizon",
    "stale_price",
    "no_history",
    "insufficient_history",
    "history_price_mismatch",
    "fund_no_levels",
]
StopFit = Literal["ok", "too_tight", "too_wide"]


class Horizon(StrEnum):
    W1 = "1w"
    M1 = "1m"
    M3 = "3m"
    M6 = "6m"
    Y1 = "1y"


# ---------------------------------------------------------------- models
class StopState(BaseModel):
    """What a trailing stop remembers between runs: the highest high seen and the stop so far."""

    model_config = FINITE

    highest_high: float | None = Field(default=None, gt=0)
    stop: float | None = Field(default=None, gt=0)

    def ratchet(self, new_stop: float | None, new_high: float | None) -> StopState:
        """The state after a new reading. Both numbers only ever go up."""
        return StopState(
            highest_high=_max_opt(self.highest_high, new_high),
            stop=_max_opt(self.stop, new_stop),
        )


def _max_opt(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


class AnalystTargets(BaseModel):
    """Optional analyst price targets in the price's own currency (no provider feeds this yet)."""

    model_config = FINITE

    mean: float | None = Field(default=None, gt=0)
    high: float | None = Field(default=None, gt=0)


class ExitLevel(BaseModel):
    model_config = FINITE

    kind: LevelKind
    label: Reason
    price: float
    distance_pct: float  # from the current price (negative: below it)
    # Change of the position's value between now and this level.
    vs_price_ils: float
    vs_price_usd: float
    # P&L against the holding's cost at this level; null without a cost.
    pnl_native: float | None = None
    pnl_ils: float | None = None
    pnl_usd: float | None = None
    rr: float | None = None  # take-profits only: reward / risk to the stop
    reached: bool = False  # the price is already at or through this level
    source: str
    reason: Reason
    explanation: Explanation


class StopCandidate(BaseModel):
    model_config = FINITE

    source: StopSource
    price: float
    distance_pct: float
    chosen: bool = False
    note: Reason


class SkippedSource(BaseModel):
    model_config = FINITE

    source: str
    reason: Reason


class ScaleOutStep(BaseModel):
    model_config = FINITE

    step: Literal["take_profit", "trail_rest"]
    label: Reason
    price: float | None = None
    fraction: float = Field(ge=0, le=1)
    quantity: float = Field(ge=0)
    reason: Reason = ""  # names the risk profile and why this share


class ScaleOutPlan(BaseModel):
    """The profile's scale-out numbers and the wording that labels them an adjustable plan."""

    model_config = FINITE

    profile: str  # the preset whose table row was used
    used_fallback: bool = False  # the filter had no preset, so the fallback preset's row was used
    first_fraction: float = Field(ge=0, le=1)
    second_fraction: float = Field(ge=0, le=1)
    trail_fraction: float = Field(ge=0, le=1)
    trail_atr_scale: float
    breakeven_atr_multiple: float
    note: Reason  # "A plan to review and change, not an instruction."
    explanation: Explanation


class SizeGuidance(BaseModel):
    """The stop stays where the chart puts it; this is how many shares fit the risk filter."""

    model_config = FINITE

    needed: bool
    keep_fraction: float = Field(ge=0, le=1)
    current_quantity: float
    suggested_quantity: float
    rules: list[Reason]
    reason: Reason


class RiskToStop(BaseModel):
    model_config = FINITE

    native: float
    ils: float
    usd: float
    pct_of_position: float
    pct_of_portfolio: float | None = None


class ExitLevelsResult(BaseModel):
    model_config = FINITE

    status: ResultStatus
    reason_code: ReasonCode | None = None
    reason: Reason
    symbol: str
    horizon: Horizon | None = None
    horizon_label: str | None = None
    risk_preset: str | None = None
    currency: str | None = None
    price: float | None = None
    price_as_of: datetime | None = None
    atr: float | None = None
    atr_timeframe: str | None = None
    stop: ExitLevel | None = None
    trailing_stop: ExitLevel | None = None
    breakeven: ExitLevel | None = None
    take_profits: list[ExitLevel] = Field(default_factory=list)
    scale_out: list[ScaleOutStep] = Field(default_factory=list)
    scale_out_plan: ScaleOutPlan | None = None
    size_guidance: SizeGuidance | None = None
    risk_to_stop: RiskToStop | None = None
    stop_fit: StopFit | None = None
    candidates: list[StopCandidate] = Field(default_factory=list)
    skipped: list[SkippedSource] = Field(default_factory=list)
    state: StopState | None = None
    explanation: Explanation
    disclaimer: str = DISCLAIMER

    @property
    def effective_stop(self) -> ExitLevel | None:
        """The stop in force: the trailing stop when it is higher than the first one."""
        if self.trailing_stop is not None and (
            self.stop is None or self.trailing_stop.price > self.stop.price
        ):
            return self.trailing_stop
        return self.stop


# ---------------------------------------------------------------- chart helpers
def _clean(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(subset=["High", "Low", "Close"]).sort_index()


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg: dict[str, str] = {"High": "max", "Low": "min", "Close": "last"}
    if "Open" in df.columns:
        agg["Open"] = "first"
    if "Volume" in df.columns:
        agg["Volume"] = "sum"
    return df.resample(rule).agg(agg).dropna(subset=["High", "Low", "Close"])  # type: ignore[arg-type]


def _frame(daily: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1wk":
        return _resample(daily, "W")
    if timeframe == "1mo":
        return _resample(daily, "ME")
    return daily  # "4h" is not offered by the history provider: daily bars stand in for it


def _last_atr(frame: pd.DataFrame, period: int) -> float | None:
    if len(frame) <= period:
        return None
    value = ind.atr(frame["High"], frame["Low"], frame["Close"], period).iloc[-1]
    return float(value) if math.isfinite(value) and value > 0 else None


def _round(x: float) -> float:
    return round(x, 6)


def _is_crypto(v: ValuedHolding) -> bool:
    return v.security.asset_type == "crypto" or v.security.market == "CRYPTO"


# ---------------------------------------------------------------- the engine
@dataclass
class _Ctx:
    v: ValuedHolding
    price: float
    currency: str
    spec: HorizonSpec
    risk: RiskFilter
    settings: Settings
    daily: pd.DataFrame
    as_of: datetime
    quote_as_of: datetime | None
    portfolio_value_ils: float | None
    atr: float | None
    atr_timeframe: str

    def pct(self, level: float) -> float:
        return (level - self.price) / self.price * 100.0

    def cost(self) -> float | None:
        """The holding's cost per unit in the price's currency (null without a cost)."""
        h = self.v.holding
        if h.avg_cost is None:
            return None
        if h.cost_currency.upper() == self.currency.upper():
            return float(h.avg_cost)
        ils = to_ils(float(h.avg_cost), h.cost_currency, self.v.usd_ils)
        if self.currency.upper() == "ILS":
            return ils
        return ils / self.v.usd_ils if self.v.usd_ils else None

    def sources(self) -> list[ExplanationSource]:
        return [
            price_history_source(self.as_of),
            ExplanationSource(
                name="Live quote",
                as_of=self.quote_as_of,
                detail=f"{self.v.quote_source or 'quote'}; the price every level starts from.",
            ),
        ]


def _make_level(
    ctx: _Ctx,
    *,
    kind: LevelKind,
    label: str,
    price: float,
    source: str,
    reason: str,
    rr: float | None = None,
    annotation: ChartAnnotation | None = None,
    inputs: dict[str, float | str | None] | None = None,
    rules: list[str] | None = None,
    risk_rules: list[str] | None = None,
    invalidation: list[str] | None = None,
) -> ExitLevel:
    qty = ctx.v.holding.quantity
    native_move = (price - ctx.price) * qty
    cost = ctx.cost()
    pnl_native = (price - cost) * qty if cost is not None else None
    rate = ctx.v.usd_ils
    pnl_ils = to_ils(pnl_native, ctx.currency, rate) if pnl_native is not None else None
    ils_move = to_ils(native_move, ctx.currency, rate)
    explanation = Explanation(
        summary=reason,
        inputs=as_float_inputs(
            {"price": ctx.price, "level": price, "atr": ctx.atr, **(inputs or {})}
        ),
        rules_applied=rules or [],
        as_of=ctx.as_of,
        annotations=[annotation] if annotation is not None else [],
        risk_rules_applied=risk_rules or [],
        invalidation_risks=invalidation or [],
        sources=ctx.sources(),
    )
    return ExitLevel(
        kind=kind,
        label=label,
        price=_round(price),
        distance_pct=round(ctx.pct(price), 4),
        vs_price_ils=round(ils_move, 2),
        vs_price_usd=round(ils_move / rate if rate else 0.0, 2),
        pnl_native=round(pnl_native, 4) if pnl_native is not None else None,
        pnl_ils=round(pnl_ils, 2) if pnl_ils is not None else None,
        pnl_usd=round(pnl_ils / rate, 2) if pnl_ils is not None and rate else None,
        rr=round(rr, 2) if rr is not None else None,
        reached=(price <= ctx.price) if kind == "take_profit" else (price >= ctx.price),
        source=source,
        reason=reason,
        explanation=explanation,
    )


def _atr_multiple(ctx: _Ctx) -> float | None:
    spec = ctx.spec
    if spec.atr_multiple_min is None or spec.atr_multiple_max is None:
        return None
    mid = (spec.atr_multiple_min + spec.atr_multiple_max) / 2.0
    scale = ctx.settings.exit_levels_preset_atr_scale.get(ctx.risk.preset or "", 1.0)
    mult = mid * scale
    if _is_crypto(ctx.v):
        mult *= ctx.settings.exit_levels_crypto_atr_multiplier
    return mult


def _support_candidates(
    ctx: _Ctx,
) -> tuple[list[tuple[StopSource, float, str]], list[SkippedSource]]:
    """Chart stops below the price: support/swing lows and the horizon's moving average."""
    s, spec = ctx.settings, ctx.spec
    buffer = s.exit_levels_support_buffer_pct / 100.0
    found: list[tuple[StopSource, float, str]] = []
    skipped: list[SkippedSource] = []
    for kind in spec.stop_structure:
        tf = {"weekly_swing_low": "1wk", "multi_month_support": "1mo"}.get(kind, "1d")
        frame = _frame(ctx.daily, tf)
        window = s.exit_levels_pivot_windows.get(kind, 5)
        pivots = find_pivots(frame["Low"], window, "low")
        levels = [
            lv
            for lv in cluster_levels([p for _, p in pivots], s.exit_levels_pivot_cluster_pct)
            if lv.price * (1 - buffer) < ctx.price
        ]
        if not levels:
            skipped.append(SkippedSource(source=kind, reason="no support level below the price"))
            continue
        lv = max(levels, key=lambda x: x.price)
        stop = lv.price * (1 - buffer)
        found.append(
            (
                "structure",
                stop,
                f"just under the {kind.replace('_', ' ')} at {lv.price:.4g} "
                f"({lv.touches} touch{'es' if lv.touches != 1 else ''} on the {tf} chart)",
            )
        )
    if spec.stop_ma_period is not None:
        ma = ind.sma(ctx.daily["Close"], spec.stop_ma_period).iloc[-1]
        if not math.isfinite(ma):
            skipped.append(
                SkippedSource(
                    source=f"sma_{spec.stop_ma_period}",
                    reason=f"fewer than {spec.stop_ma_period} daily bars for the moving average",
                )
            )
        elif ma * (1 - buffer) < ctx.price:
            found.append(
                (
                    "moving_average",
                    float(ma) * (1 - buffer),
                    f"just under the {spec.stop_ma_period}-day moving average at {float(ma):.4g}",
                )
            )
        else:
            skipped.append(
                SkippedSource(
                    source=f"sma_{spec.stop_ma_period}",
                    reason=f"the {spec.stop_ma_period}-day average is above the price",
                )
            )
    return found, skipped


def _choose_stop(
    ctx: _Ctx,
) -> tuple[float, StopSource, str, list[StopCandidate], list[SkippedSource], float | None]:
    """Returns (stop, source, reason, candidates, skipped, atr_multiple_used)."""
    s = ctx.settings
    mult = _atr_multiple(ctx)
    supports, skipped = _support_candidates(ctx)
    cands: list[StopCandidate] = []

    def add(src: StopSource, price: float, note: str) -> None:
        if 0 < price < ctx.price:
            cands.append(
                StopCandidate(
                    source=src,
                    price=_round(price),
                    distance_pct=round(ctx.pct(price), 4),
                    note=note,
                )
            )

    atr_stop: float | None = None
    if mult is not None and ctx.atr is not None:
        atr_stop = ctx.price - mult * ctx.atr
        if atr_stop <= 0:
            skipped.append(
                SkippedSource(source="atr", reason="the ATR stop would be at or below 0")
            )
            atr_stop = None
        else:
            add("atr", atr_stop, f"{mult:.2f} x ATR({ctx.spec.atr_period}, {ctx.atr_timeframe})")
    elif mult is not None:
        skipped.append(
            SkippedSource(
                source="atr",
                reason=f"not enough {ctx.atr_timeframe} bars for ATR({ctx.spec.atr_period})",
            )
        )
    for src, price, note in supports:
        add(src, price, note)
    add(
        "max_loss",
        ctx.price * (1 - ctx.risk.max_loss_per_position_pct / 100.0),
        f"your {ctx.risk.max_loss_per_position_pct:g}% max loss per position",
    )

    chart = [c for c in cands if c.source in ("structure", "moving_average")]
    chosen: StopCandidate | None = None
    reason = ""
    if atr_stop is not None:
        reach = (ctx.atr or 0.0) * s.exit_levels_structure_reach_atr
        behind = [c for c in chart if atr_stop - reach <= c.price <= atr_stop]
        for c in chart:
            if c not in behind and c.price > atr_stop:
                c.note += " (inside the ATR noise band, not used)"
        if behind:
            chosen = max(behind, key=lambda c: c.price)
            reason = (
                f"Stop {chosen.note}, which sits beyond the {mult:.2f} x ATR distance "
                f"so normal noise does not reach it."
            )
        else:
            chosen = next(c for c in cands if c.source == "atr")
            reason = f"Stop at {chosen.note} below the price ({chosen.distance_pct:.1f}%)."
    elif chart:
        floor = (ctx.atr or 0.0) * s.exit_levels_min_stop_atr
        ok = [c for c in chart if ctx.price - c.price >= floor]
        chosen = max(ok, key=lambda c: c.price) if ok else min(chart, key=lambda c: c.price)
        reason = f"Stop {chosen.note} (no ATR range for this horizon)."
    else:
        mx = next((c for c in cands if c.source == "max_loss"), None)
        if mx is not None:
            chosen = mx
            reason = (
                "No chart level or ATR was available, so the stop is your own "
                f"{ctx.risk.max_loss_per_position_pct:g}% max loss per position."
            )
    if chosen is None:  # pragma: no cover - max_loss is always below the price
        raise ValueError("no stop candidate")
    chosen.chosen = True
    return chosen.price, chosen.source, reason, cands, skipped, mult


def _size_guidance(ctx: _Ctx, stop: float) -> SizeGuidance:
    qty = ctx.v.holding.quantity
    dist_pct = (ctx.price - stop) / ctx.price * 100.0
    keep = 1.0
    rules: list[str] = []
    limit = ctx.risk.max_loss_per_position_pct
    if dist_pct > limit + 1e-9:
        frac = limit / dist_pct
        keep = min(keep, frac)
        rules.append(
            f"max loss per position {limit:g}%: the stop is {dist_pct:.1f}% away, so about "
            f"{frac * 100:.0f}% of the shares fit"
        )
    if ctx.portfolio_value_ils and ctx.portfolio_value_ils > 0:
        risk_ils = to_ils((ctx.price - stop) * qty, ctx.currency, ctx.v.usd_ils)
        allowed = ctx.portfolio_value_ils * ctx.risk.max_portfolio_risk_per_trade_pct / 100.0
        if risk_ils > allowed + 1e-9 and risk_ils > 0:
            frac = allowed / risk_ils
            keep = min(keep, frac)
            rules.append(
                f"risk per trade {ctx.risk.max_portfolio_risk_per_trade_pct:g}% of the portfolio: "
                f"losing {risk_ils:,.0f} ILS at the stop is more than {allowed:,.0f} ILS, so about "
                f"{frac * 100:.0f}% of the shares fit"
            )
    needed = keep < 1.0 - 1e-9
    if needed:
        reason = (
            f"The stop stays at {stop:.4g}; it is not moved closer to fit your limits. "
            f"To fit them, keep about {keep * 100:.0f}% of the current shares "
            f"({qty * keep:.4g} of {qty:.4g})."
        )
    else:
        reason = "The stop and the position size fit your risk limits."
    return SizeGuidance(
        needed=needed,
        keep_fraction=round(keep, 4),
        current_quantity=qty,
        suggested_quantity=round(qty * keep, 6),
        rules=rules,
        reason=reason,
    )


def _resistances(ctx: _Ctx, source: str) -> list[float]:
    """Resistance levels above the price, nearest first, for one take-profit source."""
    s = ctx.settings
    tf = "1d" if source == "resistance" else "1wk"
    frame = _frame(ctx.daily, tf)
    window = s.exit_levels_pivot_windows.get(source, 5)
    prices = [p for _, p in find_pivots(frame["High"], window, "high")]
    if source == "long_term_resistance":
        prices.append(float(frame["High"].max()))
    levels = [lv.price for lv in cluster_levels(prices, s.exit_levels_pivot_cluster_pct)]
    return sorted(p for p in levels if p > ctx.price)[:3]


def _fib_levels(ctx: _Ctx) -> list[float]:
    s = ctx.settings
    tail = ctx.daily.tail(s.exit_levels_fib_lookback_bars)
    low_pos = int(tail["Low"].to_numpy().argmin())
    after = tail.iloc[low_pos:]
    high = float(after["High"].max())
    low = float(tail["Low"].iloc[low_pos])
    if high <= low:
        return []
    return sorted(
        low + ext * (high - low)
        for ext in s.exit_levels_fib_extensions
        if low + ext * (high - low) > ctx.price
    )


def _take_profits(
    ctx: _Ctx, stop: float, analyst: AnalystTargets | None
) -> tuple[list[ExitLevel], list[SkippedSource]]:
    s, spec = ctx.settings, ctx.spec
    skipped: list[SkippedSource] = []
    risk = ctx.price - stop
    raw: list[tuple[str, float, str, ChartAnnotation | None]] = []
    ann_time = ctx.as_of

    def res_ann(p: float, label: str) -> ChartAnnotation:
        return ChartAnnotation(kind="resistance", label=label, price=_round(p), as_of=ann_time)

    for src in spec.take_profit_sources:
        if src in ("resistance", "weekly_resistance", "long_term_resistance"):
            levels = _resistances(ctx, src)
            if not levels:
                skipped.append(
                    SkippedSource(source=src, reason="no resistance level above the price")
                )
            for p in levels:
                raw.append(
                    (src, p, f"the next {src.replace('_', ' ')} at {p:.4g}", res_ann(p, src))
                )
        elif src == "r_multiple":
            if spec.r_multiple_min is None or spec.r_multiple_max is None:
                continue
            multiples = sorted({spec.r_multiple_min, spec.r_multiple_max})
            for r in multiples:
                r_eff = max(
                    r, ctx.risk.min_rr
                )  # a level that does not meet your minimum is useless
                note = f"{r_eff:g}R" + (
                    f" (raised from {r:g}R to meet your {ctx.risk.min_rr:g} minimum)"
                    if r_eff > r
                    else ""
                )
                raw.append(
                    (
                        src,
                        ctx.price + r_eff * risk,
                        f"{note}: {r_eff:g} x the distance to the stop",
                        None,
                    )
                )
        elif src == "upper_bollinger":
            upper = ind.bollinger(ctx.daily["Close"], s.exit_levels_bollinger_period)[2].iloc[-1]
            if math.isfinite(upper) and upper > ctx.price:
                raw.append(
                    (
                        src,
                        float(upper),
                        f"the upper Bollinger band at {float(upper):.4g}",
                        res_ann(float(upper), "upper Bollinger"),
                    )
                )
            else:
                skipped.append(
                    SkippedSource(
                        source=src, reason="the upper Bollinger band is not above the price"
                    )
                )
        elif src in ("analyst_mean_target", "analyst_high_target"):
            val = (
                None
                if analyst is None
                else (analyst.mean if src == "analyst_mean_target" else analyst.high)
            )
            if val is None:
                skipped.append(
                    SkippedSource(source=src, reason="no analyst price target was provided")
                )
            elif val <= ctx.price:
                skipped.append(
                    SkippedSource(
                        source=src, reason=f"the analyst target {val:.4g} is not above the price"
                    )
                )
            else:
                name = "mean" if src == "analyst_mean_target" else "high"
                raw.append((src, val, f"the analyst {name} price target {val:.4g}", None))
        elif src == "fibonacci_extension":
            fibs = _fib_levels(ctx)
            if not fibs:
                skipped.append(
                    SkippedSource(source=src, reason="no Fibonacci extension above the price")
                )
            for p in fibs:
                raw.append(
                    (
                        src,
                        p,
                        f"a Fibonacci extension of the last swing at {p:.4g}",
                        res_ann(p, "Fibonacci extension"),
                    )
                )
        elif src == "trailing_only":
            skipped.append(
                SkippedSource(
                    source=src, reason="no fixed take-profit for this horizon: trail the stop"
                )
            )

    kept: list[tuple[str, float, str, float, ChartAnnotation | None]] = []
    if risk <= 0:
        skipped.append(
            SkippedSource(
                source="r_multiple",
                reason="the stop is not below the price, so no R:R can be computed",
            )
        )
        return [], skipped
    for src2, price, why, ann in sorted(raw, key=lambda t: t[1]):
        rr = (price - ctx.price) / risk
        if rr < ctx.risk.min_rr - 1e-9:
            if len(skipped) < 20:
                skipped.append(
                    SkippedSource(
                        source=src2,
                        reason=f"{why}: R:R {rr:.2f} is below your {ctx.risk.min_rr:g} minimum",
                    )
                )
            continue
        if kept and abs(price / kept[-1][1] - 1) * 100 <= s.exit_levels_dedupe_pct:
            continue
        kept.append((src2, price, why, rr, ann))
    kept = kept[: s.exit_levels_max_take_profits]
    out: list[ExitLevel] = []
    for i, (src3, price, why, rr, ann) in enumerate(kept, start=1):
        out.append(
            _make_level(
                ctx,
                kind="take_profit",
                label=f"TP{i}",
                price=price,
                source=src3,
                reason=f"TP{i} at {price:.4g}: {why}; R:R {rr:.2f}.",
                rr=rr,
                annotation=ann,
                inputs={"rr": rr, "risk_per_unit": risk},
                rules=[f"source: {src3}", f"R:R at least {ctx.risk.min_rr:g}"],
                risk_rules=[f"min_rr {ctx.risk.min_rr:g}"],
                invalidation=["A close back under the stop cancels these levels."],
            )
        )
    return out, skipped


def _plan_for(risk: RiskFilter, s: Settings) -> tuple[str, bool, ScaleOutPlanSpec]:
    """The table row for the filter's preset (the fallback preset's row for a custom filter)."""
    name = risk.preset or ""
    if name in s.exit_levels_scale_out_plans:
        return name, False, s.exit_levels_scale_out_plans[name]
    fb = s.exit_levels_scale_out_fallback_preset
    return fb, True, s.exit_levels_scale_out_plans[fb]


def _profile_name(preset: str) -> str:
    return preset.replace("_", " ").capitalize()


def _scale_out(
    ctx: _Ctx, tps: list[ExitLevel], profile: str, spec: ScaleOutPlanSpec
) -> list[ScaleOutStep]:
    qty = ctx.v.holding.quantity
    fractions = [spec.first_fraction, spec.second_fraction]
    who = f"{_profile_name(profile)} profile"
    steps: list[ScaleOutStep] = []
    used = 0.0
    for i, (tp, frac) in enumerate(zip(tps, fractions, strict=False)):
        if frac <= 0:
            continue
        used += frac
        if i == 0:
            why = (
                f"{who}: secures {frac:.0%} of the position at the first level"
                f" ({tp.label} at {tp.price:.4g}), so part of the gain is locked in early."
            )
        else:
            why = (
                f"{who}: takes a further {frac:.0%} at {tp.label} ({tp.price:.4g}),"
                " leaving the remainder to trail."
            )
        steps.append(
            ScaleOutStep(
                step="take_profit",
                label=f"Take partial profit at {tp.label}",
                price=tp.price,
                fraction=round(frac, 4),
                quantity=round(qty * frac, 6),
                reason=why,
            )
        )
    rest = max(0.0, 1.0 - used)
    steps.append(
        ScaleOutStep(
            step="trail_rest",
            label="Keep the rest and follow the trailing stop",
            fraction=round(rest, 4),
            quantity=round(qty * rest, 6),
            reason=(
                f"{who}: the remaining {rest:.0%} stays in and follows the trailing stop"
                f" ({spec.trail_atr_scale:g} x the base ATR trail, "
                f"breakeven stop after {spec.breakeven_atr_multiple:g} ATR of gain)."
            ),
        )
    )
    return steps


def _scale_out_plan(
    profile: str, fallback: bool, spec: ScaleOutPlanSpec, steps: list[ScaleOutStep], as_of: datetime
) -> ScaleOutPlan:
    who = f"{_profile_name(profile)} profile"
    lean = (
        "earlier and a larger share" if spec.first_fraction >= 0.4 else "later and a smaller share"
    )
    summary = (
        f"{who}: take {spec.first_fraction:.0%} at the first level and {spec.second_fraction:.0%}"
        f" at the second, trail the other {spec.trail_fraction:.0%}. Profit is secured {lean};"
        f" the trail is {spec.trail_atr_scale:g} x the base ATR multiple."
    )
    if fallback:
        summary += f" Your filter has no preset, so the {_profile_name(profile)} row is used."
    return ScaleOutPlan(
        profile=profile,
        used_fallback=fallback,
        first_fraction=round(spec.first_fraction, 4),
        second_fraction=round(spec.second_fraction, 4),
        trail_fraction=round(spec.trail_fraction, 4),
        trail_atr_scale=spec.trail_atr_scale,
        breakeven_atr_multiple=spec.breakeven_atr_multiple,
        note="A plan to review and change, not an instruction; the numbers come from your risk profile.",
        explanation=Explanation(
            summary=summary[:2000],
            inputs=as_float_inputs(
                {
                    "first_fraction": spec.first_fraction,
                    "second_fraction": spec.second_fraction,
                    "trail_fraction": spec.trail_fraction,
                    "trail_atr_scale": spec.trail_atr_scale,
                    "breakeven_atr_multiple": spec.breakeven_atr_multiple,
                }
            ),
            rules_applied=[f"scale-out plan of the {profile} profile"] + [x.reason for x in steps],
            invalidation_risks=["Take-profit levels come from past prices and can be missed."],
            as_of=as_of,
        ),
    )


def _empty_explanation(summary: str, as_of: datetime | None = None) -> Explanation:
    return Explanation(summary=summary, as_of=as_of)


def no_levels_result(
    symbol: str,
    status: ResultStatus,
    code: ReasonCode,
    reason: str,
    *,
    horizon: Horizon | None = None,
    spec: HorizonSpec | None = None,
    risk: RiskFilter | None = None,
) -> ExitLevelsResult:
    return ExitLevelsResult(
        status=status,
        reason_code=code,
        reason=reason,
        symbol=symbol,
        horizon=horizon,
        horizon_label=spec.label if spec else None,
        risk_preset=risk.preset if risk else None,
        explanation=_empty_explanation(reason),
    )


def parse_horizon(value: str | Horizon | None) -> Horizon | None:
    if value is None:
        return None
    return value if isinstance(value, Horizon) else Horizon(value)


def compute_exit_levels(
    valued: ValuedHolding,
    horizon: str | Horizon | None,
    risk: RiskFilter,
    history: pd.DataFrame | None,
    *,
    analyst: AnalystTargets | None = None,
    state: StopState | None = None,
    portfolio_value_ils: float | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> ExitLevelsResult:
    """Stop and take-profit levels for one holding. See the module docstring for the rules."""
    s = settings or get_settings()
    symbol = valued.holding.symbol
    hz = parse_horizon(horizon)
    if valued.security.asset_type == "fund":
        return no_levels_result(
            symbol,
            "no_levels",
            "fund_no_levels",
            "No levels: this is a fund. It has monthly data and no tradable price, so stop and "
            "take-profit levels are not computed.",
            horizon=hz,
            risk=risk,
        )
    if hz is None:
        return no_levels_result(
            symbol,
            "needs_horizon",
            "needs_horizon",
            "How long do you plan to keep this? No levels are computed until you choose a horizon.",
            risk=risk,
        )
    spec = s.horizon_table[hz.value]
    try:
        price = exit_level_price(valued, now, s)
    except StalePriceError as e:
        return no_levels_result(
            symbol, "no_levels", "stale_price",
            f"No levels: there is no fresh market price ({e.reason}).",
            horizon=hz, spec=spec, risk=risk,
        )  # fmt: skip
    if history is None or history.empty or not {"High", "Low", "Close"} <= set(history.columns):
        return no_levels_result(
            symbol, "no_levels", "no_history",
            "No levels: no price history is available for this symbol.",
            horizon=hz, spec=spec, risk=risk,
        )  # fmt: skip
    daily = _clean(history)
    if len(daily) < s.exit_levels_min_bars:
        return no_levels_result(
            symbol, "no_levels", "insufficient_history",
            f"No levels: {len(daily)} daily bars, at least {s.exit_levels_min_bars} are needed.",
            horizon=hz, spec=spec, risk=risk,
        )  # fmt: skip
    last_close = float(daily["Close"].iloc[-1])
    if last_close <= 0 or abs(price / last_close - 1) * 100 > s.exit_levels_max_history_gap_pct:
        return no_levels_result(
            symbol, "no_levels", "history_price_mismatch",
            f"No levels: the live price {price:.4g} and the last chart close {last_close:.4g} "
            "disagree too much (a unit or currency slip). Nothing is computed on mismatched data.",
            horizon=hz, spec=spec, risk=risk,
        )  # fmt: skip

    as_of = as_utc(pd.Timestamp(daily.index[-1]).to_pydatetime())
    atr_tf = spec.atr_timeframe
    atr = _last_atr(_frame(daily, atr_tf), spec.atr_period)
    ctx = _Ctx(
        v=valued, price=price, currency=valued.currency, spec=spec, risk=risk, settings=s,
        daily=daily, as_of=as_of, quote_as_of=as_utc(valued.as_of) if valued.as_of else None,
        portfolio_value_ils=portfolio_value_ils, atr=atr, atr_timeframe=atr_tf,
    )  # fmt: skip

    plan_name, plan_fb, plan = _plan_for(risk, s)
    stop_price, src, why, cands, skipped, mult = _choose_stop(ctx)
    prior = state or StopState()
    rules = [f"horizon {hz.value}: {spec.label}", f"preset {risk.preset or 'custom'}"]
    risk_rules = [
        f"max loss per position {risk.max_loss_per_position_pct:g}%",
        f"stop type {risk.stop_type}",
    ]
    if prior.stop is not None and prior.stop > stop_price:
        stop_price, src = prior.stop, "saved_stop"
        why = (
            f"Kept your saved stop at {prior.stop:.4g}: a stop is only ever raised, never lowered."
        )
    stop_level = _make_level(
        ctx, kind="stop", label="Stop", price=stop_price, source=src, reason=why,
        inputs={"atr_multiple": mult, "max_loss_pct": risk.max_loss_per_position_pct},
        rules=rules, risk_rules=risk_rules,
        annotation=ChartAnnotation(kind="support", label="Stop", price=_round(stop_price), as_of=as_of),
        invalidation=["A gap through the stop can fill worse than the stop price."],
    )  # fmt: skip

    guidance = _size_guidance(ctx, stop_price)
    size_risk_native = max(0.0, price - stop_price) * valued.holding.quantity
    risk_ils = to_ils(size_risk_native, valued.currency, valued.usd_ils)
    rts = RiskToStop(
        native=round(size_risk_native, 4),
        ils=round(risk_ils, 2),
        usd=round(risk_ils / valued.usd_ils if valued.usd_ils else 0.0, 2),
        pct_of_position=round(max(0.0, price - stop_price) / price * 100.0, 4),
        pct_of_portfolio=round(risk_ils / portfolio_value_ils * 100.0, 4)
        if portfolio_value_ils
        else None,
    )

    # Trailing stop (chandelier): highest high minus the ATR multiple, then a ratchet.
    trailing: ExitLevel | None = None
    highest = float(daily["High"].tail(s.exit_levels_chandelier_lookback).max())
    if risk.stop_type != "fixed":
        t_mult = plan.trail_atr_scale * (
            mult
            if mult is not None
            else s.exit_levels_trailing_atr_default
            * (s.exit_levels_crypto_atr_multiplier if _is_crypto(valued) else 1.0)
        )
        if atr is None:
            skipped.append(SkippedSource(source="trailing_stop", reason="no ATR to trail with"))
        else:
            hh = _max_opt(highest, prior.highest_high) or highest
            chandelier = hh - t_mult * atr
            floor = max(stop_price, chandelier)
            ratcheted = prior.ratchet(floor, hh).stop or floor
            moved = ratcheted > stop_price + 1e-12
            in_profit = ctx.cost() is not None and price > (ctx.cost() or 0.0)
            t_why = (
                f"Trailing stop {ratcheted:.4g}: highest high {hh:.4g} minus {t_mult:.2f} x ATR "
                f"({_profile_name(plan_name)} profile trail); it only moves up."
                if moved
                else f"Trailing stop starts at the stop {ratcheted:.4g} and rises as the price makes new highs."
            )
            trailing = _make_level(
                ctx, kind="trailing_stop", label="Trailing stop", price=ratcheted, source="trailing",
                reason=t_why,
                inputs={"highest_high": hh, "atr_multiple": t_mult, "in_profit": "yes" if in_profit else "no"},
                rules=[*rules, "a trailing stop only moves up"], risk_rules=risk_rules,
                annotation=ChartAnnotation(kind="support", label="Trailing stop", price=_round(ratcheted), as_of=as_of),
                invalidation=["A sharp drop can pass the stop before it is acted on."],
            )  # fmt: skip

    breakeven: ExitLevel | None = None
    cost = ctx.cost()
    if cost is not None and atr is not None:
        gain_atr = (price - cost) / atr
        current_stop = max(stop_price, trailing.price if trailing else 0.0)
        if gain_atr >= plan.breakeven_atr_multiple and cost > current_stop:
            breakeven = _make_level(
                ctx, kind="breakeven", label="Breakeven stop", price=cost, source="cost",
                reason=(
                    f"The price is {gain_atr:.1f} ATR above your cost {cost:.4g} "
                    f"({_profile_name(plan_name)} profile suggests this from "
                    f"{plan.breakeven_atr_multiple:g} ATR): moving the stop up to your cost means this position can no longer lose money."
                ),
                inputs={"gain_in_atr": gain_atr}, rules=rules, risk_rules=risk_rules,
                annotation=ChartAnnotation(kind="support", label="Breakeven", price=_round(cost), as_of=as_of),
            )  # fmt: skip

    tps, tp_skipped = _take_profits(ctx, stop_price, analyst)
    skipped.extend(tp_skipped)

    fit: StopFit | None = None
    if prior.stop is not None and atr is not None:
        dist_atr = (price - prior.stop) / atr
        fit = (
            "too_tight"
            if dist_atr < s.exit_levels_too_tight_atr
            else "too_wide"
            if dist_atr > s.exit_levels_too_wide_atr
            else "ok"
        )

    steps = _scale_out(ctx, tps, plan_name, plan)
    scale_plan = _scale_out_plan(plan_name, plan_fb, plan, steps, as_of)
    new_state = prior.ratchet(trailing.price if trailing else stop_price, highest)
    summary = (
        f"{spec.label}: {stop_level.reason} "
        + (f"{len(tps)} take-profit level(s). " if tps else "No fixed take-profit. ")
        + guidance.reason
    )
    explanation = Explanation(
        summary=summary[:2000],
        inputs=as_float_inputs(
            {
                "price": price,
                "atr": atr,
                "atr_multiple": mult,
                "stop": stop_price,
                "min_rr": risk.min_rr,
            }
        ),
        rules_applied=[
            *rules,
            *(f"{c.source}: {c.note}" for c in cands),
            scale_plan.explanation.summary,
        ][:50],
        as_of=as_of,
        annotations=[
            a
            for lv in [stop_level, trailing, breakeven, *tps]
            if lv is not None
            for a in lv.explanation.annotations
        ],
        risk_rules_applied=[*risk_rules, *guidance.rules][:50],
        invalidation_risks=[
            "Levels come from past prices and can be wrong; a gap can pass a stop.",
            "Levels are recomputed as new bars arrive; a saved stop is only ever raised.",
        ],
        sources=ctx.sources(),
    )
    return ExitLevelsResult(
        status="levels",
        reason=summary[:500],
        symbol=symbol,
        horizon=hz,
        horizon_label=spec.label,
        risk_preset=risk.preset,
        currency=valued.currency,
        price=_round(price),
        price_as_of=ctx.quote_as_of,
        atr=_round(atr) if atr is not None else None,
        atr_timeframe=atr_tf,
        stop=stop_level,
        trailing_stop=trailing,
        breakeven=breakeven,
        take_profits=tps,
        scale_out=steps,
        scale_out_plan=scale_plan,
        size_guidance=guidance,
        risk_to_stop=rts,
        stop_fit=fit,
        candidates=cands,
        skipped=skipped,
        state=new_state,
        explanation=explanation,
    )
