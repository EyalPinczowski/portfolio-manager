"""Chart-pattern signal: support/resistance, golden/death cross, breakout, double top/bottom."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.config import Settings, get_settings
from app.signals import indicators as ind
from app.signals.base import Explanation, SignalResult, as_float_inputs, clamp
from app.timeutil import utcnow

NAME = "patterns"
REQUIRED_COLUMNS = ("High", "Low", "Close", "Volume")


@dataclass
class Level:
    price: float
    touches: int


def find_pivots(series: pd.Series, window: int, kind: str) -> list[tuple[int, float]]:
    """Confirmed pivot highs ('high') or lows ('low'): extreme within +/- `window` bars."""
    arr = series.to_numpy(dtype="float64")
    out: list[tuple[int, float]] = []
    for i in range(window, len(arr) - window):
        seg = arr[i - window : i + window + 1]
        val = arr[i]
        if (kind == "high" and val == seg.max() and (seg == val).sum() == 1) or (
            kind == "low" and val == seg.min() and (seg == val).sum() == 1
        ):
            out.append((i, float(val)))
    return out


def cluster_levels(prices: list[float], tolerance_pct: float) -> list[Level]:
    """Group nearby pivot prices into support/resistance levels."""
    levels: list[Level] = []
    for p in sorted(prices):
        if levels and abs(p / levels[-1].price - 1) * 100 <= tolerance_pct:
            lv = levels[-1]
            lv.price = (lv.price * lv.touches + p) / (lv.touches + 1)
            lv.touches += 1
        else:
            levels.append(Level(price=p, touches=1))
    return levels


def _double_pattern(
    pivots: list[tuple[int, float]],
    opposite: pd.Series,
    close: float,
    tol_pct: float,
    min_depth_pct: float,
    top: bool,
) -> tuple[float, str, str] | None:
    """Detect a simple double top (top=True) or double bottom using the last two pivots."""
    if len(pivots) < 2:
        return None
    (i1, p1), (i2, p2) = pivots[-2], pivots[-1]
    if abs(p2 / p1 - 1) * 100 > tol_pct or i2 - i1 < 5:
        return None
    mid = opposite.iloc[i1 : i2 + 1]
    neck = float(mid.min() if top else mid.max())
    ref = (p1 + p2) / 2
    depth = abs(ref / neck - 1) * 100
    if depth < min_depth_pct:
        return None
    if top:
        if close < neck:
            return (
                -70.0,
                f"Double top near {ref:.2f} confirmed: price broke below the neckline {neck:.2f}.",
                "double top confirmed",
            )
        if close < ref:
            return (
                -25.0,
                f"Possible double top near {ref:.2f}; neckline {neck:.2f} not yet broken.",
                "double top forming",
            )
        return None
    if close > neck:
        return (
            70.0,
            f"Double bottom near {ref:.2f} confirmed: price broke above the neckline {neck:.2f}.",
            "double bottom confirmed",
        )
    if close > ref:
        return (
            25.0,
            f"Possible double bottom near {ref:.2f}; neckline {neck:.2f} not yet broken.",
            "double bottom forming",
        )
    return None


def patterns_signal(df: pd.DataFrame | None, settings: Settings | None = None) -> SignalResult:
    s = settings or get_settings()
    if df is None or df.empty or any(c not in df.columns for c in REQUIRED_COLUMNS):
        return SignalResult.missing(NAME, "No price history available for pattern detection.")
    df = df.dropna(subset=["Close"])
    n = len(df)
    as_of = pd.Timestamp(df.index[-1]).to_pydatetime() if n else utcnow()
    if n < s.patterns_min_rows:
        return SignalResult.missing(
            NAME,
            f"Only {n} daily bars; at least {s.patterns_min_rows} are needed for patterns.",
            as_of,
        )
    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
    price = float(close.iloc[-1])
    w = s.patterns_pivot_window
    inputs: dict[str, float | str | None] = {"close": price, "bars": float(n)}
    scores: list[float] = []
    reasons: list[str] = []
    rules: list[str] = []
    distinct = 0

    def add(score: float, reason: str, rule: str, pattern: bool = False) -> None:
        nonlocal distinct
        scores.append(clamp(score))
        reasons.append(reason)
        rules.append(rule)
        distinct += 1 if pattern else 0

    # ---- support / resistance ----
    ph, pl = find_pivots(high, w, "high"), find_pivots(low, w, "low")
    levels = cluster_levels(
        [p for _, p in ph] + [p for _, p in pl], s.patterns_cluster_tolerance_pct
    )
    supports = [lv for lv in levels if lv.price < price]
    resists = [lv for lv in levels if lv.price > price]
    sup = max(supports, key=lambda lv: lv.price) if supports else None
    res = min(resists, key=lambda lv: lv.price) if resists else None
    near = s.patterns_near_level_pct
    sr_score = 0.0
    sr_note: list[str] = []
    if sup is not None:
        d = (price / sup.price - 1) * 100
        inputs["support"] = sup.price
        if d <= near:
            sr_score += 50
            sr_note.append(
                f"Price is {d:.1f}% above support at {sup.price:.2f} (touched {sup.touches}x)."
            )
    if res is not None:
        d = (res.price / price - 1) * 100
        inputs["resistance"] = res.price
        if d <= near:
            sr_score -= 50
            sr_note.append(
                f"Price is {d:.1f}% below resistance at {res.price:.2f} (touched {res.touches}x)."
            )
    if sr_note:
        add(sr_score, " ".join(sr_note), "price near support/resistance")
    else:
        parts = []
        if sup is not None:
            parts.append(f"support {sup.price:.2f}")
        if res is not None:
            parts.append(f"resistance {res.price:.2f}")
        add(
            0,
            "No key level is close to the price" + (f" ({', '.join(parts)})." if parts else "."),
            "no nearby level",
        )

    # ---- breakout / breakdown ----
    lb = s.patterns_breakout_lookback
    if n > lb + 1:
        prior_high = float(high.iloc[-lb - 1 : -1].max())
        prior_low = float(low.iloc[-lb - 1 : -1].min())
        avg_vol = float(volume.iloc[-lb - 1 : -1].mean())
        vol_boost = avg_vol > 0 and float(volume.iloc[-1]) >= 1.5 * avg_vol
        if price > prior_high:
            add(
                70 + (15 if vol_boost else 0),
                f"Breakout: close {price:.2f} is above the {lb}-day high {prior_high:.2f}"
                + (" on heavy volume." if vol_boost else "."),
                "breakout above N-day high",
                True,
            )
        elif price < prior_low:
            add(
                -70 - (15 if vol_boost else 0),
                f"Breakdown: close {price:.2f} is below the {lb}-day low {prior_low:.2f}"
                + (" on heavy volume." if vol_boost else "."),
                "breakdown below N-day low",
                True,
            )

    # ---- golden / death cross ----
    sma50, sma200 = ind.sma(close, 50), ind.sma(close, 200)
    diff = (sma50 - sma200).dropna()
    if len(diff) > 2:
        look = diff.iloc[-s.patterns_cross_lookback - 1 :]
        signs = np.sign(look.to_numpy())
        crossed = bool(
            len(signs) > 1 and signs[0] != signs[-1] and signs[0] != 0 and signs[-1] != 0
        )
        inputs["sma50_minus_sma200"] = float(diff.iloc[-1])
        if crossed and signs[-1] > 0:
            add(
                80,
                f"Golden cross: the 50-day average crossed above the 200-day within the last {s.patterns_cross_lookback} sessions.",
                "golden cross",
                True,
            )
        elif crossed:
            add(
                -80,
                f"Death cross: the 50-day average crossed below the 200-day within the last {s.patterns_cross_lookback} sessions.",
                "death cross",
                True,
            )
        else:
            up = float(diff.iloc[-1]) > 0
            add(
                20 if up else -20,
                f"The 50-day average is {'above' if up else 'below'} the 200-day (no recent cross).",
                "SMA50 vs SMA200 state",
            )

    # ---- double top / bottom (last ~150 bars) ----
    tail = df.iloc[-150:]
    pht = find_pivots(tail["High"], w, "high")
    plt_ = find_pivots(tail["Low"], w, "low")
    dt = _double_pattern(
        pht,
        tail["Low"],
        price,
        s.patterns_double_tolerance_pct,
        s.patterns_double_min_depth_pct,
        True,
    )
    db = _double_pattern(
        plt_,
        tail["High"],
        price,
        s.patterns_double_tolerance_pct,
        s.patterns_double_min_depth_pct,
        False,
    )
    for found in (dt, db):
        if found is not None:
            add(found[0], found[1], found[2], True)

    score = sum(scores) / len(scores)
    depth = min(1.0, (n - s.patterns_min_rows) / 190.0)
    confidence = round(min(1.0, 0.3 + 0.5 * depth + (0.2 if distinct else 0.0)), 3)
    return SignalResult(
        name=NAME,
        score=round(clamp(score), 2),
        confidence=confidence,
        reasons=reasons,
        data_as_of=as_of,
        explanation=Explanation(
            summary=f"Pattern score {score:+.0f} from {len(scores)} rule(s); {distinct} pattern(s) detected.",
            inputs=as_float_inputs(inputs),
            rules_applied=rules,
        ),
    )
