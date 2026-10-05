"""Tesseract OCR provider (free default, heb+eng)."""

from __future__ import annotations

import io
import shutil

from app.config import Settings, get_settings
from app.importer.preprocess import preprocess_for_ocr
from app.providers.base import OcrResult, OcrUnavailableError

ON_DEVICE_MESSAGE = (
    "Reading screenshots on the server is not available here (Tesseract is not installed). "
    "Use on-device reading instead."
)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


class TesseractProvider:
    name = "tesseract"
    third_party = False

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return tesseract_available()

    def extract(self, image_bytes: bytes) -> OcrResult:
        if not tesseract_available():
            raise OcrUnavailableError(ON_DEVICE_MESSAGE)
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            prepared = preprocess_for_ocr(img)
            try:
                text = pytesseract.image_to_string(
                    prepared, lang=self.settings.tesseract_lang, config="--psm 6"
                )
            finally:
                prepared.close()
        return OcrResult(provider=self.name, text=text)
