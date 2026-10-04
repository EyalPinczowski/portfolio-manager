"""`Retriever.search`: top-k public chunks for one symbol under a hard token budget.

No user id anywhere: retrieval is user-independent by construction (the RAG tables hold public text
only). Stale chunks (older than their doc type's TTL) are filtered in the query. An empty result is
an explicit `no_coverage` marker, so the caller sets `confidence=0` and never invents text (TASE
often has none). Tokens-in per retrieval are counted in the LLM usage ledger (provider `rag`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.llm.ledger import record_usage
from app.models import DocChunk
from app.rag.chunker import DOC_TYPES
from app.rag.index import ChunkIndex
from app.timeutil import utcnow

log = logging.getLogger(__name__)

CANDIDATE_FACTOR = 4  # candidates fetched per wanted chunk, so the budget can skip long ones


class Hit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: int
    symbol: str
    market: str
    doc_type: str
    source_url: str
    as_of: datetime
    text: str
    token_count: int
    score: float


class RetrievalResult(BaseModel):
    status: Literal["ok", "no_coverage"]
    hits: list[Hit]
    tokens: int  # estimated tokens of the returned chunk texts
    budget: int  # the hard cap that applied

    @property
    def no_coverage(self) -> bool:
        return self.status == "no_coverage"


class Retriever:
    def __init__(
        self, db: Session, index: ChunkIndex | None = None, settings: Settings | None = None
    ) -> None:
        self.db = db
        self.index = index or ChunkIndex.for_session(db)
        self.settings = settings or get_settings()

    def search(
        self,
        symbol: str,
        query: str,
        doc_types: list[str] | None = None,
        k: int = 5,
        max_tokens: int | None = None,
        *,
        role: str | None = None,
        now: datetime | None = None,
    ) -> RetrievalResult:
        s = self.settings
        types = list(doc_types) if doc_types else list(DOC_TYPES)
        bad = [t for t in types if t not in DOC_TYPES]
        if bad:
            raise ValueError(f"unknown doc_type {bad[0]!r}")
        budget = max_tokens if max_tokens is not None else max(s.rag_role_budgets.values())
        if role is not None:
            if role not in s.rag_role_budgets:
                raise ValueError(f"unknown role {role!r}")
            budget = min(budget, s.rag_role_budgets[role])
        budget = max(0, budget)
        k = max(0, min(k, s.rag_max_k))

        today = now or utcnow()
        cutoffs = {
            t: today - timedelta(days=s.rag_doc_ttl_days[t])
            for t in types
            if t in s.rag_doc_ttl_days
        }
        sym = symbol.strip().upper()
        ranked = (
            self.index.search(self.db, query, sym, cutoffs, k * CANDIDATE_FACTOR)
            if k and budget and cutoffs
            else []
        )
        rows = {}
        if ranked:
            ids = [i for i, _ in ranked]
            rows = {
                r.id: r for r in self.db.exec(select(DocChunk).where(col(DocChunk.id).in_(ids)))
            }
        hits: list[Hit] = []
        used = 0
        for cid, score in ranked:
            row = rows.get(cid)
            if row is None or row.id is None:
                continue
            if used + row.token_count > budget:
                continue  # too big for what is left; a shorter one later may still fit
            hits.append(
                Hit(
                    chunk_id=row.id,
                    symbol=row.symbol,
                    market=row.market,
                    doc_type=row.doc_type,
                    source_url=row.source_url,
                    as_of=row.as_of,
                    text=row.text,
                    token_count=row.token_count,
                    score=score,
                )
            )
            used += row.token_count
            if len(hits) >= k:
                break
        self._count(role, used)
        return RetrievalResult(
            status="ok" if hits else "no_coverage", hits=hits, tokens=used, budget=budget
        )

    def _count(self, role: str | None, tokens: int) -> None:
        """Retrieval counter in the LLM usage ledger: requests = searches, tokens = tokens-in."""
        try:
            bind = self.db.get_bind()
            record_usage(
                "rag",
                role or "any",
                requests=1,
                tokens=tokens,
                session_factory=lambda: Session(bind),
            )
        except Exception:  # metrics must never break retrieval
            log.warning("rag usage counter failed", exc_info=True)
