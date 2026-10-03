from __future__ import annotations

import pytest

from app.config import Settings
from app.scoring.combine import combine_signals
from app.scoring.risk import (
    PRESETS,
    Position,
    check_limits,
    compute_exposures,
    list_presets,
    merge_dual_listings,
    resolve_risk_filter,
)
from app.signals.base import Explanation, SignalResult
from app.timeutil import utcnow


def sig(name: str, score: float, conf: float) -> SignalResult:
    return SignalResult(
        name=name,
        score=score,
        confidence=conf,
        reasons=["r"],
        data_as_of=utcnow(),
        explanation=Explanation(summary="s"),
    )


def test_default_weights_match_the_decision() -> None:
    w = Settings().signal_weights
    assert w == {
        "technical": 25,
        "patterns": 10,
        "fundamentals": 20,
        "analysts": 20,
        "geo_news": 12.5,
        "sentiment": 12.5,
    }
    assert sum(w.values()) == 100


def test_phase1_redistributes_missing_signal_weights() -> None:
    res = combine_signals(
        {"technical": sig("technical", 50, 1.0), "patterns": sig("patterns", -20, 1.0)}
    )
    by = {b.name: b for b in res.breakdown}
    assert by["technical"].effective_weight == pytest.approx(25 / 35 * 100, abs=1e-2)
    assert by["patterns"].effective_weight == pytest.approx(10 / 35 * 100, abs=1e-2)
    for n in ("fundamentals", "analysts", "geo_news", "sentiment"):
        assert by[n].effective_weight == 0.0
        assert by[n].confidence == 0.0
    assert res.total == pytest.approx((50 * 25 - 20 * 10) / 35, abs=0.01)
    # only 35% of the nominal weight had data
    assert res.confidence == pytest.approx(0.35)
    assert res.available
    assert set(res.missing) == {"fundamentals", "analysts", "geo_news", "sentiment"}


def test_partial_confidence_scales_weight() -> None:
    res = combine_signals(
        {"technical": sig("technical", 100, 1.0), "patterns": sig("patterns", 0, 0.5)}
    )
    by = {b.name: b for b in res.breakdown}
    assert by["technical"].effective_weight == pytest.approx(25 / 30 * 100, abs=1e-2)
    assert res.total == pytest.approx(100 * 25 / 30, abs=0.01)


def test_all_missing_is_not_a_neutral_full_confidence_score() -> None:
    res = combine_signals({})
    assert res.available is False
    assert res.confidence == 0.0
    assert all(b.effective_weight == 0 for b in res.breakdown)


def test_weights_come_from_config() -> None:
    s = Settings(
        signal_weights={
            "technical": 1,
            "patterns": 1,
            "fundamentals": 0,
            "analysts": 0,
            "geo_news": 0,
            "sentiment": 0,
        }
    )
    res = combine_signals(
        {"technical": sig("technical", 40, 1), "patterns": sig("patterns", 0, 1)}, s
    )
    assert res.total == pytest.approx(20)
    assert res.confidence == pytest.approx(1.0)


# ------------------------------------------------------------------ risk
def test_six_presets_with_all_fields() -> None:
    assert list(PRESETS) == [
        "very_conservative", "conservative", "balanced", "balanced_aggressive", "aggressive", "very_aggressive",
    ]  # fmt: skip
    ps = list_presets()
    assert len(ps) == 6
    assert [p.max_position_pct for p in ps] == sorted(p.max_position_pct for p in ps)
    assert resolve_risk_filter(None).preset == "balanced_aggressive"
    for p in ps:
        assert p.stop_type in ("fixed", "trailing", "both")


def test_resolve_risk_filter_overrides_and_unknown_preset() -> None:
    rf = resolve_risk_filter({"preset": "conservative", "max_position_pct": 6})
    assert rf.max_position_pct == 6 and rf.max_sector_pct == PRESETS["conservative"].max_sector_pct
    assert resolve_risk_filter({"preset": "nope"}).preset == "balanced_aggressive"


def pos(
    sym: str,
    value: float,
    sector: str = "Technology",
    country: str = "United States",
    cur: str = "USD",
    group: str | None = None,
) -> Position:
    return Position(sym, sym, value, sector, country, cur, dual_group=group)


def test_exposures_and_home_bias() -> None:
    ps = [
        pos("A", 600),
        pos("B", 300, "Financials", "Israel", "ILS"),
        pos("C", 100, "Financials", "Israel", "ILS"),
    ]
    ex, total = compute_exposures(ps)
    assert total == 1000
    assert {i.name: i.weight_pct for i in ex.country} == {"United States": 60.0, "Israel": 40.0}
    assert {i.name: i.weight_pct for i in ex.currency} == {"USD": 60.0, "ILS": 40.0}
    assert ex.home_bias_pct == 40.0
    assert ex.concentration[0]["symbol"] == "A"


def test_breaches_have_why_text_and_respect_overrides() -> None:
    limits = resolve_risk_filter({"preset": "balanced"})  # 10 / 30 / 70
    ps = [
        pos("BIG", 700),
        pos("S", 200, "Financials", "Israel", "ILS"),
        pos("T", 100, "Financials", "Israel", "ILS"),
    ]
    breaches = check_limits(ps, limits)
    rules = {(b.rule, b.symbol) for b in breaches}
    assert ("max_position_pct", "BIG") in rules
    assert ("max_sector_pct", None) in rules  # Technology 70% > 30%
    assert ("max_country_pct", None) not in rules  # United States is exactly 70%, not above
    conservative = {
        b.rule for b in check_limits(ps, resolve_risk_filter({"preset": "conservative"}))
    }
    assert "max_country_pct" in conservative  # 70% > 65%
    for b in breaches:
        assert b.why and b.value > b.limit
    big = next(b for b in breaches if b.symbol == "BIG")
    assert "70.0%" in big.why and "10%" in big.why
    # per-holding override raises that holding's own cap
    ps2 = [
        Position("BIG", "BIG", 700, "Technology", "United States", "USD", max_position_pct=80),
        *ps[1:],
    ]
    assert not [
        b for b in check_limits(ps2, limits) if b.rule == "max_position_pct" and b.symbol == "BIG"
    ]


def test_diversified_etfs_do_not_trigger_sector_breach() -> None:
    ps = [pos("SPY", 900, "Diversified"), pos("X", 100, "Financials")]
    rules = [b.rule for b in check_limits(ps, resolve_risk_filter({"preset": "conservative"}))]
    assert "max_sector_pct" not in rules


def test_dual_listings_are_not_double_counted() -> None:
    ps = [
        pos("TEVA.TA", 50, "Healthcare", "Israel", "ILS", "TEVA"),
        pos("TEVA", 60, "Healthcare", "Israel", "USD", "TEVA"),
        pos("X", 890),
    ]
    merged = merge_dual_listings(ps)
    assert len(merged) == 2 and merged[0].value_ils == 110
    ex, _ = compute_exposures(ps)
    assert sum(1 for c in ex.concentration if "TEVA" in c["symbol"]) == 1
    # currency exposure still separates the two lines
    assert {i.name for i in ex.currency} == {"ILS", "USD"}
    # the original positions are not mutated
    assert ps[0].value_ils == 50
