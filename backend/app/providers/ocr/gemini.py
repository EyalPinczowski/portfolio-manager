"""Gemini vision OCR provider with a JSON response schema (only if GEMINI_API_KEY is set)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.providers.base import OcrResult, OcrRow, OcrUnavailableError

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

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract(self, image_bytes: bytes) -> OcrResult:
        if not self.settings.gemini_api_key:
            raise OcrUnavailableError("GEMINI_API_KEY is not set")
        from google import genai
        from google.genai import types

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
        return parse_gemini_json(response.text or "{}")


def parse_gemini_json(payload: str) -> OcrResult:
    """Parse the model's JSON output into rows (pure; unit-tested with fixtures)."""
    data = json.loads(payload)
    raw_rows = data["rows"] if isinstance(data, dict) else data
    rows = [OcrRow.model_validate(r) for r in raw_rows]
    return OcrResult(provider="gemini", rows=rows)
