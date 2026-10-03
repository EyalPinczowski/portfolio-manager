"""Typed Explanation v1: shape, compatibility with the v0 fields, storage and the API."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import ValidationError
from sqlmodel import Session

from app.config import Settings
from app.models import SignalCache
from app.scoring.scorecard import compute_scorecard, refresh_scorecard
from app.signals.base import (
    EXPLANATION_VERSION,
    ChartAnnotation,
    Explanation,
    SignalContribution,
    SignalResult,
)
from app.signals.patterns import patterns_signal
from app.signals.technical import technical_signal
from tests.fixtures.series import from_points, make_ohlcv, uptrend

S = Settings(_env_file=None)


def test_v0_payload_still_loads_with_defaults() -> None:
    old = Explanation.model_validate({"summary": "x", "inputs": {"a": 1.0}, "rules_applied": ["r"]})
    assert old.version == EXPLANATION_VERSION == 1
    assert old.contributions == [] and old.sources == [] and old.annotations == []


def test_naive_datetimes_become_utc() -> None:
    e = Explanation(summary="s", as_of=datetime(2026, 1, 2, 3, 4))
    assert e.as_of is not None and e.as_of.tzinfo is UTC


def test_contribution_and_annotation_are_validated() -> None:
    with pytest.raises(ValidationError):
        SignalContribution(name="x", score=1, weight=10, confidence=1.5)
    with pytest.raises(ValidationError):
        ChartAnnotation(kind="nonsense", label="x")  # type: ignore[arg-type]
    c = SignalContribution(
        name="t", score=1, weight=10, confidence=1, raw={"a": 1.0, "b": None, "c": "x"}
    )
    assert c.raw["b"] is None


def test_missing_signal_has_an_explanation_without_sources() -> None:
    res = SignalResult.missing("technical", "nothing")
    assert res.explanation.version == 1 and res.explanation.sources == []


@pytest.mark.parametrize("fn", [technical_signal, patterns_signal])
def test_chart_signals_carry_sources_and_as_of(fn) -> None:  # type: ignore[no-untyped-def]
    df = uptrend(400)
    res = fn(df, S)
    assert res.confidence > 0
    exp = res.explanation
    assert exp.version == 1
    assert exp.as_of is not None and exp.as_of.tzinfo is not None
    assert exp.as_of.date() == pd.Timestamp(df.index[-1]).date()
    assert exp.sources and exp.sources[0].as_of == exp.as_of
    assert exp.rules_applied and exp.summary
    Explanation.model_validate_json(exp.model_dump_json())  # round trip


def test_technical_annotations_are_moving_averages() -> None:
    exp = technical_signal(uptrend(400), S).explanation
    assert {a.label for a in exp.annotations} == {"SMA20", "SMA50", "SMA200"}
    assert all(a.kind == "moving_average" and a.price for a in exp.annotations)
    assert exp.invalidation_risks


def test_pattern_annotations_are_levels_and_patterns() -> None:
    pts = [(0, 100.0), (40, 100.0), (60, 80.0), (75, 95.0), (90, 81.0), (105, 100.0), (110, 100.0)]
    exp = patterns_signal(make_ohlcv(from_points(pts, 111), spread=0.002), S).explanation
    kinds = {a.kind for a in exp.annotations}
    assert "pattern" in kinds and kinds & {"support", "resistance"}
    assert all(a.price for a in exp.annotations if a.kind != "pattern")
    assert exp.invalidation_risks


def test_scorecard_explanation_has_per_signal_contributions() -> None:
    class H:
        def get_history(self, symbol: str, days: int) -> pd.DataFrame:
            return uptrend(400)

    card = compute_scorecard("AAPL", H(), S)  # type: ignore[arg-type]
    exp = Explanation.model_validate(card["explanation"])
    names = {c.name for c in exp.contributions}
    assert {"technical", "patterns"} <= names
    active = [c for c in exp.contributions if c.weight > 0]
    assert abs(sum(c.weight for c in active) - 100.0) < 0.01
    assert all(c.raw is not None for c in exp.contributions)
    assert exp.sources and exp.as_of is not None
    for sig in card["signals"]:
        assert Explanation.model_validate(sig["explanation"]).version == 1


def test_explanation_json_is_stored_with_the_score_card(db: Session) -> None:
    class H:
        def get_history(self, symbol: str, days: int) -> pd.DataFrame:
            return uptrend(400)

    refresh_scorecard(db, "AAPL", H(), S)  # type: ignore[arg-type]
    row = db.get(SignalCache, "AAPL")
    assert row is not None
    assert row.payload["explanation"]["version"] == 1
    assert isinstance(row.payload["explanation"]["as_of"], str)  # JSON, not a datetime object
    assert row.payload["signals"][0]["explanation"]["sources"]
    assert datetime.fromisoformat(row.payload["explanation"]["as_of"]).tzinfo is not None
