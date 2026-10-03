"""NaN volume must never leak into reasons ("Volume is nanx...") or break JSON export."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from app.signals.patterns import patterns_signal
from app.signals.technical import technical_signal
from tests.fixtures.series import downtrend, uptrend


def _has_nan_text(texts: list[str]) -> list[str]:
    return [t for t in texts if "nan" in t.lower().replace("finance", "")]


def test_nan_last_bar_volume_does_not_produce_a_nanx_reason() -> None:
    df = uptrend(260)
    df.loc[df.index[-1], "Volume"] = np.nan
    res = technical_signal(df)
    assert res.confidence > 0
    assert not _has_nan_text(res.reasons), res.reasons
    assert not any("nan" in str(v).lower() for v in res.explanation.inputs.values())
    json.dumps(res.model_dump(mode="json"), allow_nan=False)  # the /me/export path


def test_nan_volume_in_the_middle_does_not_poison_obv() -> None:
    df = uptrend(260)
    df.loc[df.index[-10:-5], "Volume"] = np.nan
    res = technical_signal(df)
    assert not _has_nan_text(res.reasons), res.reasons
    assert math.isfinite(res.score)


def test_all_nan_volume_is_just_skipped() -> None:
    df = downtrend(260)
    df["Volume"] = np.nan
    res = technical_signal(df)
    assert res.confidence > 0 and not _has_nan_text(res.reasons)
    json.dumps(res.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("build", [uptrend, downtrend])
def test_property_no_reason_contains_nan_for_random_nan_volume(seed: int, build) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    df: pd.DataFrame = build(260)
    mask = rng.random(len(df)) < rng.choice([0.0, 0.02, 0.2, 0.9])
    if rng.random() < 0.5:
        mask[-1] = True  # the in-progress bar often has no volume yet
    df.loc[mask, "Volume"] = np.nan
    if rng.random() < 0.3:
        df.loc[rng.random(len(df)) < 0.05, ["High", "Low"]] = np.nan
    for res in (technical_signal(df), patterns_signal(df)):
        assert not _has_nan_text(res.reasons), (seed, res.reasons)
        assert math.isfinite(res.score)
        json.dumps(res.model_dump(mode="json"), allow_nan=False)
