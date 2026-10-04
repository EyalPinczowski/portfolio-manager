"""History store: round trip, strict as-of slicing, provenance, and the look-ahead guard."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.backtest.data import HistoryStore, fetch_history
from tests.fixtures.series import gbm_ohlcv, write_synthetic_store


def test_round_trip_and_symbol_names(tmp_path: Path) -> None:
    st = HistoryStore(tmp_path)
    df = gbm_ohlcv(1, 50)
    for sym in ("AAPL", "^GSPC", "ILS=X", "TEVA.TA", "BRK-B"):
        assert st.save_history(sym, df) == 50
    assert st.symbols() == sorted(["AAPL", "^GSPC", "ILS=X", "TEVA.TA", "BRK-B"])
    back = HistoryStore(tmp_path).load_history("^GSPC")
    assert back is not None
    pd.testing.assert_frame_equal(back, df.rename_axis("Date"), check_freq=False, atol=1e-6)
    assert HistoryStore(tmp_path).load_history("MISSING") is None
    assert HistoryStore(tmp_path).asof("MISSING", "2020-01-01") is None


def test_asof_returns_only_rows_up_to_the_day(tmp_path: Path) -> None:
    st = HistoryStore(tmp_path)
    df = gbm_ohlcv(2, 100, start="2024-01-01")
    st.save_history("X", df)
    day = df.index[40]
    got = st.asof("X", day)
    assert got is not None
    assert got.index.max() == day and len(got) == 41
    # a calendar day between two sessions (weekend) still stops at the last session before it
    sat = pd.Timestamp("2024-01-06")
    got = st.asof("X", sat)
    assert got is not None and got.index.max() == pd.Timestamp("2024-01-05")
    # a day before the data starts: nothing, not the first row
    assert st.asof("X", "2023-12-31") is None
    # lookback keeps only the last N calendar days
    got = st.asof("X", day, lookback_days=30)
    assert got is not None and got.index.min() > day - pd.Timedelta(days=30)


def test_asof_returns_a_copy(tmp_path: Path) -> None:
    st = HistoryStore(tmp_path)
    st.save_history("X", gbm_ohlcv(3, 30))
    a = st.asof("X", "2030-01-01")
    assert a is not None
    a["Close"] = -1.0
    b = st.asof("X", "2030-01-01")
    assert b is not None and (b["Close"] > 0).all()


def test_altering_future_rows_never_changes_an_earlier_view(tmp_path: Path) -> None:
    clean, dirty = tmp_path / "clean", tmp_path / "dirty"
    df = gbm_ohlcv(4, 300, start="2022-01-03")
    cut = df.index[199]
    HistoryStore(clean).save_history("X", df)
    tampered = df.copy()
    tampered.loc[tampered.index > cut, ["Open", "High", "Low", "Close"]] *= 7.0
    HistoryStore(dirty).save_history("X", tampered)
    a = HistoryStore(clean).asof("X", cut)
    b = HistoryStore(dirty).asof("X", cut)
    assert a is not None and b is not None
    pd.testing.assert_frame_equal(a, b)


def test_dirty_input_is_normalised(tmp_path: Path) -> None:
    st = HistoryStore(tmp_path)
    idx = pd.DatetimeIndex(["2024-01-03", "2024-01-02", "2024-01-02", "2024-01-04"], tz="UTC")
    df = pd.DataFrame({"Close": [11.0, 10.0, 10.5, np.nan]}, index=idx)
    assert st.save_history("X", df) == 2
    got = st.load_history("X")
    assert got is not None
    assert list(got.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert got["Close"].tolist() == [10.5, 11.0]  # last duplicate wins
    assert got["High"].tolist() == [10.5, 11.0]  # missing OHL falls back to the close


def test_manifest_provenance(tmp_path: Path) -> None:
    st = HistoryStore(tmp_path)
    assert not st.provenance_known and not st.is_synthetic
    write_synthetic_store(tmp_path, ["A", "B"], n=40)
    st = HistoryStore(tmp_path)
    assert st.provenance_known and st.is_synthetic
    assert {"A", "B", "^GSPC", "ILS=X"} <= set(st.symbols())


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        self.calls.append((symbol, days))
        return None if symbol == "NONE" else gbm_ohlcv(5, 20)


def test_fetch_history_uses_the_provider_interface(tmp_path: Path) -> None:
    prov, st = _FakeProvider(), HistoryStore(tmp_path)
    got = fetch_history(["AAA", "NONE"], 8, prov, st, pause_seconds=0)
    assert got == {"AAA": 20, "NONE": 0}
    assert prov.calls[0][1] >= 8 * 365
    assert st.provenance_known and not st.is_synthetic
    assert st.manifest()["symbols"] == ["AAA"]
