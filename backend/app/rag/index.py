"""`ChunkIndex`: one keyword-search interface over two engines (BM25-style ranking).

- SQLite (dev, tests): an FTS5 virtual table `doc_chunk_fts` (rowid = `doc_chunk.id`), ranked with
  `bm25()`. The index is written by `add()` in the same transaction as the chunk.
- Postgres (cloud): a GIN expression index on `to_tsvector('simple', text)`; nothing to write per row.
  Ranked with `ts_rank_cd` (the closest built-in to BM25). The `simple` config keeps Hebrew and
  English tokens as they are (no stemming for either language).

The index objects are not in the SQLModel metadata, so `ensure()` (index job, idempotent) creates
them and the migration test "schema equals metadata" stays exact. Before `ensure()` ran, SQLite
search returns nothing.

Embeddings hook (not built): a second implementation of `search()` may rank by cosine similarity
over a small `doc_chunk_vec(chunk_id, vector)` table filled by the index job with a local
multilingual model. It must satisfy the same `ChunkIndex` contract and stay behind
`rag_embeddings_enabled`; the API process never loads a model.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

_WORD = re.compile(r"\w+", re.UNICODE)
MIN_PREFIX = 3  # tokens at least this long also match as a prefix (cheap Hebrew prefix handling)


_HEBREW = re.compile(r"[\u0590-\u05ff]")
_HE_PREFIXES = "בהלמושכ"  # one-letter prefixes: in, the, to, from, and, that, like


def _hebrew_variants(word: str) -> list[str]:
    """Hebrew glues prefixes to words (במיגרנה, למיגרנה) and the FTS has no stemmer, so a word is
    searched bare and with each one-letter prefix; a word that starts with a prefix letter is also
    searched without it."""
    if not _HEBREW.search(word) or len(word) < 3:
        return [word]
    bases = [word]
    if len(word) >= 4 and word[0] in _HE_PREFIXES:
        bases.append(word[1:])
    out = list(bases)
    for b in bases:
        out += [p + b for p in _HE_PREFIXES]
    return list(dict.fromkeys(out))


def query_terms(query: str, limit: int = 24) -> list[str]:
    """Lower-cased unique word tokens of a free-text query (letters and digits only, so the
    result is safe to embed in an FTS expression), with Hebrew prefix variants added."""
    seen: list[str] = []
    for w in _WORD.findall(query.casefold()):
        if len(w) >= 2 and w not in seen:
            seen.append(w)
    out: list[str] = []
    for w in seen[:limit]:
        for v in _hebrew_variants(w):
            if v not in out:
                out.append(v)
    return out[: limit * 4]


class ChunkIndex:
    """Interface; use `ChunkIndex.for_session(db)`."""

    @staticmethod
    def for_session(db: Session) -> ChunkIndex:
        name = db.get_bind().dialect.name
        return PostgresIndex() if name == "postgresql" else SqliteFtsIndex()

    def ensure(self, db: Session) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def add(self, db: Session, chunk_id: int, body: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def delete(self, db: Session, chunk_ids: list[int]) -> None:  # pragma: no cover
        raise NotImplementedError

    def rebuild(self, db: Session) -> int:  # pragma: no cover
        raise NotImplementedError

    def search(
        self,
        db: Session,
        query: str,
        symbol: str,
        cutoffs: dict[str, datetime],
        limit: int,
    ) -> list[tuple[int, float]]:
        """(chunk_id, score) best first, score > 0. `cutoffs` maps each wanted doc type to the
        oldest `as_of` still allowed for it."""
        raise NotImplementedError


def _filters(cutoffs: dict[str, datetime]) -> tuple[str, dict[str, object]]:
    parts: list[str] = []
    params: dict[str, object] = {}
    for i, (doc_type, cutoff) in enumerate(sorted(cutoffs.items())):
        parts.append(f"(c.doc_type = :t{i} AND c.as_of >= :d{i})")
        params[f"t{i}"] = doc_type
        params[f"d{i}"] = cutoff
    return "(" + " OR ".join(parts) + ")", params


class SqliteFtsIndex(ChunkIndex):
    def ensure(self, db: Session) -> None:
        db.connection().execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunk_fts USING fts5("
                "body, tokenize='unicode61 remove_diacritics 2')"
            )
        )
        db.commit()

    def add(self, db: Session, chunk_id: int, body: str) -> None:
        db.connection().execute(
            text("INSERT INTO doc_chunk_fts (rowid, body) VALUES (:i, :b)"),
            {"i": chunk_id, "b": body},
        )

    def delete(self, db: Session, chunk_ids: list[int]) -> None:
        for cid in chunk_ids:
            db.connection().execute(text("DELETE FROM doc_chunk_fts WHERE rowid = :i"), {"i": cid})

    def rebuild(self, db: Session) -> int:
        self.ensure(db)
        conn = db.connection()
        conn.execute(text("DELETE FROM doc_chunk_fts"))
        conn.execute(text("INSERT INTO doc_chunk_fts (rowid, body) SELECT id, text FROM doc_chunk"))
        n = int(conn.execute(text("SELECT count(*) FROM doc_chunk_fts")).scalar() or 0)
        db.commit()
        return n

    def search(
        self, db: Session, query: str, symbol: str, cutoffs: dict[str, datetime], limit: int
    ) -> list[tuple[int, float]]:
        terms = query_terms(query)
        if not terms or not cutoffs:
            return []
        match = " OR ".join(f'"{t}"*' if len(t) >= MIN_PREFIX else f'"{t}"' for t in terms)
        where, params = _filters(cutoffs)
        sql = text(
            "SELECT c.id, bm25(doc_chunk_fts) AS r FROM doc_chunk_fts "
            "JOIN doc_chunk c ON c.id = doc_chunk_fts.rowid "
            f"WHERE doc_chunk_fts MATCH :q AND c.symbol = :s AND {where} "
            "ORDER BY r LIMIT :n"
        )
        try:
            rows = (
                db.connection().execute(sql, {"q": match, "s": symbol, "n": limit, **params}).all()
            )
        except OperationalError:  # the FTS table does not exist yet: nothing is indexed
            db.rollback()
            return []
        # bm25() is negative (more negative = better): flip so a higher score is better
        return [(int(i), max(-float(r), 1e-9)) for i, r in rows]


class PostgresIndex(ChunkIndex):
    _INDEX = "ix_doc_chunk_fts"

    def ensure(self, db: Session) -> None:
        db.connection().execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {self._INDEX} ON doc_chunk "
                "USING gin (to_tsvector('simple', text))"
            )
        )
        db.commit()

    def add(self, db: Session, chunk_id: int, body: str) -> None:
        return None  # the GIN expression index follows the table

    def delete(self, db: Session, chunk_ids: list[int]) -> None:
        return None

    def rebuild(self, db: Session) -> int:
        db.connection().execute(text(f"REINDEX INDEX {self._INDEX}"))
        db.commit()
        return int(db.connection().execute(text("SELECT count(*) FROM doc_chunk")).scalar() or 0)

    def search(
        self, db: Session, query: str, symbol: str, cutoffs: dict[str, datetime], limit: int
    ) -> list[tuple[int, float]]:
        terms = query_terms(query)
        if not terms or not cutoffs:
            return []
        exists = db.connection().execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :n AND tablename = 'doc_chunk'"),
            {"n": self._INDEX},
        )
        if exists.first() is None:  # ensure() has not run: nothing is indexed (same as SQLite)
            return []
        tsq = " | ".join(f"{t}:*" if len(t) >= MIN_PREFIX else t for t in terms)
        where, params = _filters(cutoffs)
        sql = text(
            "SELECT c.id, ts_rank_cd(to_tsvector('simple', c.text), q.q) AS r "
            "FROM doc_chunk c, to_tsquery('simple', :q) AS q(q) "
            f"WHERE to_tsvector('simple', c.text) @@ q.q AND c.symbol = :s AND {where} "
            "ORDER BY r DESC LIMIT :n"
        )
        rows = db.connection().execute(sql, {"q": tsq, "s": symbol, "n": limit, **params}).all()
        return [(int(i), max(float(r), 1e-9)) for i, r in rows]
