"""Pick the OCR provider: Gemini only if GEMINI_API_KEY is set, else Tesseract heb+eng."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.base import OcrProvider
from app.providers.ocr.gemini import GeminiProvider
from app.providers.ocr.tesseract import TesseractProvider


def get_ocr_provider(settings: Settings | None = None) -> OcrProvider:
    s = settings or get_settings()
    if s.gemini_api_key:
        return GeminiProvider(s)
    return TesseractProvider(s)
