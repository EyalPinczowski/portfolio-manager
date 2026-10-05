"""Overfitting statistics, trial counter, embargo and reporting-only calibration (synthetic)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.backtest.experiment import split_of
from app.backtest.overfit import deflated_sharpe_ratio, expected_max_sharpe, pbo_cscv
from app.backtest.trials import TrialLog
from app.scoring.calibration import brier_score, calibration, reliability_table, score_to_prob


def test_pbo_noise_near_half() -> None:
    rng = np.random.default_rng(1)
    vals = [pbo_cscv(rng.normal(0, 1, (160, 12)), n_slices=8) for _ in range(6)]
    assert all(v is not None for v in vals)
    assert 0.3 < float(np.mean([v for v in vals if v is not None])) < 0.7


def test_pbo_real_skill_is_low() -> None:
    rng = np.random.default_rng(2)
    m = rng.normal(0, 1, (160, 8))
    m[:, 0] += 0.5  # one config genuinely better in every slice
    pbo = pbo_cscv(m, n_slices=8)
    assert pbo is not None and pbo < 0.1


def test_pbo_degenerate() -> None:
    assert pbo_cscv(np.zeros((100, 1))) is None
    assert pbo_cscv(np.zeros((3, 4))) is None


def test_dsr_noise_low_and_skill_high() -> None:
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 1, 120)
    best = max((deflated_sharpe_ratio(rng.normal(0, 1, 120), 50) or 0.0) for _ in range(20))
    assert best < 0.95  # the best of many noise runs is not "significant" after deflation
    skill = rng.normal(0.5, 1, 120)
    assert (deflated_sharpe_ratio(skill, 1) or 0) > 0.95
    # more trials never raise it
    assert (deflated_sharpe_ratio(noise, 100, 0.02) or 0) <= (
        deflated_sharpe_ratio(noise, 2, 0.02) or 0
    )


def test_dsr_edge_cases() -> None:
    assert deflated_sharpe_ratio(np.array([1.0, 1.0, 1.0]), 5) is None
    assert deflated_sharpe_ratio(np.array([1.0]), 5) is None
    assert expected_max_sharpe(1, 0.1) == 0.0
    assert expected_max_sharpe(100, 0.1) > expected_max_sharpe(10, 0.1) > 0


def test_trial_log_persists_and_grows(tmp_path: Path) -> None:
    log = TrialLog(tmp_path / "t.json")
    assert log.total() == 0
    assert log.add(3, "a") == 3
    assert TrialLog(tmp_path / "t.json").add(2) == 5


def test_embargo_split() -> None:
    tu = pd.Timestamp("2021-06-30")
    ts = pd.Timestamp
    assert split_of(ts("2021-01-01"), ts("2021-06-30"), tu, 91) == ("train", False)
    assert split_of(ts("2021-05-01"), ts("2021-08-01"), tu, 91) == (None, False)  # straddles
    assert split_of(ts("2021-07-15"), ts("2021-10-15"), tu, 91) == (None, True)  # inside the gap
    assert split_of(ts("2021-07-15"), ts("2021-10-15"), tu, 0) == ("heldout", False)
    assert split_of(ts("2021-10-01"), ts("2022-01-01"), tu, 91) == ("heldout", False)


def test_calibration() -> None:
    assert calibration([], []) is None
    assert brier_score([], []) is None
    probs = [0.9, 0.9, 0.6, 0.6]
    outs = [True, True, True, False]
    assert brier_score(probs, outs) == (0.01 + 0.01 + 0.16 + 0.36) / 4
    cal = calibration(probs, outs)
    assert cal is not None and cal.count == 4 and cal.base_rate == 0.75
    assert [b.count for b in cal.bins] == [2, 2]
    assert cal.bins[0].observed_rate == 0.5 and cal.bins[1].observed_rate == 1.0
    assert reliability_table([1.0], [True])[-1].count == 1
    assert score_to_prob(-100) == 1.0 and score_to_prob(0) == 0.5
