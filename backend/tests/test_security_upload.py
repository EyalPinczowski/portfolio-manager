"""Screenshot rule (docs/security.md section 1): raw body, bounded, in memory, stock rows only."""

from __future__ import annotations

import io
import logging
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import zlib
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from app.db import new_session
from app.importer import imageio, service
from app.importer.imageio import ImageRejectedError
from app.models import ImportDraft
from app.providers.base import OcrResult
from app.providers.registry import Providers, set_providers
from app.timeutil import utcnow
from tests.asgi_helpers import asgi_request
from tests.conftest import FakeHistory, FakeOcr, FakeQuotes, png_bytes

SignupFn = Callable[..., TestClient]
OCR_STOCKS = "שם נייר כמות שער (אג') שווי\nטבע 1,000 6,500 65,000\nלאומי 500 3,200 16,000\n"
PNG = {"Content-Type": "image/png"}


def setup_user(signup: SignupFn, consent: bool = True) -> tuple[TestClient, int]:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"]
    if consent:
        c.post("/api/auth/consent/ocr")
    return c, pid


def noisy_png(w: int, h: int) -> bytes:
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def bomb_png(w: int = 12000, h: int = 12000) -> bytes:
    """A valid, tiny PNG that would decode to w*h pixels (built without allocating them)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    comp = zlib.compressobj(9)
    row = bytes(w + 1)
    idat = b"".join(comp.compress(row) for _ in range(h)) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ---------------------------------------------------------------- format and type
def test_upload_must_be_a_raw_image_body_not_multipart(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    r = c.post(
        f"/api/portfolios/{pid}/imports", files={"file": ("s.png", png_bytes(), "image/png")}
    )
    assert r.status_code == 415
    r = c.post(
        f"/api/portfolios/{pid}/imports",
        content=png_bytes(),
        headers={"Content-Type": "text/plain"},
    )
    assert r.status_code == 415


def test_magic_bytes_decide_not_the_header(signup: SignupFn, ocr_text: dict[str, str]) -> None:
    ocr_text["text"] = OCR_STOCKS
    c, pid = setup_user(signup)
    gif = b"GIF89a" + bytes(100)
    assert c.post(f"/api/portfolios/{pid}/imports", content=gif, headers=PNG).status_code == 415
    ok_types = {
        "image/png": png_bytes(),
        "image/jpeg": _encode("JPEG"),
        "image/webp": _encode("WEBP"),
    }
    for ctype, data in ok_types.items():
        r = c.post(f"/api/portfolios/{pid}/imports", content=data, headers={"Content-Type": ctype})
        assert r.status_code == 201, (ctype, r.text)


def _encode(fmt: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (200, 300), (255, 255, 255)).save(out, format=fmt)
    return out.getvalue()


# ---------------------------------------------------------------- body size, auth first
def test_declared_oversize_is_413_before_reading_anything(signup: SignupFn) -> None:
    from app.main import create_app

    c, pid = setup_user(signup)
    app = create_app()
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    big = 9 * 1024 * 1024  # over max_upload_bytes (8 MiB)
    res = asgi_request(
        app,
        "POST",
        f"/api/portfolios/{pid}/imports",
        [bytes(big)],
        {**PNG, "x-csrf-token": "x"},
        cookie=cookie,
    )
    assert res.status == 413 and res.bytes_read == 0
    res = asgi_request(
        app,
        "POST",
        "/api/portfolios",
        [bytes(2 * 1024 * 1024)],
        {"content-type": "application/json"},
    )
    assert res.status == 413 and res.bytes_read == 0  # the 1 MiB default for every other /api body


def test_streamed_body_without_content_length_is_counted(signup: SignupFn) -> None:
    from app.main import create_app

    c, pid = setup_user(signup)
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    chunks = [bytes(1024 * 1024)] * 10  # 10 MiB streamed, no Content-Length
    res = asgi_request(
        app=create_app(),
        method="POST",
        path=f"/api/portfolios/{pid}/imports",
        chunks=chunks,
        headers={**PNG, "x-csrf-token": c.headers["X-CSRF-Token"]},
        declare_length=False,
        cookie=cookie,
    )
    assert res.status == 413
    assert res.bytes_read < 10 * 1024 * 1024  # it stopped reading once over the limit


def test_unauthenticated_big_upload_is_rejected_without_reading_the_body(
    client: TestClient,
) -> None:
    from app.main import create_app

    chunks = [bytes(1024 * 1024)] * 3  # 3 MiB: under the limit, so only auth can stop it
    res = asgi_request(create_app(), "POST", "/api/portfolios/1/imports", chunks, PNG)
    assert res.status == 401
    assert res.bytes_read == 0, "the body must not be read before authentication"


def test_upload_requires_consent_before_the_body_is_read(signup: SignupFn) -> None:
    from app.main import create_app

    c, pid = setup_user(signup, consent=False)
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    res = asgi_request(
        create_app(),
        "POST",
        f"/api/portfolios/{pid}/imports",
        [bytes(1024 * 1024)],
        {**PNG, "x-csrf-token": c.headers["X-CSRF-Token"]},
        cookie=cookie,
    )
    assert res.status == 403 and res.bytes_read == 0


# ---------------------------------------------------------------- memory only
def test_big_upload_leaves_no_temp_files(
    signup: SignupFn, ocr_text: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ocr_text["text"] = OCR_STOCKS
    c, pid = setup_user(signup)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    calls: list[str] = []

    def spy(name: str, orig: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*a: Any, **k: Any) -> Any:
            calls.append(name)
            return orig(*a, **k)

        return wrapper

    for name in (
        "SpooledTemporaryFile",
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
        "mkdtemp",
    ):
        monkeypatch.setattr(tempfile, name, spy(name, getattr(tempfile, name)))
    import starlette.formparsers as sd

    monkeypatch.setattr(
        sd, "SpooledTemporaryFile", spy("starlette.Spooled", sd.SpooledTemporaryFile)
    )
    data = noisy_png(900, 700)
    assert (
        len(data) > 1024 * 1024
    )  # the size at which Starlette would spool a multipart part to disk
    r = c.post(f"/api/portfolios/{pid}/imports", content=data, headers=PNG)
    assert r.status_code == 201, r.text
    assert calls == [] and list(tmpdir.iterdir()) == []


# ---------------------------------------------------------------- stock rows only
def test_draft_contains_only_stock_fields_and_never_the_account_lines(
    signup: SignupFn, ocr_text: dict[str, str]
) -> None:
    ocr_text["text"] = (
        "Account 12345678 John Cohen\n"
        "חשבון 123-456789 ישראל ישראלי\n"
        "Tel 03 5551234 Herzl 5 Tel Aviv\n" + OCR_STOCKS
    )
    c, pid = setup_user(signup)
    r = c.post(f"/api/portfolios/{pid}/imports", content=png_bytes(), headers=PNG)
    assert r.status_code == 201, r.text
    draft = r.json()
    assert [row["symbol"] for row in draft["rows"]] == ["TEVA.TA", "LUMI.TA"]
    allowed = {
        "index",
        "name",
        "symbol",
        "tase_number",
        "quantity",
        "price",
        "value",
        "cost",
        "currency",
        "unit",
        "matched_name",
        "candidates",
        "flags",
        "exchange",
        "conflict",
    }  # fmt: skip  (2.0-E: listing exchange, the losing copy)
    for row in draft["rows"]:
        assert set(row) <= allowed
    with new_session() as db:
        stored = db.exec(select(ImportDraft)).one()
        blob = repr(stored.rows) + repr(stored.proposed_changes)
    for secret in ("John", "Cohen", "12345678", "ישראלי", "Herzl", "5551234"):
        assert secret not in blob


def test_logs_contain_no_ocr_text(
    signup: SignupFn, ocr_text: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    ocr_text["text"] = "Account 12345678 Zebediah Quux\n" + OCR_STOCKS
    c, pid = setup_user(signup)
    with caplog.at_level(logging.DEBUG):
        assert (
            c.post(f"/api/portfolios/{pid}/imports", content=png_bytes(), headers=PNG).status_code
            == 201
        )
    text = caplog.text
    for secret in ("Zebediah", "Quux", "12345678", "טבע", "65,000"):
        assert secret not in text


def test_row_keeps_only_validating_or_matched_rows() -> None:
    from app.importer.parse import ParsedRow

    ok = ParsedRow(name="x", quantity=2, price=5, value=10, currency="USD", unit="USD")
    matched_broken = ParsedRow(
        name="y", symbol="AAPL", quantity=2, price=5, value=99, flags=["value_mismatch"]
    )
    junk = ParsedRow(name="Cohen 123", quantity=1, value=5, flags=["missing_fields", "unmatched"])
    kept = service.keep_stock_rows([junk, ok, matched_broken])
    assert [r.name for r in kept] == ["x", "y"] and [r.index for r in kept] == [0, 1]


# ---------------------------------------------------------------- decompression bomb, strips
def test_pixel_bomb_is_rejected_quickly(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    data = bomb_png()
    assert len(data) < 1024 * 1024
    t0 = time.monotonic()
    r = c.post(f"/api/portfolios/{pid}/imports", content=data, headers=PNG)
    assert r.status_code == 413 and time.monotonic() - t0 < 2.0


def test_pixel_bomb_uses_bounded_memory(tmp_path: Path) -> None:
    """A 12000x12000 PNG used to push peak RSS to 1.4 GB. Check in a clean subprocess."""
    f = tmp_path / "bomb.png"
    f.write_bytes(bomb_png())
    code = textwrap.dedent(
        f"""
        import sys
        from app.config import Settings
        from app.importer.imageio import ImageRejectedError, open_checked, to_rgb_bounded
        s = Settings()
        data = open({str(f)!r}, "rb").read()
        try:
            to_rgb_bounded(open_checked(data, s), s)
        except ImageRejectedError as e:
            print("rejected", e.status)
        # VmHWM is this process's own peak RSS (ru_maxrss can inherit the parent's peak)
        hwm = [ln for ln in open("/proc/self/status") if ln.startswith("VmHWM")][0]
        print(int(hwm.split()[1]) // 1024)
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent.parent,
    ).stdout.split()
    assert out[:2] == ["rejected", "413"]
    assert int(out[2]) < 250  # MB; decoding would take 144 MB (L) to 432 MB (RGB) more


