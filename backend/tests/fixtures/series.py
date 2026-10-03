"""Deterministic OHLCV fixtures (no live data)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    closes: list[float] | np.ndarray,
    volume: list[float] | np.ndarray | None = None,
    start: str = "2025-01-01",
    spread: float = 0.01,
) -> pd.DataFrame:
    c = np.asarray(closes, dtype="float64")
    idx = pd.bdate_range(start=start, periods=len(c))
    opens = np.concatenate([[c[0]], c[:-1]])
    vol = np.full(len(c), 1_000_000.0) if volume is None else np.asarray(volume, dtype="float64")
    return pd.DataFrame(
        {
            "Open": opens,
            "High": c * (1 + spread),
            "Low": c * (1 - spread),
            "Close": c,
            "Volume": vol,
        },
        index=idx,
    )


def uptrend(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    base = 100 * 1.0025 ** np.arange(n)
    noise = rng.normal(0, 0.4, n)
    return make_ohlcv(base + noise)


def downtrend(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    base = 200 * 0.9975 ** np.arange(n)
    noise = rng.normal(0, 0.4, n)
    return make_ohlcv(base + noise)


def from_points(points: list[tuple[int, float]], n: int) -> np.ndarray:
    xs, ys = zip(*points, strict=True)
    return np.interp(np.arange(n), xs, ys)
