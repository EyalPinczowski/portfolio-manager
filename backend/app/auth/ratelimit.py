"""In-memory sliding-window login rate limiter (per process; the API runs a single worker)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, window: float) -> deque[float]:
        q = self._failures[key]
        now = self._clock()
        while q and now - q[0] > window:
            q.popleft()
        return q

    def is_blocked(self, key: str, max_attempts: int, window: float) -> bool:
        with self._lock:
            return len(self._prune(key, window)) >= max_attempts

    def retry_after(self, key: str, window: float) -> int:
        with self._lock:
            q = self._prune(key, window)
            if not q:
                return 0
            return max(1, int(window - (self._clock() - q[0])))

    def record_failure(self, key: str, window: float) -> None:
        with self._lock:
            self._prune(key, window).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()
