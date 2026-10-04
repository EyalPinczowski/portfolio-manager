"""Block 2.0-E item 4: Meitav Trade redaction: a 14 % header and the TASE number stays readable."""

from __future__ import annotations

import io

from PIL import Image

from app.config import Settings
from app.importer.layouts import header_fraction_for
from app.importer.redact import WordBox, redact_image

S = Settings()


def _img(w: int = 300, h: int = 500) -> bytes:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for x in range(w):
        for y in range(h):
            if (x // 4 + y // 4) % 2 == 0:
                img.putpixel((x, y), (0, 0, 0))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _changed(src: bytes, out: bytes, box: tuple[int, int, int, int]) -> bool:
    a, b = Image.open(io.BytesIO(src)).convert("RGB"), Image.open(io.BytesIO(out)).convert("RGB")
    return a.crop(box).tobytes() != b.crop(box).tobytes()


def test_header_fraction_is_per_layout() -> None:
    assert header_fraction_for("generic", S) == 0.12
    assert header_fraction_for("meitav_trade", S) == 0.14


MEITAV_BOXES = [
    WordBox("NASDAQ", 20, 150, 60, 20),
    WordBox("•", 85, 150, 10, 20),
    WordBox("ACME", 100, 150, 50, 20),
    WordBox("TLV", 20, 250, 40, 20),
    WordBox("•", 65, 250, 10, 20),
    WordBox("1234567", 80, 250, 90, 20),  # a TASE security number: kept readable
    WordBox("0123456789", 20, 350, 120, 20),  # an account number somewhere else: blurred
]


def test_meitav_header_is_14_percent_and_the_tlv_number_is_not_blurred() -> None:
    src = _img()
    out, report = redact_image(src, S, word_boxes=MEITAV_BOXES)
    assert report.layout == "meitav_trade"
    assert report.boxes_blurred == 1 and report.boxes_kept == 1
    assert _changed(src, out, (0, 0, 300, 70))  # top 14 % of 500 px = 70 px
    assert _changed(src, out, (0, 60, 300, 69))  # beyond the generic 12 % (60 px)
    assert not _changed(src, out, (80, 250, 170, 270))  # the TLV number
    assert _changed(src, out, (20, 350, 140, 370))  # the account number


def test_generic_layout_keeps_12_percent_and_blurs_every_long_digit_run() -> None:
    src = _img()
    boxes = [WordBox("TLV", 20, 250, 40, 20), WordBox("1234567", 80, 250, 90, 20)]
    out, report = redact_image(src, S, word_boxes=boxes, layout="generic")
    assert report.layout == "generic" and report.boxes_blurred == 1 and report.boxes_kept == 0
    assert _changed(src, out, (80, 250, 170, 270))
    assert not _changed(src, out, (0, 62, 300, 70))  # below 12 %


def test_a_long_number_next_to_a_tlv_anchor_that_is_not_the_anchors_number_is_blurred() -> None:
    src = _img()
    boxes = [
        WordBox("NASDAQ", 20, 150, 60, 20),
        WordBox("•", 85, 150, 10, 20),
        WordBox("ACME", 100, 150, 50, 20),
        WordBox("TLV", 20, 250, 40, 20),
        WordBox("•", 65, 250, 10, 20),
        WordBox("1234567", 80, 250, 90, 20),
        WordBox("987654321", 180, 250, 100, 20),  # same line, not the TASE number
    ]
    out, report = redact_image(src, S, word_boxes=boxes)
    assert report.boxes_kept == 1 and report.boxes_blurred == 1
    assert _changed(src, out, (180, 250, 280, 270))


def test_a_reversed_right_to_left_line_is_recognised() -> None:
    src = _img()
    boxes = [
        WordBox("1234567", 20, 250, 90, 20),  # visual order: the number is left of the anchor
        WordBox("•", 115, 250, 10, 20),
        WordBox("TLV", 130, 250, 40, 20),
    ]
    _, report = redact_image(src, S, word_boxes=boxes)
    assert report.layout == "meitav_trade" and report.boxes_kept == 1
