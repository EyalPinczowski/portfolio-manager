"""Many rolling windows x risk presets x seeded runs, split by date into train and held-out.

Success of one run = return >= the preset's target AND excess over the benchmark > 0 AND max
drawdown <= the preset's cap (`Settings.backtest_targets`, PROPOSALS awaiting user approval).
The held-out success rate (windows that start after `train_until`) is the headline number; the
train rate is shown only so overfitting is visible. Windows that straddle the split are dropped.

Windows overlap (a 6-month window every month), so their results are not independent. The report
says how many independent windows that amounts to.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from app.backtest.data import HistoryStore, as_timestamp
from app.backtest.screen import ScoreCache
from app.backtest.simulator import PickMode, RunResult, simulate
from app.config import BacktestTarget, Settings, get_settings
from app.models import Security

Split = str  # "train" | "heldout"


@dataclass(frozen=True)
class ExperimentConfig:
    profiles: tuple[str, ...]
    window_months: int
    step_months: int
    mode: PickMode
    runs: int
    seed: int
    train_until: pd.Timestamp | None = None  # None: `Settings.backtest_default_train_fraction`
    benchmark: str | None = None


@dataclass
class RunRecord:
    preset: str
    split: Split
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    run: int
    return_pct: float
    max_drawdown_pct: float
    benchmark_return_pct: float
    excess_pct: float
    n_trades: int
    n_stop_outs: int
    n_wins: int
    success: bool


@dataclass
class Summary:
    preset: str
    split: Split
    n_runs: int = 0
    n_windows: int = 0
    successes: int = 0
    rate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    mean_return_pct: float | None = None
    mean_excess_pct: float | None = None
    mean_drawdown_pct: float | None = None
    mean_trades: float | None = None
    stop_out_share: float | None = None
    hit_rate: float | None = None
    independent_windows: float = 0.0


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    train_until: pd.Timestamp
    train_until_automatic: bool
    first_start: pd.Timestamp
    last_end: pd.Timestamp
    records: list[RunRecord] = field(default_factory=list)
    dropped_straddling: int = 0
    n_symbols: int = 0
    summaries: dict[tuple[str, Split], Summary] = field(default_factory=dict)

    def summary(self, preset: str, split: Split) -> Summary:
        return self.summaries[(preset, split)]


# ---------------------------------------------------------------- statistics
def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for a success rate. None without runs."""
    if n <= 0:
        return None
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def is_success(r: RunResult, target: BacktestTarget) -> bool:
    return (
        r.return_pct >= target.min_return_pct
        and r.excess_pct > 0
        and r.max_drawdown_pct <= target.max_drawdown_pct
    )


