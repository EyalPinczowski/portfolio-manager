"""Tiny TTL cache and rate-limit aware retry helper."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class TTLCache[T]:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_seconds
        self._clock = clock
        self._data: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        item = self._data.get(key)
        if item is None:
            return None
        stamp, value = item
        if self._clock() - stamp > self.ttl:
            return None
        return value

    def get_stale(self, key: str) -> T | None:
        """Return the value even if expired (used when the upstream is failing)."""
        item = self._data.get(key)
        return None if item is None else item[1]

    def set(self, key: str, value: T) -> None:
        self._data[key] = (self._clock(), value)

    def clear(self) -> None:
        self._data.clear()


def is_rate_limited(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return (
        "429" in text or "too many requests" in text or "ratelimit" in text or "rate limit" in text
    )


def retry_with_backoff[T](
    fn: Callable[[], T],
    *,
    retries: int,
    base_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying with exponential backoff only on 429-style errors."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if not is_rate_limited(exc) or attempt >= retries:
                raise
            delay = base_seconds * (2**attempt)
            log.warning("rate limited (%s); retrying in %.1fs", exc, delay)
            sleep(delay)
            attempt += 1
