"""The DB-backed token bucket and usage ledger under two concurrent workers.

Each worker has its own engine and sessions, like a separate process would (SQLite file in the
default run, Postgres when the suite runs with DATABASE_URL pointing at it)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.config import Settings, get_settings
from app.db import make_engine
from app.llm import TokenBucket, record_usage, usage_for_day
from app.timeutil import utcnow

S = Settings(_env_file=None)


def worker_factory() -> Callable[[], Session]:
    """A session factory on a brand-new engine (own connection pool = own 'process')."""
    engine = make_engine(get_settings().database_url)
    return lambda: Session(engine)


def run_two(fn: Callable[[Callable[[], Session]], None]) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def target() -> None:
        factory = worker_factory()
        barrier.wait()
        try:
            fn(factory)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=target) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def test_bucket_starts_full_and_empties(env: None) -> None:
    b = TokenBucket(S)
    now = utcnow()
    got = [b.try_acquire("gemini", now) for _ in range(10)]
    assert got == [True] * 8 + [False] * 2  # 8 requests per minute
    assert b.available("gemini", now) < 1.0


def test_bucket_refills_over_time_and_caps_at_capacity(env: None) -> None:
    b = TokenBucket(S)
    t0 = utcnow()
    for _ in range(8):
        assert b.try_acquire("groq", t0)
    assert not b.try_acquire("groq", t0 + timedelta(seconds=3))  # 0.4 tokens
    assert b.try_acquire("groq", t0 + timedelta(seconds=8))  # 1.07 tokens
    assert not b.try_acquire("groq", t0 + timedelta(seconds=8))
    far = t0 + timedelta(hours=2)
    assert b.available("groq", far) == pytest.approx(8.0)
    assert [b.try_acquire("groq", far) for _ in range(9)].count(True) == 8


def test_buckets_are_per_provider(env: None) -> None:
    b = TokenBucket(S, capacity=1)
    now = utcnow()
    assert b.try_acquire("gemini", now) and not b.try_acquire("gemini", now)
    assert b.try_acquire("groq", now)


def test_an_older_clock_never_creates_tokens(env: None) -> None:
    b = TokenBucket(S, capacity=1)
    now = utcnow()
    assert b.try_acquire("gemini", now)
    assert not b.try_acquire("gemini", now - timedelta(minutes=5))


def test_two_workers_share_one_bucket_exactly(env: None) -> None:
    """8 tokens, 2 workers x 12 attempts at the same instant: exactly 8 succeed in total."""
    now = utcnow()
    granted: list[int] = []
    lock = threading.Lock()

    def work(factory: Callable[[], Session]) -> None:
        bucket = TokenBucket(S, session_factory=factory)
        n = sum(bucket.try_acquire("gemini", now) for _ in range(12))
        with lock:
            granted.append(n)

    run_two(work)
    assert sum(granted) == 8, granted
    assert TokenBucket(S).available("gemini", now) < 1.0


def test_two_workers_racing_to_create_the_bucket_row(env: None) -> None:
    now = utcnow()
    granted: list[bool] = []
    lock = threading.Lock()

    def work(factory: Callable[[], Session]) -> None:
        ok = TokenBucket(S, session_factory=factory, capacity=1).try_acquire("fresh", now)
        with lock:
            granted.append(ok)

    run_two(work)
    assert sorted(granted) == [False, True]


def test_two_workers_never_lose_a_ledger_increment(env: None) -> None:
    models = [f"m{i}" for i in range(6)]  # many first-of-the-day inserts race each other

    def work(factory: Callable[[], Session]) -> None:
        for model in models:
            for _ in range(15):
                record_usage(
                    "gemini", model, requests=1, tokens=3, fallbacks=1, session_factory=factory
                )

    run_two(work)
    rows = usage_for_day()
    assert sorted(r.model for r in rows) == models
    for r in rows:
        assert (r.requests, r.tokens, r.fallbacks) == (30, 90, 30)


def test_usage_is_per_provider_model_and_day(env: None) -> None:
    today = utcnow().date()
    record_usage("gemini", "a", requests=1, tokens=5)
    record_usage("gemini", "a", requests=2, tokens=7, fallbacks=1)
    record_usage("groq", "a", requests=1)
    record_usage("gemini", "a", requests=1, day=today - timedelta(days=1))
    rows = {(r.provider, r.model): r for r in usage_for_day(today)}
    assert (rows[("gemini", "a")].requests, rows[("gemini", "a")].tokens) == (3, 12)
    assert rows[("gemini", "a")].fallbacks == 1 and rows[("groq", "a")].requests == 1
    assert [r.requests for r in usage_for_day(today - timedelta(days=1))] == [1]
