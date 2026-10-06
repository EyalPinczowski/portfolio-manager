"""In-memory rate limiting (per process; the API runs a single worker).

Two shapes, both with bounded LRU storage so a flood of distinct keys cannot grow memory:

- `BackoffLimiter`: failure counting with exponential backoff (login, per IP and per email).
  After `free_attempts` failures the key is blocked for base * 2**(n - free_attempts) seconds
  (capped at `window`), counted from the last failure. Failures are forgotten after `window`.
  A hard lock is never used, so an attacker cannot lock a victim out for long.
- `CountLimiter`: N events per window (signup, upload). Every attempt counts.
"""

from __future__ import annotations

import hmac
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

    def failures(self, key: str, window: float) -> int:
        """Failures currently remembered for the key."""
        with self._lock:
            st = self._live(key, window)
            return st[0] if st else 0

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

    def count(self, key: str, window: float) -> int:
        """Attempts counted inside the window, without counting a new one."""
        with self._lock:
            now = self._clock()
            q = self._hits.get(key)
            return sum(1 for t in q if now - t <= window) if q else 0

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def __len__(self) -> int:
        return len(self._hits)


login_limiter = BackoffLimiter()
signup_limiter = CountLimiter()
upload_limiter = CountLimiter()
import_edit_limiter = CountLimiter()
holding_add_limiter = CountLimiter()
telegram_code_limiter = CountLimiter()  # per user: new link codes
telegram_link_limiter = CountLimiter()  # per Telegram chat: /start attempts
admin_invite_limiter = CountLimiter()  # per admin: new invites
fund_search_limiter = CountLimiter()  # per user: fund searches (each may call GemelNet)
symbol_search_limiter = CountLimiter()  # per user: remote symbol searches
ask_limiter = CountLimiter()  # per user: ask-my-portfolio questions
committee_limiter = CountLimiter()  # per user: committee runs per hour (up to 4 LLM calls each)
committee_daily_limiter = CountLimiter()  # per user: committee runs per day


def clear_all_limiters() -> None:
    login_limiter.clear()
    signup_limiter.clear()
    upload_limiter.clear()
    import_edit_limiter.clear()
    holding_add_limiter.clear()
    telegram_code_limiter.clear()
    telegram_link_limiter.clear()
    admin_invite_limiter.clear()
    fund_search_limiter.clear()
    symbol_search_limiter.clear()
    ask_limiter.clear()
    committee_limiter.clear()
    committee_daily_limiter.clear()


def too_many(retry_after: int, what: str = "Too many requests. Try again later.") -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, what, headers={"Retry-After": str(retry_after)}
    )


def enforce_limit(limiter: CountLimiter, key: str, limit: int, window: float) -> None:
    wait = limiter.hit(key, limit, window)
    if wait:
        raise too_many(wait)


def _valid_ip(value: str) -> str | None:
    """Canonical key of an address, or None when it is not one.

    - an IPv4-mapped IPv6 address (`::ffff:203.0.113.5`) is the IPv4 address;
    - a zone id (`fe80::1%eth0`) is dropped;
    - an IPv6 address becomes its /64 network address: one subscriber usually holds a whole /64, so
      rotating the low 64 bits must not buy fresh rate-limit keys (verified probe: 60 guesses a
      window).
    """
    addr = _address(value)
    if addr is None:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
    return str(addr)


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address without zone id, brackets or IPv4-mapping (not yet bucketed)."""
    raw = value.strip().split("%", 1)[0].strip("[]")
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's IP.

    The proxy's client-IP header (`trusted_proxy_header`, e.g. `X-Client-IP`) is trusted only when
    the request also carries the shared secret in `proxy_auth_header` (constant-time compare) and,
    if `trusted_proxy_cidrs` is set, the TCP peer is inside one of those networks. Otherwise the
    optional `fallback_ip_header` (the host's own, e.g. Render's CF-Connecting-IP) and finally the
    peer address are used. A client-supplied `X-Client-IP` without the secret is ignored.

    The result is a rate-limit key: IPv4-mapped IPv6 is the IPv4 address, zone ids are removed and
    an IPv6 address is reduced to its /64 (see `_valid_ip`).
    """
    raw_peer = request.client.host if request.client else "unknown"
    peer = _valid_ip(raw_peer) or raw_peer
    header = settings.trusted_proxy_header
    secret = settings.proxy_shared_secret
    if header and secret and _peer_allowed(raw_peer, settings):
        sent = request.headers.get(settings.proxy_auth_header, "")
        if sent and hmac.compare_digest(sent.encode(), secret.encode()):
            parsed = _first_ip(request.headers.get(header))
            if parsed:
                return parsed
    # The fallback header is a plain client-controlled header unless the host overwrites it, so it
    # is trusted only when the TCP peer is inside an explicitly configured proxy network.
    if (
        settings.fallback_ip_header
        and settings.trusted_proxy_cidrs
        and _peer_allowed(raw_peer, settings)
    ):
        parsed = _first_ip(request.headers.get(settings.fallback_ip_header))
        if parsed:
            return parsed
    return peer


def _first_ip(raw: str | None) -> str | None:
    return _valid_ip(raw.split(",")[0]) if raw else None


def _peer_allowed(peer: str, settings: Settings) -> bool:
    if not settings.trusted_proxy_cidrs:
        return True
    addr = _address(peer)
    if addr is None:
        return False
    return any(addr in ipaddress.ip_network(c, strict=False) for c in settings.trusted_proxy_cidrs)
