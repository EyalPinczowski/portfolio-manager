"""Launch-gate metrics (2.0-F item 7): errors only inside the window, calls tied to the active
hashes, and the sell-side excess return."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.config import Settings
from app.launchgate import weights_fingerprint
from app.models import PaperCall
from app.papertrading import paper_metrics, resolve_call
from app.signals.base import Explanation
from app.timeutil import utcnow

S = Settings(_env_file=None)
WH = weights_fingerprint(S.signal_weights)


def put(db: Session, *, age_days: float, **kw: object) -> PaperCall:
    """Insert a call that was made `age_days` ago (tests only: the repository cannot backdate)."""
    args: dict[str, object] = {
        "symbol": "AAPL", "side": "buy", "horizon": "1m", "entry": 100.0, "targets": [110.0],
        "explanation": Explanation(summary="t").model_dump(mode="json"), "model_hash": "m1",
        "prompt_hash": "p1", "weights_hash": WH, "is_global": True,
        "created_at": utcnow() - timedelta(days=age_days),
    }  # fmt: skip
    args.update(kw)
    row = PaperCall(**args)  # type: ignore[arg-type]
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve(
    db: Session, call: PaperCall, price: float, bench: dict[str, float], days: float
) -> None:
    assert call.id is not None
    resolve_call(
        db, call.id, outcome="horizon_end", outcome_price=price, benchmark_returns=bench,
        resolved_at=call.created_at + timedelta(days=days),
    )  # fmt: skip


def test_an_old_error_outside_the_window_no_longer_blocks_the_gate_for_good(db: Session) -> None:
    old = put(db, age_days=120)
    assert old.id is not None
    resolve_call(db, old.id, outcome="error", outcome_price=None,
                 resolved_at=old.created_at + timedelta(days=1))  # fmt: skip
    assert paper_metrics(db, S).critical_errors == 0  # 119 days ago: outside the 4-week window


def test_an_error_inside_the_window_counts(db: Session) -> None:
    c = put(db, age_days=40)
    assert c.id is not None
    resolve_call(db, c.id, outcome="error", outcome_price=None,
                 resolved_at=utcnow() - timedelta(days=10))  # fmt: skip
    assert paper_metrics(db, S).critical_errors == 1


def test_the_window_comes_from_config(db: Session) -> None:
    c = put(db, age_days=60)
    assert c.id is not None
    resolve_call(db, c.id, outcome="error", outcome_price=None,
                 resolved_at=utcnow() - timedelta(days=30))  # fmt: skip
    assert paper_metrics(db, S).critical_errors == 0  # 4 weeks = 28 days
    wide = Settings(_env_file=None, launch_paper_min_weeks=6)
    assert paper_metrics(db, wide).critical_errors == 1


def test_calls_made_with_other_weights_do_not_count(db: Session) -> None:
    for _ in range(3):
        resolve(db, put(db, age_days=50, weights_hash="old-weights"), 103.0, {"^GSPC": 2.0}, 30)
    good = put(db, age_days=50)
    resolve(db, good, 103.0, {"^GSPC": 2.0, "^TA125.TA": 2.0}, 30)
    m = paper_metrics(db, S)
    assert m.resolved_calls_1m == 1 and m.calls_recorded == 1
    assert m.excess_return_pct["^GSPC"] == pytest.approx(1.0)


def test_other_weights_do_not_stretch_weeks_running(db: Session) -> None:
    put(db, age_days=200, weights_hash="old-weights")
    put(db, age_days=7)
    assert paper_metrics(db, S).weeks_running == pytest.approx(1.0, abs=0.01)


def test_model_hash_filter_applies_only_when_configured(db: Session) -> None:
    resolve(db, put(db, age_days=50, model_hash="gemini-a"), 103.0, {"^GSPC": 2.0}, 30)
    resolve(db, put(db, age_days=50, model_hash="gemini-b"), 103.0, {"^GSPC": 2.0}, 30)
    assert paper_metrics(db, S).resolved_calls_1m == 2
    only_a = Settings(_env_file=None, launch_paper_model_hash="gemini-a")
    assert paper_metrics(db, only_a).resolved_calls_1m == 1


@pytest.mark.parametrize(
    ("side", "asset_ret", "bench_ret", "excess"),
    [
        ("buy", 5.0, 2.0, 3.0),  # beat the benchmark by 3 points
        ("buy", -1.0, 2.0, -3.0),
        ("sell", -4.0, 2.0, 6.0),  # fell while the benchmark rose: the call was right by 6 points
        ("sell", 5.0, 2.0, -3.0),  # rose more than the benchmark: wrong by 3 points
        ("sell", -4.0, -4.0, 0.0),  # fell exactly with the market: no edge (old formula said +8)
        ("sell", 2.0, 2.0, 0.0),  # old formula said -4 for a call that merely matched the market
    ],
)
def test_sell_side_excess_return_is_relative_to_the_benchmark(
    db: Session, side: str, asset_ret: float, bench_ret: float, excess: float
) -> None:
    c = put(db, age_days=50, side=side)
    resolve(db, c, 100.0 * (1 + asset_ret / 100), {"^GSPC": bench_ret, "^TA125.TA": bench_ret}, 30)
    assert paper_metrics(db, S).excess_return_pct["^GSPC"] == pytest.approx(excess)
