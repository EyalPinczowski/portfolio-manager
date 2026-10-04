"""In-house technical indicators (pandas/numpy only). pandas-ta is abandoned upstream.

Conventions
- Series are aligned with their input index; warm-up values are NaN.
- RSI and ATR use Wilder smoothing seeded with a simple average (the textbook definition).
- Bollinger uses the population standard deviation (ddof=0).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """EMA with alpha = 2/(period+1), seeded with the first value."""
    return close.ewm(span=period, adjust=False, min_periods=1).mean()


def _wilder(values: pd.Series, period: int, first_index: int) -> pd.Series:
    """Wilder smoothing. The seed is the mean of `period` values ending at `first_index`."""
    res = np.full(len(values), np.nan, dtype="float64")
    arr = values.to_numpy(dtype="float64")
    if len(arr) <= first_index:
        return pd.Series(res, index=values.index, dtype="float64")
    seed = float(np.mean(arr[first_index - period + 1 : first_index + 1]))
    res[first_index] = seed
    prev = seed
    for i in range(
        first_index + 1, len(arr)
    ):  # plain floats: same arithmetic, no per-cell pandas cost
        prev = (prev * (period - 1) + arr[i]) / period
        res[i] = prev
    return pd.Series(res, index=values.index, dtype="float64")


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI. The first value appears at index `period`."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, period, period)
    avg_loss = _wilder(loss, period, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(~((avg_loss == 0.0) & avg_gain.notna()), 100.0)
    return out.where(~((avg_loss == 0.0) & (avg_gain == 0.0)), 50.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (lower, middle, upper)."""
    mid = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    return mid - num_std * std, mid, mid + num_std * std


def percent_b(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    lower, _, upper = bollinger(close, period, num_std)
    width = (upper - lower).replace(0.0, np.nan)
    return (close - lower) / width


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(
        axis=1
    )
    tr.iloc[0] = np.nan  # no previous close on the first bar
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR. The first value appears at index `period` (mean of TR[1..period])."""
    return _wilder(true_range(high, low, close), period, period)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-balance volume starting at 0."""
    direction = np.sign(close.diff()).fillna(0.0)
    result: pd.Series = (direction * volume).cumsum()
    return result


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Fast stochastic: returns (%K, %D). A flat range gives 50."""
    ll = low.rolling(window=k_period, min_periods=k_period).min()
    hh = high.rolling(window=k_period, min_periods=k_period).max()
    span = hh - ll
    k = 100.0 * (close - ll) / span.replace(0.0, np.nan)
    k = k.where(~((span == 0.0) & span.notna()), 50.0)
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return k, d
