"""Overfitting statistics for a backtest, as pure numpy functions (no scipy).

- `deflated_sharpe_ratio`: Bailey & Lopez de Prado (2014), the probability that the best Sharpe
  ratio found is above what the best of `n_trials` pure-noise configurations would show.
- `pbo_cscv`: Probability of Backtest Overfitting by combinatorially symmetric cross-validation
  (Bailey, Borwein, Lopez de Prado, Zhu 2015). About 0.5 when the configs are pure noise.

Both are reporting only. They never change a score, a weight or the launch gate decision.
"""

from __future__ import annotations

import math
from itertools import combinations
from statistics import NormalDist

import numpy as np

_N = NormalDist()
_EULER = 0.5772156649015329


def sharpe(returns: np.ndarray) -> float | None:
    """Per-period Sharpe ratio (mean / sample std). None for <2 points or zero variance."""
    x = np.asarray(returns, dtype=float)
    if x.size < 2:
        return None
    sd = float(x.std(ddof=1))
    if sd <= 1e-12:
        return None
    return float(x.mean() / sd)


def expected_max_sharpe(n_trials: int, trial_sharpe_var: float) -> float:
    """Expected maximum Sharpe of `n_trials` unskilled trials (the DSR benchmark SR0)."""
    if n_trials <= 1 or trial_sharpe_var <= 0:
        return 0.0
    n = float(n_trials)
    z = (1 - _EULER) * _N.inv_cdf(1 - 1 / n) + _EULER * _N.inv_cdf(1 - 1 / (n * math.e))
    return math.sqrt(trial_sharpe_var) * z


def deflated_sharpe_ratio(
    returns: np.ndarray, n_trials: int, trial_sharpe_var: float | None = None
) -> float | None:
    """Probability in [0, 1] that the true Sharpe of `returns` beats the best-of-`n_trials` noise.

    `trial_sharpe_var` is the variance of the Sharpe ratios across the configs tried; when not
    given, the sampling variance of one Sharpe ratio (1/(T-1) approx.) is used. Uses the
    skewness and kurtosis of `returns` and its length. None when it cannot be computed.
    """
    x = np.asarray(returns, dtype=float)
    t = x.size
    sr = sharpe(x)
    if sr is None or t < 3:
        return None
    sd = float(x.std(ddof=0))
    z = (x - x.mean()) / sd
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))  # non-excess (normal = 3)
    var = trial_sharpe_var if trial_sharpe_var is not None else 1.0 / (t - 1)
    sr0 = expected_max_sharpe(max(1, int(n_trials)), var)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    stat = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)
    return float(_N.cdf(stat))


def pbo_cscv(matrix: np.ndarray, n_slices: int = 16, max_combos: int = 5000) -> float | None:
    """Probability of Backtest Overfitting.

    `matrix` is T periods x N configs of returns. The rows are cut into an even number of
    contiguous slices; for every way of choosing half of them as in-sample, the config with the
    best in-sample Sharpe is ranked out-of-sample. PBO = share of splits where it ranks at or
    below the out-of-sample median. None with fewer than 2 configs or too few rows.
    """
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        return None
    t, n = m.shape
    s = min(n_slices, (t // 2) * 2)
    s -= s % 2
    if s < 4:
        return None
    blocks = np.array_split(np.arange(t - t % s if t % s else t), s)  # drop the remainder rows
    idx = list(range(s))
    splits = list(combinations(idx, s // 2))
    if len(splits) > max_combos:  # deterministic thinning, keeps the symmetry argument roughly
        step = len(splits) / max_combos
        splits = [splits[int(i * step)] for i in range(max_combos)]

    def sr_cols(rows: np.ndarray) -> np.ndarray:
        sub = m[rows]
        sd = sub.std(axis=0, ddof=1)
        sd = np.where(sd <= 1e-12, np.nan, sd)
        out: np.ndarray = sub.mean(axis=0) / sd
        return out

    logits: list[float] = []
    for tr in splits:
        te = [b for b in idx if b not in tr]
        r_in = sr_cols(np.concatenate([blocks[b] for b in tr]))
        r_out = sr_cols(np.concatenate([blocks[b] for b in te]))
        if np.all(np.isnan(r_in)) or np.all(np.isnan(r_out)):
            continue
        best = int(np.nanargmax(r_in))
        out = np.nan_to_num(r_out, nan=-np.inf)
        rank = float((out < out[best]).sum() + 0.5 * ((out == out[best]).sum() - 1)) + 1.0
        w = rank / (n + 1)
        logits.append(math.log(w / (1 - w)))
    if not logits:
        return None
    return float(np.mean(np.asarray(logits) <= 0))
