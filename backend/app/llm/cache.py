"""Response cache keyed by the hash of the input (role, prompts, output schema).

Only validated LLM answers are stored, never template results (a later successful call should be
able to replace them) and never the input itself, only its hash. Lives in the DB so every process
shares it and it survives restarts (a company profile is reused for weeks).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.llm.ledger import SessionFactory, _default_factory
from app.models import LlmCache
from app.timeutil import utcnow


def input_hash(
    role: str,
    system: str,
    prompt: str,
    schema: dict[str, Any] | None,
    *,
    scope: str,
    provider: str,
    model: str,
    untrusted: Sequence[tuple[str, str, str]] | None = None,
) -> str:
    """The cache key. `scope` ("global" or "user:<id>") keeps one user's answer away from another's;
    provider and model are part of it, so a different model never answers from a cached answer of
    another one; `untrusted` is the fenced input as (label, source, text)."""
    payload = json.dumps(
        {
            "role": role, "system": system, "prompt": prompt, "schema": schema, "scope": scope,
            "provider": provider, "model": model, "untrusted": list(untrusted or []),
        },
        sort_keys=True,
        ensure_ascii=False,
    )  # fmt: skip
    return hashlib.sha256(payload.encode()).hexdigest()


def prompt_hash(system: str, prompt: str) -> str:
    """Short id of the prompt text, stored with every paper call that an LLM shaped."""
    return hashlib.sha256(f"{system}\n\x00\n{prompt}".encode()).hexdigest()[:16]


def model_hash(provider: str, model: str) -> str:
    return hashlib.sha256(f"{provider}:{model}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CachedAnswer:
    response: str
    provider: str
    model: str
    created_at: datetime


def get_cached(
    key: str,
    ttl_hours: float,
    now: datetime | None = None,
    session_factory: SessionFactory | None = None,
) -> CachedAnswer | None:
    now = now or utcnow()
    with (session_factory or _default_factory)() as db:
        row = db.get(LlmCache, key)
        if row is None or row.created_at < now - timedelta(hours=ttl_hours):
            return None
        return CachedAnswer(row.response, row.provider, row.model, row.created_at)


def get_cached_any(
    keys: Sequence[str],
    ttl_hours: float,
    now: datetime | None = None,
    session_factory: SessionFactory | None = None,
) -> CachedAnswer | None:
    """The first of `keys` (in the order given: the preferred provider first) that holds a fresh
    answer. Lets a validated answer from another provider or model serve the same prompt."""
    if not keys:
        return None
    now = now or utcnow()
    cutoff = now - timedelta(hours=ttl_hours)
    with (session_factory or _default_factory)() as db:
        rows = {
            r.key: r
            for r in db.exec(select(LlmCache).where(col(LlmCache.key).in_(list(keys)))).all()
            if r.created_at >= cutoff
        }
    for k in keys:
        row = rows.get(k)
        if row is not None:
            return CachedAnswer(row.response, row.provider, row.model, row.created_at)
    return None


def put_cached(
    key: str,
    role: str,
    provider: str,
    model: str,
    response: str,
    now: datetime | None = None,
    session_factory: SessionFactory | None = None,
) -> None:
    with (session_factory or _default_factory)() as db:
        db.merge(
            LlmCache(
                key=key, role=role, provider=provider, model=model, response=response,
                created_at=now or utcnow(),
            )
        )  # fmt: skip
        try:
            db.commit()
        except IntegrityError:  # another process stored the same answer first
            db.rollback()
