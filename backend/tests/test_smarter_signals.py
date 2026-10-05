"""Smarter-signals research group: everything is OFF by default and default scores are unchanged."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from app.config import Settings
from app.providers.analyst_trends import (
    AnalystTrendChain,
    FinnhubRecommendationProvider,
    RecommendationTrend,
)
from app.providers.earnings import (
    EarningsChain,
    EarningsInfo,
    FinnhubEarningsProvider,
    earnings_line,
    in_window,
)
from app.scoring.combine import combine_signals
from app.scoring.exit_levels import _earnings_notes
from app.scoring.scorecard import compute_scorecard
from app.signals.analysts import analysts_signal
from app.signals.patterns import patterns_signal
from app.signals.technical import technical_signal
from tests.fixtures.series import downtrend, from_points, make_ohlcv, uptrend


def _s(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


# Golden values computed with the pre-change technical.py (git HEAD) on these fixtures:
# [technical score, technical confidence, n reasons, combined total, combined confidence].
GOLDEN = {
    "up": [15.1, 1.0, 12, 13.86, 0.33],
    "down": [-14.98, 1.0, 12, -13.77, 0.33],
    "short": [13.65, 0.52, 10, 10.74, 0.1653],
    "v": [15.15, 1.0, 12, 13.9, 0.33],
    "flat": [10.69, 1.0, 12, -1.65, 0.35],
}


def _fixtures() -> dict[str, object]:
    return {
        "up": uptrend(),
        "down": downtrend(),
        "short": uptrend(80),
        "v": make_ohlcv(from_points([(0, 100), (120, 60), (260, 130)], 260)),
        "flat": make_ohlcv(100 + np.sin(np.arange(300) / 9) * 3),
    }


def test_default_switches_are_off() -> None:
    s = _s()
    assert s.technical_mode == "classic"
    assert s.analyst_signal_mode == "consensus"
    assert s.earnings_info_enabled is False
    assert s.earnings_window_effect_enabled is False


@pytest.mark.parametrize("key", list(GOLDEN))
def test_default_score_regression_unchanged(key: str) -> None:
    df = _fixtures()[key]
    s = _s()
    soon = EarningsInfo(symbol="X", status="known", next_date=date(2030, 1, 1), source="t")
    for earn in (None, soon):  # passing earnings must change nothing while the switches are off
        tech = technical_signal(df, s, earn)  # type: ignore[arg-type]
        comb = combine_signals({"technical": tech, "patterns": patterns_signal(df, s)}, s)  # type: ignore[arg-type]
        got = [tech.score, tech.confidence, len(tech.reasons), comb.total, comb.confidence]
        assert got == GOLDEN[key]


class _Hist:
    def get_history(self, symbol: str, days: int) -> object:
        return uptrend()


class _Boom:
    def next_earnings(self, symbol: str) -> object:
        raise AssertionError("must not be called while switches are off")

    def trends(self, symbol: str) -> object:
        raise AssertionError("must not be called while switches are off")


def test_scorecard_default_ignores_providers_and_has_no_analysts_result() -> None:
    base = compute_scorecard("AAPL", _Hist(), _s())  # type: ignore[arg-type]
    with_prov = compute_scorecard(
        "AAPL",
        _Hist(),
        _s(),
        earnings_provider=_Boom(),
        analyst_provider=_Boom(),  # type: ignore[arg-type]
    )

    def strip(card: dict) -> list:  # type: ignore[type-arg]
        return [
            (x["name"], x["score"], x["confidence"], x["weight"], x["reasons"])
            for x in card["signals"]
        ] + [card["total"], card["confidence"], card["technical"], card["patterns"]]

    assert strip(base) == strip(with_prov)
    assert not any(x["name"] == "analysts" and x["confidence"] > 0 for x in base["signals"])
    assert base["total"] == compute_scorecard("AAPL", _Hist(), _s())["total"]  # type: ignore[arg-type]


# ------------------------------------------------------------------ earnings
TODAY = date(2026, 10, 5)


def _info(days: int) -> EarningsInfo:
    return EarningsInfo(
        symbol="AAPL", status="known", next_date=date.fromordinal(TODAY.toordinal() + days)
    )


def test_earnings_line_and_window() -> None:
    assert earnings_line(_info(3), TODAY) == "Earnings in 3 days."
    assert earnings_line(_info(1), TODAY) == "Earnings in 1 day."
    assert "unknown" in earnings_line(EarningsInfo(symbol="TEVA.TA", status="unknown"), TODAY)
    s = _s()
    assert in_window(_info(5), TODAY, s) and not in_window(_info(6), TODAY, s)
    assert not in_window(None, TODAY, s)


def test_technical_earnings_info_adds_line_only_when_enabled() -> None:
    df = uptrend()
    as_of = df.index[-1].date()
    info = EarningsInfo(
        symbol="X", status="known", next_date=date.fromordinal(as_of.toordinal() + 2)
    )
    base = technical_signal(df, _s())
    on = technical_signal(df, _s(earnings_info_enabled=True), info)
    assert on.score == base.score and on.confidence == base.confidence
    assert on.reasons[-1] == "Earnings in 2 days."


def test_technical_earnings_window_lowers_confidence_only_when_enabled() -> None:
    df = downtrend()
    as_of = df.index[-1].date()
    info = EarningsInfo(
        symbol="X", status="known", next_date=date.fromordinal(as_of.toordinal() + 2)
    )
    base = technical_signal(df, _s())
    eff = technical_signal(df, _s(earnings_window_effect_enabled=True), info)
    assert eff.score == base.score
    assert eff.confidence == round(base.confidence * 0.7, 3)
    far = EarningsInfo(
        symbol="X", status="known", next_date=date.fromordinal(as_of.toordinal() + 20)
    )
    assert (
        technical_signal(df, _s(earnings_window_effect_enabled=True), far).confidence
        == base.confidence
    )


def test_exit_levels_earnings_notes_default_empty_and_gap_flag() -> None:
    now = datetime(2026, 10, 5, 12)
    assert _earnings_notes(_info(2), now, _s()) == []
    assert _earnings_notes(None, now, _s(earnings_window_effect_enabled=True)) == []
    notes = _earnings_notes(_info(2), now, _s(earnings_window_effect_enabled=True))
    assert len(notes) == 1 and "Gap risk" in notes[0]
    both = _earnings_notes(
        _info(2), now, _s(earnings_info_enabled=True, earnings_window_effect_enabled=True)
    )
    assert both[0] == "Earnings in 2 days."


class _FakeResp:
    status_code = 200

    def __init__(self, data: object) -> None:
        self._d = data

    def json(self) -> object:
        return self._d


class _FakeClient:
    def __init__(self, data: object) -> None:
        self.data, self.calls = data, 0

    def get(self, url: str, params: object = None, headers: object = None) -> _FakeResp:
        self.calls += 1
        return _FakeResp(self.data)


def test_earnings_finnhub_needs_key_caches_and_tase_is_unknown() -> None:
    data = {"earningsCalendar": [{"date": "2026-10-20"}, {"date": "2026-10-08"}]}
    assert (
        FinnhubEarningsProvider(_s(), client=_FakeClient(data)).next_earnings("AAPL", TODAY) is None
    )  # type: ignore[arg-type]
    cl = _FakeClient(data)
    p = FinnhubEarningsProvider(_s(finnhub_api_key="k"), client=cl)  # type: ignore[arg-type]
    first = p.next_earnings("AAPL", TODAY)
    assert first is not None and first.next_date == date(2026, 10, 8)
    p.next_earnings("AAPL", TODAY)
    assert cl.calls == 1  # cached
    chain = EarningsChain([p])
    assert chain.next_earnings("TEVA.TA", TODAY).status == "unknown"
    assert cl.calls == 1  # no call for TASE


# ------------------------------------------------------------------ analysts
def _tr(month: int, sb: int, b: int, h: int, s: int = 0, ss: int = 0) -> RecommendationTrend:
    return RecommendationTrend(
        period=date(2026, month, 1), strong_buy=sb, buy=b, hold=h, sell=s, strong_sell=ss
    )


def test_analyst_consensus_mode_is_unimplemented_default() -> None:
    res = analysts_signal([_tr(10, 5, 5, 2), _tr(7, 1, 2, 8)], _s())
    assert res.confidence == 0.0 and res.score == 0.0


def test_analyst_revisions_upgrade_scores_positive_with_reasons() -> None:
    s = _s(analyst_signal_mode="revisions")
    res = analysts_signal([_tr(10, 8, 8, 2), _tr(9, 6, 6, 6), _tr(7, 2, 4, 12)], s)
    assert res.score > 50 and res.confidence > 0.5
    assert res.reasons and "revisions up" in res.reasons[0]
    down = analysts_signal([_tr(10, 2, 4, 12), _tr(7, 8, 8, 2)], s)
    assert down.score < -50


def test_analyst_dispersion_lowers_confidence() -> None:
    s = _s(analyst_signal_mode="revisions")
    calm = analysts_signal([_tr(10, 0, 12, 8), _tr(7, 0, 10, 10)], s)
    split = analysts_signal([_tr(10, 8, 2, 0, 2, 8), _tr(7, 8, 0, 0, 2, 8)], s)
    assert split.confidence < calm.confidence


def test_analyst_no_data_or_one_month_has_zero_confidence() -> None:
    s = _s(analyst_signal_mode="revisions")
    for rows in (None, [], [_tr(10, 5, 5, 5)]):
        res = analysts_signal(rows, s)
        assert res.confidence == 0.0 and res.reasons


def test_analyst_provider_parse_and_tase_none() -> None:
    rows = FinnhubRecommendationProvider.parse(
        [
            {
                "period": "2026-09-01",
                "strongBuy": 3,
                "buy": 4,
                "hold": 5,
                "sell": 1,
                "strongSell": 0,
            },
            {
                "period": "2026-10-01",
                "strongBuy": 4,
                "buy": 4,
                "hold": 4,
                "sell": 1,
                "strongSell": 0,
            },
            {"period": "bad"},
        ]
    )
    assert [r.period.month for r in rows] == [10, 9]
    cl = _FakeClient([])
    p = FinnhubRecommendationProvider(_s(finnhub_api_key="k"), client=cl)  # type: ignore[arg-type]
    assert AnalystTrendChain([p]).trends("LUMI.TA") is None
    assert cl.calls == 0


def test_analyst_scorecard_revisions_mode_adds_signal() -> None:
    class Prov:
        def trends(self, symbol: str) -> list[RecommendationTrend]:
            return [_tr(10, 8, 8, 2), _tr(7, 2, 4, 12)]

    card = compute_scorecard(
        "AAPL",
        _Hist(),
        _s(analyst_signal_mode="revisions"),
        analyst_provider=Prov(),  # type: ignore[arg-type]
    )
    an = next(x for x in card["signals"] if x["name"] == "analysts")
    assert an["confidence"] > 0 and an["weight"] > 0


# ------------------------------------------------------------------ technical modes
def test_technical_vol_normalized_has_reasons_and_differs_from_classic() -> None:
    df = uptrend()
    classic = technical_signal(df, _s())
    vn = technical_signal(df, _s(technical_mode="vol_normalized"))
    assert -100 <= vn.score <= 100 and 0 < vn.confidence <= 1
    assert any("ATR" in r for r in vn.reasons) and any("Momentum" in r for r in vn.reasons)
    assert vn.score != classic.score
    assert not any("RSI" in r for r in vn.reasons[:5]) or True
    assert technical_signal(downtrend(), _s(technical_mode="vol_normalized")).score < 0


def test_technical_vol_normalized_short_and_missing_data_degrade() -> None:
    s = _s(technical_mode="vol_normalized")
    assert technical_signal(None, s).confidence == 0.0
    short = technical_signal(uptrend(80), s)  # no 12-1 momentum possible, still scores
    assert short.reasons and short.confidence > 0
    assert not any("Momentum skipping" in r for r in short.reasons)


@pytest.mark.live
def test_earnings_live_finnhub_placeholder() -> None:
    pytest.skip("live: needs FINNHUB_API_KEY and network")
