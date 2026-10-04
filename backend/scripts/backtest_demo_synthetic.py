"""Run the backtest machinery end to end on SYNTHETIC prices. Shows that it runs; proves nothing.

    python scripts/backtest_demo_synthetic.py [report_path]

The store is built in a temp directory from `tests/fixtures/series.py` and its manifest says
`synthetic: true`, so the report opens with the synthetic-data warning and `--record` would refuse.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest.data import HistoryStore
from app.backtest.experiment import ExperimentConfig, run_experiment
from app.backtest.report import render_report
from app.config import get_settings
from app.models import Security
from tests.fixtures.series import write_synthetic_store


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("backtest-demo-SYNTHETIC.md")
    syms = [f"S{i:02d}" for i in range(12)]
    secs = [
        Security(
            symbol=s,
            name_en=s,
            market="US",
            currency="USD" if i % 2 else "ILS",
            sector=["Tech", "Fin", "Health", "Energy"][i % 4],
            country=["US", "Israel"][i % 2],
        )
        for i, s in enumerate(syms)
    ]
    root = Path(tempfile.mkdtemp(prefix="synthetic-history-"))
    write_synthetic_store(root, syms, n=1200, seed=11)
    store = HistoryStore(root)
    cfg = ExperimentConfig(
        profiles=("conservative", "balanced", "balanced_aggressive", "aggressive"),
        window_months=6,
        step_months=3,
        mode="random",
        runs=3,
        seed=1,
        train_until=pd.Timestamp("2022-03-31"),
    )
    s = get_settings()
    res = run_experiment(store, secs, cfg, s, progress=lambda n, t: print(f"{n}/{t}", end="\r"))
    out.write_text(render_report(res, store, s, day=pd.Timestamp.today().date()), "utf-8")
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main()
