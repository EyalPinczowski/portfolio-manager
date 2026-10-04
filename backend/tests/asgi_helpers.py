"""Call the ASGI app directly so a test can see exactly how much of the request body was read."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AsgiResult:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    bytes_read: int = 0
    receive_calls: int = 0


def asgi_request(
    app: Any,
    method: str,
    path: str,
    chunks: list[bytes],
    headers: dict[str, str] | None = None,
    declare_length: bool = True,
    cookie: str | None = None,
) -> AsgiResult:
    out = AsgiResult()
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    if declare_length:
        hdrs["content-length"] = str(sum(len(c) for c in chunks))
    if cookie:
        hdrs["cookie"] = cookie
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
        "client": ("203.0.113.9", 5000),
        "server": ("testserver", 80),
    }
    pending = list(chunks)

    async def receive() -> dict[str, Any]:
        out.receive_calls += 1
        if pending:
            body = pending.pop(0)
            out.bytes_read += len(body)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            out.status = message["status"]
            out.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            out.body += message.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return out
