"""Technical signal: trend, momentum, volatility and volume, each with reasons."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from app.config import Settings, get_settings
from app.signals import indicators as ind
from app.signals.base import (
    ChartAnnotation,
    Explanation,
    SignalResult,
    as_float_inputs,
    clamp,
    price_history_source,
)
from app.timeutil import utcnow

NAME = "technical"
REQUIRED_COLUMNS = ("High", "Low", "Close", "Volume")


@dataclass
class Category:
    name: str
    scores: list[float] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def add(self, score: float, reason: str, rule: str) -> None:
        self.scores.append(clamp(score))
        self.reasons.append(reason)
        self.rules.append(rule)

    @property
    def score(self) -> float | None:
        return sum(self.scores) / len(self.scores) if self.scores else None


def _last(series: pd.Series) -> float | None:
    s = series.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _index_as_of(df: pd.DataFrame) -> pd.Timestamp | None:
    return df.index[-1] if len(df.index) else None


def _invalidation(inputs: dict[str, float | str | None], price: float) -> list[str]:
    """What would make this reading wrong, from the moving averages the score used."""
    out: list[str] = []
    for label in ("50", "200"):
        v = inputs.get(f"sma{label}")
        if isinstance(v, float):
            side = "below" if price > v else "above"
            out.append(
                f"A close {side} the {label}-day average ({v:.2f}) would reverse the trend reading."
            )
    return out


def technical_signal(df: pd.DataFrame | None, settings: Settings | None = None) -> SignalResult:
    """Score a daily OHLCV frame in [-100, 100]. Missing/short data gives confidence 0."""
    s = settings or get_settings()
    if df is None or df.empty or any(c not in df.columns for c in REQUIRED_COLUMNS):
        return SignalResult.missing(NAME, "No price history available for technical analysis.")
    df = df.dropna(subset=["Close"])
    n = len(df)
    as_of = pd.Timestamp(df.index[-1]).to_pydatetime() if n else utcnow()
    if n < s.tech_min_rows:
        return SignalResult.missing(
            NAME,
            f"Only {n} daily bars; at least {s.tech_min_rows} are needed for technical analysis.",
            as_of,
        )
    close, high, low = df["Close"], df["High"], df["Low"]
    price = float(close.iloc[-1])
    inputs: dict[str, float | str | None] = {"close": price, "bars": float(n)}

    trend = Category("trend")
    mom = Category("momentum")
    vol = Category("volatility")
    volume_cat = Category("volume")

    # ---- trend ----
    sma20, sma50, sma200 = ind.sma(close, 20), ind.sma(close, 50), ind.sma(close, 200)
    for label, series, pts in (("20", sma20, 50.0), ("50", sma50, 70.0), ("200", sma200, 90.0)):
        v = _last(series)
        if v is None:
            continue
        inputs[f"sma{label}"] = v
        above = price > v
        dist = (price / v - 1) * 100
        trend.add(
            pts if above else -pts,
            f"Price is {abs(dist):.1f}% {'above' if above else 'below'} its {label}-day average.",
            f"price vs SMA{label}",
        )
    s50, s200 = _last(sma50), _last(sma200)
    if s50 is not None and s200 is not None:
        up = s50 > s200
        trend.add(
            50 if up else -50,
            f"The 50-day average is {'above' if up else 'below'} the 200-day average "
            f"({'long-term uptrend' if up else 'long-term downtrend'}).",
            "SMA50 vs SMA200",
        )
    ema20 = ind.ema(close, 20)
    if len(ema20) > 10:
        slope = (float(ema20.iloc[-1]) / float(ema20.iloc[-10]) - 1) * 100
        inputs["ema20_slope_10d_pct"] = slope
        trend.add(
            clamp(slope * 15),
            f"The 20-day EMA moved {slope:+.1f}% over the last 10 sessions.",
            "EMA20 slope over 10 sessions",
        )

    # ---- momentum ----
    rsi_v = _last(ind.rsi(close, 14))
    if rsi_v is not None:
        inputs["rsi14"] = rsi_v
        if rsi_v >= s.tech_rsi_overbought:
            mom.add(
                -40 - (rsi_v - s.tech_rsi_overbought),
                f"RSI is {rsi_v:.0f}: overbought.",
                "RSI overbought",
            )
        elif rsi_v <= s.tech_rsi_oversold:
            mom.add(
                40 + (s.tech_rsi_oversold - rsi_v), f"RSI is {rsi_v:.0f}: oversold.", "RSI oversold"
            )
        else:
            mom.add(
                (rsi_v - 50) * 1.5,
                f"RSI is {rsi_v:.0f}: {'positive' if rsi_v >= 50 else 'weak'} momentum.",
                "RSI neutral zone",
            )
    _, _, hist = ind.macd(close)
    h_now, h_prev = _last(hist), _last(hist.iloc[:-1])
    if h_now is not None and h_prev is not None:
        inputs["macd_hist"] = h_now
        scale = max(price * 0.002, 1e-9)
        base = clamp(h_now / scale * 40)
        crossed_up = h_prev <= 0 < h_now
        crossed_down = h_prev >= 0 > h_now
        if crossed_up:
            mom.add(
                max(base, 60),
                "MACD just crossed above its signal line (bullish).",
                "MACD bullish cross",
            )
        elif crossed_down:
            mom.add(
                min(base, -60),
                "MACD just crossed below its signal line (bearish).",
                "MACD bearish cross",
            )
        else:
            mom.add(
                base,
                f"MACD is {'above' if h_now > 0 else 'below'} its signal line.",
                "MACD histogram sign",
            )
    k, d = ind.stochastic(high, low, close)
    k_v, d_v = _last(k), _last(d)
    if k_v is not None and d_v is not None:
        inputs["stoch_k"] = k_v
        if k_v >= s.tech_stoch_overbought:
            mom.add(-35, f"Stochastic %K is {k_v:.0f}: overbought zone.", "Stochastic overbought")
        elif k_v <= s.tech_stoch_oversold:
            mom.add(35, f"Stochastic %K is {k_v:.0f}: oversold zone.", "Stochastic oversold")
        else:
            mom.add(
                20 if k_v > d_v else -20,
                f"Stochastic %K ({k_v:.0f}) is {'above' if k_v > d_v else 'below'} %D.",
                "Stochastic K vs D",
            )

    # ---- volatility ----
    pb = _last(ind.percent_b(close))
    if pb is not None:
        inputs["bollinger_pct_b"] = pb
        if pb >= 1:
            vol.add(
                -40,
                "Price is at or above the upper Bollinger band (stretched).",
                "Bollinger %B >= 1",
            )
        elif pb <= 0:
            vol.add(
                40,
                "Price is at or below the lower Bollinger band (stretched down).",
                "Bollinger %B <= 0",
            )
        else:
            vol.add(
                (0.5 - pb) * 40,
                f"Price sits at {pb * 100:.0f}% of the Bollinger band range.",
                "Bollinger %B position",
            )
    atr_v = _last(ind.atr(high, low, close, 14))
    if atr_v is not None and price > 0:
        atr_pct = atr_v / price * 100
        inputs["atr14_pct"] = atr_pct
        if atr_pct >= s.tech_high_atr_pct:
            vol.add(
                -30,
                f"Daily range (ATR) is {atr_pct:.1f}% of price: high volatility.",
                "ATR% above cap",
            )
        else:
            vol.add(
                10,
                f"Daily range (ATR) is {atr_pct:.1f}% of price: contained volatility.",
                "ATR% within cap",
            )

    # ---- volume ----
    # Bars without a volume (e.g. the in-progress day) are dropped *together with their close*, so
    # OBV and the volume ratio never see NaN.
    vpairs = df[["Close", "Volume"]].dropna()
    vclose, vvol = vpairs["Close"], vpairs["Volume"]
    if len(vvol) > 21 and float(vvol.tail(20).sum()) > 0:
        obv = ind.obv(vclose, vvol)
        obv_now, obv_then = float(obv.iloc[-1]), float(obv.iloc[-20])
        if math.isfinite(obv_now) and math.isfinite(obv_then):
            obv_up = obv_now > obv_then
            px_up = float(vclose.iloc[-1]) > float(vclose.iloc[-20])
            inputs["obv_rising_20d"] = "yes" if obv_up else "no"
            if obv_up == px_up:
                volume_cat.add(
                    40 if obv_up else -40,
                    f"On-balance volume {'confirms the rise' if obv_up else 'confirms the decline'} over 20 sessions.",
                    "OBV confirms price",
                )
            else:
                volume_cat.add(
                    -30 if px_up else 30,
                    "On-balance volume diverges from price over 20 sessions.",
                    "OBV diverges from price",
                )
        avg_vol = float(vvol.iloc[-21:-1].mean())
        if math.isfinite(avg_vol) and avg_vol > 0:
            ratio = float(vvol.iloc[-1]) / avg_vol
            inputs["volume_ratio"] = ratio
            day_up = float(vclose.iloc[-1]) >= float(vclose.iloc[-2])
            if ratio >= s.tech_volume_spike_ratio:
                volume_cat.add(
                    60 if day_up else -60,
                    f"Volume is {ratio:.1f}x its 20-day average on {'an up' if day_up else 'a down'} day.",
                    "Volume spike",
                )
            else:
                volume_cat.add(
                    0, f"Volume is {ratio:.1f}x its 20-day average: no spike.", "Volume normal"
                )

    cats = [trend, mom, vol, volume_cat]
    weights = s.tech_category_weights
    used = [(c, weights.get(c.name, 0.0)) for c in cats if c.score is not None]
    total_w = sum(w for _, w in used)
    if not used or total_w <= 0:
        return SignalResult.missing(
            NAME, "Not enough data to compute any technical indicator.", as_of
        )
    score = sum((c.score or 0.0) * w for c, w in used) / total_w
    coverage = total_w / sum(weights.values())
    depth = min(1.0, (n - s.tech_min_rows) / 150.0)
    confidence = round(max(0.05, min(1.0, (0.4 + 0.6 * depth) * coverage)), 3)
    reasons: list[str] = []
    for c, _ in used:
        reasons.extend(c.reasons)
    cat_summary = ", ".join(f"{c.name} {c.score:+.0f}" for c, _ in used if c.score is not None)
    rules = [f"{c.name}: {r}" for c, _ in used for r in c.rules]
    if math.isnan(score):
        return SignalResult.missing(NAME, "Indicators produced no valid values.", as_of)
    return SignalResult(
        name=NAME,
        score=round(clamp(score), 2),
        confidence=confidence,
        reasons=reasons,
        data_as_of=as_of,
        explanation=Explanation(
            summary=f"Technical score {score:+.0f} ({cat_summary}).",
            inputs=as_float_inputs(inputs),
            rules_applied=rules,
            as_of=as_of,
            annotations=[
                ChartAnnotation(
                    kind="moving_average",
                    label=f"SMA{label}",
                    price=inputs[f"sma{label}"],
                    as_of=as_of,
                )
                for label in ("20", "50", "200")
                if isinstance(inputs.get(f"sma{label}"), float)
            ],
            invalidation_risks=_invalidation(inputs, price),
            sources=[price_history_source(as_of)],
        ),
    )
