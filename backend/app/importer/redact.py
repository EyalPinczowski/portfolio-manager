"""Local redaction before any image leaves the server.

1. Blur the top ~12% header (account name/number lives there; configurable).
2. Blur words that contain runs of >= 6 digits (account / ID numbers) found by Tesseract word boxes.
   Thousands-separated numbers (1,234,567) are not affected because the run is broken by commas.

The image is processed in memory only and is never written to disk.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from PIL import Image, ImageFilter

from app.config import Settings, get_settings
from app.importer.imageio import open_checked, to_rgb_bounded
from app.providers.ocr.tesseract import tesseract_available

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WordBox:
    text: str
    left: int
    top: int
    width: int
    height: int


@dataclass
class RedactionReport:
    header_blurred: bool
    boxes_blurred: int
    word_boxes_available: bool


def find_word_boxes(img: Image.Image, lang: str) -> list[WordBox] | None:
    """Word boxes via Tesseract, or None if Tesseract is not installed."""
    if not tesseract_available():
        return None
    import pytesseract

    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    boxes: list[WordBox] = []
    for i, text in enumerate(data["text"]):
        if text and text.strip():
            boxes.append(
                WordBox(text, data["left"][i], data["top"][i], data["width"][i], data["height"][i])
            )
    return boxes


def _blur_region(img: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    left, top, right, bottom = box
    right, bottom = min(right, img.width), min(bottom, img.height)
    left, top = max(left, 0), max(top, 0)
    if right <= left or bottom <= top:
        return
    region = img.crop((left, top, right, bottom)).filter(ImageFilter.GaussianBlur(radius))
    # A blur alone can leave big digits legible; darken as well.
    region = Image.blend(region, Image.new("RGB", region.size, (128, 128, 128)), 0.6)
    img.paste(region, (left, top))


def redact_image(
    image_bytes: bytes,
    settings: Settings | None = None,
    word_boxes: list[WordBox] | None = None,
) -> tuple[bytes, RedactionReport]:
    """Return (redacted PNG bytes, report). `word_boxes` can be injected (tests / other OCR)."""
    s = settings or get_settings()
    with open_checked(image_bytes, s) as src:  # raises ImageRejectedError (413 / 400)
        img = to_rgb_bounded(src, s)
    available = True
    if word_boxes is None:
        try:
            word_boxes = find_word_boxes(img, s.tesseract_lang)
        except Exception as exc:
            log.warning("word-box detection failed: %s", type(exc).__name__)
            word_boxes = None
        if word_boxes is None:
            available = False
            word_boxes = []
    header_h = int(img.height * s.redact_header_fraction)
    if header_h > 0:
        _blur_region(img, (0, 0, img.width, header_h), s.redact_blur_radius)
    pattern = re.compile(rf"\d{{{s.redact_min_digit_run},}}")
    blurred = 0
    for wb in word_boxes:
        if pattern.search(wb.text):
            pad = 2
            _blur_region(
                img,
                (wb.left - pad, wb.top - pad, wb.left + wb.width + pad, wb.top + wb.height + pad),
                s.redact_blur_radius,
            )
            blurred += 1
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), RedactionReport(header_h > 0, blurred, available)
