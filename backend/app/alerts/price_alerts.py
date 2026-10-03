"""Price alerts fire only on a change: when the rule first triggers (then it deactivates)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.alerts.telegram import send_telegram
from app.config import DISCLAIMER, Settings, get_settings
from app.models import Notification, PriceAlert, PriceQuote, User
from app.outbound import (
    CurrencyCode,
    Movement,
    Symbol,
    TemplateText,
    join_lines,
    make_notification,
    render,
)
from app.timeutil import utcnow

log = logging.getLogger(__name__)
Sender = Callable[[str | None, str], bool]


def _triggered(alert: PriceAlert, price: float) -> bool:
    return price >= alert.price if alert.op == "above" else price <= alert.price


TITLE = "Price alert: {symbol}"
BODY = "{symbol} {direction} your alert price {level} (last {last} {currency}). " + DISCLAIMER


def price_alert_text(
    alert: PriceAlert, quote: PriceQuote, settings: Settings | None = None
) -> tuple[TemplateText, TemplateText]:
    """(title, body) built from fixed templates and typed fields.

    The symbol is a user-controlled value: it is validated as a ticker and substituted into the
    template, never scanned as text, so an alert on `BUY-USD` can never block anything.
    """
    fields: dict[str, Any] = {
        "symbol": Symbol(alert.symbol),
        "direction": Movement.ABOVE if alert.op == "above" else Movement.BELOW,
        "level": float(alert.price),
        "last": float(quote.price),
        "currency": CurrencyCode(quote.currency.upper()),
    }
    title = render(TITLE, settings=settings, symbol=fields["symbol"])
    return title, render(BODY, settings=settings, **fields)


def check_price_alerts(
    db: Session, settings: Settings | None = None, sender: Sender | None = None
) -> list[Notification]:
    """Create a Notification (and a Telegram message if configured) for each newly hit alert.

    One alert can never stop the others: each alert is handled in its own try/except; a failure is
    logged and the alert is marked as triggered (so it is not retried every cycle).
    """
    s = settings or get_settings()
    send: Sender = sender or (lambda chat, text: send_telegram(chat, text, s))
    created: list[Notification] = []
    for alert in db.exec(select(PriceAlert).where(PriceAlert.active)).all():
        quote = db.get(PriceQuote, alert.symbol)
        if quote is None or not _triggered(alert, quote.price):
            continue
        alert_id = alert.id
        try:
            # The claim is one conditional UPDATE in the database, so even two processes checking at
            # the same moment (a second scheduler, a manual run) can never both fire the same alert.
            claim = db.execute(
                update(PriceAlert)
                .where(col(PriceAlert.id) == alert_id, col(PriceAlert.active))
                .values(active=False, triggered_at=utcnow())
            )
            if claim.rowcount != 1:  # type: ignore[attr-defined]
                continue
            title, body = price_alert_text(alert, quote, s)
            note = make_notification(alert.user_id, "price_alert", title, body, settings=s)
            db.add(note)
            created.append(note)
            db.flush()
        except Exception:
            log.exception("price alert %s could not be created; marked as triggered", alert_id)
            continue
        user = db.get(User, alert.user_id)
        if user is not None:
            try:
                send(user.telegram_chat_id, join_lines(title, body))
            except Exception as exc:
                log.warning(
                    "price alert %s: telegram send failed: %s", alert_id, type(exc).__name__
                )
    db.commit()
    return created
