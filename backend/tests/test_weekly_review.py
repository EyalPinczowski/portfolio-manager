"""Weekly review job: per-user settings, dedupe per week, quiet hours, template-only text."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.alerts.price_alerts import check_price_alerts
from app.alerts.telegram import FakeTelegramSender
from app.alerts.weekly_review import run_weekly_review
from app.config import DISCLAIMER, Settings
from app.db import new_session
from app.models import Notification, PriceAlert, PriceQuote, User, UserSettings
from app.outbound import verdict_words_in
from tests.conftest import FakeQuotes

SignupFn = Callable[..., TestClient]
S = Settings(_env_file=None)
# Sunday 2026-10-04 20:05 in Israel (UTC+3 until 2026-10-25)
SUN_2005 = datetime(2026, 10, 4, 17, 5, tzinfo=UTC)


def link(email: str, chat: str) -> None:
    with new_session() as db:
        u = db.exec(select(User).where(User.email == email)).one()
        u.telegram_chat_id = chat
        db.add(u)
        db.commit()


def with_portfolio(c: TestClient, quotes: FakeQuotes, name: str = "My Secret Name") -> int:
    quotes.set("AAPL", 200.0, "USD")
    pid = int(c.post("/api/portfolios", json={"name": name, "base_currency": "ILS"}).json()["id"])
    r = c.post(
        f"/api/portfolios/{pid}/holdings", json={"symbol": "AAPL", "quantity": 3, "avg_cost": 100}
    )
    assert r.status_code == 201, r.text
    return pid


def run(db: Session, sender: FakeTelegramSender, now: datetime = SUN_2005) -> int:
    return run_weekly_review(db, None, S, sender, now)


def setup_user(signup: SignupFn, quotes: FakeQuotes, email: str = "a@mail.com") -> TestClient:
    c = signup(email)
    with_portfolio(c, quotes)
    link(email, "111")
    return c


def test_default_schedule_sends_once_per_week(signup: SignupFn, quotes: FakeQuotes) -> None:
    setup_user(signup, quotes)
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake) == 1
        assert run(db, fake, SUN_2005 + timedelta(minutes=15)) == 0  # same week: no repeat
        assert run(db, fake, SUN_2005 + timedelta(hours=3)) == 0
    assert len(fake.sent) == 1 and fake.sent[0][0] == "111"
    text = fake.sent[0][1]
    assert text.startswith("Weekly review for the week starting 2026-10-04.")
    assert DISCLAIMER in text
    assert "Portfolio 1:" in text and "positions have a stop level" in text
    assert "My Secret Name" not in text  # user-chosen names are never put in messages
    assert verdict_words_in(text, S) == []
    with new_session() as db:
        row = db.get(UserSettings, 1)
        assert row is not None and row.last_weekly_review_week == date(2026, 10, 4)
    # next week it is sent again
    with new_session() as db:
        assert run(db, fake, SUN_2005 + timedelta(days=7)) == 1
    assert len(fake.sent) == 2 and "2026-10-11" in fake.sent[1][1]


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 10, 4, 16, 59, tzinfo=UTC),  # 19:59 local, a minute early
        datetime(2026, 10, 5, 17, 5, tzinfo=UTC),  # Monday
        datetime(2026, 10, 3, 17, 5, tzinfo=UTC),  # Saturday
    ],
)
def test_not_before_the_chosen_day_and_time(
    signup: SignupFn, quotes: FakeQuotes, when: datetime
) -> None:
    setup_user(signup, quotes)
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake, when) == 0
    assert fake.sent == []


def test_time_is_israel_time_across_the_dst_change(signup: SignupFn, quotes: FakeQuotes) -> None:
    setup_user(signup, quotes)
    fake = FakeTelegramSender()
    with new_session() as db:
        # 2026-11-01 is a Sunday; Israel is UTC+2 by then: 20:00 local = 18:00 UTC
        assert run(db, fake, datetime(2026, 11, 1, 17, 55, tzinfo=UTC)) == 0
        assert run(db, fake, datetime(2026, 11, 1, 18, 5, tzinfo=UTC)) == 1


def test_no_send_when_telegram_is_unlinked_or_the_account_is_disabled(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup("a@mail.com")
    with_portfolio(c, quotes)  # never linked
    d = signup("d@mail.com")
    with_portfolio(d, quotes)
    link("d@mail.com", "222")
    with new_session() as db:
        u = db.exec(select(User).where(User.email == "d@mail.com")).one()
        u.disabled_at = datetime(2026, 1, 1)
        db.add(u)
        db.commit()
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake) == 0
        assert db.exec(select(UserSettings)).all() == []  # nothing was written either
    assert fake.sent == []


def test_user_can_switch_it_off_and_change_day_and_time(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = setup_user(signup, quotes)
    fake = FakeTelegramSender()
    c.patch("/api/settings", json={"weekly_review": {"enabled": False}})
    with new_session() as db:
        assert run(db, fake) == 0
    c.patch(
        "/api/settings", json={"weekly_review": {"enabled": True, "day": "friday", "time": "09:30"}}
    )
    with new_session() as db:
        assert run(db, fake) == 0  # Sunday is no longer the day
        fri = datetime(2026, 10, 9, 6, 31, tzinfo=UTC)  # Friday 09:31 local
        assert run(db, fake, fri - timedelta(minutes=2)) == 0  # 09:29
        assert run(db, fake, fri) == 1
    assert "week starting 2026-10-04" in fake.sent[0][1]  # Sunday-based week of that Friday


def test_week_start_monday_sets_the_week_key(signup: SignupFn, quotes: FakeQuotes) -> None:
    c = setup_user(signup, quotes)
    c.patch("/api/settings", json={"week_start_day": "monday"})
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake) == 1
        row = db.get(UserSettings, 1)
        assert row is not None and row.last_weekly_review_week == date(2026, 9, 28)  # a Monday


def test_quiet_hours_delay_the_review_until_they_end(signup: SignupFn, quotes: FakeQuotes) -> None:
    c = setup_user(signup, quotes)
    c.patch("/api/settings", json={"quiet_hours": {"start": "19:00", "end": "21:00"}})
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake) == 0  # 20:05 is quiet
        assert run(db, fake, SUN_2005 + timedelta(hours=1)) == 1  # 21:05
    assert len(fake.sent) == 1


def test_a_failed_send_is_retried_and_not_marked_as_sent(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    setup_user(signup, quotes)
    failing = FakeTelegramSender(ok=False)
    with new_session() as db:
        assert run(db, failing) == 0
        row = db.get(UserSettings, 1)
        assert row is not None and row.last_weekly_review_week is None
    ok = FakeTelegramSender()
    with new_session() as db:
        assert run(db, ok, SUN_2005 + timedelta(minutes=15)) == 1


def test_a_sender_that_raises_does_not_stop_the_other_users(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    setup_user(signup, quotes, "a@mail.com")
    b = signup("b@mail.com")
    with_portfolio(b, quotes)
    link("b@mail.com", "222")

    class Flaky(FakeTelegramSender):
        def send(self, chat_id: str | None, text: str) -> bool:
            if chat_id == "111":
                raise RuntimeError("boom 111")
            return super().send(chat_id, text)

    fake = Flaky()
    with new_session() as db:
        assert run(db, fake) == 1
    assert [c for c, _ in fake.sent] == ["222"]


def test_no_portfolio_no_message(signup: SignupFn) -> None:
    signup("a@mail.com")
    link("a@mail.com", "111")
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake) == 0


def test_each_user_gets_only_their_own_numbers(signup: SignupFn, quotes: FakeQuotes) -> None:
    setup_user(signup, quotes, "a@mail.com")
    b = signup("b@mail.com")
    with_portfolio(b, quotes)
    quotes.set("AAPL", 200.0, "USD")
    b.post("/api/portfolios", json={"name": "second", "base_currency": "USD"})
    link("b@mail.com", "222")
    fake = FakeTelegramSender()
    with new_session() as db:
        assert run(db, fake) == 2
    by_chat: dict[str, str] = dict(fake.sent)
    assert "Portfolio 2" not in by_chat["111"]
    assert "Portfolio 2" in by_chat["222"]


def test_the_message_says_when_a_holding_period_is_missing(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    setup_user(signup, quotes)
    fake = FakeTelegramSender()
    with new_session() as db:
        run(db, fake)
    # the holding has no horizon: the review asks for it instead of guessing one
    assert "need a holding period" in fake.sent[0][1]


def test_price_alert_push_respects_the_user_switch_but_the_in_app_note_stays(
    signup: SignupFn, quotes: FakeQuotes
) -> None:
    c = signup("a@mail.com")
    link("a@mail.com", "111")
    with new_session() as db:
        db.add(PriceAlert(user_id=1, symbol="AAPL", op="above", price=100.0))
        db.add(PriceQuote(symbol="AAPL", price=101.0, currency="USD"))
        db.commit()
    c.patch("/api/settings", json={"price_alerts_enabled": False})
    pushed: list[Any] = []
    with new_session() as db:
        notes = check_price_alerts(db, S, sender=lambda chat, text: bool(pushed.append(text) or 1))
        assert len(notes) == 1 and len(db.exec(select(Notification)).all()) == 1
    assert pushed == []
    with new_session() as db:  # switched on again: a new alert pushes
        db.add(PriceAlert(user_id=1, symbol="AAPL", op="above", price=100.0))
        db.commit()
    c.patch("/api/settings", json={"price_alerts_enabled": True})
    with new_session() as db:
        check_price_alerts(db, S, sender=lambda chat, text: bool(pushed.append(text) or 1))
    assert len(pushed) == 1
