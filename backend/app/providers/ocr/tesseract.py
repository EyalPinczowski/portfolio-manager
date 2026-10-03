"""Tesseract OCR provider (free default, heb+eng)."""

from __future__ import annotations

import io
import shutil

from app.config import Settings, get_settings
from app.providers.base import OcrResult, OcrUnavailableError


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


class TesseractProvider:
    name = "tesseract"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract(self, image_bytes: bytes) -> OcrResult:
        if not tesseract_available():
            raise OcrUnavailableError("Tesseract is not installed (need tesseract-ocr + heb data)")
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            text = pytesseract.image_to_string(
                img.convert("L"), lang=self.settings.tesseract_lang, config="--psm 6"
            )
        return OcrResult(provider=self.name, text=text)
