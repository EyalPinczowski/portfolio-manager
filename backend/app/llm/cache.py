"""Response cache keyed by the hash of the input (role, prompts, output schema).

Only validated LLM answers are stored, never template results (a later successful call should be
able to replace them) and never the input itself, only its hash. Lives in the DB so every process
shares it and it survives restarts (a company profile is reused for weeks).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.llm.ledger import SessionFactory, _default_factory
from app.models import LlmCache
from app.timeutil import utcnow


def input_hash(role: str, system: str, prompt: str, schema: dict[str, Any] | None) -> str:
    payload = json.dumps(
        {"role": role, "system": system, "prompt": prompt, "schema": schema},
        sort_keys=True,
        ensure_ascii=False,
    )
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
