"""Gemini vision OCR provider with a JSON response schema (only if GEMINI_API_KEY is set)."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.providers.base import OcrResult, OcrRow, OcrUnavailableError

log = logging.getLogger(__name__)

PROMPT = (
    "This is a screenshot of a brokerage holdings table (Hebrew and/or English). "
    "Extract one object per holding row. name: the security name exactly as shown. "
    "symbol: ticker if visible else null. tase_number: the security number if visible else null. "
    "quantity, price (as displayed), value (total market value as displayed), cost (average cost "
    "or total cost per share as displayed, null if absent). currency: ILS, USD or null. "
    "unit: 'agorot' if prices are shown in agorot (אגורות / אג'), 'ILS' if shekels, 'USD' if "
    "dollars, else null. Use plain numbers (no thousands separators). Do not invent rows."
)


class _Rows(BaseModel):
    rows: list[OcrRow]


class GeminiProvider:
    name = "gemini"
    third_party = True  # the image leaves our infrastructure: redaction must be complete first

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract(self, image_bytes: bytes) -> OcrResult:
        if not self.settings.gemini_api_key:
            raise OcrUnavailableError("GEMINI_API_KEY is not set")
        from google import genai
        from google.genai import types

        try:
            client = genai.Client(api_key=self.settings.gemini_api_key)
            contents: list[Any] = [
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                PROMPT,
            ]
            response: Any = client.models.generate_content(
                model=self.settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_Rows,
                    temperature=0,
                ),
            )
        except Exception as exc:
            # Log the type only: error objects from the SDK can carry request or response text.
            log.warning("gemini request failed: %s", type(exc).__name__)
            raise OcrUnavailableError(
                "The image-reading service is not available right now"
            ) from None
        return parse_gemini_json(response.text or "{}")


def parse_gemini_json(payload: str) -> OcrResult:
    """Parse the model's JSON output into rows (pure; unit-tested with fixtures).

    A bad answer raises `OcrUnavailableError` without echoing the input: a pydantic
    `ValidationError` includes `input_value`, which here is text read from the screenshot.
    """
    try:
        data = json.loads(payload)
        raw_rows = data["rows"] if isinstance(data, dict) else data
        rows = [OcrRow.model_validate(r) for r in raw_rows]
    except ValidationError as exc:
        # Locations and error types only, never the offending values.
        bad = sorted(
            {(".".join(map(str, e["loc"])), e["type"]) for e in exc.errors(include_input=False)}
        )
        log.warning("gemini returned %d invalid field(s): %s", len(bad), bad)
        raise OcrUnavailableError(
            "The image-reading service returned an unreadable answer"
        ) from None
    except (ValueError, KeyError, TypeError):
        log.warning("gemini returned an unparseable answer")
        raise OcrUnavailableError(
            "The image-reading service returned an unreadable answer"
        ) from None
    return OcrResult(provider="gemini", rows=rows)
