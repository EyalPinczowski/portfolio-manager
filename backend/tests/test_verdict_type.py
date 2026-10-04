"""`Verdict` can only come from `LaunchGate.release()`."""

from __future__ import annotations

import ast
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.launchgate import LaunchGate, LaunchGateClosedError, Verdict
from tests.test_launch_gate import GOOD_BACKTEST, GOOD_PAPER, FakeBacktests, FakePaper, gate

APP = Path(__file__).resolve().parent.parent / "app"
ARGS = {"symbol": "AAPL", "action": "buy", "score": 40.0, "confidence": 0.6, "reasons": ["why"]}


def test_a_closed_gate_releases_nothing() -> None:
    closed = LaunchGate(Settings(_env_file=None))
    with pytest.raises(LaunchGateClosedError) as err:
        closed.release(**ARGS)  # type: ignore[arg-type]
    assert err.value.reasons and "backtest" in " ".join(err.value.reasons).lower()


def test_an_open_gate_releases_a_frozen_verdict() -> None:
    v = gate().release(**ARGS)  # type: ignore[arg-type]
    assert isinstance(v, Verdict) and v.symbol == "AAPL" and v.reasons == ("why",)
    assert v.released_at.tzinfo is UTC and v.weights_hash
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.action = "sell"  # type: ignore[misc]


def test_a_verdict_cannot_be_constructed_directly() -> None:
    kwargs = {**ARGS, "reasons": ("why",), "released_at": datetime.now(UTC), "weights_hash": "x"}
    with pytest.raises(TypeError, match=r"LaunchGate\.release"):
        Verdict(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"LaunchGate\.release"):
        Verdict(**kwargs, _mint=object())  # type: ignore[arg-type]
    real = gate().release(**ARGS)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"LaunchGate\.release"):
        dataclasses.replace(real, action="sell")  # editing a released verdict re-checks the key


def test_release_needs_reasons_and_sane_numbers() -> None:
    g = LaunchGate(Settings(_env_file=None), FakeBacktests(GOOD_BACKTEST), FakePaper(GOOD_PAPER))
    with pytest.raises(ValueError, match="reason"):
        g.release("AAPL", "buy", 10.0, 0.5, [])
    with pytest.raises(ValueError, match="reason"):
        g.release("AAPL", "buy", 10.0, 0.5, ["  "])
    with pytest.raises(ValueError, match="score"):
        g.release("AAPL", "buy", 101.0, 0.5, ["x"])
    with pytest.raises(ValueError, match="score"):
        g.release("AAPL", "buy", 10.0, 1.5, ["x"])


def _constructs_verdict(source: str) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "Verdict")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "Verdict")
        )
    ]


def test_verdict_is_constructed_only_inside_the_launch_gate_module() -> None:
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "launchgate.py":
            continue
        if _constructs_verdict(path.read_text()):
            offenders.append(str(path.relative_to(APP)))
    assert offenders == []


def test_the_scanner_would_catch_a_bypass() -> None:
    assert _constructs_verdict("from app.launchgate import Verdict\nx = Verdict(symbol='A')")
    assert _constructs_verdict("import app.launchgate as g\nx = g.Verdict()")
    assert not _constructs_verdict("x = 1")
