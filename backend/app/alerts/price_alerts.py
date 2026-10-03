"""Price alerts fire only on a change: when the rule first triggers (then it deactivates)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.alerts.telegram import send_telegram
from app.config import DISCLAIMER, Settings, get_settings
from app.models import Notification, PriceAlert, PriceQuote, User
from app.outbound import release_text
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
        # The claim is one conditional UPDATE in the database, so even two processes checking at the
        # same moment (a second scheduler, a manual run) can never both fire the same alert.
        claim = db.execute(
            update(PriceAlert)
            .where(col(PriceAlert.id) == alert.id, col(PriceAlert.active))
            .values(active=False, triggered_at=utcnow())
        )
        if claim.rowcount != 1:  # type: ignore[attr-defined]
            continue
        direction = "rose to or above" if alert.op == "above" else "fell to or below"
        body = (
            f"{alert.symbol} {direction} your alert price {alert.price:g} "
            f"(last {quote.price:g} {quote.currency}). {DISCLAIMER}"
        )
        note = Notification(
            user_id=alert.user_id,
            kind="price_alert",
            title=release_text(f"Price alert: {alert.symbol}", settings=s),
            body=release_text(body, settings=s),
        )
        db.add(note)
        created.append(note)
        user = db.get(User, alert.user_id)
        if user is not None:
            send(user.telegram_chat_id, release_text(f"{note.title}\n{body}", settings=s))
    db.commit()
    return created
