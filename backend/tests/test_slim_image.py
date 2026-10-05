"""The slim image has no Tesseract: server OCR answers a clean 503, and the image file stays honest."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.importer import imageio, redact
from app.providers.base import OcrResult
from app.providers.ocr import gemini, tesseract
from app.providers.registry import Providers, set_providers
from tests.conftest import FakeHistory, FakeQuotes, png_bytes

SignupFn = Callable[..., TestClient]
BACKEND = Path(__file__).resolve().parent.parent
PNG = {"Content-Type": "image/png"}


@pytest.fixture
def no_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    assert not tesseract.tesseract_available()


def _default_providers(quotes: FakeQuotes, history: FakeHistory) -> None:
    """Real OCR selection (no fake factory): Gemini if a key is set, else Tesseract."""
    set_providers(Providers(quotes=quotes, history=history))


def _user(signup: SignupFn) -> tuple[TestClient, int]:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"]
    c.post("/api/auth/consent/ocr")
    return c, pid


def test_server_ocr_without_tesseract_is_a_503_that_points_to_on_device_reading(
    signup: SignupFn,
    quotes: FakeQuotes,
    history: FakeHistory,
    no_tesseract: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_providers(quotes, history)

    def must_not_decode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the image must not be decoded when no engine can read it")

    monkeypatch.setattr(redact, "redact_image", must_not_decode)
    monkeypatch.setattr("app.importer.service.redact_image", must_not_decode)
    c, pid = _user(signup)
    r = c.post(f"/api/portfolios/{pid}/imports", content=png_bytes(), headers=PNG)
    assert r.status_code == 503
    assert "on-device" in r.json()["detail"].lower()


def test_gemini_is_not_called_when_redaction_needs_a_missing_tesseract(
    signup: SignupFn,
    quotes: FakeQuotes,
    history: FakeHistory,
    no_tesseract: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    from app.config import get_settings

    get_settings.cache_clear()
    _default_providers(quotes, history)

    def must_not_call(*_a: Any, **_k: Any) -> OcrResult:
        raise AssertionError("nothing may be sent to the third party")

    monkeypatch.setattr(gemini.GeminiProvider, "extract", must_not_call)
    c, pid = _user(signup)
    r = c.post(f"/api/portfolios/{pid}/imports", content=png_bytes(), headers=PNG)
    assert r.status_code == 503 and "on-device" in r.json()["detail"].lower()


def test_on_device_rows_still_work_without_tesseract(signup: SignupFn, no_tesseract: None) -> None:
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"]
    row = {"name": "טבע", "quantity": 10, "price": 6500, "value": 6500, "currency": "ILS"}
    r = c.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": [row | {"unit": "agorot"}]})
    assert r.status_code == 201, r.text


def test_an_rgb_image_within_bounds_is_not_copied_for_the_conversion() -> None:
    s = imageio.get_settings()
    img = Image.new("RGB", (200, 300), (255, 255, 255))
    assert imageio.to_rgb_bounded(img, s) is img  # one decoded copy in memory, not two
    gray = Image.new("L", (200, 300), 255)
    assert imageio.to_rgb_bounded(gray, s).mode == "RGB"


# --- Dockerfile.slim -----------------------------------------------------------------------------


def _slim() -> str:
    return (BACKEND / "Dockerfile.slim").read_text()


def _instructions(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )  # drop comment lines (they may legitimately mention Tesseract)


def test_slim_dockerfile_has_no_tesseract_and_installs_the_postgres_extra() -> None:
    body = _instructions(_slim())
    assert "tesseract" not in body.lower()
    assert "apt-get" not in body
    assert "--extra postgres" in body and "--frozen" in body and "uv.lock" in body


def test_slim_dockerfile_uses_the_same_pinned_base_as_the_full_image() -> None:
    pin = re.compile(r"^FROM (python:3\.12-slim@sha256:[0-9a-f]{64})", re.M)
    full = pin.search((BACKEND / "Dockerfile").read_text())
    slim = pin.search(_slim())
    assert full and slim and full.group(1) == slim.group(1)


def test_slim_dockerfile_runs_one_worker_in_process_scheduler_as_non_root() -> None:
    body = _instructions(_slim())
    assert "--workers 1" in body
    assert "SCHEDULER_IN_PROCESS=true" in body
    assert "MALLOC_ARENA_MAX=2" in body
    assert "python -m app.cli boot" in body  # boot = migrate + bootstrap-admin
    user_lines = re.findall(r"^USER (\S+)", body, re.M)
    assert user_lines and user_lines[-1] not in ("root", "0")
