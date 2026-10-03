"""argon2id password hashing with OWASP parameters (m=19 MiB, t=2, p=1) and bounded concurrency.

Each hash needs ~19 MiB, so the number of concurrent hashes is capped (a Semaphore) to keep the
512 MB host safe under a login flood. Rate limits run before any hashing (see api/auth.py).
Hashes made with older parameters are upgraded on login (`needs_rehash`).
"""

from __future__ import annotations

import threading
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings


@lru_cache
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_kib,
        parallelism=s.argon2_parallelism,
    )  # argon2id by default


@lru_cache
def _gate() -> threading.Semaphore:
    return threading.Semaphore(max(1, get_settings().argon2_max_concurrent))


@lru_cache
def _dummy_hash() -> str:
    # Hash of a random password, verified against when the email is unknown (timing equalisation).
    return hash_password("not-a-real-password-for-timing")


def reset_hasher_cache() -> None:
    """Re-read the settings (tests that change the argon2 parameters)."""
    _hasher.cache_clear()
    _gate.cache_clear()
    _dummy_hash.cache_clear()


def hash_password(password: str) -> str:
    with _gate():
        return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        with _gate():
            return _hasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_verify(password: str) -> None:
    verify_password(_dummy_hash(), password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher().check_needs_rehash(password_hash)
