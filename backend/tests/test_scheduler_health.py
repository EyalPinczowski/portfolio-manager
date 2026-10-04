"""Phase 2.0-B item 6: the election has its own thread; /api/health shows the scheduler state."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.config import Settings, get_settings
from app.db import get_engine, new_session
from app.health import HealthProbe
from app.models import Portfolio, PortfolioSnapshot, PriceQuote, User
from app.scheduler import inprocess
from app.scheduler.inprocess import ELECTION_THREAD_NAME, InProcessScheduler
from app.scheduler.leader import FileLeaderLock, PostgresLeaderLock, make_leader_lock
from app.timeutil import utcnow


class Flaky:
    def __init__(self) -> None:
        self.held = True

    def acquire(self) -> bool:
        return self.held

    def still_held(self) -> bool:
        return self.held

    def release(self) -> None:
        self.held = False


@pytest.fixture
def runners() -> Iterator[list[InProcessScheduler]]:
    made: list[InProcessScheduler] = []
    yield made
    for r in made:
        r.shutdown()


def make(runners: list[InProcessScheduler], lock: object, **kw: object) -> InProcessScheduler:
    r = InProcessScheduler(
        lock=lock,  # type: ignore[arg-type]
        settings=Settings(_env_file=None, scheduler_leader_check_seconds=1, **kw),  # type: ignore[arg-type]
        startup=lambda: None,
    )
    runners.append(r)
    return r


# ---------------------------------------------------------------- the election thread
def test_a_long_job_cannot_delay_lock_loss_detection(runners: list[InProcessScheduler]) -> None:
    """Before: the election shared the single job worker, so a lost lock was noticed only after the
    running job finished (split brain for that long)."""
    lock = Flaky()
    r = make(runners, lock)
    assert r.start() is True
    assert r.scheduler is not None
    started, release = threading.Event(), threading.Event()

    def slow_job() -> None:
        started.set()
        release.wait(30)  # occupies the only worker thread

    r.scheduler.add_job(slow_job, id="slow")
    assert started.wait(5)
    lock.held = False  # the Postgres connection dropped
    deadline = time.monotonic() + 4.0
    while r.is_leader and time.monotonic() < deadline:
        time.sleep(0.05)
    still_running = not release.is_set()
    release.set()
    assert not r.is_leader, "lock loss must be noticed while the job is still running"
    assert still_running
    assert r.state == "standby"


def test_the_election_runs_in_its_own_named_thread(runners: list[InProcessScheduler]) -> None:
    r = make(runners, Flaky())
    r.start()
    names = {t.name for t in threading.enumerate()}
    assert ELECTION_THREAD_NAME in names
    r.shutdown()
    time.sleep(0.1)
    assert ELECTION_THREAD_NAME not in {t.name for t in threading.enumerate()}


def test_a_standby_takes_over_by_itself(runners: list[InProcessScheduler], tmp_path: Path) -> None:
    path = tmp_path / "l"
    first = make(runners, FileLeaderLock(path))
    second = make(runners, FileLeaderLock(path))
    assert first.start() and not second.start()
    first.shutdown()
    deadline = time.monotonic() + 4.0
    while not second.is_leader and time.monotonic() < deadline:
        time.sleep(0.05)
    assert second.is_leader and second.state == "leader"


def test_an_unopenable_lock_file_is_loud_but_not_fatal(
    runners: list[InProcessScheduler], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = FileLeaderLock(tmp_path / "no-such-dir" / "x.lock")  # open() fails: a read-only disk
    r = make(runners, bad)
    with caplog.at_level(logging.ERROR, logger="scheduler"):
        assert r.start() is False  # does not raise
    assert r.state == "unavailable" and r.error == "FileNotFoundError"
    assert any("scheduler: unavailable" in rec.message for rec in caplog.records)
    # logged once per kind of error, not every interval
    time.sleep(1.5)
    assert sum("scheduler: unavailable" in rec.message for rec in caplog.records) == 1
    (tmp_path / "no-such-dir").mkdir()  # the disk recovers: the election picks it up
    deadline = time.monotonic() + 4.0
    while not r.is_leader and time.monotonic() < deadline:
        time.sleep(0.05)
    assert r.is_leader and r.state == "leader" and r.error is None


def test_a_failing_still_held_check_is_treated_as_lost(runners: list[InProcessScheduler]) -> None:
    class Dies(Flaky):
        def still_held(self) -> bool:
            raise OSError("connection reset")

    r = make(runners, Dies())
    r.start()
    assert r.is_leader
    r.elect()
    assert not r.is_leader and r.state == "unavailable"


# ---------------------------------------------------------------- /api/health
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lock: str | None = None) -> None:
    monkeypatch.setenv("SCHEDULER_IN_PROCESS", "true")
    monkeypatch.setenv("SCHEDULER_LOCK_PATH", lock or str(tmp_path / "api.lock"))
    monkeypatch.setattr(inprocess, "_catchup", lambda: None)
    monkeypatch.setattr(inprocess, "_quotes", lambda: None)
    get_settings.cache_clear()


def test_health_shows_a_leader_and_the_newest_quote_and_snapshot(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import create_app

    _api(monkeypatch, tmp_path)
    with new_session() as db:
        user = User(email="h@mail.com", password_hash="x")
        db.add(user)
        db.flush()
        assert user.id is not None
        p = Portfolio(owner_id=user.id, name="p", base_currency="ILS")
        db.add(p)
        db.flush()
        assert p.id is not None
        db.add(PriceQuote(symbol="AAPL", price=1.0, currency="USD", as_of=utcnow()))
        old = utcnow() - timedelta(hours=5)
        db.add(PriceQuote(symbol="MSFT", price=1.0, currency="USD", as_of=old))
        db.add(
            PortfolioSnapshot(portfolio_id=p.id, date=date(2026, 10, 1), value_ils=1, value_usd=1)
        )
        db.add(
            PortfolioSnapshot(portfolio_id=p.id, date=date(2026, 10, 2), value_ils=1, value_usd=1)
        )
        db.commit()
    with TestClient(create_app()) as c:
        body = c.get("/api/health").json()
    assert body["status"] == "ok" and body["scheduler"] == "leader" and body["leader"] is True
    assert body["last_snapshot_at"] == "2026-10-02"
    assert body["last_quotes_at"].endswith("Z") or "+00:00" in body["last_quotes_at"]


def test_health_shows_standby_and_unavailable_and_external(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import create_app

    with TestClient(create_app()) as c:  # scheduler off in this process
        body = c.get("/api/health").json()
    assert body["scheduler"] == "external" and body["leader"] is False
    assert body["last_quotes_at"] is None and body["last_snapshot_at"] is None

    _api(monkeypatch, tmp_path)
    holder = make_leader_lock(get_settings())  # file lock on SQLite, advisory lock on Postgres
    assert holder.acquire()
    with TestClient(create_app()) as c:
        body = c.get("/api/health").json()
    holder.release()
    assert body["scheduler"] == "standby" and body["leader"] is False

    _api(
        monkeypatch, tmp_path, lock=str(tmp_path / "missing-dir" / "x.lock")
    )  # SQLite: cannot open

    def boom(*_a: object, **_k: object) -> bool:
        raise OSError("lock unavailable")

    monkeypatch.setattr(PostgresLeaderLock, "acquire", boom)  # the same failure on Postgres
    app = create_app()
    with TestClient(app) as c:  # the app still starts; health says why jobs are not running
        res = c.get("/api/health")
        assert res.status_code == 200
        assert res.json()["scheduler"] == "unavailable" and res.json()["leader"] is False


def test_health_reads_the_database_at_most_once_per_ten_seconds(env: None) -> None:
    now = [0.0]
    probe = HealthProbe(ttl_seconds=10.0, clock=lambda: now[0])
    statements: list[str] = []

    def count(_c: object, _cur: object, statement: str, *_a: object) -> None:
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        for _ in range(50):
            probe.read()
        assert len(statements) == 1  # one cheap SELECT of two MAX() values
        now[0] = 9.9
        probe.read()
        assert len(statements) == 1
        now[0] = 10.1
        probe.read()
        assert len(statements) == 2
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)


def test_health_survives_a_database_error(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import health

    def boom() -> object:
        raise RuntimeError("db down")

    monkeypatch.setattr(health, "new_session", boom)
    assert HealthProbe().read() == (None, None, None)


def test_health_is_in_the_openapi_contract(client: TestClient) -> None:
    schema = client.get("/api/openapi.json").json()["components"]["schemas"]["HealthOut"]
    assert set(schema["properties"]) == {
        "status", "scheduler", "leader", "last_quotes_at", "last_snapshot_at",
        "last_paper_resolve_at",
    }  # fmt: skip
