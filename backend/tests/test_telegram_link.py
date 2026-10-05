"""Telegram link flow with a fake sender: no network, no real bot."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.alerts.telegram import FakeTelegramSender, set_sender
from app.config import DISCLAIMER, get_settings
from app.db import new_session
from app.models import TelegramLinkCode, User
from app.outbound import verdict_words_in
from app.telegram_link import hash_code, parse_start
from app.timeutil import utcnow

SignupFn = Callable[..., TestClient]
SECRET = "hook-secret-0123456789"
HDR = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
BOT_TOKEN = "123456789:" + "A" * 35


@pytest.fixture
def tg(env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTelegramSender]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "pm_test_bot")
    get_settings.cache_clear()
    fake = FakeTelegramSender()
    set_sender(fake)
    yield fake
    set_sender(None)
    get_settings.cache_clear()


def update(chat_id: int, text: str, chat_type: str = "private") -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {"message_id": 5, "text": text, "chat": {"id": chat_id, "type": chat_type}},
    }


def new_code(c: TestClient) -> str:
    r = c.post("/api/telegram/link-code")
    assert r.status_code == 201, r.text
    return str(r.json()["code"])


def chat_of(email: str) -> str | None:
    with new_session() as db:
        return db.exec(select(User).where(User.email == email)).one().telegram_chat_id


def test_link_flow_binds_the_chat_and_confirms(tg: FakeTelegramSender, signup: SignupFn) -> None:
    a = signup("a@mail.com")
    assert a.get("/api/telegram/status").json() == {
        "configured": True,
        "webhook_ready": True,
        "linked": False,
        "bot_username": "pm_test_bot",
    }
    r = a.post("/api/telegram/link-code")
    body = r.json()
    code = body["code"]
    assert len(code) == 8 and body["ttl_minutes"] == 15
    assert body["command"] == f"/start {code}"
    assert body["deep_link"] == f"https://t.me/pm_test_bot?start={code}"
    hook = TestClient(a.app)  # Telegram: no cookie, no CSRF header
    assert hook.post(
        "/api/telegram/webhook", json=update(777, f"/start {code}"), headers=HDR
    ).json() == {"ok": True}
    assert chat_of("a@mail.com") == "777"
    assert a.get("/api/telegram/status").json()["linked"] is True
    assert a.get("/api/settings").json()["telegram_linked"] is True
    assert len(tg.sent) == 1 and tg.sent[0][0] == "777"
    assert DISCLAIMER in tg.sent[0][1] and verdict_words_in(tg.sent[0][1]) == []
    assert "777" not in a.get("/api/telegram/status").text  # the chat id is never returned


def test_codes_are_hashed_at_rest(tg: FakeTelegramSender, signup: SignupFn) -> None:
    a = signup()
    code = new_code(a)
    with new_session() as db:
        row = db.exec(select(TelegramLinkCode)).one()
        assert code not in row.code_hash and row.code_hash == hash_code(code, get_settings())
        assert row.code_hash != hash_code("ZZZZZZZZ", get_settings())


def test_single_use_and_replay_is_idempotent_for_the_same_chat(
    tg: FakeTelegramSender, signup: SignupFn
) -> None:
    a = signup("a@mail.com")
    code = new_code(a)
    hook = TestClient(a.app)
    for _ in range(2):  # Telegram redelivers an update: same answer, no error
        assert (
            hook.post(
                "/api/telegram/webhook", json=update(1, f"/start {code}"), headers=HDR
            ).status_code
            == 200
        )
    assert chat_of("a@mail.com") == "1"
    assert all("linked to your account" in t for _, t in tg.sent) and len(tg.sent) == 2
    # another chat cannot reuse the spent code
    hook.post("/api/telegram/webhook", json=update(2, f"/start {code}"), headers=HDR)
    assert chat_of("a@mail.com") == "1"
    assert "invalid or has expired" in tg.sent[-1][1]


def test_expired_code_is_refused(tg: FakeTelegramSender, signup: SignupFn) -> None:
    a = signup("a@mail.com")
    code = new_code(a)
    with new_session() as db:
        row = db.exec(select(TelegramLinkCode)).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()
    TestClient(a.app).post("/api/telegram/webhook", json=update(1, f"/start {code}"), headers=HDR)
    assert chat_of("a@mail.com") is None
    assert "invalid or has expired" in tg.sent[-1][1]


def test_a_new_code_replaces_the_old_one(tg: FakeTelegramSender, signup: SignupFn) -> None:
    a = signup("a@mail.com")
    old = new_code(a)
    new = new_code(a)
    hook = TestClient(a.app)
    hook.post("/api/telegram/webhook", json=update(1, f"/start {old}"), headers=HDR)
    assert chat_of("a@mail.com") is None
    hook.post("/api/telegram/webhook", json=update(1, f"/start {new}"), headers=HDR)
    assert chat_of("a@mail.com") == "1"


def test_one_chat_belongs_to_one_account(tg: FakeTelegramSender, signup: SignupFn) -> None:
    a = signup("a@mail.com")
    b = signup("b@mail.com")
    hook = TestClient(a.app)
    hook.post("/api/telegram/webhook", json=update(9, f"/start {new_code(a)}"), headers=HDR)
    hook.post("/api/telegram/webhook", json=update(9, f"/start {new_code(b)}"), headers=HDR)
    assert chat_of("a@mail.com") == "9" and chat_of("b@mail.com") is None
    assert "already linked" in tg.sent[-1][1]


def test_relinking_to_a_new_chat_replaces_the_old_one(
    tg: FakeTelegramSender, signup: SignupFn
) -> None:
    a = signup("a@mail.com")
    hook = TestClient(a.app)
    hook.post("/api/telegram/webhook", json=update(1, f"/start {new_code(a)}"), headers=HDR)
    hook.post("/api/telegram/webhook", json=update(2, f"/start {new_code(a)}"), headers=HDR)
    assert chat_of("a@mail.com") == "2"


def test_unlink_is_idempotent_and_clears_pending_codes(
    tg: FakeTelegramSender, signup: SignupFn
) -> None:
    a = signup("a@mail.com")
    hook = TestClient(a.app)
    hook.post("/api/telegram/webhook", json=update(1, f"/start {new_code(a)}"), headers=HDR)
    pending = new_code(a)
    assert a.delete("/api/telegram/link").status_code == 204
    assert a.delete("/api/telegram/link").status_code == 204
    assert chat_of("a@mail.com") is None
    hook.post("/api/telegram/webhook", json=update(1, f"/start {pending}"), headers=HDR)
    assert chat_of("a@mail.com") is None


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        {"X-Telegram-Bot-Api-Secret-Token": SECRET + "x"},
    ],
)
def test_webhook_needs_the_secret_token(
    tg: FakeTelegramSender, signup: SignupFn, headers: dict[str, str]
) -> None:
    a = signup("a@mail.com")
    code = new_code(a)
    r = TestClient(a.app).post(
        "/api/telegram/webhook", json=update(1, f"/start {code}"), headers=headers
    )
    assert r.status_code == 403
    assert chat_of("a@mail.com") is None and tg.sent == []


def test_webhook_is_off_without_a_secret(env: None, client: TestClient) -> None:
    r = client.post("/api/telegram/webhook", json=update(1, "/start ABCDEFGH"), headers=HDR)
    assert r.status_code == 503


def test_link_code_needs_the_bot_token_and_a_session(
    env: None, signup: SignupFn, client: TestClient
) -> None:
    assert client.post("/api/telegram/link-code").status_code == 401
    assert client.get("/api/telegram/status").status_code == 401
    assert client.delete("/api/telegram/link").status_code == 401
    a = signup()
    r = a.post("/api/telegram/link-code")
    assert r.status_code == 503 and r.json()["code"] == "telegram_not_configured"


def test_link_code_requests_are_rate_limited(
    tg: FakeTelegramSender, signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_LINK_CODES_PER_HOUR", "2")
    get_settings.cache_clear()
    a = signup()
    assert a.post("/api/telegram/link-code").status_code == 201
    assert a.post("/api/telegram/link-code").status_code == 201
    r = a.post("/api/telegram/link-code")
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_guessing_codes_from_one_chat_is_limited(
    tg: FakeTelegramSender, signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_LINK_ATTEMPTS_PER_HOUR", "3")
    get_settings.cache_clear()
    a = signup("a@mail.com")
    code = new_code(a)
    hook = TestClient(a.app)
    for guess in ("AAAAAAAA", "BBBBBBBB", "CCCCCCCC"):
        hook.post("/api/telegram/webhook", json=update(5, f"/start {guess}"), headers=HDR)
    # even the right code is not processed any more for this chat: the limit hides the answer
    r = hook.post("/api/telegram/webhook", json=update(5, f"/start {code}"), headers=HDR)
    assert r.status_code == 200 and chat_of("a@mail.com") is None
    assert len(tg.sent) == 3  # three "invalid" replies, nothing for the limited one
    # another chat is unaffected
    hook.post("/api/telegram/webhook", json=update(6, f"/start {code}"), headers=HDR)
    assert chat_of("a@mail.com") == "6"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"update_id": 1},
        {"message": "text"},
        {"message": {"text": "/start ABCDEFGH"}},
        {"message": {"text": "/start ABCDEFGH", "chat": {"id": -100, "type": "group"}}},
        {"message": {"text": "/start ABCDEFGH", "chat": {"id": True, "type": "private"}}},
        {"message": {"text": "hello", "chat": {"id": 1, "type": "private"}}},
        {"message": {"text": 5, "chat": {"id": 1, "type": "private"}}},
        {"edited_message": {"text": "/start ABCDEFGH", "chat": {"id": 1, "type": "private"}}},
    ],
)
def test_malformed_or_irrelevant_updates_are_acknowledged_and_ignored(
    tg: FakeTelegramSender, signup: SignupFn, payload: dict[str, Any]
) -> None:
    a = signup("a@mail.com")
    new_code(a)
    r = TestClient(a.app).post("/api/telegram/webhook", json=payload, headers=HDR)
    assert r.status_code == 200 and tg.sent == []


def test_a_disabled_user_cannot_link(tg: FakeTelegramSender, signup: SignupFn) -> None:
    a = signup("a@mail.com")
    code = new_code(a)
    with new_session() as db:
        u = db.exec(select(User)).one()
        u.disabled_at = utcnow()
        db.add(u)
        db.commit()
    TestClient(a.app).post("/api/telegram/webhook", json=update(1, f"/start {code}"), headers=HDR)
    assert chat_of("a@mail.com") is None


def test_chat_id_and_token_are_never_logged(
    tg: FakeTelegramSender, signup: SignupFn, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    a = signup("a@mail.com")
    code = new_code(a)
    hook = TestClient(a.app)
    hook.post("/api/telegram/webhook", json=update(424242, f"/start {code}"), headers=HDR)
    hook.post(
        "/api/telegram/webhook",
        json=update(424242, "/start ZZZZZZZZ"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "bad"},
    )
    logged = caplog.text
    for secret in ("424242", BOT_TOKEN, SECRET, code):
        assert secret not in logged


def test_parse_start() -> None:
    assert parse_start("/start abcd2345") == "abcd2345"
    assert parse_start("/start@my_bot ABCD2345") == "ABCD2345"
    for bad in (
        None,
        "",
        "/start",
        "start ABCD2345",
        "/start ab",
        "/start ABCD 2345",
        "/stop ABCD2345",
        "/start ABCD2345; DROP",
    ):
        assert parse_start(bad) is None


def test_fake_sender_applies_the_outbound_gate_and_never_calls_the_network(
    tg: FakeTelegramSender,
) -> None:
    from app.outbound import OutboundBlocked

    with pytest.raises(OutboundBlocked):
        tg.send("1", "Strong buy AAPL")  # free text is refused while the launch gate is closed
    assert tg.sent == []


def test_replayed_start_gets_the_same_answer_and_binds_once(
    tg: FakeTelegramSender, signup: SignupFn
) -> None:
    """M8: Telegram redelivers an update after a cold start; also once the code row has expired."""
    a = signup("a@mail.com")
    code = new_code(a)
    hook = TestClient(a.app)
    for _ in range(3):
        hook.post("/api/telegram/webhook", json=update(7, f"/start {code}"), headers=HDR)
        if _ == 0:
            with new_session() as db:  # the spent code ages past its expiry between deliveries
                row = db.exec(select(TelegramLinkCode)).one()
                row.expires_at = utcnow() - timedelta(hours=1)
                db.add(row)
                db.commit()
    assert chat_of("a@mail.com") == "7"
    assert len(tg.sent) == 3 and len({t for _, t in tg.sent}) == 1
    assert "linked to your account" in tg.sent[0][1]
    with new_session() as db:
        assert len(db.exec(select(TelegramLinkCode)).all()) == 1


def test_replay_of_a_chat_taken_answer_stays_chat_taken(
    tg: FakeTelegramSender, signup: SignupFn
) -> None:
    a = signup("a@mail.com")
    hook = TestClient(a.app)
    hook.post("/api/telegram/webhook", json=update(5, f"/start {new_code(a)}"), headers=HDR)
    b = signup("b@mail.com")
    code_b = new_code(b)
    for _ in range(2):
        hook.post("/api/telegram/webhook", json=update(5, f"/start {code_b}"), headers=HDR)
    assert chat_of("b@mail.com") is None
    assert tg.sent[-1][1] == tg.sent[-2][1] and "already" in tg.sent[-1][1].lower()
