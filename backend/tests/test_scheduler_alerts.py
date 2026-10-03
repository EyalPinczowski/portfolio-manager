from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, select

from app.alerts.price_alerts import check_price_alerts
from app.alerts.telegram import send_telegram
from app.config import Settings, get_settings
from app.models import (
    Holding,
    Notification,
    Portfolio,
    PortfolioSnapshot,
    PriceAlert,
    PriceQuote,
    Transaction,
    User,
)
from app.providers.cache import TTLCache, is_rate_limited, retry_with_backoff
from app.scheduler.calendars import is_market_open, is_tase_open, is_us_open, markets_status
from app.scheduler.jobs import run_daily_snapshots, run_quotes_cycle, symbols_for_cycle
from app.timeutil import local_today, utcnow
from tests.conftest import FakeQuotes

JLM = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")


def jl(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=JLM)


# ---------------------------------------------------------------- calendars
def test_tase_hours_mon_thu_and_friday_override() -> None:
    mon = (2026, 3, 2)
    assert not is_tase_open(jl(*mon, 9, 58))
    assert is_tase_open(jl(*mon, 9, 59))
    assert is_tase_open(jl(*mon, 17, 24))
    assert not is_tase_open(jl(*mon, 17, 25))
    fri = (2026, 3, 6)
    assert is_tase_open(jl(*fri, 13, 49))
    assert not is_tase_open(jl(*fri, 13, 50))
    assert not is_tase_open(jl(*fri, 15, 0))
    assert not is_tase_open(jl(2026, 3, 7, 11, 0))  # Saturday
    assert not is_tase_open(jl(2026, 3, 8, 11, 0))  # Sunday: TASE trades Mon-Fri since Jan 2026


def test_tase_hours_come_from_config_and_holidays() -> None:
    s = Settings(tase_hours={"mon": ("10:00", "11:00")}, tase_holidays=[date(2026, 3, 2)])
    assert not is_tase_open(jl(2026, 3, 2, 10, 30), s)  # holiday
    assert not is_tase_open(jl(2026, 3, 3, 10, 30), s)  # Tuesday not configured
    assert is_tase_open(jl(2026, 3, 9, 10, 30), Settings(tase_hours={"mon": ("10:00", "11:00")}))


def test_us_hours_follow_new_york_dst_not_fixed_utc_offsets() -> None:
    # 13:30 UTC is 08:30 EST before the US switch (Mar 8, 2026) but 09:30 EDT after it.
    before = datetime(2026, 3, 2, 14, 29, tzinfo=UTC)  # 09:29 EST
    assert not is_us_open(before)
    assert is_us_open(datetime(2026, 3, 2, 14, 30, tzinfo=UTC))
    assert not is_us_open(datetime(2026, 3, 9, 13, 29, tzinfo=UTC))  # 09:29 EDT
    assert is_us_open(datetime(2026, 3, 9, 13, 30, tzinfo=UTC))
    assert not is_us_open(datetime(2026, 3, 2, 21, 0, tzinfo=UTC))  # 16:00 EST close
    assert is_us_open(datetime(2026, 3, 2, 20, 59, tzinfo=UTC))


def test_israel_and_us_dst_change_on_different_dates() -> None:
    # Between Mar 8 (US) and Mar 27 (Israel) the offset between the two exchanges is 6 hours, not 7.
    t = datetime(2026, 3, 16, 9, 30, tzinfo=NY)
    assert t.astimezone(JLM).hour == 15 and t.astimezone(JLM).minute == 30
    t2 = datetime(2026, 3, 2, 9, 30, tzinfo=NY)
    assert t2.astimezone(JLM).hour == 16


def test_us_weekend_holiday_and_naive_datetimes_are_utc() -> None:
    assert not is_us_open(datetime(2026, 3, 7, 16, 0, tzinfo=UTC))  # Saturday
    assert not is_us_open(datetime(2026, 7, 3, 15, 0, tzinfo=UTC))  # observed Independence Day
    assert is_us_open(datetime(2026, 3, 2, 15, 0))  # naive = UTC
    assert is_market_open("CRYPTO", datetime(2026, 3, 7, 3, 0, tzinfo=UTC))
    st = markets_status(datetime(2026, 3, 7, 3, 0, tzinfo=UTC))
    assert st == {"US": {"open": False}, "TASE": {"open": False}, "CRYPTO": {"open": True}}


