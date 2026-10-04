"""Every outgoing text (Telegram, Notification, weekly review) passes `release_text`."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.alerts import telegram
from app.alerts.price_alerts import check_price_alerts
from app.config import DISCLAIMER, Settings
from app.models import Notification, PriceAlert, PriceQuote, User
from app.outbound import (
    CurrencyCode,
    Movement,
    OutboundBlocked,
    Symbol,
    TemplateText,
    release_text,
    render,
    verdict_words_in,
)
from tests.test_launch_gate import gate

APP = Path(__file__).resolve().parent.parent / "app"
S = Settings(_env_file=None)
OPEN = gate()


# ---------------------------------------------------------------- the gate on text
@pytest.mark.parametrize(
    "text",
    [
        "Strong BUY on AAPL",
        "Analysts rate it a sell",
        "We recommend this stock",
        "Upgraded to overweight",
        "Bullish outlook",
        "Time to trim your position",
        "Hold for now",
        "Our rating: A",
        "downgrade ahead",
    ],
)
def test_verdict_text_is_refused_while_the_gate_is_closed(text: str) -> None:
    with pytest.raises(OutboundBlocked):
        release_text(text, settings=S)


def test_verdict_text_is_allowed_once_the_gate_is_open() -> None:
    assert release_text("Strong BUY on AAPL", gate=OPEN, settings=S) == "Strong BUY on AAPL"


def test_free_text_is_refused_while_the_gate_is_closed_even_when_it_is_neutral() -> None:
    for text in ("Price alert: TEVA.TA", "", f"AAPL moved. {DISCLAIMER}"):
        with pytest.raises(OutboundBlocked):
            release_text(text, settings=S)
    assert release_text("Price alert: TEVA.TA", gate=OPEN, settings=S) == "Price alert: TEVA.TA"


def test_template_text_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        TemplateText("Buy now")
    with pytest.raises(TypeError):
        TemplateText("Buy now", object())


def test_render_builds_text_from_a_fixed_template_and_typed_fields() -> None:
    text = render(
        "{symbol} {direction} {level} {currency} on {day}. " + DISCLAIMER,
        symbol=Symbol("TEVA.TA"),
        direction=Movement.ABOVE,
        level=12.5,
        currency=CurrencyCode("ILS"),
        day=date(2026, 3, 2),
        settings=S,
    )
    assert isinstance(text, TemplateText)
    assert text == "TEVA.TA rose to or above 12.5 ILS on 2026-03-02. " + DISCLAIMER
    assert release_text(text, settings=S) is text


def test_render_refuses_free_text_fields_and_bad_templates() -> None:
    with pytest.raises(TypeError):
        render("{symbol}", symbol="Strong buy", settings=S)  # a plain str is free text
    with pytest.raises(ValueError):
        Symbol("Strong buy now")
    with pytest.raises(ValueError):
        render("{symbol} and {other}", symbol=Symbol("A"), settings=S)
    with pytest.raises(ValueError):
        render("{n}", n=float("nan"), settings=S)
    with pytest.raises(OutboundBlocked):
        render("Time to buy {symbol}", symbol=Symbol("A"), settings=S)
    # a verdict word in a typed *field* is not scanned: the field cannot carry free text
    assert render("Price alert: {symbol}", symbol=Symbol("BUY-USD"), settings=S) == (
        "Price alert: BUY-USD"
    )


def test_the_disclaimer_alone_does_not_trigger() -> None:
    assert verdict_words_in(DISCLAIMER, S) == []
    assert verdict_words_in(f"Buy now. {DISCLAIMER}", S) == ["buy"]


def test_the_word_list_is_config() -> None:
    custom = Settings(_env_file=None, outbound_verdict_words=["zap"])
    with pytest.raises(OutboundBlocked):
        render("zap it", settings=custom)
    assert render("buy it", settings=custom) == "buy it"


# ---------------------------------------------------------------- behaviour of the real senders
def test_telegram_refuses_verdict_text_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[object] = []

    class Ok:
        def raise_for_status(self) -> None: ...

    def fake_post(*a: object, **k: object) -> Ok:
        posted.append(k)
        return Ok()

    monkeypatch.setattr("app.alerts.telegram.httpx.post", fake_post)
    cfg = Settings(_env_file=None, telegram_bot_token="T")
    assert telegram.send_telegram("42", "Strong buy AAPL", cfg) is False  # free text
    assert posted == []
    ok = render("Price alert: {symbol}", symbol=Symbol("AAPL"))
    assert telegram.send_telegram("42", ok, cfg) is True
    assert len(posted) == 1


def test_a_price_alert_message_is_clean_and_released(db: Session) -> None:
    user = User(email="u@mail.com", password_hash="x", telegram_chat_id="42")
    db.add(user)
    db.commit()
    assert user.id is not None
    db.add(PriceAlert(user_id=user.id, symbol="AAPL", op="above", price=100.0))
    db.add(PriceQuote(symbol="AAPL", price=101.0, currency="USD"))
    db.commit()
    sent: list[str] = []
    notes = check_price_alerts(db, S, sender=lambda chat, text: bool(sent.append(text) or True))
    assert len(notes) == 1 and len(db.exec(select(Notification)).all()) == 1
    for text in (*sent, notes[0].title, notes[0].body):
        assert verdict_words_in(text, S) == []


# ---------------------------------------------------------------- no module can bypass it
def unreleased_notifications(source: str) -> list[int]:
    """Lines where `Notification(...)` is built without `release_text` around title and body."""
    bad: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "Notification")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "Notification")
            )
        ):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        released = all(
            isinstance(kw.get(name), ast.Call)
            and getattr(kw[name].func, "id", getattr(kw[name].func, "attr", None))  # type: ignore[union-attr]
            == "release_text"
            for name in ("title", "body")
        )
        if not released or None in kw:  # `**kwargs` hides what goes in
            bad.append(node.lineno)
    return bad


def raw_telegram_calls(source: str, path: str) -> list[int]:
    """Lines that mention the Telegram API URL outside the one sender module."""
    if path.endswith("alerts/telegram.py"):
        return []
    return [
        n.lineno
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "api.telegram.org" in n.value
    ]


def test_no_module_creates_a_notification_without_release_text() -> None:
    offenders = {}
    for path in APP.rglob("*.py"):
        lines = unreleased_notifications(path.read_text())
        if lines:
            offenders[str(path.relative_to(APP))] = lines
    assert offenders == {}


def test_no_module_talks_to_telegram_except_the_gated_sender() -> None:
    offenders = {}
    for path in APP.rglob("*.py"):
        lines = raw_telegram_calls(path.read_text(), str(path))
        if lines:
            offenders[str(path.relative_to(APP))] = lines
    assert offenders == {}


def test_the_telegram_sender_itself_calls_release_text() -> None:
    tree = ast.parse((APP / "alerts" / "telegram.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "send_telegram")
    calls = [getattr(c.func, "id", None) for c in ast.walk(fn) if isinstance(c, ast.Call)]
    assert "release_text" in calls
    first_post = min(
        c.lineno
        for c in ast.walk(fn)
        if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "post"
    )
    first_release = min(
        c.lineno
        for c in ast.walk(fn)
        if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "release_text"
    )
    assert first_release < first_post


def test_the_scanners_catch_bypasses() -> None:
    assert unreleased_notifications("Notification(user_id=1, kind='k', title='Buy', body='x')")
    assert unreleased_notifications("m.Notification(title=release_text('a'), body='Sell')")
    assert unreleased_notifications("Notification(**data)")
    assert not unreleased_notifications(
        "Notification(user_id=1, title=release_text('a'), body=outbound.release_text('b'))"
    )
    assert raw_telegram_calls("httpx.post('https://api.telegram.org/botX/sendMessage')", "app/x.py")
    assert not raw_telegram_calls("x = 1", "app/x.py")
