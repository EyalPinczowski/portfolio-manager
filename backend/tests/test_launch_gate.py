"""LaunchGate service, GET /api/launch-gate, and the no-verdict-fields contract test."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import Settings
from app.launchgate import (
    BacktestRecord,
    LaunchGate,
    PaperMetrics,
    get_launch_gate,
    require_launch_gate,
    weights_fingerprint,
)
from app.main import app, create_app
from tests.verdict_contract import find_violations, verdict_token

# Fields that may carry a verdict, mapped to why. Each one is only legal on a route that depends on
# `require_launch_gate`. Phase 2 adds entries here (e.g. "CIOVerdict.action"); keep it empty until then.
VERDICT_ALLOWLIST: dict[str, str] = {}

S = Settings(_env_file=None)
GOOD_BACKTEST = BacktestRecord(weights_fingerprint(S.signal_weights), True, datetime(2026, 10, 1))
GOOD_PAPER = PaperMetrics(
    weeks_running=5,
    critical_errors=0,
    resolved_calls_1m=60,
    excess_return_pct={"^GSPC": 2.0, "^TA125.TA": 1.0},
)


class FakeBacktests:
    def __init__(self, record: BacktestRecord | None) -> None:
        self.record = record

    def latest(self) -> BacktestRecord | None:
        return self.record


class FakePaper:
    def __init__(self, metrics: PaperMetrics | None) -> None:
        self.m = metrics

    def metrics(self) -> PaperMetrics | None:
        return self.m


def gate(
    bt: BacktestRecord | None = GOOD_BACKTEST, paper: PaperMetrics | None = GOOD_PAPER
) -> LaunchGate:
    return LaunchGate(S, FakeBacktests(bt), FakePaper(paper))


# ---------------------------------------------------------------- the service
def test_gate_is_closed_for_now_and_says_why() -> None:
    result = LaunchGate(S).evaluate()
    assert result.open is False
    text = " ".join(result.reasons).lower()
    assert "backtest" in text and "paper trading" in text


def test_gate_opens_only_when_both_gates_pass() -> None:
    assert gate().evaluate().open is True
    assert gate().evaluate().reasons == []


@pytest.mark.parametrize(
    ("bt", "paper", "needle"),
    [
        (None, GOOD_PAPER, "no backtest"),
        (
            BacktestRecord(weights_fingerprint(S.signal_weights), False, datetime(2026, 1, 1)),
            GOOD_PAPER,
            "did not pass",
        ),
        (
            BacktestRecord("other-weights", True, datetime(2026, 1, 1)),
            GOOD_PAPER,
            "different weights",
        ),
        (GOOD_BACKTEST, None, "not started"),
        (GOOD_BACKTEST, PaperMetrics(3.9, 0, 60, {"^GSPC": 1, "^TA125.TA": 1}), "3.9 of 4"),
        (GOOD_BACKTEST, PaperMetrics(5, 1, 60, {"^GSPC": 1, "^TA125.TA": 1}), "critical error"),
        (GOOD_BACKTEST, PaperMetrics(5, 0, 49, {"^GSPC": 1, "^TA125.TA": 1}), "49 of 50"),
        (GOOD_BACKTEST, PaperMetrics(5, 0, 60, {"^GSPC": 1, "^TA125.TA": -0.5}), "^TA125.TA"),
        (GOOD_BACKTEST, PaperMetrics(5, 0, 60, {"^GSPC": 0.0, "^TA125.TA": 1}), "^GSPC"),
        (GOOD_BACKTEST, PaperMetrics(5, 0, 60, {"^GSPC": 1}), "no result against ^TA125.TA"),
    ],
)
def test_each_condition_alone_keeps_the_gate_closed(
    bt: BacktestRecord | None, paper: PaperMetrics | None, needle: str
) -> None:
    result = gate(bt, paper).evaluate()
    assert result.open is False
    assert any(needle.lower() in r.lower() for r in result.reasons), result.reasons


def test_thresholds_come_from_config() -> None:
    strict = Settings(_env_file=None, launch_paper_min_weeks=8, launch_paper_min_resolved_calls=100)
    bt = BacktestRecord(weights_fingerprint(strict.signal_weights), True, datetime(2026, 1, 1))
    result = LaunchGate(strict, FakeBacktests(bt), FakePaper(GOOD_PAPER)).evaluate()
    assert not result.open and len(result.reasons) == 2
    lax = Settings(_env_file=None, launch_require_backtest=False)
    assert LaunchGate(lax, paper=FakePaper(GOOD_PAPER)).evaluate().open is True


def test_changing_the_weights_invalidates_the_backtest() -> None:
    other = Settings(_env_file=None, signal_weights={**S.signal_weights, "technical": 30.0})
    assert weights_fingerprint(other.signal_weights) != weights_fingerprint(S.signal_weights)
    assert (
        LaunchGate(other, FakeBacktests(GOOD_BACKTEST), FakePaper(GOOD_PAPER)).evaluate().open
        is False
    )


# ---------------------------------------------------------------- the endpoint
def test_launch_gate_endpoint_is_closed_with_reasons(signup: object, client: TestClient) -> None:
    assert client.get("/api/launch-gate").status_code == 401
    c = signup()  # type: ignore[operator]
    body = c.get("/api/launch-gate").json()
    assert set(body) == {"open", "reasons"} and body["open"] is False
    assert body["reasons"] and all(isinstance(r, str) for r in body["reasons"])


def test_the_gated_dependency_blocks_routes_while_closed() -> None:
    demo = FastAPI()

    @demo.get("/v", dependencies=[Depends(require_launch_gate)])
    def verdict() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(demo)
    r = client.get("/v")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "launch_gate_closed"
    demo.dependency_overrides[get_launch_gate] = lambda: gate()
    assert client.get("/v").status_code == 200


# ---------------------------------------------------------------- the contract test
def test_no_response_model_has_a_verdict_field() -> None:
    violations = find_violations(app, VERDICT_ALLOWLIST)
    assert violations == [], "\n".join(str(v) for v in violations)


def test_the_real_app_is_checked_over_a_meaningful_number_of_models() -> None:
    spec = create_app().openapi()
    assert len(spec["components"]["schemas"]) > 30 and len(spec["paths"]) > 25


@pytest.mark.parametrize(
    "name",
    ["verdict", "recommendation", "action", "buy", "sell", "signal_strength", "SignalStrength", "buyIdea",
     "cio_verdict", "recommended_action", "hold", "Sell"],
)  # fmt: skip
def test_verdict_like_names_are_detected(name: str) -> None:
    assert verdict_token(name) is not None


@pytest.mark.parametrize(
    "name",
    [
        "total",
        "score",
        "signals",
        "holdings",
        "holding_id",
        "stop_tp_status",
        "weight_pct",
        "holder",
        "reasons",
        "type",
    ],
)
def test_ordinary_names_are_not_flagged(name: str) -> None:
    assert verdict_token(name) is None


def _demo(gated: bool) -> FastAPI:
    class Idea(BaseModel):
        symbol: str
        recommendation: str

    class Wrapper(BaseModel):
        ideas: list[Idea]

    demo = FastAPI()
    deps = [Depends(require_launch_gate)] if gated else []

    @demo.get("/ideas", response_model=Wrapper, dependencies=deps)
    def ideas() -> Wrapper:
        return Wrapper(ideas=[])

    return demo


def test_checker_flags_a_verdict_field_nested_in_a_response() -> None:
    found = find_violations(
        _demo(gated=True)
    )  # gating alone does not excuse it: no allowlist entry
    assert [(v.schema, v.field) for v in found] == [("Idea", "recommendation")]


def test_allowlist_requires_the_gate_dependency() -> None:
    allow = {"Idea.recommendation": "Phase 2 CIO output"}
    unguarded = find_violations(_demo(gated=False), allow)
    assert len(unguarded) == 1 and "require_launch_gate" in unguarded[0].reason
    assert find_violations(_demo(gated=True), allow) == []
