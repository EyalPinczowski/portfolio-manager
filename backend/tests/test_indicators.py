"""Indicators are checked against hand-computed values and an independent naive reference."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals import indicators as ind

STOCKCHARTS_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
    46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35,
    44.03, 44.18, 44.22, 44.57, 43.42, 42.66, 43.13,
]  # fmt: skip
# Published RSI(14) values for this series (StockCharts ChartSchool); they round gains/losses
# slightly differently, hence the 0.1 tolerance.
STOCKCHARTS_RSI = [
    70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99,
    41.46, 41.87, 45.46, 37.30, 33.08, 37.77,
]  # fmt: skip


def naive_rsi(closes: list[float], period: int) -> list[float]:
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    out = [100 - 100 / (1 + avg_g / avg_l)]
    for g, loss in zip(gains[period:], losses[period:], strict=True):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + loss) / period
        out.append(100 - 100 / (1 + avg_g / avg_l))
    return out


def test_sma_by_hand() -> None:
    s = ind.sma(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert s.isna().tolist() == [True, True, False, False, False]
    assert s.dropna().tolist() == [2.0, 3.0, 4.0]


def test_ema_by_hand() -> None:
    # span 3 -> alpha 0.5: 1, 1.5, 2.25, 3.125
    e = ind.ema(pd.Series([1.0, 2, 3, 4]), 3)
    assert e.tolist() == pytest.approx([1.0, 1.5, 2.25, 3.125])


def test_rsi_matches_naive_reference_and_published_series() -> None:
    closes = pd.Series(STOCKCHARTS_CLOSES)
    got = ind.rsi(closes, 14).dropna().tolist()
    assert got == pytest.approx(naive_rsi(STOCKCHARTS_CLOSES, 14), abs=1e-9)
    assert len(got) == len(STOCKCHARTS_RSI)
    assert got == pytest.approx(STOCKCHARTS_RSI, abs=0.1)
    assert ind.rsi(closes, 14).iloc[:14].isna().all()


def test_rsi_edge_cases() -> None:
    rising = pd.Series(np.arange(1.0, 40.0))
    assert ind.rsi(rising, 14).iloc[-1] == 100.0
    flat = pd.Series([5.0] * 40)
    assert ind.rsi(flat, 14).iloc[-1] == 50.0


def test_macd_by_hand() -> None:
    close = pd.Series([1.0, 2, 3, 4])
    line, sig, hist = ind.macd(close, fast=2, slow=3, signal=2)
    # ema2: 1, 1.6667, 2.5556, 3.5185 ; ema3: 1, 1.5, 2.25, 3.125
    assert line.tolist() == pytest.approx([0.0, 0.166667, 0.305556, 0.393519], abs=1e-5)
    # signal = ema(span 2, alpha 2/3) of the line
    assert sig.iloc[1] == pytest.approx(0.0 + (2 / 3) * 0.166667, abs=1e-5)
    assert hist.tolist() == pytest.approx((line - sig).tolist())


def test_macd_constant_series_is_zero() -> None:
    line, sig, hist = ind.macd(pd.Series([10.0] * 60))
    assert line.abs().max() == pytest.approx(0.0)
    assert hist.abs().max() == pytest.approx(0.0)
    assert sig.abs().max() == pytest.approx(0.0)


def test_bollinger_by_hand() -> None:
    lower, mid, upper = ind.bollinger(pd.Series([1.0, 2, 3, 4, 5]), period=5, num_std=2)
    assert mid.iloc[-1] == 3.0
    # population std of 1..5 is sqrt(2)
    assert upper.iloc[-1] == pytest.approx(3 + 2 * np.sqrt(2))
    assert lower.iloc[-1] == pytest.approx(3 - 2 * np.sqrt(2))
    pb = ind.percent_b(pd.Series([1.0, 2, 3, 4, 5]), period=5, num_std=2)
    assert pb.iloc[-1] == pytest.approx((5 - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]))


def test_true_range_and_atr_by_hand() -> None:
    high = pd.Series([10.0, 11, 12, 11, 13])
    low = pd.Series([9.0, 10, 10, 9, 11])
    close = pd.Series([9.5, 10.5, 11, 10, 12])
    tr = ind.true_range(high, low, close)
    # TR[1]=max(1, |11-9.5|, |10-9.5|)=1.5 ; TR[2]=max(2, 1.5, 0.5)=2 ; TR[3]=max(2,0,2)=2 ; TR[4]=max(2,3,1)=3
    assert tr.iloc[1:].tolist() == [1.5, 2.0, 2.0, 3.0]
    a = ind.atr(high, low, close, period=3)
    first = (1.5 + 2 + 2) / 3
    assert a.iloc[3] == pytest.approx(first)
    assert a.iloc[4] == pytest.approx((first * 2 + 3) / 3)
    assert a.iloc[:3].isna().all()


def test_obv_by_hand() -> None:
    close = pd.Series([10.0, 11, 10.5, 10.5, 12])
    volume = pd.Series([100.0, 200, 150, 300, 400])
    assert ind.obv(close, volume).tolist() == [0.0, 200.0, 50.0, 50.0, 450.0]


def test_stochastic_by_hand() -> None:
    high = pd.Series([10.0, 12, 11, 13, 12])
    low = pd.Series([8.0, 9, 9, 10, 10])
    close = pd.Series([9.0, 11, 10, 12, 11])
    k, d = ind.stochastic(high, low, close, k_period=3, d_period=2)
    # idx2: LL=8 HH=12 -> (10-8)/4=50 ; idx3: LL=9 HH=13 -> (12-9)/4=75 ; idx4: LL=9 HH=13 -> 50
    assert k.iloc[2:].tolist() == pytest.approx([50.0, 75.0, 50.0])
    assert d.iloc[3:].tolist() == pytest.approx([62.5, 62.5])


def test_stochastic_flat_range_is_50() -> None:
    flat = pd.Series([5.0] * 20)
    k, _ = ind.stochastic(flat, flat, flat, k_period=5, d_period=3)
    assert k.dropna().eq(50.0).all()
