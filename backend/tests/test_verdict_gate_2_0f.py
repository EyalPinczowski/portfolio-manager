"""Outbound gate by type, the shared word list and the hardened verdict contract (2.0-F item 3).

Every bypass the Phase 2.0 diff review verified is a parametrised case here."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, create_model

from app import verdict_words
from app.config import DISCLAIMER, Settings
from app.main import app
from app.outbound import (
    OutboundBlocked,
    Symbol,
    TemplateText,
    make_notification,
    release_text,
    render,
    verdict_words_in,
)
from app.scoring.scorecard import NOT_VALIDATED, compute_scorecard
from app.signals.base import Explanation, SignalContribution
from tests.fixtures.series import uptrend
from tests.test_launch_gate import gate
from tests.verdict_contract import (
    VALUE_TOKENS,
    VERDICT_TOKENS,
    find_channel_violations,
    find_error_text_violations,
    find_text_violations,
    find_violations,
    verdict_token,
)

S = Settings(_env_file=None)
APP = Path(__file__).resolve().parent.parent / "app"


# ---------------------------------------------------------------- one shared list
def test_the_gate_and_the_contract_use_the_same_word_list() -> None:
    assert tuple(S.outbound_verdict_words) == verdict_words.VERDICT_WORDS
    import tests.verdict_contract as contract

    for word in verdict_words.VERDICT_WORDS:
        for form in verdict_words.forms_of(word):
            assert form in contract.VERDICT_TOKENS, form
    assert set(verdict_words.FIELD_ONLY_TOKENS) <= contract.VERDICT_TOKENS
    assert set(verdict_words.VALUE_ONLY_TOKENS) <= contract.VALUE_TOKENS
    assert contract.VERDICT_PHRASES is verdict_words.FIELD_PHRASES  # the very same object


# ---------------------------------------------------------------- the word scan (defence in depth)
@pytest.mark.parametrize(
    "text",
    [
        # -ing and irregular forms (verified bypasses)
        "Buying opportunity",
        "Consider selling",
        "I bought more and sold the rest",
        "Accumulating below 100",
        "Trimming the position",
        "Upgrading to a stronger view",
        # phrases
        "Go long",
        "Exit the position",
        "Add to your position",
        "Top pick",
        "Avoid",
        # Unicode tricks
        "ＢＵＹ",  # fullwidth
        "B​uy",  # zero-width space
        "s‍e‌l‍l",  # zero-width joiners
        "sel­l",  # soft hyphen
        "B⁠u﻿y",  # word joiner, BOM
        "Bυy",  # Greek upsilon
        "Ѕell",  # Cyrillic dze
        "ѕеll",  # Cyrillic s and e
        "𝐁𝐔𝐘",  # mathematical bold
        # Hebrew
        "המלצה: קנייה",
        "מכור",
        "כדאי לקנות",
        "ממליצים למכור",
        "מניה שורית",
        "מניה דובי",
        "הדירוג עלה",
        "קְנִיָּה",  # with niqqud
        # the old list still works
        "Strong BUY on AAPL",
        "Hold for now",
        "Bullish outlook",
        "Our rating: A",
    ],
)
def test_verdict_text_is_found(text: str) -> None:
    assert verdict_words_in(text, S), text
    with pytest.raises(OutboundBlocked):
        render(text, settings=S)


@pytest.mark.parametrize(
    "text",
    [
        "MACD just crossed above its signal line (bullish).",
        "MACD bullish cross",
        "MACD bearish cross",
        "RSI is 25: oversold.",
        "Bearish divergence between price and RSI",
        "Price crossed above the 50-day moving average (bullish).",
        "A bullish engulfing candle formed at support",
        "Your holding AAPL moved; your holdings are shown in USD",
        "Weekly P&L: +1.2% vs S&P 500 +0.8%",
        "Your stop at 90 was hit (target 120 not reached)",
        "AAPL rose to or above your alert price 200 (last 201 USD).",
        "החזקות שלך מוצגות בשקלים",
        "המחזיקים במניה",
        "הרווח השבועי היה 1.2%",
        "",
    ],
)
def test_legitimate_technical_and_neutral_text_still_passes(text: str) -> None:
    assert verdict_words_in(text, S) == [], text
    if text:
        assert render(text, settings=S) == text


def test_bullish_alone_is_still_a_verdict_even_next_to_a_technical_word_in_another_sentence() -> (
    None
):
    assert verdict_words_in("We are bullish. MACD crossed.", S) == ["bullish"]


def test_the_disclaimer_is_ignored_by_the_scan() -> None:
    assert verdict_words_in(f"Price alert. {DISCLAIMER}", S) == []


# ---------------------------------------------------------------- the type gate
def test_a_symbol_made_of_verdict_words_cannot_block_or_leak() -> None:
    sym = Symbol("BUY-USD")
    text = render("Price alert: {symbol}", symbol=sym, settings=S)
    assert isinstance(text, TemplateText) and text == "Price alert: BUY-USD"
    assert release_text(text, settings=S) == text


@pytest.mark.parametrize("bad", ["Strong buy", "AAPL; sell now", "B​uy", "", "a" * 40, "A B"])
def test_symbol_refuses_free_text(bad: str) -> None:
    with pytest.raises(ValueError):
        Symbol(bad)


def test_free_text_cannot_become_a_notification_while_closed() -> None:
    with pytest.raises(OutboundBlocked):
        make_notification(1, "k", "Neutral title", "Neutral body", settings=S)
    ok = render("Price alert: {symbol}", symbol=Symbol("AAPL"), settings=S)
    note = make_notification(1, "price_alert", ok, ok, settings=S)
    assert note.title == "Price alert: AAPL"
    open_gate = gate()
    assert (
        make_notification(1, "k", "Anything", "goes", gate=open_gate, settings=S).title
        == "Anything"
    )


def test_str_subclass_cannot_pose_as_template_text() -> None:
    class Fake(str):
        __slots__ = ()

    with pytest.raises(OutboundBlocked):
        release_text(Fake("Neutral"), settings=S)


# ---------------------------------------------------------------- static guards on the real code
def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == name
    ]


def test_render_is_only_called_with_a_constant_template() -> None:
    """A template built with an f-string, `+` of variables or `.format` would smuggle free text."""
    bad: list[str] = []
    for path in APP.rglob("*.py"):
        if path.name == "outbound.py":
            continue
        tree = ast.parse(path.read_text())
        consts = {
            t.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name) and t.id.isupper()
        }
        for call in _calls(tree, "render"):
            first = call.args[0] if call.args else None
            ok = (isinstance(first, ast.Name) and first.id in consts) or (
                isinstance(first, ast.Constant) and isinstance(first.value, str)
            )
            if not ok:
                bad.append(f"{path.relative_to(APP)}:{call.lineno}")
    assert bad == []


def test_notifications_are_built_only_by_make_notification() -> None:
    offenders = [
        f"{p.relative_to(APP)}:{c.lineno}"
        for p in APP.rglob("*.py")
        if p.name not in ("outbound.py", "tables.py", "__init__.py")
        for c in _calls(ast.parse(p.read_text()), "Notification")
    ]
    assert offenders == []


def test_the_scanner_for_render_catches_a_dynamic_template() -> None:
    tree = ast.parse("render(f'x {y}', a=1)\nrender(name, a=1)\nrender('ok {a}', a=1)")
    assert len(_calls(tree, "render")) == 3


# ---------------------------------------------------------------- the contract: fields and enum values
def _demo(**fields: Any) -> FastAPI:
    model = create_model("Payload", **{k: (t, ...) for k, t in fields.items()})
    demo = FastAPI()

    @demo.get("/x", response_model=model)
    def route() -> Any:
        return {}

    return demo


@pytest.mark.parametrize(
    "field", ["side", "decision", "top_pick", "exit_now", "go_long", "bias", "topPick", "goShort"]
)
def test_review_field_names_are_caught(field: str) -> None:
    assert verdict_token(field) is not None, field
    assert [v.field for v in find_violations(_demo(**{field: str}))] == [field]


@pytest.mark.parametrize(
    "value",
    ["accumulate", "outperform", "overweight", "long", "short", "exit", "avoid", "Underweight"],
)
def test_review_enum_values_are_caught(value: str) -> None:
    found = find_violations(_demo(kind=Literal[value, "neutral"]))  # type: ignore[valid-type]
    assert len(found) == 1 and "enum value" in found[0].reason, value


def test_value_and_field_token_sets_are_consistent() -> None:
    assert VERDICT_TOKENS <= VALUE_TOKENS and {"long", "short", "exit"} <= VALUE_TOKENS
    assert "long" not in VERDICT_TOKENS  # a field called `long_term_debt` stays legal
    assert verdict_token("long_term_debt") is None
    assert verdict_token("home_country") is None and verdict_token("sidebar") is None


# ---------------------------------------------------------------- the contract: free text and raw
class Why(BaseModel):
    summary: str
    invalidation_risks: list[str]
    raw: dict[str, float | str | None]


def test_free_text_fields_and_raw_string_values_are_scanned() -> None:
    clean = Why(
        summary="MACD bullish cross.", invalidation_risks=["A close below SMA50"], raw={"a": 1.0}
    )
    assert find_text_violations(clean.model_dump()) == []
    dirty = Why(
        summary="Strong buy",
        invalidation_risks=["none; consider selling"],
        raw={"rating": "Go long", "note": "המלצה: קנייה", "ok": "x"},
    )
    found = find_text_violations(dirty.model_dump())
    assert len(found) >= 5
    keys = {"$.summary", "$.invalidation_risks[0]", "$.raw.rating", "$.raw.note"}
    assert keys <= {f.split(":")[0] for f in found}
    assert any("<key 'rating'>" in f for f in found)  # a dict key is text too


def test_explanation_objects_are_scanned_through_the_real_model() -> None:
    exp = Explanation(
        summary="Our view: sell",
        invalidation_risks=["Avoid"],
        contributions=[
            SignalContribution(name="t", score=1.0, weight=10.0, confidence=1.0, raw={"x": "Hold"})
        ],
    )
    assert len(find_text_violations(exp.model_dump(mode="json"))) == 3


def test_real_score_card_text_has_no_verdict_but_the_gate_notice() -> None:
    class H:
        def get_history(self, symbol: str, days: int) -> Any:
            return uptrend(400)

    card = compute_scorecard("AAPL", H(), S)  # type: ignore[arg-type]
    assert find_text_violations(card, ignore=(NOT_VALIDATED,)) == []
    # the fixed notice is the only place where the words appear: without ignoring it, it is found
    assert find_text_violations(card)


def test_recorded_api_responses_carry_no_verdict_words(signup: Any) -> None:
    c = signup()
    for url in ("/api/launch-gate", "/api/risk/presets", "/api/notifications", "/api/alerts"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert find_text_violations(r.json(), ignore=(NOT_VALIDATED,)) == [], url


# ---------------------------------------------------------------- channels outside OpenAPI
def test_the_real_app_has_no_unscanned_channel() -> None:
    assert find_channel_violations(app) == []


def test_websocket_and_raw_responses_are_flagged() -> None:
    demo = FastAPI()

    @demo.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()

    @demo.get("/export.csv", response_class=PlainTextResponse)
    def csv() -> str:
        return "symbol,action\nAAPL,buy"

    @demo.get("/stream", response_class=StreamingResponse)
    def stream() -> Any:
        return None

    found = find_channel_violations(demo)
    assert any(f.startswith("WS /ws") for f in found)
    assert any(f.startswith("GET /export.csv") for f in found)
    assert any(f.startswith("GET /stream") for f in found)
    allowed = {"WS /ws": "r", "GET /export.csv": "r", "GET /stream": "r"}
    assert find_channel_violations(demo, allowed) == []
    assert TestClient(demo).get("/export.csv").text.startswith("symbol")


def test_error_detail_strings_in_the_app_carry_no_verdict_words() -> None:
    found: list[str] = []
    for path in APP.rglob("*.py"):
        found += find_error_text_violations(path.read_text(), str(path.relative_to(APP)))
    assert found == []


def test_error_detail_scanner_catches_hostile_details() -> None:
    src = (
        "raise HTTPException(400, 'We recommend you buy')\n"
        "raise ApiError(403, 'x', f'Sell {n}', extra={})\n"
        "raise HTTPException(404, 'Portfolio not found')\n"
    )
    assert len(find_error_text_violations(src)) == 2
