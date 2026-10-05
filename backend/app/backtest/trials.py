"""Persistent trial counter: every configuration ever evaluated by an experiment adds one.

The count feeds the Deflated Sharpe Ratio (more tries = a higher bar). It lives in a small JSON
file beside the history store and only ever grows.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.timeutil import utcnow


class TrialLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def total(self) -> int:
        try:
            return int(json.loads(self.path.read_text(encoding="utf-8")).get("n_trials", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0

    def add(self, n: int, label: str = "") -> int:
        """Record `n` more configurations tried; returns the new total."""
        total = self.total() + max(0, n)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            log = data.get("log", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            log = []
        log.append({"at": utcnow().isoformat(), "added": n, "label": label})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"n_trials": total, "log": log[-200:]}), encoding="utf-8")
        return total
