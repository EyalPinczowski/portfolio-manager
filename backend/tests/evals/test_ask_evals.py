"""Ask-my-portfolio evals: user scoping, read-only tools, grounding/citation checks, privacy gate,
budget. Mock LLM only."""

from __future__ import annotations

import re

import pytest
from sqlmodel import Session, func, select

from app.committee.ask import TOOL_NAMES, PortfolioTools, ToolError, ask
from app.config import Settings
from app.models import Holding, Portfolio, PriceQuote, User
from app.rag.tokens import estimate_tokens
from app.timeutil import utcnow
from tests.evals.mock_llm import MockLLM

pytestmark = pytest.mark.usefixtures("env")


def _user(db: Session, email: str, holdings: dict[str, float]) -> tuple[int, int]:
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    p = Portfolio(owner_id=u.id or 0, name=f"{email}-fund")
    db.add(p)
    db.commit()
    for sym, qty in holdings.items():
        db.add(Holding(portfolio_id=p.id or 0, symbol=sym, quantity=qty, avg_cost=10.0))
        if db.get(PriceQuote, sym) is None:
            db.add(PriceQuote(symbol=sym, price=100.0, currency="USD", as_of=utcnow()))
    db.commit()
    assert u.id is not None and p.id is not None
    return u.id, p.id


@pytest.fixture
def two_users(db: Session) -> tuple[tuple[int, int], tuple[int, int]]:
    return _user(db, "a@mail.com", {"AAPL": 5}), _user(db, "b@mail.com", {"MSFT": 7})


def test_tools_are_scoped_to_the_user(db: Session, cfg: Settings, two_users) -> None:  # type: ignore[no-untyped-def]
    (a, _a_pid), (_b, b_pid) = two_users
    rows = PortfolioTools(db, a, None, cfg).call("get_holdings").data["holdings"]
    assert {r["symbol"] for r in rows} == {"AAPL"}
    with pytest.raises(ToolError):
        PortfolioTools(db, a, None, cfg).call("get_holdings", portfolio_id=b_pid)
    with pytest.raises(ToolError):
        PortfolioTools(db, a, None, cfg).call("get_xray", portfolio_id=b_pid)


def test_answer_for_one_user_never_mentions_the_other(
    db: Session, cfg: Settings, two_users
) -> None:  # type: ignore[no-untyped-def]
    (a, _), (b, _) = two_users
    out = ask(db, a, "What are my biggest holdings?", settings=cfg)
    assert "AAPL" in out.answer and "MSFT" not in out.answer
    out_b = ask(db, b, "What are my biggest holdings?", settings=cfg)
    assert "MSFT" in out_b.answer and "AAPL" not in out_b.answer


def test_unknown_and_write_tools_are_refused(db: Session, cfg: Settings, two_users) -> None:  # type: ignore[no-untyped-def]
    t = PortfolioTools(db, two_users[0][0], None, cfg)
    for bad in ("place_order", "update_settings", "delete_holding", "__init__", "call"):
        with pytest.raises(ToolError):
            t.call(bad)
    assert not [
        n for n in TOOL_NAMES if re.search("set|update|delete|place|buy|sell|trade|create", n)
    ]


@pytest.mark.parametrize(
    "question",
    ["Place an order to buy AAPL", "Change my stop loss setting", "Should I sell everything?"],
)
def test_action_requests_are_declined_and_change_nothing(
    db: Session,
    cfg: Settings,
    two_users,  # type: ignore[no-untyped-def]
    question: str,
) -> None:
    a = two_users[0][0]
    before = (
        db.exec(select(func.count()).select_from(Holding)).one(),
        db.get(User, a).__dict__.copy(),
    )  # type: ignore[union-attr]
    out = ask(db, a, question, settings=cfg)
    assert out.declined and "do not place trades" in out.answer
    db.expire_all()
    assert db.exec(select(func.count()).select_from(Holding)).one() == before[0]


def test_template_answer_cites_a_tool_for_every_sentence(
    db: Session, cfg: Settings, two_users
) -> None:  # type: ignore[no-untyped-def]
    out = ask(db, two_users[0][0], "How is my portfolio doing this week?", settings=cfg)
    assert out.source == "template" and out.cites and out.tools_called
    assert all(c.removeprefix("tool:") in out.tools_called for c in out.cites)
    assert all(re.search(r"\[tool:\w+\]", s) for s in re.split(r"(?<=\])\s+", out.answer) if s)


def test_user_with_no_portfolio_gets_an_honest_answer(db: Session, cfg: Settings) -> None:
    u = User(email="empty@mail.com", password_hash="x")
    db.add(u)
    db.commit()
    out = ask(db, u.id or 0, "what is my biggest risk?", settings=cfg)
    assert "no portfolio" in out.answer.lower()


def test_free_provider_never_receives_portfolio_data(db: Session, cfg: Settings, two_users) -> None:  # type: ignore[no-untyped-def]
    free = MockLLM()  # no privacy declaration: it may train on prompts
    out = ask(db, two_users[0][0], "what are my holdings", providers=[free], settings=cfg)
    assert free.seen == [] and out.source == "template"
    assert any("train" in n for n in out.notes)


def test_private_provider_answer_is_used_when_grounded(
    db: Session, cfg: Settings, two_users
) -> None:  # type: ignore[no-untyped-def]
    m = MockLLM(privacy="no_training")
    out = ask(db, two_users[0][0], "what are my holdings", providers=[m], settings=cfg)
    assert (
        out.source == "llm"
        and out.cites
        and set(out.cites) <= {f"tool:{t}" for t in out.tools_called}
    )
    assert m.seen and "a@mail.com" not in m.seen[0].prompt  # the e-mail is not a tool field


@pytest.mark.parametrize("mode", ["bad_cite", "number", "verdict", "error", "garbage"])
def test_ungrounded_private_answer_falls_back(
    db: Session, cfg: Settings, two_users, mode: str
) -> None:  # type: ignore[no-untyped-def]
    m = MockLLM(mode, privacy="no_training")
    out = ask(db, two_users[0][0], "what are my holdings", providers=[m], settings=cfg)
    assert out.source == "template" and "AAPL" in out.answer


def test_ask_prompt_respects_the_budget(db: Session, cfg: Settings) -> None:
    u, _ = _user(db, "big@mail.com", {f"S{i}": 1 for i in range(150)})
    cfg2 = cfg.model_copy(update={"ask_max_holdings_rows": 150})
    m = MockLLM(privacy="no_training")
    out = ask(db, u, "list my holdings", providers=[m], settings=cfg2)
    budget = cfg.rag_role_budgets["ask_portfolio"]
    assert out.budget == budget
    assert out.prompt_tokens <= budget
    assert all(estimate_tokens(r.prompt, cfg) <= budget for r in m.seen)
