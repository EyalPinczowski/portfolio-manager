"""Local redaction before any image leaves the server.

1. Blur the top ~12% header (account name/number lives there; configurable per layout: the
   Meitav Trade app header spans ~14%).
2. Blur words that contain runs of >= 6 digits (account / ID numbers) found by Tesseract word boxes.
   Thousands-separated numbers (1,234,567) are not affected because the run is broken by commas.
   Exception (Meitav Trade): the 6-8 digit TASE security number on a `TLV • <number>` line is not
   an account number and is needed for matching, so it is not blurred.

The image is processed in memory only and is never written to disk.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageFilter

from app.config import Settings, get_settings
from app.importer.imageio import decode_slot, open_checked, to_rgb_bounded
from app.importer.layouts import LayoutId, detect_layout, header_fraction_for
from app.importer.meitav import find_anchors
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
    layout: LayoutId = "generic"
    boxes_kept: int = 0  # digit runs left alone because they are TASE numbers on `TLV •` lines


def group_lines(word_boxes: list[WordBox]) -> list[list[WordBox]]:
    """Word boxes grouped into text lines (by vertical centre), each left to right."""
    lines: list[tuple[float, float, list[WordBox]]] = []  # (centre, height, words)
    for wb in sorted(word_boxes, key=lambda w: (w.top + w.height / 2, w.left)):
        centre = wb.top + wb.height / 2
        for i, (c, h, words) in enumerate(lines):
            if abs(centre - c) <= 0.6 * max(h, wb.height, 1):
                words.append(wb)
                lines[i] = (c, max(h, wb.height), words)
                break
        else:
            lines.append((centre, float(wb.height), [wb]))
    return [sorted(words, key=lambda w: w.left) for _, _, words in lines]


def tase_numbers_on_tlv_lines(lines: list[list[WordBox]]) -> set[int]:
    """`id()`s of word boxes that hold the security number of a `TLV • <number>` anchor."""
    keep: set[int] = set()
    for words in lines:
        text = " ".join(w.text for w in words)
        numbers = {a.ticker for a in find_anchors(text) if a.exchange == "TLV"}
        if not numbers:
            continue
        for w in words:
            if re.sub(r"\D", "", w.text) in numbers:
                keep.add(id(w))
    return keep


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
    layout: LayoutId | Literal["auto"] = "auto",
) -> tuple[bytes, RedactionReport]:
    """Return (redacted PNG bytes, report). `word_boxes` can be injected (tests / other OCR).

    `layout` picks the header fraction; "auto" detects it from the words Tesseract found (without
    word boxes the layout is unknown and the generic fraction applies).
    """
    s = settings or get_settings()
    with decode_slot(s):  # one image in memory at a time
        return _redact(image_bytes, s, word_boxes, layout)


def _redact(
    image_bytes: bytes,
    s: Settings,
    word_boxes: list[WordBox] | None,
    layout: LayoutId | Literal["auto"] = "auto",
) -> tuple[bytes, RedactionReport]:
    src = open_checked(image_bytes, s)  # raises ImageRejectedError (413 / 400)
    try:
        img = to_rgb_bounded(src, s)  # may be `src` itself: the pixels are decoded only once
    except BaseException:
        src.close()
        raise
    if img is not src:
        src.close()
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
    lines = group_lines(word_boxes)
    if layout == "auto":
        layout = detect_layout("\n".join(" ".join(w.text for w in ln) for ln in lines))
    keep = tase_numbers_on_tlv_lines(lines) if layout == "meitav_trade" else set()
    header_h = int(img.height * header_fraction_for(layout, s))
    if header_h > 0:
        _blur_region(img, (0, 0, img.width, header_h), s.redact_blur_radius)
    pattern = re.compile(rf"\d{{{s.redact_min_digit_run},}}")
    blurred = kept = 0
    for wb in word_boxes:
        if pattern.search(wb.text):
            if id(wb) in keep:
                kept += 1
                continue
            pad = 2
            _blur_region(
                img,
                (wb.left - pad, wb.top - pad, wb.left + wb.width + pad, wb.top + wb.height + pad),
                s.redact_blur_radius,
            )
            blurred += 1
    out = io.BytesIO()
    img.save(out, format="PNG")
    img.close()
    return out.getvalue(), RedactionReport(header_h > 0, blurred, available, layout, kept)
