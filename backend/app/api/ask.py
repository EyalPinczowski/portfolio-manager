"""Ask-my-portfolio: POST /api/ask and the chat history (list, get, delete).

Read-only and user-scoped: the tools in `app/committee/ask.py` read only the caller's portfolios and
the history helpers filter on the caller's id, so another user's conversation or portfolio is a 404.
The answer is a template built from the tool results; an AI provider may only phrase it when it
declares `privacy == "no_training"` (none exists by default). Stored per message: the text, the cited
tool names and the tools called. Never a raw tool payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.schemas import Body
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.auth.ratelimit import ask_limiter, enforce_limit
from app.committee.ask import NeedsHorizon
from app.committee.ask import ask as run_ask
from app.committee.history import (
    add_message,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    start_conversation,
)
from app.config import DISCLAIMER
from app.errors import ApiError
from app.llm.base import LLMProvider
from app.llm.providers import build_providers
from app.models import AskConversation, AskMessage
from app.providers.registry import get_providers
from app.repo import get_portfolio
from app.strictjson import StrictJsonRoute

router = APIRouter(tags=["ask"], route_class=StrictJsonRoute)


def ask_providers(settings: SettingsDep) -> list[LLMProvider]:
    """Only a provider that declares `privacy == "no_training"` may see portfolio data. Override
    in tests."""
    return [p for p in build_providers(settings) if getattr(p, "privacy", None) == "no_training"]


ProvidersDep = Depends(ask_providers)


class AskQuestionIn(Body):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None
    portfolio_id: int | None = None


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    cites: list[str] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    source: Literal["template", "llm", "cache"] | None = None
    declined: bool = False
    created_at: datetime


class ConversationOut(BaseModel):
    id: int
    title: str
    portfolio_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class PortfolioAskOut(BaseModel):
    conversation_id: int
    question: MessageOut
    answer: MessageOut
    declined: bool = False
    notes: list[str] = Field(default_factory=list)
    needs_horizon: list[NeedsHorizon] = Field(default_factory=list)  # holdings to set a horizon on
    disclaimer: str = DISCLAIMER


def _msg(m: AskMessage) -> MessageOut:
    assert m.id is not None
    return MessageOut(
        id=m.id,
        role=m.role,  # type: ignore[arg-type]
        content=m.content,
        cites=list(m.cites or []),
        tools_called=list(m.tools_called or []),
        source=m.source,  # type: ignore[arg-type]
        declined=m.declined,
        created_at=m.created_at,
    )


def _conv(c: AskConversation) -> ConversationOut:
    assert c.id is not None
    return ConversationOut(
        id=c.id,
        title=c.title,
        portfolio_id=c.portfolio_id,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _not_found() -> ApiError:
    return ApiError(404, "conversation_not_found", "Conversation not found.")


@router.post("/ask", response_model=PortfolioAskOut)
def ask_question(
    body: AskQuestionIn,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    providers: list[LLMProvider] = ProvidersDep,
) -> PortfolioAskOut:
    """Answer one question from read-only tools and store the exchange. A `conversation_id` that is
    not the caller's is a 404; so is a `portfolio_id` that is not the caller's."""
    assert user.id is not None
    question = body.question.strip()
    if not question or len(question) > settings.ask_max_question_chars:
        raise ApiError(
            422,
            "bad_question",
            f"Ask a question of 1 to {settings.ask_max_question_chars} characters.",
        )
    conv: AskConversation | None = None
    if body.conversation_id is not None:
        conv = get_conversation(db, user.id, body.conversation_id)
        if conv is None:
            raise _not_found()
    portfolio_id = conv.portfolio_id if conv is not None else body.portfolio_id
    if portfolio_id is not None:
        get_portfolio(db, user.id, portfolio_id)  # 404 unless it is the caller's
    enforce_limit(ask_limiter, f"user:{user.id}", settings.ask_rate_limit_per_hour, 3600.0)

    result = run_ask(
        db,
        user.id,
        question,
        history=get_providers().history,
        providers=providers,
        settings=settings,
        portfolio_id=portfolio_id,
    )
    if conv is None:
        conv = start_conversation(db, user.id, question, portfolio_id, settings)
    q = add_message(db, conv, "user", question)
    a = add_message(
        db,
        conv,
        "assistant",
        result.answer,
        cites=result.cites,
        tools_called=result.tools_called,
        source=result.source,
        declined=result.declined,
    )
    db.commit()
    db.refresh(q)
    db.refresh(a)
    assert conv.id is not None
    return PortfolioAskOut(
        conversation_id=conv.id,
        question=_msg(q),
        answer=_msg(a),
        declined=result.declined,
        notes=result.notes,
        needs_horizon=result.needs_horizon,
    )


@router.get("/ask/conversations", response_model=list[ConversationOut])
def conversations(user: UserDep, db: DbDep) -> list[ConversationOut]:
    assert user.id is not None
    return [_conv(c) for c in list_conversations(db, user.id)]


@router.get("/ask/conversations/{conversation_id}", response_model=ConversationDetailOut)
def conversation(conversation_id: int, user: UserDep, db: DbDep) -> ConversationDetailOut:
    assert user.id is not None
    c = get_conversation(db, user.id, conversation_id)
    if c is None:
        raise _not_found()
    return ConversationDetailOut(
        **_conv(c).model_dump(),
        messages=[_msg(m) for m in list_messages(db, user.id, conversation_id)],
    )


@router.delete("/ask/conversations/{conversation_id}", status_code=204)
def remove_conversation(conversation_id: int, user: UserDep, db: DbDep) -> Response:
    assert user.id is not None
    if not delete_conversation(db, user.id, conversation_id):
        raise _not_found()
    return Response(status_code=204)
