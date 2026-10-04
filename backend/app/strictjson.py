"""One strict JSON parser for every request body: NaN, Infinity and 1e999 are rejected (422).

Python's `json` accepts `NaN`/`Infinity` and turns `1e999` into `inf`; pydantic would then store it
(Postgres refuses NaN in JSON columns, SQLite stores it, the summary breaks). Every router uses
`StrictJsonRoute`, so the body is parsed here before any model sees it. Models additionally set
`allow_inf_nan=False` on their numeric fields.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response, status
from fastapi.routing import APIRoute

from app.middleware import BodyTooLargeError


class _NonFiniteError(json.JSONDecodeError):
    """Raised while decoding; FastAPI turns a `JSONDecodeError` into a 422."""


def _reject_constant(name: str) -> Any:
    raise _NonFiniteError(f"{name} is not allowed", name, 0)


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _NonFiniteError("number out of range", text, 0)
    return value


def strict_loads(data: str | bytes) -> Any:
    return json.loads(data, parse_constant=_reject_constant, parse_float=_parse_float)


class StrictJsonRequest(Request):
    async def body(self) -> bytes:
        # The size-limit middleware aborts a streamed body that grows past the limit by raising from
        # `receive`. FastAPI would turn that into a generic 400, so answer 413 here.
        try:
            return await super().body()
        except BodyTooLargeError:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, "Request body is too large"
            ) from None

    async def json(self) -> Any:
        if not hasattr(self, "_json"):
            self._json = strict_loads(await self.body())
        return self._json


class StrictJsonRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            return await original(StrictJsonRequest(request.scope, request.receive))

        return handler
