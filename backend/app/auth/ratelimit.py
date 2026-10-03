"""In-memory rate limiting (per process; the API runs a single worker).

Two shapes, both with bounded LRU storage so a flood of distinct keys cannot grow memory:

- `BackoffLimiter`: failure counting with exponential backoff (login, per IP and per email).
  After `free_attempts` failures the key is blocked for base * 2**(n - free_attempts) seconds
  (capped at `window`), counted from the last failure. Failures are forgotten after `window`.
  A hard lock is never used, so an attacker cannot lock a victim out for long.
- `CountLimiter`: N events per window (signup, upload). Every attempt counts.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.config import Settings

MAX_KEYS = 10_000


class _LruMap[V]:
    def __init__(self, max_keys: int) -> None:
        self._max = max_keys
        self._data: OrderedDict[str, V] = OrderedDict()

    def get(self, key: str) -> V | None:
        v = self._data.get(key)
        if v is not None:
            self._data.move_to_end(key)
        return v

    def put(self, key: str, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def pop(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class BackoffLimiter:
    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_KEYS
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: _LruMap[tuple[int, float]] = _LruMap(max_keys)  # key -> (failures, last)

    def _live(self, key: str, window: float) -> tuple[int, float] | None:
        st = self._state.get(key)
        if st is not None and self._clock() - st[1] > window:
            self._state.pop(key)
            return None
        return st

    @staticmethod
    def _delay(failures: int, free_attempts: int, base: float, window: float) -> float:
        if failures < free_attempts:
            return 0.0
        return float(min(window, base * (2 ** (failures - free_attempts))))

    def retry_after(self, key: str, free_attempts: int, base: float, window: float) -> int:
        """Seconds until the key may try again (0 if it may try now)."""
        with self._lock:
            st = self._live(key, window)
            if st is None:
                return 0
            wait = self._delay(st[0], free_attempts, base, window) - (self._clock() - st[1])
            return max(1, int(wait) + 1) if wait > 0 else 0

    def record_failure(self, key: str, window: float) -> None:
        with self._lock:
            st = self._live(key, window)
            self._state.put(key, ((st[0] if st else 0) + 1, self._clock()))

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key)

    def clear(self) -> None:
        with self._lock:
            self._state.clear()

    def __len__(self) -> int:
        return len(self._state)


class CountLimiter:
    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_KEYS
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: _LruMap[deque[float]] = _LruMap(max_keys)

    def hit(self, key: str, limit: int, window: float) -> int:
        """Count one attempt. Returns 0 if allowed, else the Retry-After seconds."""
        with self._lock:
            now = self._clock()
            q = self._hits.get(key)
            if q is None:
                q = deque()
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                self._hits.put(key, q)
                return max(1, int(window - (now - q[0])) + 1)
            q.append(now)
            self._hits.put(key, q)
            return 0

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def __len__(self) -> int:
        return len(self._hits)


login_limiter = BackoffLimiter()
signup_limiter = CountLimiter()
upload_limiter = CountLimiter()
import_edit_limiter = CountLimiter()


def clear_all_limiters() -> None:
    login_limiter.clear()
    signup_limiter.clear()
    upload_limiter.clear()
    import_edit_limiter.clear()


def too_many(retry_after: int, what: str = "Too many requests. Try again later.") -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, what, headers={"Retry-After": str(retry_after)}
    )


def enforce_limit(limiter: CountLimiter, key: str, limit: int, window: float) -> None:
    wait = limiter.hit(key, limit, window)
    if wait:
        raise too_many(wait)


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's IP. A proxy header (e.g. CF-Connecting-IP) is trusted only when
    `trusted_proxy_header` is set and, if `trusted_proxy_cidrs` is set, the TCP peer is inside one
    of those networks. Otherwise (the default) the header is ignored: it is client-controlled."""
    peer = request.client.host if request.client else "unknown"
    header = settings.trusted_proxy_header
    if not header:
        return peer
    if settings.trusted_proxy_cidrs:
        try:
            addr = ipaddress.ip_address(peer)
        except ValueError:
            return peer
        if not any(
            addr in ipaddress.ip_network(c, strict=False) for c in settings.trusted_proxy_cidrs
        ):
            return peer
    raw = request.headers.get(header)
    if raw:
        parsed = _valid_ip(raw.split(",")[0])
        if parsed:
            return parsed
    return peer
