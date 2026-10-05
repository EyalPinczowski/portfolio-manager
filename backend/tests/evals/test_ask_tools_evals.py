"""Evals for the `get_analysis` and `get_exit_levels` ask tools: user scoping, held-or-watched,
no default horizon, citations and grounding with the mock LLM."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.committee.ask import TOOL_NAMES, PortfolioTools, ToolError, ask
from app.config import Settings
from app.models import Holding, PriceQuote, WatchlistItem
from app.providers.registry import Providers
from tests.conftest import FakeHistory, FakeQuotes
from tests.evals.mock_llm import MockLLM
from tests.evals.test_ask_evals import _user
from tests.test_exit_levels import frame

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def world(
    db: Session, providers: Providers, quotes: FakeQuotes, history: FakeHistory
) -> tuple[int, int, int]:
    """User A holds AAPL (horizon 1m) and MSFT (no horizon); user B holds NVDA."""
    df = frame()
    price = float(df["Close"].iloc[-1])
    for sym in ("AAPL", "MSFT", "NVDA", "TSLA"):
        history.frames[sym] = df
        quotes.set(sym, price, "USD")
    a, a_pid = _user(db, "a@mail.com", {"AAPL": 5, "MSFT": 3})
    b, _ = _user(db, "b@mail.com", {"NVDA": 4})
    for q in db.exec(select(PriceQuote)).all():  # `_user` seeds 100.0; match the history
        q.price = price
        db.add(q)
    row = db.exec(select(Holding).where(Holding.symbol == "AAPL")).first()
    assert row is not None
    row.horizon = "1m"
    db.add(row)
    db.commit()
    return a, b, a_pid


def test_new_tools_are_registered_read_only(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    assert "get_analysis" in TOOL_NAMES and "get_exit_levels" in TOOL_NAMES
    t = PortfolioTools(db, world[0], None, cfg)
    assert t.call("get_analysis", symbol="AAPL").tool == "get_analysis"
    with pytest.raises(ToolError):
        t.call("get_analysis", symbol="not a symbol!")
    with pytest.raises(ToolError):
        t.call("get_exit_levels", symbol="AAPL", unknown_arg=1)


def test_analysis_is_only_for_held_or_watched_symbols(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    a, b, _ = world
    ta = PortfolioTools(db, a, None, cfg)
    held = ta.call("get_analysis", symbol="aapl").data
    assert held["available"] and held["symbol"] == "AAPL" and held["reasons"]
    # another user's holding is not mine: refused as "not held or watched"
    other = ta.call("get_analysis", symbol="NVDA").data
    assert other["available"] is False and other["reason"] == "not held or watched"
    # a watched symbol is allowed
    db.add(WatchlistItem(user_id=a, symbol="TSLA"))
    db.commit()
    assert ta.call("get_analysis", symbol="TSLA").data["available"] is True
    assert (
        PortfolioTools(db, b, None, cfg).call("get_analysis", symbol="TSLA").data["available"]
        is False
    )


def test_exit_levels_for_a_holding_with_a_horizon(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    d = PortfolioTools(db, world[0], None, cfg).call("get_exit_levels", symbol="AAPL").data
    assert d["status"] == "levels" and d["horizon"] == "1m"
    assert 0 < d["stop_price"] < d["price"] and d["stop_distance_pct"] < 0


def test_exit_levels_without_a_horizon_never_assume_one(
    db: Session,
    cfg: Settings,
    world,  # type: ignore[no-untyped-def]
) -> None:
    a = world[0]
    d = PortfolioTools(db, a, None, cfg).call("get_exit_levels", symbol="MSFT").data
    assert d["status"] == "needs_horizon" and d["available"] is False
    assert "stop_price" not in d and "horizon" not in d
    row = db.exec(select(Holding).where(Holding.symbol == "MSFT")).first()
    assert row is not None and row.horizon is None  # nothing was filled in
    out = ask(db, a, "What is my stop for MSFT?", settings=cfg)
    assert "no horizon" in out.answer and "[tool:get_exit_levels]" in out.answer
    assert [(n.symbol, n.holding_id) for n in out.needs_horizon] == [("MSFT", row.id)]


def test_exit_levels_are_scoped_to_the_user(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    a, b, a_pid = world
    d = PortfolioTools(db, a, None, cfg).call("get_exit_levels", symbol="NVDA").data
    assert d["status"] == "not_held"
    with pytest.raises(ToolError):  # a foreign portfolio id looks like "not found"
        PortfolioTools(db, b, None, cfg).call("get_exit_levels", symbol="AAPL", portfolio_id=a_pid)


def test_ask_plans_the_new_tools_and_cites_them(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    a = world[0]
    out = ask(db, a, "Give me the exit levels and an analysis of AAPL", settings=cfg)
    assert {"get_exit_levels", "get_analysis"} <= set(out.tools_called)
    assert "tool:get_exit_levels" in out.cites and "tool:get_analysis" in out.cites
    assert "stop" in out.answer and "AAPL" in out.answer


def test_private_llm_answer_for_new_tools_is_grounded(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    m = MockLLM(privacy="no_training")
    out = ask(db, world[0], "stop levels for AAPL", providers=[m], settings=cfg)
    assert out.source == "llm" and out.cites == ["tool:get_exit_levels"]


@pytest.mark.parametrize("mode", ["bad_cite", "number", "verdict"])
def test_ungrounded_answer_about_new_tools_falls_back(
    db: Session,
    cfg: Settings,
    world,
    mode: str,  # type: ignore[no-untyped-def]
) -> None:
    m = MockLLM(mode, privacy="no_training")
    out = ask(db, world[0], "stop levels for AAPL", providers=[m], settings=cfg)
    assert out.source == "template" and "AAPL" in out.answer


def test_free_provider_sees_nothing_for_the_new_tools(db: Session, cfg: Settings, world) -> None:  # type: ignore[no-untyped-def]
    free = MockLLM()
    out = ask(db, world[0], "analysis of AAPL", providers=[free], settings=cfg)
    assert free.seen == [] and out.source == "template"
