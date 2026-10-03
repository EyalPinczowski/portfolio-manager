"""Price alerts fire only on a change: when the rule first triggers (then it deactivates)."""

from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session, select

from app.alerts.telegram import send_telegram
from app.config import DISCLAIMER, Settings, get_settings
from app.models import Notification, PriceAlert, PriceQuote, User
from app.timeutil import utcnow

Sender = Callable[[str | None, str], bool]


def _triggered(alert: PriceAlert, price: float) -> bool:
    return price >= alert.price if alert.op == "above" else price <= alert.price


def check_price_alerts(
    db: Session, settings: Settings | None = None, sender: Sender | None = None
) -> list[Notification]:
    """Create a Notification (and a Telegram message if configured) for each newly hit alert."""
    s = settings or get_settings()
    send: Sender = sender or (lambda chat, text: send_telegram(chat, text, s))
    created: list[Notification] = []
    for alert in db.exec(select(PriceAlert).where(PriceAlert.active)).all():
        quote = db.get(PriceQuote, alert.symbol)
        if quote is None or not _triggered(alert, quote.price):
            continue
        alert.active = False
        alert.triggered_at = utcnow()
        db.add(alert)
        direction = "rose to or above" if alert.op == "above" else "fell to or below"
        body = (
            f"{alert.symbol} {direction} your alert price {alert.price:g} "
            f"(last {quote.price:g} {quote.currency}). {DISCLAIMER}"
        )
        note = Notification(
            user_id=alert.user_id,
            kind="price_alert",
            title=f"Price alert: {alert.symbol}",
            body=body,
        )
        db.add(note)
        created.append(note)
        user = db.get(User, alert.user_id)
        if user is not None:
            send(user.telegram_chat_id, f"{note.title}\n{body}")
    db.commit()
    return created
