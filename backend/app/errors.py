"""API errors that carry a stable machine-readable `code` next to the usual `detail` string.

Body shape: `{"detail": "<human message>", "code": "<stable_code>", ...extra}`. `detail` stays a string
like every other API error, so the frontend's generic handler keeps working; `code` is what the UI
branches on (for example `turnstile_required` shows the challenge widget).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}
        self.headers = headers


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    body = {"detail": exc.message, "code": exc.code, **exc.extra}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


class ChallengeRequiredOut(BaseModel):
    """403 body of `POST /auth/login` when a Cloudflare Turnstile token is needed."""

    detail: str
    code: Literal["turnstile_required"]
    site_key: str | None = None


class PayloadTooLargeOut(BaseModel):
    detail: str