def test_tall_images_are_downscaled_in_strips() -> None:
    s = imageio.get_settings().model_copy(
        update={"import_max_side_px": 1000, "import_strip_rows": 128}
    )
    img = Image.new("RGB", (400, 4000), (255, 255, 255))
    img.paste((255, 0, 0), (0, 2000, 400, 2400))  # a red band at 50% - 60% of the height
    out_bytes = io.BytesIO()
    img.save(out_bytes, format="PNG")
    with imageio.open_checked(out_bytes.getvalue(), s) as src:
        small = imageio.to_rgb_bounded(src, s)
    assert small.size == (100, 1000)
    assert small.getpixel((50, 550))[1] < 40  # the band landed where it should (rows 500-600)
    assert small.getpixel((50, 100)) == (255, 255, 255)
    assert small.getpixel((50, 900)) == (255, 255, 255)


def test_over_budget_images_are_rejected_before_decoding() -> None:
    s = imageio.get_settings().model_copy(update={"max_image_pixels": 10_000})
    with pytest.raises(ImageRejectedError) as ei:
        imageio.open_checked(png_bytes(200, 300), s)
    assert ei.value.status == 413


# ---------------------------------------------------------------- never half-redacted to a third party
class ThirdPartyOcr(FakeOcr):
    third_party = True

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.calls = 0

    def extract(self, image_bytes: bytes) -> OcrResult:
        self.calls += 1
        return super().extract(image_bytes)


