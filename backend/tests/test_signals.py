from __future__ import annotations

import numpy as np
import pandas as pd

from app.signals import indicators as ind
from app.signals.base import SignalResult
from app.signals.patterns import cluster_levels, find_pivots, patterns_signal
from app.signals.technical import technical_signal
from tests.fixtures.series import downtrend, from_points, make_ohlcv, uptrend


def _check_contract(res: SignalResult) -> None:
    assert -100 <= res.score <= 100
    assert 0 <= res.confidence <= 1
    assert res.reasons and all(isinstance(r, str) and r for r in res.reasons)
    assert res.explanation.summary
    assert isinstance(res.explanation.inputs, dict)


def test_technical_uptrend_is_positive_with_reasons() -> None:
    res = technical_signal(uptrend())
    _check_contract(res)
    assert res.name == "technical"
    assert res.score > 5  # trend is strongly positive; extreme RSI tempers the total
    assert res.confidence > 0.5
    assert "trend +" in res.explanation.summary
    assert any("200-day" in r for r in res.reasons)
    assert "sma200" in res.explanation.inputs
    assert res.explanation.rules_applied


def test_technical_downtrend_is_negative() -> None:
    res = technical_signal(downtrend())
    _check_contract(res)
    assert res.score < -5
    assert "trend -" in res.explanation.summary


def test_technical_missing_or_short_data_has_zero_confidence() -> None:
    for df in (None, pd.DataFrame(), uptrend(30)):
        res = technical_signal(df)
        _check_contract(res)
        assert res.confidence == 0.0
        assert res.score == 0.0


def test_technical_is_deterministic_and_as_of_is_last_bar() -> None:
    df = uptrend()
    a, b = technical_signal(df), technical_signal(df)
    assert a == b
    assert a.data_as_of == pd.Timestamp(df.index[-1]).to_pydatetime()


def test_technical_without_sma200_history_still_scores_with_lower_confidence() -> None:
    short = technical_signal(uptrend(80))
    long = technical_signal(uptrend(260))
    assert short.confidence > 0
    assert short.confidence < long.confidence


def test_find_pivots_and_levels() -> None:
    s = pd.Series([1.0, 2, 3, 2, 1, 2, 3.1, 2, 1, 0.5, 1, 2, 3, 2, 1])
    highs = find_pivots(s, 2, "high")
    assert [i for i, _ in highs] == [2, 6, 12]
    levels = cluster_levels([100.0, 100.5, 120.0], tolerance_pct=1.0)
    assert len(levels) == 2 and levels[0].touches == 2


def test_patterns_breakout_with_volume() -> None:
    rng = np.random.default_rng(3)
    closes = 100 + rng.normal(0, 0.3, 80)
    closes[-1] = 108.0
    vol = np.full(80, 1_000_000.0)
    vol[-1] = 3_000_000.0
    res = patterns_signal(make_ohlcv(closes, vol))
    _check_contract(res)
    assert any("Breakout" in r for r in res.reasons)
    assert res.score > 20


def test_patterns_breakdown() -> None:
    rng = np.random.default_rng(4)
    closes = 100 + rng.normal(0, 0.3, 80)
    closes[-1] = 92.0
    res = patterns_signal(make_ohlcv(closes))
    assert any("Breakdown" in r for r in res.reasons)
    assert res.score < -20


def test_patterns_double_top_confirmed() -> None:
    pts = [
        (0, 100.0),
        (40, 100.0),
        (60, 120.0),
        (75, 105.0),
        (90, 119.0),
        (105, 100.0),
        (110, 100.0),
    ]
    closes = from_points(pts, 111)
    res = patterns_signal(make_ohlcv(closes, spread=0.002))
    _check_contract(res)
    assert any("Double top" in r and "confirmed" in r for r in res.reasons)


def test_patterns_double_bottom_confirmed() -> None:
    pts = [(0, 100.0), (40, 100.0), (60, 80.0), (75, 95.0), (90, 81.0), (105, 100.0), (110, 100.0)]
    closes = from_points(pts, 111)
    res = patterns_signal(make_ohlcv(closes, spread=0.002))
    assert any("Double bottom" in r and "confirmed" in r for r in res.reasons)
    assert res.score > 0


def test_patterns_golden_and_death_cross() -> None:
    # decline then sharp recovery -> SMA50 crosses above SMA200 at a known bar
    closes = from_points([(0, 150.0), (220, 100.0), (330, 190.0)], 331)
    df = make_ohlcv(closes)
    diff = (ind.sma(df["Close"], 50) - ind.sma(df["Close"], 200)).dropna()
    sign = np.sign(diff.to_numpy())
    cross = int(np.where(np.diff(sign) > 0)[0][0]) + 1
    pos = diff.index.get_loc(diff.index[cross])
    idx = df.index.get_loc(diff.index[pos])
    res = patterns_signal(df.iloc[: idx + 6])
    assert any("Golden cross" in r for r in res.reasons)

    closes2 = from_points([(0, 100.0), (220, 150.0), (330, 60.0)], 331)
    df2 = make_ohlcv(closes2)
    diff2 = (ind.sma(df2["Close"], 50) - ind.sma(df2["Close"], 200)).dropna()
    sign2 = np.sign(diff2.to_numpy())
    cross2 = int(np.where(np.diff(sign2) < 0)[0][0]) + 1
    idx2 = df2.index.get_loc(diff2.index[cross2])
    res2 = patterns_signal(df2.iloc[: idx2 + 6])
    assert any("Death cross" in r for r in res2.reasons)
    assert res2.score < 0


def test_patterns_support_resistance_proximity() -> None:
    # range-bound market oscillating between ~95 and ~105, ending near support
    t = np.arange(120)
    closes = 100 + 5 * np.sin(t / 6.0)
    closes[-1] = closes[-6:].min()
    res = patterns_signal(make_ohlcv(closes))
    _check_contract(res)
    assert (
        res.explanation.inputs.get("support") is not None
        or res.explanation.inputs.get("resistance") is not None
    )


def test_patterns_missing_data() -> None:
    for df in (None, pd.DataFrame(), uptrend(20)):
        res = patterns_signal(df)
        _check_contract(res)
        assert res.confidence == 0.0


def test_signal_result_requires_reasons() -> None:
    import pytest
    from pydantic import ValidationError

    from app.signals.base import Explanation

    with pytest.raises(ValidationError):
        SignalResult(
            score=10,
            confidence=1,
            reasons=[],
            data_as_of=pd.Timestamp("2026-01-01").to_pydatetime(),
            explanation=Explanation(summary="x"),
        )
    with pytest.raises(ValidationError):
        SignalResult.model_validate(
            {
                "score": 150,
                "confidence": 1,
                "reasons": ["x"],
                "data_as_of": "2026-01-01T00:00:00",
                "explanation": {"summary": "x"},
            }
        )
