"""Phase 2 provider protocols: typed Field[T], declared market coverage, graceful degradation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.providers.base import (
    Field,
    FilingsProvider,
    FundamentalsProvider,
    FundamentalsSnapshot,
    NewsProvider,
    TranscriptProvider,
    describe_missing,
    market_of_symbol,
)
from app.providers.fakes import (
    FakeFilings,
    FakeFundamentals,
    FakeNews,
    FakeTranscripts,
)
from app.scoring.combine import combine_signals
from app.signals.base import SignalResult
from app.signals.technical import technical_signal
from tests.fixtures.series import uptrend

S = Settings(_env_file=None)
US_ROW = {"pe_trailing": 25.0, "roic_pct": 18.0, "debt_to_equity": 0.4}


def test_field_is_a_value_or_a_reason() -> None:
    ok = Field[float].ok(1.5, "src")
    assert ok.value == 1.5 and not ok.is_missing and ok.missing_reason is None
    gone = Field[float].missing("src", "coverage")
    assert gone.is_missing and gone.missing_reason == "coverage"
    with pytest.raises(ValidationError):
        Field[float](source="src")  # neither value nor reason
    with pytest.raises(ValidationError):
        Field[float](value=1.0, source="src", missing_reason="stale")
    with pytest.raises(ValidationError):
        Field[float](source="src", missing_reason="because")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("symbol", "market"),
    [("AAPL", "US"), ("TEVA.TA", "TASE"), ("^TA125.TA", "TASE"), ("BTC-USD", "CRYPTO")],
)
def test_market_of_symbol(symbol: str, market: str) -> None:
    assert market_of_symbol(symbol) == market


def test_fakes_satisfy_the_protocols() -> None:
    assert isinstance(FakeFundamentals(), FundamentalsProvider)
    assert isinstance(FakeNews(), NewsProvider)
    assert isinstance(FakeTranscripts(), TranscriptProvider)
    assert isinstance(FakeFilings(), FilingsProvider)


def test_us_only_fundamentals_on_a_tase_symbol_is_missing_by_coverage() -> None:
    provider = FakeFundamentals({"TEVA.TA": US_ROW, "AAPL": US_ROW}, markets=frozenset({"US"}))
    snap = provider.get_fundamentals("TEVA.TA")
    assert snap.all_fields_missing
    assert {f.missing_reason for f in snap.fields.values()} == {"coverage"}
    assert snap.pe_trailing.source == "fake-fundamentals"
    # Same data exists for the symbol, but coverage is decided by the declared markets, not by data.
    assert provider.get_fundamentals("AAPL").pe_trailing.value == 25.0


def test_covered_symbol_without_data_is_not_found_not_coverage() -> None:
    snap = FakeFundamentals({"AAPL": US_ROW}).get_fundamentals("MSFT")
    assert {f.missing_reason for f in snap.fields.values()} == {"not_found"}
    partial = FakeFundamentals({"AAPL": US_ROW}).get_fundamentals("AAPL")
    assert partial.roic_pct.value == 18.0 and partial.eps_ttm.missing_reason == "not_found"
    assert not partial.all_fields_missing


def test_other_provider_kinds_declare_coverage_too() -> None:
    for call in (
        lambda: FakeNews({}, markets=frozenset({"US"})).get_news("NICE.TA"),
        lambda: FakeTranscripts().get_transcripts("NICE.TA"),
        lambda: FakeFilings().get_filings("NICE.TA"),
    ):
        got = call()
        assert got.is_missing and got.missing_reason == "coverage"
    assert FakeFilings().get_filings("AAPL").missing_reason == "not_found"


def test_combine_treats_a_coverage_gap_as_confidence_zero_and_redistributes() -> None:
    """A TASE symbol through a US-only fundamentals provider must not drag the score to neutral."""
    snap = FakeFundamentals({"TEVA.TA": US_ROW}).get_fundamentals("TEVA.TA")
    fundamentals = SignalResult.missing(
        "fundamentals", describe_missing("fundamentals", snap.pe_trailing)
    )
    assert fundamentals.confidence == 0
    assert "does not cover this market" in fundamentals.reasons[0]

    tech = technical_signal(uptrend(300), S)
    assert tech.confidence > 0 and tech.score != 0
    both = combine_signals({"technical": tech, "fundamentals": fundamentals}, S)
    alone = combine_signals({"technical": tech}, S)

    by = {w.name: w for w in both.breakdown}
    assert by["fundamentals"].effective_weight == 0
    assert "fundamentals" in both.missing
    assert by["technical"].effective_weight == pytest.approx(100.0)  # its share was redistributed
    assert both.total == alone.total  # not diluted toward 0 by the missing fundamentals
    assert both.confidence < 1.0  # but the lost coverage is visible


def test_all_missing_snapshot_round_trips_json() -> None:
    snap = FundamentalsSnapshot.all_missing("X", "src", "stale")
    again = FundamentalsSnapshot.model_validate_json(snap.model_dump_json())
    assert again.all_fields_missing
