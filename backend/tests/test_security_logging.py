"""Logs must not contain the Telegram bot token or text read from a screenshot."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.logging_setup import RedactionFilter, configure_logging, redact_text
from app.providers.base import OcrResult, OcrUnavailableError
from app.providers.ocr.gemini import parse_gemini_json
from app.providers.registry import Providers, set_providers
from tests.conftest import FakeHistory, FakeOcr, FakeQuotes, png_bytes

SignupFn = Callable[..., TestClient]
TOKEN = "bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw-_x"


def send_via_httpx() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        client.post(
            f"https://api.telegram.org/{TOKEN}/sendMessage", json={"chat_id": "1", "text": "x"}
        )


def test_httpx_logger_does_not_log_the_bot_token(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    with caplog.at_level(logging.DEBUG):
        send_via_httpx()
    assert "AAHdqTcv" not in caplog.text and "123456789" not in caplog.text


def test_redaction_filter_masks_tokens_even_if_httpx_logs_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging()
    httpx_logger = logging.getLogger("httpx")
    old = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)  # someone turned it up: the filter is the second layer
    try:
        with caplog.at_level(logging.DEBUG):
            send_via_httpx()
    finally:
        httpx_logger.setLevel(old)
    assert "HTTP Request" in caplog.text  # httpx did log...
    assert "AAHdqTcv" not in caplog.text and "bot<redacted>" in caplog.text  # ...redacted


def test_redaction_covers_args_and_plain_handlers() -> None:
    assert redact_text(f"POST https://x/{TOKEN}/y") == "POST https://x/bot<redacted>/y"
    rec = logging.LogRecord("n", logging.INFO, "f", 1, "url=%s", (f"https://x/{TOKEN}/y",), None)
    RedactionFilter().filter(rec)
    assert "AAHdq" not in rec.getMessage() and "bot<redacted>" in rec.getMessage()
    configure_logging()
    made = logging.getLogger("t").makeRecord("t", logging.INFO, "f", 1, "u=%s", (TOKEN,), None)
    assert "AAHdq" not in made.getMessage()
    plain = logging.LogRecord("n", logging.INFO, "f", 1, "no secret here", (), None)
    RedactionFilter().filter(plain)
    assert plain.getMessage() == "no secret here"


def test_logging_is_configured_by_the_api_and_the_scheduler(env: None) -> None:
    from app.main import create_app
    from app.scheduler import __main__ as sched_main

    logging.getLogger("httpx").setLevel(logging.INFO)
    create_app()
    assert logging.getLogger("httpx").level == logging.WARNING
    logging.getLogger("httpx").setLevel(logging.INFO)
    import inspect

    assert "configure_logging()" in inspect.getsource(sched_main.main)


def test_telegram_sender_never_logs_the_token(
    env: None, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.alerts.telegram import send_telegram
    from app.config import Settings

    configure_logging()

    def boom(url: str, **kw: object) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(httpx, "post", boom)
    s = Settings(telegram_bot_token="123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw-_x")
    with caplog.at_level(logging.DEBUG):
        assert send_telegram("42", "hi", s) is False
    assert "AAHdqTcv" not in caplog.text


# ---------------------------------------------------------------- Gemini ValidationError
SECRET = "OWNER-NAME-Zebediah-Quux"


def bad_payload() -> str:
    return (
        '{"rows": [{"name": "'
        + SECRET
        + '", "quantity": "not-a-number", "price": "'
        + SECRET
        + '"}]}'
    )


def test_gemini_validation_error_does_not_echo_the_input(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(OcrUnavailableError) as ei:
        parse_gemini_json(bad_payload())
    assert SECRET not in str(ei.value) and SECRET not in caplog.text
    full = "".join(traceback.format_exception(ei.value))
    assert SECRET not in full, "the chained ValidationError would print input_value"
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_gemini_garbage_json_is_handled_too(caplog: pytest.LogCaptureFixture) -> None:
    for payload in ("not json at all " + SECRET, '{"nothing": 1}', "[1, 2]"):
        with pytest.raises(OcrUnavailableError) as ei:
            parse_gemini_json(payload)
        assert SECRET not in "".join(traceback.format_exception(ei.value))


def test_bad_gemini_answer_is_a_clean_503_with_no_text_in_response_or_logs(
    signup: SignupFn,
    quotes: FakeQuotes,
    history: FakeHistory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BadGemini(FakeOcr):
        def extract(self, image_bytes: bytes) -> OcrResult:
            return parse_gemini_json(bad_payload())

    set_providers(Providers(quotes=quotes, history=history, ocr_factory=lambda: BadGemini("")))
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p", "base_currency": "ILS"}).json()["id"]
    c.post("/api/auth/consent/ocr")
    with caplog.at_level(logging.DEBUG):
        r = c.post(
            f"/api/portfolios/{pid}/imports",
            content=png_bytes(),
            headers={"Content-Type": "image/png"},
        )
    assert r.status_code == 503 and SECRET not in r.text and SECRET not in caplog.text
