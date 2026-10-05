"""Committee prompts are cache-stable: a price tick gives the same prompt, a real change does not."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from app.analyze.public_facts import PublicFacts
from app.committee import roles
from app.committee.schemas import BearCase, BearRisk
from app.config import get_settings
from app.rag.chunker import ingest_documents
from app.rag.indexjob import load_dir
from app.rag.prompt import build_prompt
from app.rag.retriever import Hit
from tests.evals.mock_llm import MockLLM

FIXTURES = Path(__file__).parent / "fixtures" / "rag"
NOW = datetime(2026, 10, 4, 12, 0)


def _facts(price: float = 187.43, score: float | None = 41.3, minutes: int = 0) -> PublicFacts:
    return PublicFacts(
        symbol="AAPL", name="Apple", market="US", asset_type="stock", sector="Tech", country="US",
        price=price, currency="USD", price_as_of=NOW + timedelta(minutes=minutes), score=score,
        confidence=0.7123, indicators={"rsi": 55.04, "atr": 3.21},
        levels=["support 180 (-4.0%)", "resistance 195 (+4.0%)"], reasons=["Trend is up."],
    )  # fmt: skip


def _hit(i: int, text: str = "The company reports results and faces supervision.") -> Hit:
    return Hit(
        chunk_id=i, symbol="AAPL", market="US", doc_type="news", source_url="u", as_of=NOW,
        text=text, token_count=10, score=1.0,
    )  # fmt: skip


@pytest.fixture
def corpus(db: Session) -> Session:
    ingest_documents(db, load_dir(FIXTURES))
    return db


def test_static_roles_leave_out_volatile_fields() -> None:
    for role in ("company_profile", "news"):
        a = build_prompt(role, _facts(), [_hit(1)]).text
        b = build_prompt(role, _facts(price=190.1, score=-20, minutes=5), [_hit(1)]).text
        assert a == b
        assert "187.43" not in a and "price" not in a and "score" not in a


def test_bear_and_cio_round_and_drop_timestamps() -> None:
    for role in ("bear", "cio"):
        a = build_prompt(role, _facts(), [_hit(1)]).text
        b = build_prompt(role, _facts(price=187.2, score=40.9, minutes=7), [_hit(1)]).text
        assert a == b
        assert '"price":187' in a and '"score":41' in a and "price_as_of" not in a
        assert "(-4.0%)" not in a and "support 180" in a
        # a real change in the rounded values changes the text
        assert build_prompt(role, _facts(score=42.0), [_hit(1)]).text != a
        assert build_prompt(role, _facts(price=195.0), [_hit(1)]).text != a


def _runner(role: str, db: Session, facts: PublicFacts, llm: MockLLM) -> Any:
    kw: dict[str, Any] = {"providers": [llm], "settings": get_settings(), "now": NOW}
    if role == "company_profile":
        return roles.company_profile(db, facts, **kw)
    if role == "news":
        return roles.news(db, facts, **kw)
    bear_case = BearCase(
        risks=[BearRisk(text="r", severity=3, chunk_ids=[1], what_would_invalidate="x")]
    )
    if role == "bear":
        return roles.bear(db, facts, None, **kw)
    return roles.cio(db, facts, bear_case, **kw)


@pytest.mark.parametrize("role", ["company_profile", "news", "bear", "cio"])
def test_a_price_tick_hits_the_cache_with_zero_provider_calls(corpus: Session, role: str) -> None:
    llm = MockLLM()
    first = _runner(role, corpus, _facts(), llm)
    assert first.source == "llm" and len(llm.seen) == 1
    second = _runner(role, corpus, _facts(price=187.2, score=40.9, minutes=9), llm)
    assert second.source == "cache" or second.value == first.value
    assert len(llm.seen) == 1  # no second request


@pytest.mark.parametrize("role", ["bear", "cio"])
def test_a_real_change_misses_the_cache(corpus: Session, role: str) -> None:
    llm = MockLLM()
    _runner(role, corpus, _facts(), llm)
    _runner(role, corpus, _facts(score=47.0), llm)  # score moved to another rounded value
    assert len(llm.seen) == 2


def test_new_news_chunk_misses_the_cache(corpus: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm.structured import structured_call

    seen: list[str] = []
    real: Callable[..., Any] = structured_call

    def spy(**kw: Any) -> Any:
        seen.append(kw["prompt"])
        return real(**kw)

    monkeypatch.setattr(roles, "structured_call", spy)
    llm = MockLLM()
    _runner("news", corpus, _facts(), llm)
    p1 = build_prompt("news", _facts(), [_hit(1)]).text
    p2 = build_prompt("news", _facts(), [_hit(1), _hit(2, "A new announcement.")]).text
    assert p1 != p2
    assert seen  # the role really used build_prompt text


def test_numbers_the_model_may_quote_are_in_the_prompt() -> None:
    text = build_prompt("bear", _facts(), [_hit(1)]).text
    assert roles.numbers_grounded(["Score 41, price 187, RSI 55, support 180."], text)
    # the exact tick price is not shown, so it cannot be quoted
    assert not roles.numbers_grounded(["Price is 187.43."], text)
