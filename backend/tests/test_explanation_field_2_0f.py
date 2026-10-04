"""`Explanation` and provider `Field` hardening (2.0-F item 8): finite numbers, bounds, length caps,
aware UTC and a stored explanation that always round-trips through JSON."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.launchgate import weights_fingerprint
from app.papertrading import record_call
from app.providers.base import (
    Field,
    Filing,
    FundamentalsSnapshot,
    NewsItem,
)
from app.signals.base import (
    ChartAnnotation,
    Explanation,
    ExplanationSource,
    SignalContribution,
    SignalResult,
)
from app.timeutil import utcnow

NAN, INF = float("nan"), float("inf")


def contribution(**kw: object) -> SignalContribution:
    base: dict[str, object] = {
        "name": "technical",
        "score": 10.0,
        "weight": 25.0,
        "confidence": 0.8,
    }
    base.update(kw)
    return SignalContribution(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- finite numbers
@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_non_finite_numbers_are_refused_everywhere_in_an_explanation(bad: float) -> None:
    with pytest.raises(ValidationError):
        contribution(score=bad)
    with pytest.raises(ValidationError):
        contribution(weight=bad)
    with pytest.raises(ValidationError):
        contribution(confidence=bad)
    with pytest.raises(ValidationError):
        contribution(raw={"x": bad})
    with pytest.raises(ValidationError):
        Explanation(summary="s", inputs={"x": bad})
    with pytest.raises(ValidationError):
        ChartAnnotation(kind="support", label="s", price=bad)
    with pytest.raises(ValidationError):
        SignalResult(
            score=bad, confidence=1.0, reasons=["r"], data_as_of=utcnow(),
            explanation=Explanation(summary="s"),
        )  # fmt: skip


def test_the_verified_nan_score_can_no_longer_be_stored_unreadably() -> None:
    """Review: a NaN score was written as `null`, and `model_validate_json` then failed."""
    with pytest.raises(ValidationError):
        Explanation(summary="s", contributions=[contribution(score=NAN)])


@pytest.mark.parametrize("field", ["score", "weight"])
def test_bounds_on_contributions(field: str) -> None:
    with pytest.raises(ValidationError):
        contribution(**{field: 1e308})
    with pytest.raises(ValidationError):
        contribution(**{field: -1e308})
    assert contribution(score=-100.0, weight=0.0).score == -100.0
    assert contribution(score=100.0, weight=100.0).weight == 100.0


def test_a_stored_explanation_always_round_trips_through_json() -> None:
    e = Explanation(
        summary="MACD bullish cross.",
        inputs={"rsi": 61.2, "trend": "up"},
        as_of=datetime(2026, 1, 2, 3, 4),
        contributions=[contribution(raw={"a": 1.5, "b": None, "c": "x"})],
        annotations=[ChartAnnotation(kind="support", label="S1", price=1.0, as_of=utcnow())],
        sources=[ExplanationSource(name="Yahoo", as_of=utcnow())],
    )
    text = e.model_dump_json()
    assert json.loads(text)  # strict JSON: no NaN literal
    assert Explanation.model_validate_json(text) == e
    assert Explanation.model_validate(json.loads(json.dumps(e.model_dump(mode="json")))) == e


def test_paper_call_explanation_is_readable_after_storage(db) -> None:  # type: ignore[no-untyped-def]
    call = record_call(
        db, symbol="AAPL", side="buy", horizon="1m", entry=100.0, stop=None, targets=[110.0],
        explanation=Explanation(summary="s", contributions=[contribution()]),
        model_hash="m", prompt_hash="p", weights_hash=weights_fingerprint(Settings().signal_weights),
        is_global=True,
    )  # fmt: skip
    db.expire_all()
    stored = db.get(type(call), call.id)
    assert Explanation.model_validate(stored.explanation).contributions[0].name == "technical"


# ---------------------------------------------------------------- length caps
def test_length_caps() -> None:
    with pytest.raises(ValidationError):
        Explanation(summary="x" * 100_000)  # the verified 100 KB summary
    with pytest.raises(ValidationError):
        Explanation(summary="s", invalidation_risks=["r" * 5000])
    with pytest.raises(ValidationError):
        Explanation(summary="s", rules_applied=["r"] * 500)
    with pytest.raises(ValidationError):
        Explanation(summary="s", inputs={f"k{i}": 1.0 for i in range(500)})
    with pytest.raises(ValidationError):
        contribution(raw={"k": "v" * 5000})
    with pytest.raises(ValidationError):
        contribution(name="n" * 501)
    with pytest.raises(ValidationError):
        ExplanationSource(name="n", detail="d" * 10_000)
    with pytest.raises(ValidationError):
        ChartAnnotation(kind="support", label="l" * 1000)
    assert len(Explanation(summary="x" * 2000).summary) == 2000  # a real summary fits


def test_the_real_signals_still_fit_the_caps() -> None:
    from app.signals.patterns import patterns_signal
    from app.signals.technical import technical_signal
    from tests.fixtures.series import uptrend

    s = Settings(_env_file=None)
    for sig in (technical_signal(uptrend(400), s), patterns_signal(uptrend(400), s)):
        Explanation.model_validate_json(sig.explanation.model_dump_json())


# ---------------------------------------------------------------- aware UTC everywhere
def test_field_as_of_is_aware_utc_and_comparable_with_explanation_times() -> None:
    f = Field[float].ok(1.0, "src", datetime(2026, 1, 2, 3, 4))  # naive in
    assert f.as_of is not None and f.as_of.tzinfo is UTC
    local = Field[float].ok(
        1.0, "src", datetime(2026, 1, 2, 5, 4, tzinfo=timezone(timedelta(hours=2)))
    )
    assert local.as_of == datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    exp = Explanation(summary="s", as_of=datetime(2026, 1, 2, 3, 4))
    assert f.as_of <= exp.as_of  # type: ignore[operator]  # the verified TypeError is gone


def test_news_and_filing_times_are_aware_utc() -> None:
    n = NewsItem(
        id="1", headline="h", published_at=datetime(2026, 1, 1), available_at=datetime(2026, 1, 2)
    )
    assert n.published_at is not None and n.published_at.tzinfo is UTC
    assert n.available_at is not None and n.available_at.tzinfo is UTC
    assert Filing(id="f", form="10-K", filed_at=datetime(2026, 1, 1)).filed_at.tzinfo is UTC  # type: ignore[union-attr]


# ---------------------------------------------------------------- provider Field
@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_field_refuses_non_finite_values(bad: float) -> None:
    with pytest.raises(ValidationError):
        Field[float].ok(bad, "src")


def test_field_value_xor_reason_still_holds() -> None:
    with pytest.raises(ValidationError):
        Field[float](value=None, source="s")
    with pytest.raises(ValidationError):
        Field[float](value=1.0, source="s", missing_reason="coverage")
    assert Field[float].missing("s", "coverage").is_missing
    with pytest.raises(ValidationError):
        Field[float].ok(1.0, "s" * 500)


# ---------------------------------------------------------------- currency and period
def fields(value: float | None = 1.0) -> dict[str, Field[float]]:
    return {
        n: (
            Field[float].ok(value, "src", utcnow())
            if value is not None
            else Field[float].missing("src", "coverage")
        )
        for n in FundamentalsSnapshot._numeric()
    }


def test_fundamentals_carry_a_currency_and_a_period() -> None:
    snap = FundamentalsSnapshot(symbol="TEVA.TA", currency="ILS", period="TTM", **fields())
    assert (snap.currency, snap.period) == ("ILS", "TTM")
    assert FundamentalsSnapshot(symbol="X", currency="USD", period="FY", **fields()).period == "FY"


def test_monetary_values_without_currency_or_period_are_refused() -> None:
    with pytest.raises(ValidationError, match="currency"):
        FundamentalsSnapshot(symbol="X", period="TTM", **fields())
    with pytest.raises(ValidationError, match="period"):
        FundamentalsSnapshot(symbol="X", currency="USD", **fields())
    # nothing to label when every field is missing
    FundamentalsSnapshot(symbol="X", **fields(None))
    assert FundamentalsSnapshot.all_missing("X", "src", "coverage").all_fields_missing


@pytest.mark.parametrize("bad", ["ILA", "GBX", "gbp", "us", "dollars", ""])
def test_currency_must_be_a_major_iso_code_never_agorot_or_pence(bad: str) -> None:
    with pytest.raises(ValidationError):
        FundamentalsSnapshot(symbol="TEVA.TA", currency=bad, period="TTM", **fields())


def test_period_is_ttm_or_fy() -> None:
    with pytest.raises(ValidationError):
        FundamentalsSnapshot(symbol="X", currency="USD", period="Q3", **fields())  # type: ignore[arg-type]


def test_the_fake_fundamentals_provider_labels_its_numbers() -> None:
    from app.providers.fakes import FakeFundamentals

    snap = FakeFundamentals(
        {"AAPL": {n: 1.0 for n in FundamentalsSnapshot._numeric()}}
    ).get_fundamentals("AAPL")
    assert snap.currency == "USD" and snap.period == "TTM"
    assert math.isfinite(snap.eps_ttm.value or 0.0)
