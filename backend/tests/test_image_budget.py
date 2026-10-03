"""Image decode: a bytes-per-pixel budget and one decode at a time (2.0-A item 5).

Verified in the Phase 1.5 re-review: three concurrent 25 MP RGBA PNGs (0.5 MB each) reached a
718 MB peak, because the pixel cap ignored bands and nothing limited concurrent decodes.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import textwrap
import threading
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app.config import Settings
from app.importer import imageio, redact
from app.importer.imageio import ImageRejectedError, open_checked


def flat_png(w: int, h: int, color_type: int, bands: int, depth: int = 8) -> bytes:
    """A valid solid PNG of any mode, streamed through zlib (never a full raw copy)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    comp = zlib.compressobj(6)
    row = b"\x00" + b"\xff" * (w * bands * depth // 8)
    parts = [comp.compress(row) for _ in range(h)]
    idat = b"".join(parts) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", w, h, depth, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


RGBA = (6, 4)  # PNG colour type, bands
RGB = (2, 3)
GRAY = (0, 1)


def test_budget_counts_bands_not_just_pixels() -> None:
    s = Settings()
    # 25 MP is within the pixel cap for every mode, but not within the byte budget for RGBA
    with pytest.raises(ImageRejectedError) as ei:
        open_checked(flat_png(5000, 5000, *RGBA), s)
    assert ei.value.status == 413
    open_checked(flat_png(3000, 3000, *RGBA), s).close()  # 9 MP RGBA is fine
    open_checked(flat_png(5000, 5000, *RGB), s).close()  # 25 MP RGB (a screenshot) is fine
    open_checked(flat_png(4500, 4500, *GRAY), s).close()  # 20 MP gray is fine (4 B/px peak)


def test_decode_peak_bytes_model() -> None:
    assert imageio.decode_peak_bytes("RGB", 3, 100) == 300  # used as is
    assert imageio.decode_peak_bytes("RGBA", 4, 100) == 700  # decoded plus the RGB copy
    assert imageio.decode_peak_bytes("CMYK", 4, 100) == 700
    assert imageio.decode_peak_bytes("L", 1, 100) == 400


def test_only_one_image_is_decoded_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    real = redact.to_rgb_bounded

    def spy(img: Image.Image, settings: Settings | None = None) -> Image.Image:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        try:
            return real(img, settings)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(redact, "to_rgb_bounded", spy)
    data = flat_png(300, 300, *RGB)
    results: list[object] = []

    def work() -> None:
        results.append(redact.redact_image(data, Settings(), word_boxes=[]))

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4 and peak == 1


def test_waiting_for_the_decode_slot_gives_up_with_503(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(image_decode_wait_seconds=0.05)
    data = flat_png(100, 100, *RGB)
    assert imageio.DECODE_SLOT.acquire(timeout=1)
    try:
        with pytest.raises(ImageRejectedError) as ei:
            redact.redact_image(data, s, word_boxes=[])
        assert ei.value.status == 503
    finally:
        imageio.DECODE_SLOT.release()


def test_three_concurrent_25mp_rgba_images_stay_under_a_bounded_peak(tmp_path: Path) -> None:
    f = tmp_path / "rgba.png"
    f.write_bytes(flat_png(5000, 5000, *RGBA))
    assert f.stat().st_size < 1024 * 1024
    code = textwrap.dedent(
        f"""
        import threading
        from app.config import Settings
        from app.importer.imageio import ImageRejectedError
        from app.importer.redact import redact_image
        data = open({str(f)!r}, "rb").read()
        s = Settings()
        out = []
        def work():
            try:
                redact_image(data, s, word_boxes=[])
                out.append("ok")
            except ImageRejectedError as e:
                out.append(e.status)
        ts = [threading.Thread(target=work) for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        hwm = [ln for ln in open("/proc/self/status") if ln.startswith("VmHWM")][0]
        print(*out, int(hwm.split()[1]) // 1024)
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent.parent,
    ).stdout.split()
    assert out[:3] == ["413", "413", "413"]
    assert int(out[3]) < 250  # MB (it was 718 MB)


def test_three_concurrent_big_but_allowed_images_decode_one_after_another(tmp_path: Path) -> None:
    f = tmp_path / "rgb.png"
    f.write_bytes(flat_png(4000, 4000, *RGB))  # 16 MP RGB: 48 MB decoded, allowed
    code = textwrap.dedent(
        f"""
        import threading
        from app.config import Settings
        from app.importer.redact import redact_image
        data = open({str(f)!r}, "rb").read()
        s = Settings()
        out = []
        def work():
            out.append(len(redact_image(data, s, word_boxes=[])[0]) > 0)
        ts = [threading.Thread(target=work) for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        hwm = [ln for ln in open("/proc/self/status") if ln.startswith("VmHWM")][0]
        print(*out, int(hwm.split()[1]) // 1024)
        """
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent.parent,
    ).stdout.split()
    assert res[:3] == ["True", "True", "True"]
    assert int(res[3]) < 300  # MB: one 48 MB decode (plus its blur copies) at a time