def _use_ocr(quotes: FakeQuotes, history: FakeHistory, ocr: FakeOcr) -> None:
    set_providers(Providers(quotes=quotes, history=history, ocr_factory=lambda: ocr))


def test_third_party_ocr_is_refused_when_redaction_is_incomplete(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.importer import redact

    monkeypatch.setattr(redact, "find_word_boxes", lambda img, lang: None)  # no Tesseract
    ocr = ThirdPartyOcr(OCR_STOCKS)
    _use_ocr(quotes, history, ocr)
    c, pid = setup_user(signup)
    r = c.post(f"/api/portfolios/{pid}/imports", content=png_bytes(), headers=PNG)
    assert r.status_code == 503 and "on-device" in r.json()["detail"].lower()
    assert ocr.calls == 0, "nothing may reach the third party"


def test_third_party_ocr_runs_when_word_box_redaction_works(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.importer import redact

    monkeypatch.setattr(redact, "find_word_boxes", lambda img, lang: [])
    ocr = ThirdPartyOcr(OCR_STOCKS)
    _use_ocr(quotes, history, ocr)
    c, pid = setup_user(signup)
    assert (
        c.post(f"/api/portfolios/{pid}/imports", content=png_bytes(), headers=PNG).status_code
        == 201
    )
    assert ocr.calls == 1


def test_gemini_provider_is_marked_third_party() -> None:
    from app.providers.ocr.gemini import GeminiProvider
    from app.providers.ocr.tesseract import TesseractProvider

    assert GeminiProvider.third_party is True and TesseractProvider.third_party is False


# ---------------------------------------------------------------- on-device rows
ROW_TEVA = {
    "name": "טבע",
    "quantity": 1000,
    "price": 6500,
    "value": 65000,
    "currency": "ILS",
    "unit": "agorot",
}
ROW_NOISE = {"name": "John Cohen 12345678", "quantity": 1, "value": 5}


def test_rows_endpoint_validates_matches_and_drops_non_stock(signup: SignupFn) -> None:
    c, pid = setup_user(signup, consent=False)  # on-device: no image, no third party, no consent
    body = {"rows": [ROW_NOISE, ROW_TEVA | {"flags": ["unmatched"], "matched_name": "client lies"}]}
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json=body)
    assert r.status_code == 201, r.text
    d = r.json()
    assert [x["symbol"] for x in d["rows"]] == ["TEVA.TA"]
    assert d["rows"][0]["flags"] == [] and d["rows"][0]["matched_name"] != "client lies"
    assert d["status"] == "draft" and "expires_at" in d
    with new_session() as db:
        assert "John" not in repr(db.exec(select(ImportDraft)).one().rows)


def test_rows_endpoint_flags_a_value_mismatch_but_keeps_matched_rows(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    bad = ROW_TEVA | {"value": 99999}
    d = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [bad]}).json()
    assert d["rows"][0]["symbol"] == "TEVA.TA" and "value_mismatch" in d["rows"][0]["flags"]


