"""Chartist: deterministic chart analysis (no LLM) -> `ChartReport`.

It reuses the score card (technical + patterns, weights redistributed over the signals that have
data, the rest reported as not available with confidence 0) and adds an indicator snapshot and
several support / resistance levels. All numbers are computed here; no model ever calculates one.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.analyze.schemas import NOT_VALIDATED_NOTICE, ChartReport, Level, SignalLine
from app.config import Settings, get_settings
from app.scoring.scorecard import NOT_VALIDATED, compute_scorecard
from app.scoring.universe import _OneFrame
from app.signals import indicators as ind
from app.signals.base import Explanation
from app.signals.patterns import cluster_levels, find_pivots
from app.timeutil import as_utc

REQUIRED = ("High", "Low", "Close", "Volume")


def _last(series: pd.Series) -> float | None:
    s = series.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def usable(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """The daily frame when it has every column the signals need, else None."""
    if df is None or df.empty or any(c not in df.columns for c in REQUIRED):
        return None
    return df.dropna(subset=["Close"]).sort_index()


def indicator_snapshot(df: pd.DataFrame, extra: dict[str, float | str]) -> dict[str, float]:
    """Numbers shown next to the chart: the technical signal's inputs plus ATR and Bollinger."""
    out: dict[str, float] = {
        k: float(v) for k, v in extra.items() if isinstance(v, int | float) and v == v
    }
    close, high, low = df["Close"], df["High"], df["Low"]
    price = float(close.iloc[-1])
    atr_v = _last(ind.atr(high, low, close, 14))
    if atr_v is not None:
        out["atr14"] = atr_v
        out["atr14_pct_of_price"] = atr_v / price * 100.0 if price else 0.0
    lower, _, upper = ind.bollinger(close, 20)
    lo_v, up_v = _last(lower), _last(upper)
    if lo_v is not None and up_v is not None:
        out["bollinger_lower"], out["bollinger_upper"] = lo_v, up_v
    if len(close) > 252:
        out["high_52w"] = float(df["High"].tail(252).max())
        out["low_52w"] = float(df["Low"].tail(252).min())
    return {k: round(v, 4) for k, v in out.items() if v == v and abs(v) != float("inf")}


def support_resistance(df: pd.DataFrame, s: Settings) -> list[Level]:
    """The nearest clustered pivot levels on each side of the last close."""
    close = float(df["Close"].iloc[-1])
    w = s.patterns_pivot_window
    pivots = [p for _, p in find_pivots(df["High"], w, "high")] + [
        p for _, p in find_pivots(df["Low"], w, "low")
    ]
    levels = cluster_levels(pivots, s.patterns_cluster_tolerance_pct)
    below = sorted((lv for lv in levels if lv.price < close), key=lambda lv: -lv.price)
    above = sorted((lv for lv in levels if lv.price > close), key=lambda lv: lv.price)
    n = s.analyze_levels_per_side
    out = [
        Level(
            kind="support",
            price=round(lv.price, 4),
            distance_pct=round((lv.price / close - 1) * 100, 2),
            touches=lv.touches,
        )
        for lv in below[:n]
    ] + [
        Level(
            kind="resistance",
            price=round(lv.price, 4),
            distance_pct=round((lv.price / close - 1) * 100, 2),
            touches=lv.touches,
        )
        for lv in above[:n]
    ]
    return out


def build_chart_report(
    symbol: str, df: pd.DataFrame | None, settings: Settings | None = None
) -> ChartReport:
    s = settings or get_settings()
    frame = usable(df)
    card = compute_scorecard(symbol, _OneFrame(frame), s)
    breakdown: list[SignalLine] = []
    tech_inputs: dict[str, float | str] = {}
    for sig in card["signals"]:
        available = float(sig["confidence"]) > 0
        breakdown.append(
            SignalLine(
                name=sig["name"],
                available=available,
                score=float(sig["score"]) if available else 0.0,
                confidence=float(sig["confidence"]),
                weight=float(sig["weight"]),
                nominal_weight=float(sig["nominal_weight"]),
                reasons=list(sig["reasons"]),
                data_as_of=sig["data_as_of"],
            )
        )
        if sig["name"] == "technical":
            tech_inputs = dict(sig["explanation"].get("inputs", {}))
    explanation = Explanation.model_validate(card["explanation"])
    # The score card's own notice names the verdict it withholds; this route words it neutrally.
    explanation = explanation.model_copy(
        update={
            "rules_applied": [
                *(r for r in explanation.rules_applied if r != NOT_VALIDATED),
                NOT_VALIDATED_NOTICE,
            ]
        }
    )
    as_of: datetime | None = (
        as_utc(pd.Timestamp(frame.index[-1]).to_pydatetime())
        if frame is not None and len(frame)
        else None
    )
    return ChartReport(
        available=bool(card["available"]),
        score=float(card["total"]),
        confidence=float(card["confidence"]),
        breakdown=breakdown,
        indicators=indicator_snapshot(frame, tech_inputs)
        if frame is not None and len(frame)
        else {},
        levels=support_resistance(frame, s) if frame is not None and len(frame) > 20 else [],
        annotations=list(explanation.annotations),
        data_as_of=as_of,
        bars=0 if frame is None else len(frame),
        explanation=explanation,
    )
