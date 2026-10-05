"""DB-backed usage ledger and token bucket, safe for several processes.

Both are plain SQL on small tables, so SQLite (dev) and Postgres (cloud) behave the same:

- `record_usage` increments `llm_usage` with `SET col = col + n`, never read-modify-write; the first
  call of a day inserts the row and a lost insert race falls back to the update.
- `TokenBucket.try_acquire` refills and takes one token with a compare-and-swap on `version`: when
  another process got there first the update matches no row and the loop reads again.

Every step runs in its own short transaction on its own session (`session_factory`), so a read
snapshot is never upgraded to a write (that fails at once on SQLite) and nothing here touches the
caller's transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import LlmBucket, LlmUsage
from app.timeutil import utcnow

SessionFactory = Callable[[], Session]


def _default_factory() -> Session:
    from app.db import new_session

    return new_session()


def record_usage(
    provider: str,
    model: str,
    *,
    requests: int = 0,
    tokens: int = 0,
    fallbacks: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_cached: int = 0,
    cache_hits: int = 0,
    day: date | None = None,
    session_factory: SessionFactory | None = None,
) -> None:
    """Add to today's counters for (provider, model). Atomic across processes."""
    factory = session_factory or _default_factory
    day = day or utcnow().date()
    key = (
        col(LlmUsage.provider) == provider,
        col(LlmUsage.model) == model,
        col(LlmUsage.day) == day,
    )
    bump = {
        "requests": col(LlmUsage.requests) + requests,
        "tokens": col(LlmUsage.tokens) + tokens,
        "fallbacks": col(LlmUsage.fallbacks) + fallbacks,
        "tokens_in": col(LlmUsage.tokens_in) + tokens_in,
        "tokens_out": col(LlmUsage.tokens_out) + tokens_out,
        "tokens_cached": col(LlmUsage.tokens_cached) + tokens_cached,
        "cache_hits": col(LlmUsage.cache_hits) + cache_hits,
    }
    for _ in range(3):
        with factory() as db:
            if db.connection().execute(update(LlmUsage).where(*key).values(**bump)).rowcount:
                db.commit()
                return
            db.add(
                LlmUsage(
                    provider=provider,
                    model=model,
                    day=day,
                    requests=requests,
                    tokens=tokens,
                    fallbacks=fallbacks,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_cached=tokens_cached,
                    cache_hits=cache_hits,
                )
            )
            try:
                db.commit()
                return
            except IntegrityError:  # another process inserted the row first: update it instead
                db.rollback()
    raise RuntimeError("llm_usage: could not record usage")


@dataclass(frozen=True)
class UsageRow:
    provider: str
    model: str
    day: date
    requests: int
    tokens: int
    fallbacks: int
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cache_hits: int = 0


def usage_for_day(
    day: date | None = None, session_factory: SessionFactory | None = None
) -> list[UsageRow]:
    day = day or utcnow().date()
    with (session_factory or _default_factory)() as db:
        rows = db.exec(select(LlmUsage).where(LlmUsage.day == day)).all()
        return [
            UsageRow(
                r.provider,
                r.model,
                r.day,
                r.requests,
                r.tokens,
                r.fallbacks,
                r.tokens_in or 0,
                r.tokens_out or 0,
                r.tokens_cached or 0,
                r.cache_hits or 0,
            )
            for r in rows
            if not r.provider.startswith(QUOTA_PREFIX)
        ]


# Per-user daily call counts live in the same table under a reserved provider name, so they get the
# same atomic increment and need no schema change: ("quota:user:5", "*", day).
QUOTA_PREFIX = "quota:"


def requests_today(
    provider: str, day: date | None = None, session_factory: SessionFactory | None = None
) -> int:
    """Requests made to `provider` today, summed over its models."""
    day = day or utcnow().date()
    with (session_factory or _default_factory)() as db:
        rows = db.exec(
            select(LlmUsage.requests).where(LlmUsage.provider == provider, LlmUsage.day == day)
        ).all()
        return int(sum(rows))


def quota_used(
    key: str, day: date | None = None, session_factory: SessionFactory | None = None
) -> int:
    return requests_today(QUOTA_PREFIX + key, day, session_factory)


def quota_add(
    key: str, n: int = 1, day: date | None = None, session_factory: SessionFactory | None = None
) -> None:
    record_usage(QUOTA_PREFIX + key, "*", requests=n, day=day, session_factory=session_factory)


class TokenBucket:
    """`capacity` requests per minute per provider (default `Settings.llm_requests_per_minute`)."""

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: SessionFactory | None = None,
        capacity: int | None = None,
    ) -> None:
        s = settings or get_settings()
        self.capacity = float(capacity if capacity is not None else s.llm_requests_per_minute)
        self.refill_per_second = self.capacity / 60.0
        self.retries = s.llm_bucket_cas_retries
        self._factory = session_factory or _default_factory

    def _refilled(
        self, tokens: float, since: datetime, now: datetime, capacity: float | None = None
    ) -> float:
        cap = self.capacity if capacity is None else capacity
        elapsed = max(0.0, (now - since).total_seconds())
        return min(cap, tokens + elapsed * cap / 60.0)

    def try_acquire(
        self,
        provider: str,
        now: datetime | None = None,
        capacity: int | None = None,
        cost: float = 1.0,
    ) -> bool:
        """Take `cost` tokens (one request by default) if there are that many. False when the bucket is empty (or contention never ends).

        `provider` is any bucket key: a provider name, or a sub-bucket such as `gemini|user:5` or
        `gemini|role:news` with its own `capacity` (per minute). A bucket may count something else than
        requests, e.g. `groq|tpm` counts estimated tokens per minute; a `cost` above `capacity`
        can never be taken."""
        now = now or utcnow()
        cap = float(capacity) if capacity is not None else self.capacity
        for _ in range(self.retries):
            with self._factory() as db:
                row = (
                    db.connection()
                    .execute(
                        select(
                            col(LlmBucket.tokens), col(LlmBucket.version), col(LlmBucket.updated_at)
                        ).where(col(LlmBucket.provider) == provider)
                    )
                    .first()
                )
                db.rollback()  # end the read transaction before writing
                if row is None:
                    db.add(LlmBucket(provider=provider, tokens=cap, version=0, updated_at=now))
                    try:
                        db.commit()
                    except IntegrityError:  # someone else created it: read again
                        db.rollback()
                    continue
                tokens, version, updated_at = row
                # Never move the clock backwards (a slow process with an older `now`).
                stamp = max(now, updated_at)
                available = self._refilled(tokens, updated_at, stamp, cap)
                if available < cost:
                    return False
                swapped = (
                    db.connection()
                    .execute(
                        update(LlmBucket)
                        .where(
                            col(LlmBucket.provider) == provider, col(LlmBucket.version) == version
                        )
                        .values(tokens=available - cost, version=version + 1, updated_at=stamp)
                    )
                    .rowcount
                )
                if swapped:
                    db.commit()
                    return True
                db.rollback()  # lost the race: read the new state and try again
        return False

    def available(self, provider: str, now: datetime | None = None) -> float:
        now = now or utcnow()
        with self._factory() as db:
            row = (
                db.connection()
                .execute(
                    select(col(LlmBucket.tokens), col(LlmBucket.updated_at)).where(
                        col(LlmBucket.provider) == provider
                    )
                )
                .first()
            )
            db.rollback()
        if row is None:
            return self.capacity
        return self._refilled(row[0], row[1], max(now, row[1]))
