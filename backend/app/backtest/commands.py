"""CLI implementations: `fetch-history` and `backtest`. Kept out of `app/cli.py` to keep that thin."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
from sqlmodel import Session

from app.backtest.data import HistoryStore, fetch_history
from app.backtest.experiment import (
    ExperimentConfig,
    ExperimentResult,
    heldout_passes,
    period_of,
    run_experiment,
    today,
)
from app.backtest.report import render_report
from app.config import Settings
from app.launchgate import weights_fingerprint
from app.models import Security
from app.papertrading import record_backtest
from app.scoring.universe import load_universe
from app.securities import read_seed_securities

DOCS_REVIEWS = Path(__file__).resolve().parents[3] / "docs" / "reviews"


def universe_securities_offline(s: Settings) -> list[Security]:
    """Universe symbols that are in the seed CSV, with their sector / country / currency (no DB)."""
    wanted = set(load_universe(s))
    return [x for x in read_seed_securities(settings=s) if x.symbol in wanted]


def history_symbols(s: Settings, symbols_from: str | None, explicit: str | None) -> list[str]:
    """What to download: the universe (or explicit symbols) plus the benchmarks and the FX series."""
    base = (
        [x.strip().upper() for x in explicit.split(",") if x.strip()]
        if explicit
        else load_universe(s)
        if symbols_from == "universe"
        else []
    )
    extra = [s.benchmark_sp500, s.benchmark_ta125, s.backtest_fx_series_symbol]
    return list(dict.fromkeys([*base, *extra]))


def run_fetch_history(
    s: Settings, symbols_from: str | None, explicit: str | None, years: int, history_dir: str | None
) -> int:
    """Download once, on a machine with network access. Never called from tests."""
    from app.providers.yfinance_provider import YFinanceProvider

    syms = history_symbols(s, symbols_from, explicit)
    if not syms:
        print("error: give --symbols-from universe or --symbols A,B", file=sys.stderr)
        return 1
    store = HistoryStore(history_dir, s)
    print(f"fetching {len(syms)} symbols, {years} years, into {store.root}")
    got = fetch_history(syms, years, YFinanceProvider(s), store)
    empty = [k for k, n in got.items() if n == 0]
    print(f"stored {len(got) - len(empty)} symbols; nothing for: {', '.join(empty) or 'none'}")
    return 0 if len(empty) < len(got) else 1


def maybe_record(
    db: Session | None, result: ExperimentResult, store: HistoryStore, s: Settings
) -> str:
    """Write the backtest to the launch gate only when every guard holds. Returns the sentence
    for the report: what was done, or why nothing was."""
    if db is None:
        return "nothing was recorded (--record not given)."
    refusals: list[str] = []
    if store.is_synthetic:
        refusals.append("the data is synthetic")
    elif not store.provenance_known:
        refusals.append("the data has no manifest, so its origin is unknown")
    if not s.backtest_targets_approved:
        refusals.append("the proposed targets are not approved (BACKTEST_TARGETS_APPROVED)")
    if not heldout_passes(result, s.backtest_success_threshold):
        refusals.append(
            f"held-out success is below {s.backtest_success_threshold:.0%} for at least one profile"
        )
    if refusals:
        return "NOT recorded: " + "; ".join(refusals) + "."
    span = period_of(result, "heldout")
    assert span is not None  # heldout_passes needs held-out runs for every profile
    metrics: dict[str, Any] = {
        "kind": "walk_forward_technical_patterns_only",
        "mode": result.config.mode,
        "runs_per_window": result.config.runs,
        "seed": result.config.seed,
        "train_until": str(result.train_until.date()),
        "threshold": s.backtest_success_threshold,
        "heldout": {
            p: {
                "rate": result.summary(p, "heldout").rate,
                "runs": result.summary(p, "heldout").n_runs,
                "independent_windows": result.summary(p, "heldout").independent_windows,
            }
            for p in result.config.profiles
        },
    }
    record_backtest(
        db,
        weights_hash=weights_fingerprint(s.signal_weights),
        period_start=span[0],
        period_end=span[1],
        metrics=metrics,
        passed=True,
    )
    return "recorded as PASSED for the active weights configuration (technical + patterns only tested)."


def run_backtest(
    s: Settings,
    *,
    profiles: str,
    window_months: int,
    step_months: int,
    mode: str,
    runs: int,
    seed: int,
    train_until: str | None,
    history_dir: str | None,
    out: str | None,
    workers: int,
    record: bool,
    benchmark: str | None,
    db: Session | None = None,
) -> int:
    store = HistoryStore(history_dir, s)
    if not store.symbols():
        print(
            f"error: no history in {store.root}. Run `python -m app.cli fetch-history "
            "--symbols-from universe --years 8` on a machine with network access first.",
            file=sys.stderr,
        )
        return 1
    secs = universe_securities_offline(s)
    cfg = ExperimentConfig(
        profiles=tuple(x.strip() for x in profiles.split(",") if x.strip()),
        window_months=window_months,
        step_months=step_months,
        mode=mode,  # type: ignore[arg-type]
        runs=runs,
        seed=seed,
        train_until=pd.Timestamp(train_until) if train_until else None,
        benchmark=benchmark,
    )

    def progress(n: int, total: int) -> None:
        if n == total or n % 10 == 0:
            print(f"  {n}/{total} runs", file=sys.stderr)

    try:
        result = run_experiment(store, secs, cfg, s, workers=workers, progress=progress)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    note = maybe_record(db if record else None, result, store, s)
    day = today()
    text = render_report(result, store, s, day=day, recorded=note)
    path = Path(out) if out else DOCS_REVIEWS / f"backtest-{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"report written to {path}")
    print(f"launch gate: {note}")
    return 0
