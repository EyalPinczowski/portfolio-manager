"""Compare `technical_mode` classic vs vol_normalized on the stored history (offline).

Run: `cd backend && python -m app.backtest.compare_signals [--profiles balanced] [--mode top]
[--history-dir DIR] [--out PATH]`. Never records to the launch gate and never touches the trial
log. The analyst "revisions" mode needs live Finnhub/yfinance data and has no history, so it
cannot be backtested here (stated in the report). Research only: the switches stay OFF.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.backtest.commands import DOCS_REVIEWS, universe_securities_offline
from app.backtest.data import HistoryStore
from app.backtest.experiment import ExperimentConfig, Summary, run_experiment, summarise, today
from app.config import Settings, get_settings

MODES = ("classic", "vol_normalized")


def _f(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_comparison(results: dict[str, dict[tuple[str, str], Summary]], meta: str) -> str:
    lines = [
        "# Signals comparison: classic vs vol-normalised technical",
        "",
        meta,
        "",
        "Research only. Both modes use the same weights and risk rules; "
        "`technical_mode` stays `classic` by default and no gate was recorded.",
        "",
        "| Profile | Split | Mode | Runs | Success | Mean return % | Mean excess % | Mean max DD % |",
        "|---|---|---|---|---|---|---|---|",
    ]
    profiles = sorted({k[0] for k in next(iter(results.values()))})
    for p in profiles:
        for split in ("train", "heldout"):
            for mode in MODES:
                sm = results[mode].get((p, split))
                if sm is None or sm.n_runs == 0:
                    continue
                rate = "n/a" if sm.rate is None else f"{sm.rate * 100:.0f}%"
                lines.append(
                    f"| {p} | {split} | {mode} | {sm.n_runs} | {rate} | "
                    f"{_f(sm.mean_return_pct)} | {_f(sm.mean_excess_pct)} | {_f(sm.mean_drawdown_pct)} |"
                )
    lines += [
        "",
        "Notes: the universe is today's names (survivorship bias); few independent windows "
        "mean wide uncertainty; the analyst revisions variant has no offline history and is "
        "not covered. Not financial advice.",
        "",
    ]
    return "\n".join(lines)


def run(
    s: Settings, profiles: str, mode: str, history_dir: str | None, out: str | None, workers: int
) -> int:
    store = HistoryStore(history_dir, s)
    if not store.symbols():
        print(f"error: no history in {store.root}; run `fetch-history` first.", file=sys.stderr)
        return 1
    secs = universe_securities_offline(s)
    cfg = ExperimentConfig(
        profiles=tuple(x.strip() for x in profiles.split(",") if x.strip()),
        window_months=s.backtest_window_months,
        step_months=s.backtest_step_months,
        mode=mode,  # type: ignore[arg-type]
        runs=3,
        seed=1,
        train_until=None,
        benchmark=None,
    )
    results: dict[str, dict[tuple[str, str], Summary]] = {}
    for m in MODES:
        variant = s.model_copy(update={"technical_mode": m})
        res = run_experiment(store, secs, cfg, variant, workers=workers, trials=None)
        results[m] = dict(summarise(res, variant))
        print(f"{m}: {len(res.records)} runs", file=sys.stderr)
    meta = (
        f"Date {today().isoformat()}; {len(store.symbols())} symbols in `{store.root.name}`; "
        f"mode `{mode}`; profiles {profiles}; "
        f"history provenance: {'synthetic' if store.is_synthetic else 'real'}."
    )
    path = Path(out) if out else DOCS_REVIEWS / "signals-comparison-2026-10-05.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_comparison(results, meta), encoding="utf-8")
    print(f"comparison written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profiles", default="balanced")
    ap.add_argument("--mode", choices=["random", "top"], default="top")
    ap.add_argument("--history-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    return run(get_settings(), a.profiles, a.mode, a.history_dir, a.out, a.workers)


if __name__ == "__main__":
    raise SystemExit(main())
