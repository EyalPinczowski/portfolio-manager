"""Deterministic OHLCV fixtures (no live data)."""

from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------- synthetic backtest history
# SYNTHETIC: random walks with a made-up drift. Anything computed from them says nothing about how
# the strategy would do on real markets; the backtest report prints that warning for such stores.
def gbm_ohlcv(
    seed: int,
    n: int = 900,
    start: str = "2019-01-01",
    start_price: float = 100.0,
    drift: float = 0.0004,
    vol: float = 0.015,
    gap_vol: float = 0.004,
) -> pd.DataFrame:
    """Deterministic daily bars: log-normal close-to-close, an overnight gap, an intraday range."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    closes = start_price * np.exp(np.cumsum(rets))
    prev = np.concatenate([[start_price], closes[:-1]])
    opens = prev * np.exp(rng.normal(0, gap_vol, n))
    rng_hi = np.abs(rng.normal(0, vol / 2, n))
    rng_lo = np.abs(rng.normal(0, vol / 2, n))
    highs = np.maximum(opens, closes) * (1 + rng_hi)
    lows = np.minimum(opens, closes) * (1 - rng_lo)
    idx = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


def write_synthetic_store(
    root: str | Path,
    symbols: list[str],
    benchmark: str = "^GSPC",
    n: int = 900,
    start: str = "2019-01-01",
    seed: int = 1,
    fx: bool = True,
) -> None:
    """Fill a `HistoryStore` directory with synthetic bars and a manifest that says so."""
    from app.backtest.data import HistoryStore

    store = HistoryStore(root)
    store.save_history(benchmark, gbm_ohlcv(seed * 1000, n, start, 1000.0, 0.0003, 0.01))
    for i, sym in enumerate(symbols):
        drift = (-0.0002, 0.0001, 0.0004, 0.0007)[i % 4]
        store.save_history(sym, gbm_ohlcv(seed * 1000 + i + 1, n, start, 50.0 + 10 * i, drift))
    if fx:
        store.save_history("ILS=X", gbm_ohlcv(seed * 1000 + 999, n, start, 3.6, 0.0, 0.003))
    store.write_manifest(synthetic=True, source="tests.fixtures.series.gbm_ohlcv", seed=seed)
