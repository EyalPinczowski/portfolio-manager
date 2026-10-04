"""The RAG index job (`python -m app.cli rag-index`): ingest local text/JSON files.

A GitHub Actions cron fetches filings and news elsewhere and drops them in a directory; this job
only reads that directory (no fetchers here, no network). Layout, either or both:

- `<dir>/<SYMBOL>/<doc_type>/<YYYY-MM-DD>_<name>.txt|md`: plain text. `as_of` is the date in the file
  name, else the file's modification time; `source_url` is `file:<SYMBOL>/<doc_type>/<name>`.
- `<dir>/**/*.json`: one object or a list of objects with `symbol`, `doc_type`, `source_url`,
  `as_of` (ISO date or datetime), `text` and optional `market`.

Idempotent: a chunk already stored for the symbol (same text hash) is skipped. Expired chunks are
purged at the end. Never runs in the request path.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import DocChunk
from app.rag.chunker import DOC_TYPES, IngestDoc, IngestStats, ingest_documents
from app.rag.index import ChunkIndex
from app.timeutil import utcnow

log = logging.getLogger(__name__)
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})[_-]")


def market_of(symbol: str) -> str:
    if symbol.endswith(".TA"):
        return "TASE"
    if symbol.endswith("-USD"):
        return "CRYPTO"
    return "US"


def _parse_dt(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value))
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def load_dir(root: Path, only: set[str] | None = None) -> list[IngestDoc]:
    docs: list[IngestDoc] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            if path.suffix == ".json":
                raw = json.loads(path.read_text(encoding="utf-8"))
                for item in raw if isinstance(raw, list) else [raw]:
                    sym = str(item["symbol"]).strip().upper()
                    if only is not None and sym not in only:
                        continue
                    if item["doc_type"] not in DOC_TYPES:
                        raise ValueError(f"unknown doc_type {item['doc_type']!r}")
                    docs.append(
                        IngestDoc(
                            symbol=sym,
                            market=str(item.get("market") or market_of(sym)),
                            doc_type=item["doc_type"],
                            source_url=str(item["source_url"]),
                            as_of=_parse_dt(item["as_of"]),
                            text=str(item["text"]),
                        )
                    )
            elif path.suffix in (".txt", ".md"):
                rel = path.relative_to(root).parts
                if len(rel) != 3 or rel[1] not in DOC_TYPES:
                    continue
                sym = rel[0].upper()
                if only is not None and sym not in only:
                    continue
                m = _DATE_PREFIX.match(path.name)
                as_of = (
                    _parse_dt(m.group(1))
                    if m
                    else datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(tzinfo=None)
                )
                docs.append(
                    IngestDoc(
                        symbol=sym,
                        market=market_of(sym),
                        doc_type=rel[1],
                        source_url=f"file:{'/'.join(rel)}",
                        as_of=as_of,
                        text=path.read_text(encoding="utf-8"),
                    )
                )
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("rag-index: skipped unreadable or invalid file %s", path.name)
    return docs


def purge_expired(
    db: Session, index: ChunkIndex, settings: Settings | None = None, now: datetime | None = None
) -> int:
    from datetime import timedelta

    s = settings or get_settings()
    today = now or utcnow()
    ids: list[int] = []
    for doc_type, days in s.rag_doc_ttl_days.items():
        cutoff = today - timedelta(days=days)
        ids += [
            i
            for i in db.exec(
                select(col(DocChunk.id)).where(
                    col(DocChunk.doc_type) == doc_type, col(DocChunk.as_of) < cutoff
                )
            ).all()
            if i is not None
        ]
    if ids:
        index.delete(db, ids)
        for i in ids:
            row = db.get(DocChunk, i)
            if row is not None:
                db.delete(row)
        db.commit()
    return len(ids)


def run_index(
    db: Session,
    from_dir: Path,
    symbols: set[str] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[IngestStats, int]:
    """(ingest stats, purged chunk count)."""
    docs = load_dir(from_dir, symbols)
    index = ChunkIndex.for_session(db)
    stats = ingest_documents(db, docs, index, settings)
    return stats, purge_expired(db, index, settings, now)
