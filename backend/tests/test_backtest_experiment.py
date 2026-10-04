"""Experiment runner, report, `--record` guards and the CLI. Synthetic data only: the numbers
in these tests say nothing about how the strategy does on real markets."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest
from sqlmodel import Session, select

from app.backtest.commands import maybe_record
from app.backtest.data import HistoryStore
from app.backtest.experiment import (
    ExperimentConfig,
    ExperimentResult,
    RunRecord,
    heldout_passes,
    is_success,
    make_windows,
    run_experiment,
    summarise,
    wilson,
)
from app.backtest.report import SYNTHETIC_BANNER, render_report
from app.backtest.simulator import RunResult
from app.cli import main
from app.config import BacktestTarget, Settings, get_settings
from app.models import BacktestRun, Security
from tests.fixtures.series import write_synthetic_store

SYMS = [f"S{i:02d}" for i in range(6)]


def _secs() -> list[Security]:
    return [
        Security(
            symbol=s,
            name_en=s,
            market="US",
            currency="USD" if i % 2 else "ILS",
            sector=["Tech", "Fin", "Health"][i % 3],
            country=["US", "Israel"][i % 2],
        )
        for i, s in enumerate(SYMS)
    ]


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {"backtest_rebalance_every_days": 10, "env": "dev"}
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    write_synthetic_store(tmp_path / "h", SYMS, n=800, seed=2)
    return HistoryStore(tmp_path / "h")


def _cfg(**kw: object) -> ExperimentConfig:
    base: dict[str, object] = {
        "profiles": ("conservative", "aggressive"),
        "window_months": 3,
        "step_months": 3,
        "mode": "random",
        "runs": 2,
        "seed": 7,
        "train_until": pd.Timestamp("2021-06-30"),
    }
    base.update(kw)
    return ExperimentConfig(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- pure helpers
def test_windows_stay_inside_the_range() -> None:
    w = make_windows(pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01"), 6, 1)
    assert w[0] == (pd.Timestamp("2020-01-01"), pd.Timestamp("2020-07-01"))
    assert w[-1][1] <= pd.Timestamp("2021-01-01") and len(w) == 7
    assert make_windows(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-03-01"), 6, 1) == []


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    lo, hi = wilson(8, 10) or (0, 0)
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.96
    assert wilson(10, 10) == pytest.approx((0.722, 1.0), abs=0.01)


def _res(ret: float, dd: float, excess: float) -> RunResult:
    t = pd.Timestamp("2024-01-01")
    return RunResult("balanced", "top", t, t, 1.0, 1.0, ret, dd, "^GSPC", ret - excess, excess)


def test_success_needs_all_three_conditions() -> None:
    tgt = BacktestTarget(min_return_pct=5.0, max_drawdown_pct=10.0)
    assert is_success(_res(6.0, 9.0, 1.0), tgt)
    assert not is_success(_res(4.9, 9.0, 1.0), tgt)  # return too low
    assert not is_success(_res(6.0, 9.0, 0.0), tgt)  # does not beat the benchmark
    assert not is_success(_res(6.0, 10.5, 1.0), tgt)  # drawdown above the cap
    assert is_success(_res(5.0, 10.0, 0.01), tgt)  # the bounds themselves pass


def test_proposed_targets_are_in_config_and_unapproved() -> None:
    s = _settings()
    assert (
        s.backtest_targets["balanced"].min_return_pct,
        s.backtest_targets["balanced"].max_drawdown_pct,
    ) == (5.0, 7.0)
    assert s.backtest_targets["aggressive"].max_drawdown_pct == 15.0
    assert s.backtest_targets_approved is False and s.backtest_success_threshold == 0.8


# ---------------------------------------------------------------- end to end on synthetic data
def test_experiment_splits_by_date_and_is_reproducible(store: HistoryStore) -> None:
    s = _settings()
    a = run_experiment(store, _secs(), _cfg(), s)
    b = run_experiment(store, _secs(), _cfg(), s)
    assert a.records == b.records and a.records
    cut = pd.Timestamp("2021-06-30")
    for r in a.records:
        assert (r.window_end <= cut) if r.split == "train" else (r.window_start > cut)
    assert {r.split for r in a.records} == {"train", "heldout"}
    assert a.dropped_straddling >= 0
    per = {(r.preset, r.split) for r in a.records}
    assert len(per) == 4
    sm = a.summary("aggressive", "heldout")
    assert sm.n_runs == sm.n_windows * 2 and sm.rate is not None
    assert sm.independent_windows == pytest.approx(sm.n_windows)  # step == window: no overlap


def test_top_mode_is_one_deterministic_run_per_window(store: HistoryStore) -> None:
    res = run_experiment(store, _secs(), _cfg(mode="top", runs=9), _settings())
    assert {r.run for r in res.records} == {0}


def test_workers_give_the_same_records(store: HistoryStore) -> None:
    s = _settings()
    cfg = _cfg(profiles=("balanced",), runs=1)
    one = run_experiment(store, _secs(), cfg, s, workers=1)
    two = run_experiment(store, _secs(), cfg, s, workers=2)
    assert sorted(one.records, key=lambda r: (r.window_start, r.run)) == sorted(
        two.records, key=lambda r: (r.window_start, r.run)
    )


def test_automatic_train_split_is_flagged(store: HistoryStore) -> None:
    res = run_experiment(
        store, _secs(), _cfg(train_until=None, profiles=("balanced",), runs=1), _settings()
    )
    assert res.train_until_automatic and {r.split for r in res.records} == {"train", "heldout"}


def test_unknown_profile_and_short_history(store: HistoryStore, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no backtest target"):
        run_experiment(store, _secs(), _cfg(profiles=("nope",)), _settings())
    short = HistoryStore(tmp_path / "short")
    write_synthetic_store(short.root, SYMS, n=100)
    with pytest.raises(ValueError, match="too short"):
        run_experiment(short, _secs(), _cfg(), _settings())


def test_report_carries_the_headline_and_every_caveat(store: HistoryStore) -> None:
    s = _settings()
    res = run_experiment(store, _secs(), _cfg(profiles=("balanced",), runs=1), s)
    text = render_report(res, store, s, day=pd.Timestamp("2026-10-04").date())
    assert SYNTHETIC_BANNER in text
    assert "NOT EVIDENCE OF REAL PERFORMANCE" in text
    assert text.index("Headline: held-out") < text.index("Train windows")
    for needle in (
        "Only the technical and patterns signals",
        "Survivorship bias",
        "Synthetic data proves nothing",
        "held-out figure is the headline",
        "PROPOSALS",
        "Approved by the user: **NO**",
    ):
        assert needle in text


def test_report_without_a_manifest_says_provenance_unknown(tmp_path: Path) -> None:
    st = HistoryStore(tmp_path / "x")
    write_synthetic_store(st.root, SYMS, n=800)
    (st.root / "manifest.json").unlink()
    s = _settings()
    res = run_experiment(st, _secs(), _cfg(profiles=("balanced",), runs=1), s)
    assert "Data provenance unknown" in render_report(
        res, st, s, day=pd.Timestamp("2026-10-04").date()
    )


# ---------------------------------------------------------------- --record guards
def _fake_result(rate_by_profile: dict[str, float]) -> ExperimentResult:
    cfg = _cfg(profiles=tuple(rate_by_profile), runs=10)
    res = ExperimentResult(
        config=cfg,
        train_until=pd.Timestamp("2021-06-30"),
        train_until_automatic=False,
        first_start=pd.Timestamp("2020-01-01"),
        last_end=pd.Timestamp("2022-01-01"),
    )
    for p, rate in rate_by_profile.items():
        for k in range(10):
            res.records.append(
                RunRecord(p, "heldout", pd.Timestamp("2021-07-01"), pd.Timestamp("2021-10-01"),
                          k, 5.0, 3.0, 1.0, 4.0, 4, 1, 2, success=k < rate * 10)
            )  # fmt: skip
    res.summaries = summarise(res, _settings())
    return res


def _real_store(tmp_path: Path) -> HistoryStore:
    st = HistoryStore(tmp_path / "real")
    st.write_manifest(synthetic=False, source="test")
    return st


def _runs(db: Session) -> list[BacktestRun]:
    return list(db.exec(select(BacktestRun)).all())


def test_record_needs_every_guard(db: Session, tmp_path: Path) -> None:
    good = _fake_result({"conservative": 0.9, "balanced": 0.8})
    ok_s = _settings(backtest_targets_approved=True)
    # synthetic data
    syn = HistoryStore(tmp_path / "syn")
    syn.write_manifest(synthetic=True)
    assert "synthetic" in maybe_record(db, good, syn, ok_s)
    # unknown provenance
    assert "no manifest" in maybe_record(db, good, HistoryStore(tmp_path / "none"), ok_s)
    # targets not approved
    assert "not approved" in maybe_record(db, good, _real_store(tmp_path), _settings())
    # one profile below the 0.8 threshold
    weak = _fake_result({"conservative": 0.9, "balanced": 0.7})
    assert "below 80%" in maybe_record(db, weak, _real_store(tmp_path), ok_s)
    # no --record: no database at all
    assert "--record not given" in maybe_record(None, good, _real_store(tmp_path), ok_s)
    assert _runs(db) == []


def test_record_writes_a_pass_when_everything_holds(db: Session, tmp_path: Path) -> None:
    good = _fake_result({"conservative": 0.9, "balanced": 0.8})
    ok_s = _settings(backtest_targets_approved=True)
    note = maybe_record(db, good, _real_store(tmp_path), ok_s)
    assert "recorded as PASSED" in note
    (row,) = _runs(db)
    assert row.passed and row.metrics["heldout"]["balanced"]["rate"] == 0.8
    assert (row.period_start.isoformat(), row.period_end.isoformat()) == (
        "2021-07-01",
        "2021-10-01",
    )
    assert heldout_passes(good, 0.8)


# ---------------------------------------------------------------- the CLI
def test_cli_backtest_writes_the_report_and_records_nothing_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = tmp_path / "hist"
    write_synthetic_store(hist, SYMS, n=800, seed=2)
    uni = tmp_path / "universe.txt"
    uni.write_text("\n".join(SYMS))
    seed = tmp_path / "seed.csv"
    with seed.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "symbol",
                "name_en",
                "name_he",
                "tase_number",
                "asset_type",
                "market",
                "currency",
                "sector",
                "country",
                "dual_listing_group",
            ]
        )
        for i, sym in enumerate(SYMS):
            w.writerow([sym, sym, "", "", "stock", "US", "USD", ["Tech", "Fin"][i % 2], "US", ""])  # fmt: skip
    monkeypatch.setenv("UNIVERSE_FILE", str(uni))
    monkeypatch.setenv("SEED_CSV_PATH", str(seed))
    monkeypatch.setenv("BACKTEST_REBALANCE_EVERY_DAYS", "10")
    get_settings.cache_clear()
    out = tmp_path / "report.md"
    rc = main(
        ["backtest", "--profiles", "balanced", "--window-months", "3", "--step-months", "3",
         "--mode", "random", "--runs", "1", "--seed", "3", "--train-until", "2021-06-30",
         "--history-dir", str(hist), "--out", str(out)]
    )  # fmt: skip
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "SYNTHETIC DATA" in text and "nothing was recorded" in text
    assert "report written" in capsys.readouterr().out
    get_settings.cache_clear()


def test_cli_backtest_without_history_says_how_to_fetch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["backtest", "--history-dir", str(tmp_path / "empty")]) == 1
    assert "fetch-history" in capsys.readouterr().err