# ---------------------------------------------------------------- cache / backoff
def test_backoff_only_on_rate_limits() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("HTTP 429 Too Many Requests")
        return "ok"

    assert retry_with_backoff(flaky, retries=3, base_seconds=2.0, sleep=sleeps.append) == "ok"
    assert sleeps == [2.0, 4.0]

    def always() -> str:
        raise RuntimeError("429")

    sleeps.clear()
    with pytest.raises(RuntimeError):
        retry_with_backoff(always, retries=2, base_seconds=1.0, sleep=sleeps.append)
    assert sleeps == [1.0, 2.0]

    def broken() -> str:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        retry_with_backoff(broken, retries=3, base_seconds=1.0, sleep=sleeps.append)
    assert is_rate_limited(RuntimeError("Too Many Requests")) and not is_rate_limited(
        ValueError("x")
    )


def test_ttl_cache_expiry_and_stale_fallback() -> None:
    now = {"t": 0.0}
    c: TTLCache[int] = TTLCache(10, clock=lambda: now["t"])
    c.set("a", 1)
    assert c.get("a") == 1
    now["t"] = 11
    assert c.get("a") is None and c.get_stale("a") == 1


# ---------------------------------------------------------------- jobs
@pytest.fixture
def seeded(db: Session) -> Session:
    user = User(email="u@mail.com", password_hash="x", telegram_chat_id="42")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p", tracking_started_at=local_today())
    db.add(p)
    db.commit()
    for sym, qty in (("TEVA.TA", 100), ("AAPL", 10), ("BTC-USD", 1)):
        db.add(
            Holding(
                portfolio_id=p.id or 0, symbol=sym, quantity=qty, avg_cost=1.0, cost_currency="USD"
            )
        )
    db.commit()
    return db


def test_symbols_follow_market_hours(seeded: Session) -> None:
    tase_only = datetime(2026, 3, 2, 10, 30, tzinfo=JLM)  # 03:30 in New York
    syms = symbols_for_cycle(seeded, tase_only)
    assert "TEVA.TA" in syms and "BTC-USD" in syms and "AAPL" not in syms and "ILS=X" in syms
    # 16:45 in Israel is 09:45 in New York: both exchanges are open
    both = symbols_for_cycle(seeded, datetime(2026, 3, 2, 16, 45, tzinfo=JLM))
    assert {"TEVA.TA", "AAPL", "BTC-USD", "ILS=X"} <= set(both)
    us_only = symbols_for_cycle(seeded, datetime(2026, 3, 2, 18, 0, tzinfo=JLM))  # TASE closed
    assert "AAPL" in us_only and "TEVA.TA" not in us_only
    sat = symbols_for_cycle(seeded, datetime(2026, 3, 7, 11, 0, tzinfo=JLM))
    assert sat == ["BTC-USD"]  # crypto only on a weekend, no FX


def test_symbols_include_watchlist_from_active_alerts(seeded: Session) -> None:
    seeded.add(PriceAlert(user_id=1, symbol="MSFT", op="above", price=1.0))
    seeded.add(PriceAlert(user_id=1, symbol="NVDA", op="above", price=1.0, active=False))
    seeded.commit()
    syms = symbols_for_cycle(seeded, datetime(2026, 3, 2, 16, 45, tzinfo=JLM))
    assert "MSFT" in syms and "NVDA" not in syms


def test_quotes_cycle_is_one_batched_call_and_stores_normalised_quotes(
    seeded: Session, quotes: FakeQuotes
) -> None:
    quotes.set("TEVA.TA", 65.0, "ILS", 1.0)
    quotes.set("BTC-USD", 60000.0, "USD", 2.0)
    n = run_quotes_cycle(seeded, quotes, datetime(2026, 3, 2, 10, 30, tzinfo=JLM))
    assert n == 2 and len(quotes.calls) == 1
    assert set(quotes.calls[0]) == {"TEVA.TA", "BTC-USD", "ILS=X"}
    stored = seeded.get(PriceQuote, "TEVA.TA")
    assert stored is not None and stored.price == 65.0 and stored.currency == "ILS"


def test_quotes_cycle_keeps_stale_cache_when_provider_fails(seeded: Session) -> None:
    seeded.add(
        PriceQuote(
            symbol="TEVA.TA", price=64.0, currency="ILS", as_of=utcnow() - timedelta(hours=1)
        )
    )
    seeded.commit()

    class Failing:
        def get_quotes(self, symbols: list[str]) -> dict[str, Any]:
            raise RuntimeError("429 Too Many Requests")

    assert run_quotes_cycle(seeded, Failing(), datetime(2026, 3, 2, 10, 30, tzinfo=JLM)) == 0
    stale = seeded.get(PriceQuote, "TEVA.TA")
    assert (
        stale is not None and stale.price == 64.0 and stale.as_of < utcnow() - timedelta(minutes=30)
    )