def test_rows_endpoint_with_only_junk_is_422_and_stores_nothing(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_NOISE]})
    assert r.status_code == 422
    with new_session() as db:
        assert db.exec(select(ImportDraft)).first() is None


def test_rows_endpoint_rejects_oversized_or_unknown_input(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    url = f"/api/portfolios/{pid}/imports/rows"
    assert c.post(url, json={"rows": [ROW_TEVA] * 201}).status_code == 422
    assert c.post(url, json={"rows": [ROW_TEVA], "text": "raw ocr"}).status_code == 422
    long_name = ROW_TEVA | {"name": "טבע" + "x" * 5000}
    d = c.post(url, json={"rows": [long_name]}).json()
    assert len(d["rows"][0]["name"]) <= 200


# ---------------------------------------------------------------- retention
def test_draft_has_a_24h_expiry_and_confirm_clears_the_rows(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    d = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_TEVA]}).json()
    from datetime import datetime

    created = utcnow()
    exp = datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    assert timedelta(hours=23, minutes=59) < exp - created < timedelta(hours=24, minutes=1)
    conf = c.post(f"/api/imports/{d['id']}/confirm")
    assert conf.status_code == 200 and conf.json()["status"] == "confirmed"
    assert conf.json()["rows"] == []
    with new_session() as db:
        stored = db.get(ImportDraft, d["id"])
        assert stored is not None and stored.rows == [] and stored.proposed_changes == []


def test_purge_deletes_unconfirmed_drafts_after_24h(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    old = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_TEVA]}).json()["id"]
    fresh = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_TEVA]}).json()["id"]
    done = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_TEVA]}).json()["id"]
    c.post(f"/api/imports/{done}/confirm")
    with new_session() as db:
        for did in (old, done):
            row = db.get(ImportDraft, did)
            assert row is not None
            row.created_at = utcnow() - timedelta(hours=25)
            db.add(row)
        db.commit()
        assert service.purge_expired_drafts(db) == 1
        assert db.get(ImportDraft, old) is None
        assert db.get(ImportDraft, fresh) is not None
        assert db.get(ImportDraft, done) is not None  # already emptied; kept as a plain record


def test_expired_draft_is_gone_even_before_the_purge_runs(signup: SignupFn) -> None:
    c, pid = setup_user(signup)
    did = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_TEVA]}).json()["id"]
    with new_session() as db:
        row = db.get(ImportDraft, did)
        assert row is not None
        row.created_at = utcnow() - timedelta(hours=25)
        db.add(row)
        db.commit()
    assert c.get(f"/api/imports/{did}").status_code == 404
    assert c.post(f"/api/imports/{did}/confirm").status_code == 404


def test_purge_job_is_registered_in_the_scheduler(env: None) -> None:
    from app.scheduler.__main__ import build_scheduler

    sched = build_scheduler()
    assert sched.get_job("purge_drafts") is not None


def test_run_draft_purge_job(signup: SignupFn) -> None:
    from app.scheduler import jobs

    c, pid = setup_user(signup)
    did = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [ROW_TEVA]}).json()["id"]
    with new_session() as db:
        row = db.get(ImportDraft, did)
        assert row is not None
        row.created_at = utcnow() - timedelta(hours=30)
        db.add(row)
        db.commit()
        assert jobs.run_draft_purge(db) == 1


def test_streamed_json_body_over_the_limit_is_413_not_400(client: TestClient) -> None:
    """Re-review: a chunked JSON body without Content-Length that grew past the limit got a 400."""

    def chunks() -> Iterator[bytes]:
        for _ in range(40):  # 40 x 50 KB = 2 MB against the 1 MiB limit
            yield b"a" * 50_000

    r = client.post(
        "/api/auth/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert r.status_code == 413 and r.json() == {"detail": "Request body is too large"}
    assert r.headers["x-content-type-options"] == "nosniff"  # still a normal API error response
