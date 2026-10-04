from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from app.analyze.public_facts import PublicFacts
from app.config import Settings
from app.rag.chunker import IngestDoc, ingest_documents
from app.rag.indexjob import load_dir

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rag"
NOW = datetime(2026, 10, 4, 12, 0)


@pytest.fixture
def cfg() -> Settings:
    """No real keys; rate limits wide open so scenarios do not throttle each other."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        gemini_api_key=None,
        groq_api_key=None,
        llm_requests_per_minute=1000,
        llm_role_rpm=1000,
        llm_user_rpm=1000,
        llm_daily_budget=10_000,
    )


@pytest.fixture
def corpus(db: Session) -> Session:
    ingest_documents(db, load_dir(FIXTURES))
    return db


def facts(
    symbol: str = "AAPL", score: float | None = -12.0, confidence: float = 0.6
) -> PublicFacts:
    return PublicFacts(
        symbol=symbol,
        name=symbol,
        market="TASE" if symbol.endswith(".TA") else "US",
        asset_type="stock",
        sector="Tech",
        country="IL" if symbol.endswith(".TA") else "US",
        score=score,
        confidence=confidence,
        indicators={"rsi14": 41.5},
        reasons=["Price is below the 50-day average."],
    )


def seed(db: Session, symbol: str, n: int, doc_type: str = "news") -> None:
    docs = [
        IngestDoc(
            symbol=symbol,
            market="US",
            doc_type=doc_type,
            source_url=f"https://example.test/{symbol}/{i}",
            as_of=datetime(2026, 10, 3),
            text=" ".join(
                f"Sentence {j} of {doc_type} {i} says the company announced results and regulation risk."
                for j in range(40)
            ),
        )
        for i in range(n)
    ]
    ingest_documents(db, docs)
