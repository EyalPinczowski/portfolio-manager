"""Layout dispatch for OCR text (port of `frontend/lib/ocr/layouts.ts`).

A layout is detected from the text itself; anything unknown goes to the generic table parser.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from app.config import Settings, get_settings
from app.importer.meitav import detect_meitav, parse_meitav_text
from app.importer.merge import merge_screenshots
from app.importer.parse import ParsedRow, parse_ocr_result, parse_ocr_text
from app.providers.base import OcrResult

LayoutId = Literal["generic", "meitav_trade"]


def header_fraction_for(layout: LayoutId, settings: Settings | None = None) -> float:
    """How much of the top of the screenshot is app header and status bar (blurred before OCR)."""
    s = settings or get_settings()
    if layout == "meitav_trade":
        return s.redact_header_fraction_meitav_trade
    return s.redact_header_fraction


def detect_layout(text: str) -> LayoutId:
    return "meitav_trade" if detect_meitav(text) else "generic"


def parse_screenshot_text(
    text: str, layout: LayoutId | Literal["auto"] = "auto", settings: Settings | None = None
) -> tuple[LayoutId, list[ParsedRow]]:
    """OCR text of one screenshot to rows. A Meitav text with no card at all falls back to the
    generic parser."""
    s = settings or get_settings()
    chosen = detect_layout(text) if layout == "auto" else layout
    if chosen == "meitav_trade":
        rows = parse_meitav_text(text, s)
        if rows:
            return "meitav_trade", rows
    return "generic", parse_ocr_text(text, s)


def parse_screenshots(
    texts: Sequence[str], settings: Settings | None = None
) -> tuple[LayoutId, list[ParsedRow]]:
    """The OCR texts of several overlapping screenshots of one list, merged: a card in two
    screenshots is counted once (never summed), see `merge_screenshots`."""
    parsed = [parse_screenshot_text(t, "auto", settings) for t in texts]
    layout: LayoutId = next((lay for lay, _ in parsed if lay != "generic"), "generic")
    return layout, merge_screenshots([rows for _, rows in parsed], reindex=True)


def rows_from_ocr_result(result: OcrResult, settings: Settings | None = None) -> list[ParsedRow]:
    """Rows of a server-side OCR result: structured rows as they are, text by layout."""
    if result.rows is not None or not result.text:
        return parse_ocr_result(result, settings)
    return parse_screenshot_text(result.text, "auto", settings)[1]
