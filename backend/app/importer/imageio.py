"""Safe image decoding for the screenshot importer (in memory only).

- the format is decided from the first bytes (PNG / JPEG / WebP), never from a header the client sent;
- a decompression-bomb guard: `Image.MAX_IMAGE_PIXELS`, the Pillow bomb warning as an error, and our
  own check of the header dimensions *before* any pixel is decoded or converted;
- tall / wide images are downscaled strip by strip so a scroll-stitched screenshot never needs a
  second full-size copy.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image

from app.config import Settings, get_settings

ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")
CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}

# Module-level default so any other Image.open in the process is guarded too.
Image.MAX_IMAGE_PIXELS = 25_000_000


@dataclass(frozen=True)
class ImageRejectedError(Exception):
    """The upload cannot be used. `status` is the HTTP status the API should answer with."""

    status: int
    message: str

    def __str__(self) -> str:
        return self.message


def sniff_image_type(data: bytes | bytearray) -> str | None:
    """'image/png' | 'image/jpeg' | 'image/webp' from the magic bytes, else None."""
    head = bytes(data[:12])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def open_checked(data: bytes, settings: Settings | None = None) -> Image.Image:
    """Open lazily (header only) and reject anything over the pixel budget."""
    s = settings or get_settings()
    Image.MAX_IMAGE_PIXELS = s.max_image_pixels
    too_big = ImageRejectedError(413, "The image has too many pixels; crop or shrink it")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=list(ALLOWED_FORMATS))
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise too_big from exc
    except Exception as exc:  # corrupt / truncated / unsupported
        raise ImageRejectedError(400, "The file is not a readable image") from exc
    width, height = img.size
    if width <= 0 or height <= 0 or width * height > s.max_image_pixels:
        img.close()
        raise too_big
    return img


def to_rgb_bounded(img: Image.Image, settings: Settings | None = None) -> Image.Image:
    """Decode and convert to RGB, downscaling to `import_max_side_px` in horizontal strips.

    May return `img` itself (an RGB image within bounds): the caller then must not close it before
    it is done with the result.
    """
    s = settings or get_settings()
    width, height = img.size
    try:
        img.load()
    except Exception as exc:
        raise ImageRejectedError(400, "The file is not a readable image") from exc
    side = max(width, height)
    if side <= s.import_max_side_px:
        # Already RGB: reuse the decoded pixels instead of making a second full-size copy (a 25 MP
        # image is 75 MB; a copy would double the memory spike on a 512 MB host).
        return img if img.mode == "RGB" else img.convert("RGB")
    scale = s.import_max_side_px / side
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    out = Image.new("RGB", (new_w, new_h))
    rows = max(1, s.import_strip_rows)
    for top in range(0, new_h, rows):
        bottom = min(new_h, top + rows)
        # the source band that maps to output rows [top, bottom)
        src_top, src_bottom = round(top / scale), min(height, round(bottom / scale))
        if src_bottom <= src_top:
            continue
        strip = img.crop((0, src_top, width, src_bottom)).convert("RGB")
        strip = strip.resize((new_w, bottom - top), Image.Resampling.LANCZOS)
        out.paste(strip, (0, top))
        strip.close()
    return out


def wipe(buf: bytearray) -> None:
    """Overwrite and release a mutable buffer that held image bytes."""
    view = memoryview(buf)
    step = 1 << 16
    zeros = bytes(step)
    for i in range(0, len(buf), step):
        n = min(step, len(buf) - i)
        view[i : i + n] = zeros[:n]
    view.release()
    buf.clear()
