"""Walk-forward simulator: execution rules on hand-made bars, metrics, and the look-ahead guard.

All data here is synthetic or hand-built. Nothing in this file says anything about real markets.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import app.backtest.simulator as sim
from app.backtest.data import HistoryStore
from app.backtest.screen import BtCandidate
from app.backtest.simulator import max_drawdown_pct, simulate
from app.config import Settings
from app.models import Security
from tests.fixtures.series import write_synthetic_store

DAYS = pd.bdate_range("2024-01-01", periods=12)
SEC = Security(
    symbol="AAA", name_en="AAA", market="TASE", currency="ILS", sector="Tech", country="Israel"
)


def _cfg(**kw: object) -> Settings:
    base: dict[str, object] = {
        "backtest_commission_bps": 0.0,
        "backtest_slippage_bps": 0.0,
        "backtest_rebalance_every_days": 1000,  # only day 0 screens
        "backtest_capital_ils": 100_000.0,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """(open, high, low, close) per day from DAYS[0]; shorter than DAYS is padded flat."""
    rows = rows + [rows[-1]] * (len(DAYS) - len(rows))
    return pd.DataFrame(
        rows, columns=["Open", "High", "Low", "Close"], index=DAYS[: len(rows)]
    ).assign(Volume=1.0)


def _flat(px: float = 100.0) -> tuple[float, float, float, float]:
    return (px, px, px, px)


def _store(tmp_path: Path, rows: list[tuple[float, float, float, float]]) -> HistoryStore:
    st = HistoryStore(tmp_path)
    st.save_history("^GSPC", _bars([_flat(1000.0)] * 12))
    st.save_history("AAA", _bars(rows))
    st.write_manifest(synthetic=True)
    return st


Pick = Callable[..., None]


@pytest.fixture
def force_pick(monkeypatch: pytest.MonkeyPatch) -> Pick:
    """Replace the screener by one hand-made candidate on day 0, so the executor can be tested alone."""

    def make(stop: float = 95.0, tp: float = 130.0, qty: float = 10.0, sec: Security = SEC) -> None:
        cand = BtCandidate(
            sec=sec,
            day=DAYS[0],
            price=100.0,
            score=50.0,
            confidence=0.35,
            rank_score=17.5,
            quantity=qty,
            stop=stop,
            take_profit=tp,
            best_rr=2.0,
            stop_type="fixed",
        )
        monkeypatch.setattr(sim, "_screen", lambda *a, **k: [cand])
        monkeypatch.setattr(sim, "evaluate_asof", lambda *a, **k: cand)

    return make


def _go(tmp_path: Path, rows: list[tuple[float, float, float, float]], cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    st = _store(tmp_path, rows)
    return simulate(st, [SEC], "balanced", DAYS[0], DAYS[-1], mode="top", settings=cfg or _cfg())


def test_stop_fills_at_the_stop_price(tmp_path: Path, force_pick: Pick) -> None:
    force_pick()
    rows = [_flat(), _flat(), (100, 101, 94, 96), _flat(96)]
    r = _go(tmp_path, rows)
    (t,) = r.trades
    assert t.reason == "stop" and t.exit_price == 95.0 and t.entry_price == 100.0
    assert t.pnl_ils == pytest.approx(-50.0)
    assert r.n_stop_outs == 1 and r.hit_rate == 0.0
    assert r.end_equity == pytest.approx(100_000 - 50)


def test_gap_through_the_stop_fills_at_the_worse_open(tmp_path: Path, force_pick: Pick) -> None:
    force_pick()
    r = _go(tmp_path, [_flat(), _flat(), (90, 91, 88, 89)])
    (t,) = r.trades
    assert t.reason == "gap_stop" and t.exit_price == 90.0
    assert t.pnl_ils == pytest.approx(-100.0)


def test_take_profit_and_gap_up_take_profit(tmp_path: Path, force_pick: Pick) -> None:
    force_pick()
    (t,) = _go(tmp_path, [_flat(), _flat(), (101, 131, 100, 128)]).trades
    assert t.reason == "take_profit" and t.exit_price == 130.0
    (g,) = _go(tmp_path / "g", [_flat(), _flat(), (135, 136, 134, 135)]).trades
    assert g.reason == "take_profit" and g.exit_price == 135.0


def test_a_bar_touching_both_levels_counts_as_the_stop(tmp_path: Path, force_pick: Pick) -> None:
    force_pick()
    (t,) = _go(tmp_path, [_flat(), _flat(), (100, 131, 94, 110)]).trades
    assert t.reason == "stop"


def test_open_position_is_sold_at_the_last_close(tmp_path: Path, force_pick: Pick) -> None:
    force_pick()
    r = _go(tmp_path, [_flat(), _flat(), (100, 105, 99, 104)])
    (t,) = r.trades
    assert t.reason == "window_end" and t.exit_date == DAYS[-1] and t.exit_price == 104.0
    assert r.return_pct == pytest.approx(0.04)  # 10 shares x 4 on 100k


def test_costs_are_charged_on_both_sides(tmp_path: Path, force_pick: Pick) -> None:
    force_pick()
    cfg = _cfg(backtest_commission_bps=10.0, backtest_slippage_bps=5.0)
    r = _go(tmp_path, [_flat(), _flat(), (100, 101, 94, 96)], cfg)
    (t,) = r.trades
    assert t.entry_price == pytest.approx(100.05)
    assert t.cost_ils == pytest.approx(100.05 * 10 * 1.001)
    assert t.exit_price == pytest.approx(95 * 0.9995)
    assert t.proceeds_ils == pytest.approx(95 * 0.9995 * 10 * 0.999)
    free = _go(tmp_path / "free", [_flat(), _flat(), (100, 101, 94, 96)])
    assert r.end_equity < free.end_equity


def test_usd_position_uses_the_fx_series(tmp_path: Path, force_pick: Pick) -> None:
    usd = Security(symbol="AAA", name_en="A", market="US", currency="USD", sector="T", country="US")
    force_pick(sec=usd, qty=10.0)
    st = _store(tmp_path, [_flat(), _flat(), (100, 105, 99, 104)])
    fx = _bars([_flat(4.0)] * 6 + [_flat(5.0)] * 6)  # USD/ILS jumps from 4 to 5 mid-window
    st.save_history("ILS=X", fx)
    r = simulate(st, [usd], "balanced", DAYS[0], DAYS[-1], mode="top", settings=_cfg())
    (t,) = r.trades
    assert t.cost_ils == pytest.approx(100 * 10 * 4.0)
    assert t.proceeds_ils == pytest.approx(104 * 10 * 5.0)  # the FX move is part of the result


def test_no_picks_means_cash_and_a_negative_excess_when_the_benchmark_rises(
    tmp_path: Path,
) -> None:
    st = HistoryStore(tmp_path)
    st.save_history("^GSPC", _bars([_flat(1000.0)] * 6 + [_flat(1100.0)] * 6))
    r = simulate(st, [], "balanced", DAYS[0], DAYS[-1], settings=_cfg())
    assert r.n_trades == 0 and r.hit_rate is None
    assert r.return_pct == 0.0 and r.max_drawdown_pct == 0.0
    assert r.benchmark_return_pct == pytest.approx(10.0) and r.excess_pct == pytest.approx(-10.0)


def test_max_drawdown() -> None:
    assert max_drawdown_pct([100, 120, 90, 110]) == pytest.approx(25.0)
    assert max_drawdown_pct([100, 101, 102]) == 0.0


def test_missing_benchmark_and_bad_window(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="benchmark"):
        simulate(HistoryStore(tmp_path), [], "balanced", DAYS[0], DAYS[-1], settings=_cfg())
    st = HistoryStore(tmp_path)
    st.save_history("^GSPC", _bars([_flat(1000.0)] * 12))
    with pytest.raises(ValueError, match="two trading days"):
        simulate(st, [], "balanced", DAYS[0], DAYS[0], settings=_cfg())
    with pytest.raises(ValueError, match="seeded"):
        simulate(st, [], "balanced", DAYS[0], DAYS[-1], mode="random", settings=_cfg())


# ---------------------------------------------------------------- the real screener path
def _synthetic_world(root: Path, n: int = 700) -> tuple[HistoryStore, list[Security]]:
    syms = [f"S{i:02d}" for i in range(8)]
    write_synthetic_store(root, syms, n=n, seed=3)
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
    return HistoryStore(root), secs


def test_same_seed_same_result_and_different_seed_can_differ(tmp_path: Path) -> None:
    st, secs = _synthetic_world(tmp_path)
    kw: dict[str, object] = {"settings": _cfg(backtest_rebalance_every_days=10)}
    a = simulate(
        st,
        secs,
        "aggressive",
        "2021-03-01",
        "2021-06-01",
        mode="random",
        rng=np.random.default_rng(5),
        **kw,
    )  # type: ignore[arg-type]
    b = simulate(
        st,
        secs,
        "aggressive",
        "2021-03-01",
        "2021-06-01",
        mode="random",
        rng=np.random.default_rng(5),
        **kw,
    )  # type: ignore[arg-type]
    assert a.equity == b.equity and a.decisions == b.decisions
    assert a.decisions, "the synthetic world should produce at least one candidate"


def test_screener_picks_respect_the_preset_limits(tmp_path: Path) -> None:
    st, secs = _synthetic_world(tmp_path)
    cfg = _cfg(backtest_rebalance_every_days=10)
    r = simulate(st, secs, "conservative", "2021-03-01", "2021-09-01", mode="top", settings=cfg)
    assert r.decisions
    for d in r.decisions:
        assert d.stop > 0 and d.take_profit > d.stop
    # the first buy of a conservative profile never exceeds its 8% single-position limit
    first = r.trades[0]
    assert first.cost_ils <= 100_000 * 0.08 * 1.01


def test_future_rows_never_change_an_earlier_decision_or_equity_point(tmp_path: Path) -> None:
    clean, dirty = tmp_path / "clean", tmp_path / "dirty"
    st_a, secs = _synthetic_world(clean)
    _synthetic_world(dirty)
    cut = pd.Timestamp("2021-05-14")
    st_b = HistoryStore(dirty)
    for sym in [*(x.symbol for x in secs), "^GSPC", "ILS=X"]:
        df = st_b.load_history(sym)
        assert df is not None
        df.loc[df.index > cut, ["Open", "High", "Low", "Close"]] *= 0.2  # a crash after the cut
        st_b.save_history(sym, df)
    cfg = _cfg(backtest_rebalance_every_days=5)
    a = simulate(st_a, secs, "balanced_aggressive", "2021-03-01", "2021-09-01", settings=cfg)
    b = simulate(st_b, secs, "balanced_aggressive", "2021-03-01", "2021-09-01", settings=cfg)
    assert [d for d in a.decisions if d.day <= cut] == [d for d in b.decisions if d.day <= cut]
    assert [e for e in a.equity if e[0] <= cut] == [e for e in b.equity if e[0] <= cut]
    assert any(d.day <= cut for d in a.decisions)
    assert a.equity != b.equity  # the tampering is visible after the cut, so the test can fail
