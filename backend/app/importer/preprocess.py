"""Cheap image clean-up before Tesseract (server path). Mirror of `frontend/lib/ocr/preprocess.ts`.

Steps, all in memory and on one grayscale copy (a 3000 px side cap keeps this inside 512 MB):
1. dark-mode screens (mean luminance below `DARK_MEAN_LUMINANCE`) are inverted;
2. narrow images (width below `UPSCALE_BELOW_WIDTH`) are upscaled 2x;
3. an Otsu threshold is applied only when the histogram is clearly two-toned (otherwise the image
   is just auto-contrasted: binarising a gradient or photo destroys text).

The second, digit-only pass (whitelist `NUMERIC_WHITELIST`) on number columns is NOT done: the
server layouts (`layouts.py`) work on OCR text and carry no column positions. See `numeric_config`.
"""

from __future__ import annotations

from PIL import Image, ImageOps, ImageStat

DARK_MEAN_LUMINANCE = 110.0
UPSCALE_BELOW_WIDTH = 1000
UPSCALE_FACTOR = 2
MAX_SIDE_PX = 3000
OTSU_MIN_SEPARATION = 0.5  # between-class variance / total variance
NUMERIC_WHITELIST = "0123456789.,-%₪$"


def mean_luminance(gray: Image.Image) -> float:
    return float(ImageStat.Stat(gray).mean[0])


def otsu_threshold(gray: Image.Image) -> tuple[int, float]:
    """(threshold, separation in [0, 1]) from the 256-bin histogram."""
    hist = gray.histogram()[:256]
    total = sum(hist)
    if total == 0:
        return 128, 0.0
    sum_all = sum(i * h for i, h in enumerate(hist))
    mean_all = sum_all / total
    var_total = sum(h * (i - mean_all) ** 2 for i, h in enumerate(hist)) / total
    best_t, best_var = 128, -1.0
    w0 = 0
    sum0 = 0.0
    for t in range(256):
        w0 += hist[t]
        if w0 == 0:
            continue
        w1 = total - w0
        if w1 == 0:
            break
        sum0 += t * hist[t]
        m0, m1 = sum0 / w0, (sum_all - sum0) / w1
        between = (w0 / total) * (w1 / total) * (m0 - m1) ** 2
        if between > best_var:
            best_var, best_t = between, t
    sep = best_var / var_total if var_total > 0 else 0.0
    return best_t, max(0.0, min(1.0, sep))


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """A grayscale image ready for Tesseract. Never modifies `img`."""
    gray = img.convert("L")
    if mean_luminance(gray) < DARK_MEAN_LUMINANCE:
        gray = ImageOps.invert(gray)
    w, h = gray.size
    if w < UPSCALE_BELOW_WIDTH and max(w, h) * UPSCALE_FACTOR <= MAX_SIDE_PX:
        gray = gray.resize((w * UPSCALE_FACTOR, h * UPSCALE_FACTOR), Image.Resampling.LANCZOS)
    threshold, separation = otsu_threshold(gray)
    if separation >= OTSU_MIN_SEPARATION:
        return gray.point(lambda p: 255 if p > threshold else 0, mode="L")
    return ImageOps.autocontrast(gray)
