"""Launch gate: the API must not return live buy/sell verdicts until BOTH gates pass.

(a) a passing backtest exists for the *active* weights config, and
(b) paper trading passes: >= N weeks with no critical errors, >= M calls resolved at 1 month, and
    beating the S&P 500 and the TA-125 (all thresholds in `Settings.launch_*`).

Nothing records backtests or paper trades yet, so the gate is always closed and says why. Any
route that returns a verdict must depend on `require_launch_gate`; `tests/test_launch_gate.py`
fails if a response model has a verdict-like field on an un-gated route.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, status

from app.config import Settings, get_settings


@dataclass(frozen=True)
class BacktestRecord:
    weights_hash: str  # which weights config was tested (see `weights_fingerprint`)
    passed: bool
    run_at: datetime


@dataclass(frozen=True)
class PaperMetrics:
    weeks_running: float
    critical_errors: int
    resolved_calls_1m: int
    excess_return_pct: dict[str, float] = field(default_factory=dict)  # benchmark -> our edge, in %


class BacktestStore(Protocol):
    def latest(self) -> BacktestRecord | None: ...


class PaperStore(Protocol):
    def metrics(self) -> PaperMetrics | None: ...


class NoBacktests:
    """Nothing is recorded yet (Phase 2 adds the backtest runner)."""

    def latest(self) -> BacktestRecord | None:
        return None


class NoPaperTrading:
    """Nothing is recorded yet (Phase 2 adds forward paper trading)."""

    def metrics(self) -> PaperMetrics | None:
        return None


def weights_fingerprint(weights: dict[str, float]) -> str:
    """A stable id of a weights config: a backtest only counts for the weights it ran with."""
    canonical = json.dumps(sorted((k, round(float(v), 9)) for k, v in weights.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class GateStatus:
    open: bool
    reasons: list[str]


class LaunchGate:
    def __init__(
        self,
        settings: Settings | None = None,
        backtests: BacktestStore | None = None,
        paper: PaperStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.backtests: BacktestStore = backtests or NoBacktests()
        self.paper: PaperStore = paper or NoPaperTrading()

    def evaluate(self) -> GateStatus:
        s = self.settings
        reasons: list[str] = []
        if s.launch_require_backtest:
            record = self.backtests.latest()
            if record is None:
                reasons.append("No backtest has been run for the active weights configuration.")
            elif record.weights_hash != weights_fingerprint(s.signal_weights):
                reasons.append(
                    "The latest backtest was run with different weights than the active ones."
                )
            elif not record.passed:
                reasons.append("The backtest for the active weights configuration did not pass.")
        m = self.paper.metrics()
        if m is None:
            reasons.append(
                f"Paper trading has not started: it needs {s.launch_paper_min_weeks} weeks without "
                f"critical errors and {s.launch_paper_min_resolved_calls} calls resolved at 1 month."
            )
        else:
            if m.weeks_running < s.launch_paper_min_weeks:
                reasons.append(
                    f"Paper trading has run {m.weeks_running:.1f} of {s.launch_paper_min_weeks} required weeks."
                )
            if m.critical_errors > s.launch_paper_max_critical_errors:
                reasons.append(
                    f"Paper trading had {m.critical_errors} critical error(s); none are allowed."
                )
            if m.resolved_calls_1m < s.launch_paper_min_resolved_calls:
                reasons.append(
                    f"Only {m.resolved_calls_1m} of {s.launch_paper_min_resolved_calls} required calls "
                    "are resolved at 1 month."
                )
            for bench in s.launch_paper_must_beat:
                edge = m.excess_return_pct.get(bench)
                if edge is None:
                    reasons.append(f"Paper trading has no result against {bench} yet.")
                elif edge <= 0:
                    reasons.append(f"Paper trading does not beat {bench} ({edge:+.1f}%).")
        return GateStatus(open=not reasons, reasons=reasons)


def get_launch_gate() -> LaunchGate:
    """Dependency (override it in tests). Always closed until backtest and paper stores exist."""
    return LaunchGate()


GateDep = Annotated[LaunchGate, Depends(get_launch_gate)]


def require_launch_gate(gate: GateDep) -> None:
    """Put this on every route that returns a buy/sell verdict. 403 while the gate is closed."""
    result = gate.evaluate()
    if not result.open:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "launch_gate_closed", "reasons": result.reasons},
        )
