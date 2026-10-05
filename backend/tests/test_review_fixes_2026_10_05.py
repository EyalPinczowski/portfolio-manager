"""Regression tests for the 2026-10-05 backend review (H1, M1-M4, L1-L6). Fixture data only."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import cli
from app.alerts.price_alerts import check_price_alerts
from app.auth.account import delete_user
from app.auth.ratelimit import client_ip
from app.config import Settings, get_settings
from app.llm.cache import put_cached
from app.llm.ledger import quota_try_add, quota_used
from app.models import (
    Holding,
    LlmCache,
    Portfolio,
    PortfolioSnapshot,
    PriceAlert,
    PriceQuote,
    Transaction,
    User,
)
from app.portfolio.performance import DayPoint, combine_points, period_result
from app.portfolio.summary import build_summary
from app.portfolio.valuation import take_catchup_snapshots, take_snapshot
from app.providers.fallback_sources import _last_close_quote
from app.providers.yfinance_provider import YFinanceProvider
from app.scheduler.calendars import session_close_utc_naive
from app.scoring.risk import Position, check_limits, resolve_risk_filter
from app.timeutil import utcnow
from tests.test_security_auth import fake_request


# ---------------------------------------------------------------- H1
def test_h1_same_day_buy_on_join_day_is_not_a_loss() -> None:
    a = [DayPoint(date(2026, 10, 1), 50_000, 0.0), DayPoint(date(2026, 10, 6), 50_000, 0.0)]
    # B joins on Oct 5 with 20k; 10k of it was a buy booked the same day (flow already in value)
    b = [DayPoint(date(2026, 10, 5), 20_000, 10_000.0), DayPoint(date(2026, 10, 6), 20_000, 0.0)]
    pts = combine_points([a, b])
    res = period_result(pts)
    assert res.pnl_ils == pytest.approx(0.0)
    assert res.pct == pytest.approx(0.0)
    joined = next(p for p in pts if p.date == date(2026, 10, 5))
    assert joined.net_flow_ils == pytest.approx(20_000)


# ---------------------------------------------------------------- M1
def test_m1_stale_last_bar_is_a_last_close_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = YFinanceProvider()
    monkeypatch.setattr(prov, "raw_currency", lambda s: "USD")
    now = datetime(2026, 10, 5, 15, 0)  # Monday 11:00 New York, US open
    frame = pd.DataFrame(
        {"Close": [100.0, 101.0]}, index=pd.to_datetime(["2026-10-01", "2026-10-02"])
    )  # last bar is Friday; the current session is Monday's
    q = prov._quote_from_frame("AAPL", frame, now)
    assert q is not None and q.basis == "last_close"
    assert q.as_of == session_close_utc_naive("US", date(2026, 10, 2))
    assert q.as_of == datetime(2026, 10, 2, 20, 0)  # 16:00 EDT, not a hard-coded winter 21:00
    fresh = pd.DataFrame(
        {"Close": [100.0, 101.0]}, index=pd.to_datetime(["2026-10-02", "2026-10-05"])
    )
    q2 = prov._quote_from_frame("AAPL", fresh, now)
    assert q2 is not None and q2.basis == "live" and q2.as_of == now


# ---------------------------------------------------------------- M2
def test_m2_summary_uses_the_users_week_start_day(db: Session) -> None:
    user = User(email="m@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p", tracking_started_at=date(2026, 2, 20))
    db.add(p)
    db.commit()
    now = datetime(2026, 3, 4, 10, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))  # Wednesday
    assert build_summary(db, [p], None, now=now)["week_start"] == date(2026, 3, 1)  # Sunday
    monday = build_summary(db, [p], None, now=now, week_start_day="monday")
    assert monday["week_start"] == date(2026, 3, 2)


def test_m2_route_resolves_the_setting_per_user(signup) -> None:  # type: ignore[no-untyped-def]
    c = signup()
    pid = c.post("/api/portfolios", json={"name": "p"}).json()["id"]
    assert c.patch("/api/settings", json={"week_start_day": "monday"}).status_code == 200
    for url in (f"/api/portfolios/{pid}/summary", "/api/portfolios/combined/summary"):
        ws = date.fromisoformat(c.get(url).json()["week_start"])
        assert ws.weekday() == 0


# ---------------------------------------------------------------- M3
def test_m3_catchup_redoes_the_start_day_baseline_when_a_same_day_buy_missed_it(
    db: Session,
) -> None:
    day = date(2026, 3, 2)
    user = User(email="c@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p", tracking_started_at=day)
    db.add(p)
    db.commit()
    db.add(Holding(portfolio_id=p.id or 0, symbol="TEVA.TA", quantity=10, avg_cost=100))
    db.add(PriceQuote(symbol="TEVA.TA", price=100.0, currency="ILS", as_of=utcnow()))
    db.add(PriceQuote(symbol=get_settings().fx_symbol, price=3.5, currency="ILS"))
    db.commit()
    take_snapshot(db, p, day)  # baseline "X only" = 1000
    h = db.exec(select(Holding)).one()
    h.quantity = 20  # Y bought later the same day ...
    db.add(h)
    db.add(Transaction(portfolio_id=p.id or 0, type="buy", amount=1000.0, currency="ILS", date=day))
    db.commit()  # ... and the 23:59 job never ran
    assert take_catchup_snapshots(db, today=day + timedelta(days=1)) == 1
    snap = db.exec(select(PortfolioSnapshot)).one()
    assert snap.value_ils == pytest.approx(2000.0) and snap.net_flow_ils == pytest.approx(1000.0)
    assert take_catchup_snapshots(db, today=day + timedelta(days=1)) == 0  # now up to date


# ---------------------------------------------------------------- M4
def _alert_db(db: Session) -> None:
    db.add(User(email="u@mail.com", password_hash="x", telegram_chat_id="42"))
    db.commit()
    db.add(PriceAlert(user_id=1, symbol="AAPL", op="above", price=100.0))
    db.add(PriceQuote(symbol="AAPL", price=120.0, currency="USD"))
    db.commit()


def test_m4_telegram_is_sent_after_the_commit(db: Session) -> None:
    _alert_db(db)
    seen: list[bool] = []

    def sender(chat: str | None, text: str) -> bool:
        seen.append(not db.in_transaction())  # the claim is already committed
        return True

    assert len(check_price_alerts(db, sender=sender)) == 1
    assert seen == [True]


def test_m4_failed_commit_sends_nothing(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _alert_db(db)
    sent: list[str] = []

    def boom() -> None:
        raise RuntimeError("db busy")

    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError):
        check_price_alerts(db, sender=lambda chat, text: bool(sent.append(text) or True))
    assert sent == []


# ---------------------------------------------------------------- L1
def test_l1_dual_listing_merge_uses_the_strictest_override() -> None:
    def p(sym: str, cap: float) -> Position:
        return Position(
            sym, sym, 20, "Healthcare", "Israel", "ILS", max_position_pct=cap, dual_group="TEVA"
        )

    ps = [p("TEVA.TA", 30), p("TEVA", 10), Position("X", "X", 160, "Tech", "US", "USD")]
    # merged value 40 of 200 = 20%: above 10%, below 30%
    rf = resolve_risk_filter({"preset": "balanced", "max_position_pct": 50})
    breaches = [b for b in check_limits(ps, rf) if b.rule == "max_position_pct"]
    assert any(b.symbol == "TEVA.TA+TEVA" and b.limit == 10 for b in breaches)


# ---------------------------------------------------------------- L2
def test_l2_quota_take_is_conditional_and_stops_at_the_cap(db: Session) -> None:
    day = date(2026, 10, 5)
    taken = [quota_try_add("committee:user:9", 3, day) for _ in range(5)]
    assert taken == [True, True, True, False, False]
    assert quota_used("committee:user:9", day) == 3
    assert quota_try_add("committee:user:9", 0, date(2026, 10, 6)) is False
    assert quota_used("committee:user:9", date(2026, 10, 6)) == 0


# ---------------------------------------------------------------- L3
def test_l3_bootstrap_admin_integrity_error_is_already_exists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Settings(
        _env_file=None, bootstrap_admin_email="a@mail.com", bootstrap_admin_password="x" * 16
    )

    def race(email: str, password: str) -> int:
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(cli, "create_admin", race)
    assert cli.bootstrap_admin(s) == "already exists"

    def broken(email: str, password: str) -> int:
        raise OSError("disk gone a@mail.com")

    monkeypatch.setattr(cli, "create_admin", broken)
    status = cli.bootstrap_admin(s)
    assert status.startswith("error") and "a@mail.com" not in status
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    assert cli.main(["bootstrap-admin"]) == 1
    out = capsys.readouterr().out
    assert "a@mail.com" not in out and "x" * 16 not in out


# ---------------------------------------------------------------- L4
def test_l4_delete_user_removes_that_users_cached_llm_answers(db: Session) -> None:
    user = User(email="d@mail.com", password_hash="x")
    other = User(email="o@mail.com", password_hash="x")
    db.add(user)
    db.add(other)
    db.commit()
    for key, scope in (("k1", f"user:{user.id}"), ("k2", f"user:{other.id}"), ("k3", "global")):
        put_cached(key, "cio", "p", "m", "{}", scope=scope)
    delete_user(db, user)
    assert {r.key for r in db.exec(select(LlmCache)).all()} == {"k2", "k3"}


# ---------------------------------------------------------------- L5
@pytest.mark.parametrize(
    ("day", "utc_hour"), [(date(2026, 7, 1), 20), (date(2026, 12, 1), 21)]
)  # EDT vs EST
def test_l5_fallback_us_close_follows_dst(day: date, utc_hour: int) -> None:
    df = pd.DataFrame({"Close": [10.0, 11.0]}, index=pd.to_datetime([day - timedelta(days=1), day]))
    q = _last_close_quote("AAPL", df, "stooq")
    assert q is not None and q.as_of == datetime.combine(day, datetime.min.time()).replace(
        hour=utc_hour
    )
    assert q.as_of.tzinfo is None and q.basis == "last_close"


# ---------------------------------------------------------------- L6
def test_l6_fallback_header_needs_a_trusted_peer() -> None:
    hdr = {"CF-Connecting-IP": "203.0.113.9"}
    base = {"fallback_ip_header": "CF-Connecting-IP", "_env_file": None}
    no_cidrs = Settings(**base)  # type: ignore[arg-type]
    assert client_ip(fake_request("10.0.0.5", hdr), no_cidrs) == "10.0.0.5"
    s = Settings(trusted_proxy_cidrs=["10.0.0.0/8"], **base)  # type: ignore[arg-type]
    assert client_ip(fake_request("10.0.0.5", hdr), s) == "203.0.113.9"
    assert client_ip(fake_request("198.51.100.4", hdr), s) == "198.51.100.4"