# ---------------------------------------------------------------- windows
def make_windows(
    first: pd.Timestamp, last: pd.Timestamp, window_months: int, step_months: int
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """(start, end) calendar pairs, the first starting at `first`, every end on or before `last`."""
    out: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    k = 0
    while True:
        start = first + pd.DateOffset(months=k * step_months)
        end = start + pd.DateOffset(months=window_months)
        if end > last:
            return out
        out.append((pd.Timestamp(start), pd.Timestamp(end)))
        k += 1


def _seed_for(seed: int, preset: str, w: int, run: int) -> list[int]:
    return [seed, zlib.crc32(preset.encode()), w, run]


# ---------------------------------------------------------------- running
@dataclass(frozen=True)
class _Task:
    preset: str
    split: Split
    w_index: int
    start: pd.Timestamp
    end: pd.Timestamp
    run: int


_WORKER: dict[str, object] = {}


def _init_worker(
    root: str, secs: list[Security], s: Settings, cfg: ExperimentConfig
) -> None:  # runs once per process
    _WORKER.update(store=HistoryStore(root), secs=secs, s=s, cfg=cfg, cache={})


def _run_task(task: _Task) -> RunRecord:
    store: HistoryStore = _WORKER["store"]  # type: ignore[assignment]
    secs: list[Security] = _WORKER["secs"]  # type: ignore[assignment]
    s: Settings = _WORKER["s"]  # type: ignore[assignment]
    cfg: ExperimentConfig = _WORKER["cfg"]  # type: ignore[assignment]
    cache: ScoreCache = _WORKER["cache"]  # type: ignore[assignment]
    rng = (
        np.random.default_rng(_seed_for(cfg.seed, task.preset, task.w_index, task.run))
        if cfg.mode == "random"
        else None
    )
    r = simulate(
        store,
        secs,
        task.preset,
        task.start,
        task.end,
        mode=cfg.mode,
        rng=rng,
        settings=s,
        benchmark=cfg.benchmark,
        score_cache=cache,
    )
    target = s.backtest_targets[task.preset]
    return RunRecord(
        preset=task.preset,
        split=task.split,
        window_start=r.start,
        window_end=r.end,
        run=task.run,
        return_pct=r.return_pct,
        max_drawdown_pct=r.max_drawdown_pct,
        benchmark_return_pct=r.benchmark_return_pct,
        excess_pct=r.excess_pct,
        n_trades=r.n_trades,
        n_stop_outs=r.n_stop_outs,
        n_wins=sum(t.pnl_ils > 0 for t in r.trades),
        success=is_success(r, target),
    )


def run_experiment(
    store: HistoryStore,
    securities: list[Security],
    cfg: ExperimentConfig,
    settings: Settings | None = None,
    *,
    workers: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> ExperimentResult:
    s = settings or get_settings()
    unknown = [p for p in cfg.profiles if p not in s.backtest_targets]
    if unknown:
        raise ValueError(f"no backtest target configured for: {', '.join(unknown)}")
    bench = cfg.benchmark or s.backtest_benchmark
    bdf = store.load_history(bench)
    if bdf is None:
        raise ValueError(f"benchmark {bench} is not in the history store")
    # leave `history_days` of history before the first window so the first screening has bars
    first = as_timestamp(bdf.index[0]) + pd.Timedelta(days=s.history_days)
    last = as_timestamp(bdf.index[-1])
    windows = make_windows(first, last, cfg.window_months, cfg.step_months)
    if not windows:
        raise ValueError("the stored history is too short for even one window after the warm-up")
    automatic = cfg.train_until is None
    train_until = (
        windows[0][0] + (windows[-1][1] - windows[0][0]) * s.backtest_default_train_fraction
        if cfg.train_until is None
        else as_timestamp(cfg.train_until)
    )
    train_until = pd.Timestamp(train_until).normalize()
    runs = cfg.runs if cfg.mode == "random" else 1  # `top` is deterministic

    tasks: list[_Task] = []
    dropped = 0
    for i, (a, b) in enumerate(windows):
        if b <= train_until:
            split = "train"
        elif a > train_until:
            split = "heldout"
        else:
            dropped += 1
            continue
        for preset in cfg.profiles:
            tasks.extend(_Task(preset, split, i, a, b, k) for k in range(runs))

    present = [x for x in securities if store.has(x.symbol)]
    result = ExperimentResult(
        config=cfg,
        train_until=train_until,
        train_until_automatic=automatic,
        first_start=windows[0][0],
        last_end=windows[-1][1],
        dropped_straddling=dropped,
        n_symbols=len(present),
    )
    if workers > 1:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(str(store.root), present, s, cfg),
        ) as pool:
            for n, rec in enumerate(pool.map(_run_task, tasks, chunksize=1), 1):
                result.records.append(rec)
                if progress:
                    progress(n, len(tasks))
    else:
        _init_worker(str(store.root), present, s, cfg)
        for n, task in enumerate(tasks, 1):
            result.records.append(_run_task(task))
            if progress:
                progress(n, len(tasks))
    result.summaries = summarise(result, s)
    return result


def summarise(result: ExperimentResult, s: Settings) -> dict[tuple[str, Split], Summary]:
    out: dict[tuple[str, Split], Summary] = {}
    step_over_window = result.config.step_months / result.config.window_months
    for preset in result.config.profiles:
        for split in ("train", "heldout"):
            recs = [r for r in result.records if r.preset == preset and r.split == split]
            sm = Summary(preset=preset, split=split, n_runs=len(recs))
            if recs:
                n_w = len({r.window_start for r in recs})
                sm.n_windows = n_w
                sm.successes = sum(r.success for r in recs)
                sm.rate = sm.successes / len(recs)
                ci = wilson(sm.successes, len(recs))
                sm.ci_low, sm.ci_high = ci if ci else (None, None)
                sm.mean_return_pct = float(np.mean([r.return_pct for r in recs]))
                sm.mean_excess_pct = float(np.mean([r.excess_pct for r in recs]))
                sm.mean_drawdown_pct = float(np.mean([r.max_drawdown_pct for r in recs]))
                sm.mean_trades = float(np.mean([r.n_trades for r in recs]))
                trades = sum(r.n_trades for r in recs)
                sm.stop_out_share = sum(r.n_stop_outs for r in recs) / trades if trades else None
                sm.hit_rate = sum(r.n_wins for r in recs) / trades if trades else None
                sm.independent_windows = max(1.0, n_w * min(1.0, step_over_window))
            out[(preset, split)] = sm
    return out


def heldout_passes(result: ExperimentResult, threshold: float) -> bool:
    """Every profile has held-out runs and a held-out success rate at or above the threshold."""
    for preset in result.config.profiles:
        sm = result.summary(preset, "heldout")
        if sm.rate is None or sm.rate < threshold:
            return False
    return True


def period_of(result: ExperimentResult, split: Split) -> tuple[date, date] | None:
    recs = [r for r in result.records if r.split == split]
    if not recs:
        return None
    return (
        min(r.window_start for r in recs).date(),
        max(r.window_end for r in recs).date(),
    )


def today() -> date:
    return datetime.now().date()
