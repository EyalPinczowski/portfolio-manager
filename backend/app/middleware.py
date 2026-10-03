"""Pure-ASGI middlewares: request body size limit (and, further down, security headers)."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.config import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

UPLOAD_PATH_RE = re.compile(r"^/api/portfolios/\d+/imports/?$")


class BodyTooLargeError(Exception):
    """Raised from `receive` once a streamed body goes over the limit."""


def body_limit_for(method: str, path: str, settings: Settings) -> int | None:
    """Max body bytes for a request, or None when the route is not limited (non-/api)."""
    if not path.startswith("/api"):
        return None
    if method == "POST" and UPLOAD_PATH_RE.match(path):
        return settings.max_upload_bytes
    return settings.max_body_bytes


async def _send_json(
    send: Send, status: int, detail: str, extra: list[tuple[bytes, bytes]]
) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"connection", b"close"),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Rejects an over-limit `Content-Length` with 413 before reading anything, and counts the
    streamed bytes when there is no (or a wrong) header. Runs before routing and auth, so an
    unauthenticated client can never make the server buffer a large body."""

    def __init__(self, app: ASGIApp, settings_factory: Callable[[], Settings]) -> None:
        self.app = app
        self._settings = settings_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit_for(scope["method"], scope["path"], self._settings())
        if limit is None:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _send_json(send, 400, "Invalid Content-Length", [])
                    return
                if declared > limit:
                    await _send_json(send, 413, "Request body is too large", [])
                    return
        seen = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLargeError:
            if not started:
                await _send_json(send, 413, "Request body is too large", [])


API_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware:
    """Adds the response headers every API answer must carry (also errors and 413s).

    - `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` on every response.
    - On `/api/*`: `Cache-Control: no-store` (account data is never cached by the browser or a
      proxy) and a CSP of `default-src 'none'; frame-ancestors 'none'` (an API never serves
      scripts, styles or frames; the interactive docs page, dev only, is exempt).
    - `Strict-Transport-Security` when the deployment is secure (`cookie_secure`).
    A header the route already set is left alone.
    """

    def __init__(self, app: ASGIApp, settings_factory: Callable[[], Settings]) -> None:
        self.app = app
        self._settings = settings_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        settings = self._settings()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                add: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if path.startswith("/api"):
                    add.append((b"cache-control", b"no-store"))
                    if not path.startswith("/api/docs"):
                        add.append((b"content-security-policy", API_CSP.encode()))
                if settings.cookie_secure:
                    add.append((b"strict-transport-security", HSTS.encode()))
                for name, value in add:
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
