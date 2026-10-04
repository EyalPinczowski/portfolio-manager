"""Chunker and ingest (index job only, never in the request path).

Text is split on blank lines into paragraphs, boilerplate lines are dropped, and paragraphs are
packed into chunks of about `rag_chunk_target_tokens`; consecutive chunks share a tail of about
`rag_chunk_overlap_tokens`. A paragraph longer than the target is split on sentence ends, then on
words. Duplicates (same symbol and normalised-text hash) are skipped, so re-running is idempotent.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import DocChunk
from app.rag.index import ChunkIndex
from app.rag.tokens import estimate_tokens

DOC_TYPES = ("filing", "news", "transcript", "profile")

_BOILERPLATE = re.compile(
    r"^\s*(?:"
    r"page\s+\d+(?:\s+of\s+\d+)?|\d+|table of contents|click here.*|subscribe.*|sign up.*|"
    r"all rights reserved.*|copyright\s*(?:©|\(c\))?.*|©.*|cookie.*|privacy policy.*|"
    r"terms (?:of use|and conditions).*|forward-looking statements?\s*$|"
    r"כל הזכויות שמורות.*|מדיניות פרטיות.*|תנאי שימוש.*|הצהרה צופה פני עתיד\s*$|עמוד\s+\d+.*"
    r")\s*$",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?։؟])\s+")
_SPACES = re.compile(r"[ \t ]+")
_BLANK = re.compile(r"\n\s*\n+")


def normalize(text: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).casefold().encode("utf-8")).hexdigest()[:32]


def strip_boilerplate(text: str) -> str:
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if not _BOILERPLATE.match(ln)]
    return "\n".join(lines)


def _pieces(paragraph: str, target: int, s: Settings) -> list[str]:
    """Split one over-long paragraph on sentences, then on words, each piece within the target."""
    if estimate_tokens(paragraph, s) <= target:
        return [paragraph]
    out: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        if estimate_tokens(sentence, s) <= target:
            out.append(sentence)
            continue
        cur: list[str] = []
        for word in sentence.split():
            cur.append(word)
            if estimate_tokens(" ".join(cur), s) > target and len(cur) > 1:
                out.append(" ".join(cur[:-1]))
                cur = [word]
        if cur:
            out.append(" ".join(cur))
    return out


def _tail(text: str, overlap: int, s: Settings) -> str:
    """The last words of `text` worth about `overlap` tokens."""
    if overlap <= 0:
        return ""
    words = text.split()
    keep: list[str] = []
    for w in reversed(words):
        keep.append(w)
        if estimate_tokens(" ".join(keep), s) >= overlap:
            break
    keep.reverse()
    return " ".join(keep) if len(keep) < len(words) else ""


def chunk_text(text: str, settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    target = s.rag_chunk_target_tokens
    overlap = min(s.rag_chunk_overlap_tokens, target // 2)
    paragraphs = [normalize(p) for p in _BLANK.split(strip_boilerplate(text)) if normalize(p)]
    pieces = [piece for p in paragraphs for piece in _pieces(p, target, s)]
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        joined = f"{cur}\n\n{piece}" if cur else piece
        if cur and estimate_tokens(joined, s) > target:
            chunks.append(cur)
            tail = _tail(cur, overlap, s)
            cur = f"{tail}\n\n{piece}" if tail else piece
            if estimate_tokens(cur, s) > target:  # the overlap must never push a chunk over
                cur = piece
        else:
            cur = joined
    if cur:
        chunks.append(cur)
    # drop a trailing chunk that is only the previous chunk's overlap
    return [c for i, c in enumerate(chunks) if i == 0 or c not in chunks[i - 1]]


@dataclass(frozen=True)
class IngestDoc:
    symbol: str
    market: str
    doc_type: str
    source_url: str
    as_of: datetime
    text: str


@dataclass(frozen=True)
class IngestStats:
    docs: int = 0
    chunks_added: int = 0
    chunks_skipped: int = 0


def ingest_document(
    db: Session, doc: IngestDoc, index: ChunkIndex, settings: Settings | None = None
) -> tuple[int, int]:
    """Chunk one document and store the new chunks (and index them). Returns (added, skipped)."""
    s = settings or get_settings()
    if doc.doc_type not in DOC_TYPES:
        raise ValueError(f"unknown doc_type {doc.doc_type!r}")
    symbol = doc.symbol.strip().upper()
    added = skipped = 0
    seen = set(db.exec(select(col(DocChunk.text_hash)).where(col(DocChunk.symbol) == symbol)).all())
    for piece in chunk_text(doc.text, s):
        h = text_hash(piece)
        if h in seen:
            skipped += 1
            continue
        seen.add(h)
        row = DocChunk(
            symbol=symbol,
            market=doc.market.strip().upper(),
            doc_type=doc.doc_type,
            source_url=doc.source_url,
            as_of=doc.as_of,
            text=piece,
            token_count=estimate_tokens(piece, s),
            text_hash=h,
        )
        db.add(row)
        db.flush()
        assert row.id is not None
        index.add(db, row.id, row.text)
        added += 1
    db.commit()
    return added, skipped


def ingest_documents(
    db: Session,
    docs: list[IngestDoc],
    index: ChunkIndex | None = None,
    settings: Settings | None = None,
) -> IngestStats:
    idx = index or ChunkIndex.for_session(db)
    idx.ensure(db)
    added = skipped = 0
    for d in docs:
        a, k = ingest_document(db, d, idx, settings)
        added += a
        skipped += k
    return IngestStats(docs=len(docs), chunks_added=added, chunks_skipped=skipped)