def test_daily_snapshot_records_value_and_todays_flows(seeded: Session) -> None:
    seeded.add(PriceQuote(symbol="TEVA.TA", price=65.0, currency="ILS"))
    seeded.add(PriceQuote(symbol="AAPL", price=200.0, currency="USD"))
    seeded.add(PriceQuote(symbol="BTC-USD", price=50000.0, currency="USD"))
    seeded.add(PriceQuote(symbol=get_settings().fx_symbol, price=3.5, currency="ILS"))
    seeded.add(
        Transaction(
            portfolio_id=1,
            symbol="AAPL",
            type="buy",
            quantity=1,
            price=200,
            amount=200,
            currency="USD",
            fx_to_ils=3.5,
            date=local_today(),
        )
    )
    seeded.add(
        Transaction(
            portfolio_id=1,
            symbol="TEVA.TA",
            type="sell",
            quantity=1,
            price=65,
            amount=65,
            currency="ILS",
            fx_to_ils=1.0,
            date=local_today(),
        )
    )
    seeded.commit()
    assert run_daily_snapshots(seeded) == 1
    snap = seeded.exec(select(PortfolioSnapshot)).one()
    assert snap.value_ils == pytest.approx(100 * 65 + 10 * 200 * 3.5 + 50000 * 3.5)
    assert snap.net_flow_ils == pytest.approx(200 * 3.5 - 65)
    assert snap.value_usd == pytest.approx(snap.value_ils / 3.5)
    run_daily_snapshots(seeded)  # idempotent per day
    assert len(seeded.exec(select(PortfolioSnapshot)).all()) == 1


def test_no_snapshot_before_tracking_starts(db: Session) -> None:
    user = User(email="n@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    db.add(Portfolio(owner_id=user.id or 0, name="untracked"))
    db.commit()
    assert run_daily_snapshots(db) == 0


# ---------------------------------------------------------------- price alerts
def test_alert_fires_once_creates_notification_and_telegram(
    seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded.add(PriceAlert(user_id=1, symbol="TEVA.TA", op="above", price=70.0))
    seeded.add(PriceAlert(user_id=1, symbol="AAPL", op="below", price=150.0))
    seeded.add(PriceQuote(symbol="TEVA.TA", price=65.0, currency="ILS"))
    seeded.add(PriceQuote(symbol="AAPL", price=200.0, currency="USD"))
    seeded.commit()
    sent: list[tuple[str | None, str]] = []

    def sender(chat: str | None, text: str) -> bool:
        sent.append((chat, text))
        return True

    assert check_price_alerts(seeded, sender=sender) == []  # nothing crossed yet
    q = seeded.get(PriceQuote, "TEVA.TA")
    assert q is not None
    q.price = 71.0
    seeded.add(q)
    seeded.commit()
    created = check_price_alerts(seeded, sender=sender)
    assert len(created) == 1
    note = created[0]
    assert (
        note.kind == "price_alert"
        and "TEVA.TA" in note.title
        and "Not financial advice." in note.body
    )
    alert = seeded.exec(select(PriceAlert).where(PriceAlert.symbol == "TEVA.TA")).one()
    assert alert.active is False and alert.triggered_at is not None
    assert len(sent) == 1 and sent[0][0] == "42" and "Not financial advice." in sent[0][1]
    # alerts fire only on a change: re-scoring with the same price creates nothing new
    assert check_price_alerts(seeded, sender=sender) == []
    assert len(seeded.exec(select(Notification)).all()) == 1 and len(sent) == 1
    # the untouched alert is still armed
    assert seeded.exec(select(PriceAlert).where(PriceAlert.symbol == "AAPL")).one().active is True


def test_below_alert_and_no_quote(seeded: Session) -> None:
    seeded.add(PriceAlert(user_id=1, symbol="AAPL", op="below", price=150.0))
    seeded.add(PriceAlert(user_id=1, symbol="MSFT", op="above", price=1.0))  # never quoted
    seeded.add(PriceQuote(symbol="AAPL", price=150.0, currency="USD"))
    seeded.commit()
    created = check_price_alerts(seeded, sender=lambda c, t: True)
    assert [n.title for n in created] == ["Price alert: AAPL"]


def test_telegram_only_sends_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[dict[str, Any]] = []

    class Resp:
        def raise_for_status(self) -> None: ...

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> Resp:
        posted.append({"url": url, **json})
        return Resp()

    monkeypatch.setattr("app.alerts.telegram.httpx.post", fake_post)
    assert send_telegram("42", "hi", Settings(telegram_bot_token=None)) is False
    assert send_telegram(None, "hi", Settings(telegram_bot_token="T")) is False  # user not linked
    assert posted == []
    assert send_telegram("42", "hi", Settings(telegram_bot_token="T")) is True
    assert posted[0]["chat_id"] == "42" and "botT/sendMessage" in posted[0]["url"]
