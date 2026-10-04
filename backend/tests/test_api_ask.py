"""POST /api/ask and the chat history routes: persistence, scoping, retention, delete, limits."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import func, select

from app.committee.history import purge_expired_conversations
from app.config import get_settings
from app.db import new_session
from app.models import AskConversation, AskMessage
from app.scheduler.setup import JOB_IDS
from app.timeutil import utcnow
from tests.conftest import FakeHistory, FakeQuotes
from tests.test_exit_levels import frame

SignupFn = Callable[..., TestClient]


@pytest.fixture
def two(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory
) -> tuple[TestClient, TestClient, int]:
    df = frame()
    price = float(df["Close"].iloc[-1])
    for sym in ("AAPL", "MSFT"):
        history.frames[sym] = df
        quotes.set(sym, price, "USD")
    a, b = signup("a@mail.com"), signup("b@mail.com")
    pid = a.post("/api/portfolios", json={"name": "A", "base_currency": "ILS"}).json()["id"]
    r = a.post(
        f"/api/portfolios/{pid}/holdings",
        json={"symbol": "AAPL", "quantity": 10, "avg_cost": price - 20, "horizon": "1m"},
    )
    assert r.status_code == 201, r.text
    b.post("/api/portfolios", json={"name": "B", "base_currency": "ILS"})
    return a, b, pid


def test_ask_answers_with_citations_and_persists(two) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    r = a.post("/api/ask", json={"question": "What are my biggest holdings?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"]["role"] == "assistant" and "AAPL" in body["answer"]["content"]
    assert body["answer"]["cites"] == ["tool:get_holdings"]
    assert body["answer"]["tools_called"] == ["get_holdings"]
    assert body["disclaimer"] == "Not financial advice."
    cid = body["conversation_id"]
    # a follow-up joins the same conversation
    r2 = a.post("/api/ask", json={"question": "stop level for AAPL", "conversation_id": cid})
    assert r2.status_code == 200 and r2.json()["conversation_id"] == cid
    assert "get_exit_levels" in r2.json()["answer"]["tools_called"]
    detail = a.get(f"/api/ask/conversations/{cid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] * 2
    assert detail["title"].startswith("What are my biggest")
    assert [c["id"] for c in a.get("/api/ask/conversations").json()] == [cid]


def test_no_raw_tool_payload_is_stored(two) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    a.post("/api/ask", json={"question": "What are my biggest holdings?"})
    with new_session() as db:
        for m in db.exec(select(AskMessage)).all():
            assert set(m.model_dump()) == {
                "id", "conversation_id", "user_id", "role", "content", "cites", "tools_called",
                "source", "declined", "created_at",
            }  # fmt: skip
            assert all(
                isinstance(x, str) and x.startswith(("tool:", "get_"))
                for x in m.cites + m.tools_called
            )


def test_another_users_conversation_is_a_404(two) -> None:  # type: ignore[no-untyped-def]
    a, b, pid = two
    cid = a.post("/api/ask", json={"question": "my holdings"}).json()["conversation_id"]
    assert b.get(f"/api/ask/conversations/{cid}").status_code == 404
    assert b.delete(f"/api/ask/conversations/{cid}").status_code == 404
    r = b.post("/api/ask", json={"question": "more", "conversation_id": cid})
    assert r.status_code == 404
    assert b.get("/api/ask/conversations").json() == []
    assert a.get(f"/api/ask/conversations/{cid}").status_code == 200  # untouched
    # a foreign portfolio is a 404 too
    assert b.post("/api/ask", json={"question": "x", "portfolio_id": pid}).status_code == 404


def test_delete_a_conversation_removes_its_messages(two) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    cid = a.post("/api/ask", json={"question": "my holdings"}).json()["conversation_id"]
    assert a.delete(f"/api/ask/conversations/{cid}").status_code == 204
    assert a.get(f"/api/ask/conversations/{cid}").status_code == 404
    with new_session() as db:
        assert db.exec(select(func.count()).select_from(AskMessage)).one() == 0


def test_requires_login_and_validates_input(two, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    assert client.post("/api/ask", json={"question": "hi"}).status_code in (401, 403)
    assert a.post("/api/ask", json={"question": ""}).status_code == 422
    assert a.post("/api/ask", json={"question": "x", "extra": 1}).status_code == 422
    too_long = "q" * (get_settings().ask_max_question_chars + 1)
    assert a.post("/api/ask", json={"question": too_long}).status_code == 422


def test_trade_requests_are_declined_and_still_stored(two) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    body = a.post("/api/ask", json={"question": "Place an order to buy AAPL"}).json()
    assert body["declined"] and body["answer"]["declined"]


def test_ask_is_rate_limited(two, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    monkeypatch.setenv("ASK_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    codes = [a.post("/api/ask", json={"question": "my holdings"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_retention_purge_removes_only_old_conversations(two) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    old = a.post("/api/ask", json={"question": "old one"}).json()["conversation_id"]
    new = a.post("/api/ask", json={"question": "new one"}).json()["conversation_id"]
    s = get_settings()
    with new_session() as db:
        c = db.get(AskConversation, old)
        assert c is not None
        c.updated_at = utcnow() - timedelta(days=s.ask_history_retention_days + 1)
        db.add(c)
        db.commit()
        assert purge_expired_conversations(db, s) == 1
        assert db.get(AskConversation, old) is None and db.get(AskConversation, new) is not None
        assert db.exec(select(func.count()).select_from(AskMessage)).one() == 2


def test_purge_is_scheduled() -> None:
    assert "purge_ask_history" in JOB_IDS


def test_history_is_in_the_account_export(two) -> None:  # type: ignore[no-untyped-def]
    a, _, _ = two
    a.post("/api/ask", json={"question": "my holdings"})
    r = a.post("/api/me/export", json={"password": "correct horse battery"})
    assert r.status_code == 200, r.text
    conv = r.json()["ask_conversations"]
    assert len(conv) == 1 and len(conv[0]["messages"]) == 2
