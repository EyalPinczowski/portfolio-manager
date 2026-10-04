"""Leader lock and the in-process scheduler (SQLite file lock and Postgres advisory lock)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlmodel import Session

from app.alerts.price_alerts import check_price_alerts
from app.config import Settings, get_settings
from app.db import new_session
from app.models import PriceAlert, PriceQuote, User
from app.scheduler import inprocess
from app.scheduler.inprocess import InProcessScheduler
from app.scheduler.leader import (
    FileLeaderLock,
    LeaderLock,
    PostgresLeaderLock,
    make_leader_lock,
)
from tests.pgfixtures import pg_url  # noqa: F401  (fixture)

# --- the lock itself ---------------------------------------------------------------------------


@pytest.fixture(params=["file", "postgres"])
def lock_factory(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[], LeaderLock]:
    if request.param == "file":
        path = tmp_path / "leader.lock"
        return lambda: FileLeaderLock(path)
    url = request.getfixturevalue("pg_url")
    return lambda: PostgresLeaderLock(url, key=424242)


def test_only_one_of_two_contenders_wins(lock_factory: Callable[[], LeaderLock]) -> None:
    a, b = lock_factory(), lock_factory()
    assert a.acquire() is True
    assert b.acquire() is False
    assert a.still_held() and not b.still_held()
    assert a.acquire() is True  # idempotent for the holder
    a.release()
    assert b.acquire() is True  # released: the other contender can lead
    assert not a.still_held()
    b.release()


def test_release_is_safe_to_repeat(lock_factory: Callable[[], LeaderLock]) -> None:
    a = lock_factory()
    a.release()  # never held
    assert a.acquire()
    a.release()
    a.release()
    assert a.acquire()  # and it can be taken again
    a.release()


def test_postgres_lock_is_lost_with_its_connection(pg_url: str) -> None:  # noqa: F811
    a, b = PostgresLeaderLock(pg_url, key=7), PostgresLeaderLock(pg_url, key=7)
    assert a.acquire()
    assert a._conn is not None
    a._conn.connection.dbapi_connection.close()  # type: ignore[union-attr]  # the server drops it
    assert a.still_held() is False  # noticed, so a standby can take over
    assert b.acquire() is True
    b.release()


def test_postgres_lock_keys_are_independent(pg_url: str) -> None:  # noqa: F811
    a, b = PostgresLeaderLock(pg_url, key=1), PostgresLeaderLock(pg_url, key=2)
    assert a.acquire() and b.acquire()
    a.release()
    b.release()


def test_file_lock_is_taken_by_another_process(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "x.lock"
    code = (
        "import sys,time;from app.scheduler.leader import FileLeaderLock;"
        f"l=FileLeaderLock({str(path)!r});print(l.acquire(),flush=True);time.sleep(30)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "True"
        assert FileLeaderLock(path).acquire() is False
    finally:
        proc.kill()
        proc.wait()
    mine = FileLeaderLock(path)
    assert mine.acquire() is True  # the kernel released it when the holder died
    mine.release()


def test_factory_picks_the_lock_by_database_url(tmp_path: Path) -> None:
    sqlite = make_leader_lock(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/a.db"))
    assert isinstance(sqlite, FileLeaderLock)
    assert sqlite.path == Path(f"{tmp_path}/a.db.scheduler.lock")
    pg = make_leader_lock(Settings(_env_file=None, database_url="postgresql://u:p@h/d"))
    assert isinstance(pg, PostgresLeaderLock)
    assert pg.url == "postgresql+psycopg://u:p@h/d"
    # The lock connection may use another URL (session pooler) than the app (transaction pooler).
    split = make_leader_lock(
        Settings(
            _env_file=None,
            database_url="postgresql://u:p@pooler:6543/d",
            scheduler_lock_database_url="postgresql://u:p@pooler:5432/d",
        )
    )
    assert isinstance(split, PostgresLeaderLock) and ":5432/" in split.url
    explicit = make_leader_lock(
        Settings(
            _env_file=None,
            database_url=f"sqlite:///{tmp_path}/b.db",
            scheduler_lock_path=str(tmp_path / "z"),
        )
    )
    assert isinstance(explicit, FileLeaderLock) and explicit.path == tmp_path / "z"


# --- the scheduler -------------------------------------------------------------------------------

EXPECTED_JOBS = {
    "quotes",
    "daily_snapshot",
    "scores",
    "universe_scores",
    "purge_drafts",
    "purge_sessions",
    "weekly_review",
    "paper_resolve",
}


def _job_ids(runner: InProcessScheduler) -> set[str]:
    assert runner.scheduler is not None
    return {j.id for j in runner.scheduler.get_jobs()}


@pytest.fixture
def runners() -> Iterator[list[InProcessScheduler]]:
    made: list[InProcessScheduler] = []
    yield made
    for r in made:
        r.shutdown()


def _make(
    runners: list[InProcessScheduler],
    lock: LeaderLock,
    startup: Callable[[], None] = lambda: None,
    **settings: Any,
) -> InProcessScheduler:
    r = InProcessScheduler(
        lock=lock, settings=Settings(_env_file=None, **settings), startup=startup
    )
    runners.append(r)
    return r


def test_the_leader_registers_the_same_jobs_as_the_standalone_scheduler(
    runners: list[InProcessScheduler], tmp_path: Path
) -> None:
    from app.scheduler.setup import build_scheduler

    standalone = {j.id for j in build_scheduler().get_jobs()}
    r = _make(runners, FileLeaderLock(tmp_path / "l"))
    assert r.start() is True and r.is_leader
    assert standalone == EXPECTED_JOBS
    assert _job_ids(r) - {"startup"} == EXPECTED_JOBS  # the election is a thread, not a job
    assert r.scheduler is not None and r.scheduler.running
    quotes = r.scheduler.get_job("quotes")
    assert quotes is not None and quotes.max_instances == 1 and quotes.coalesce
    assert quotes.misfire_grace_time == get_settings().scheduler_misfire_grace_seconds


def test_the_catch_up_runs_once_per_leadership(
    runners: list[InProcessScheduler], tmp_path: Path
) -> None:
    ran = threading.Event()
    calls: list[int] = []

    def startup() -> None:
        calls.append(1)
        ran.set()

    r = _make(runners, FileLeaderLock(tmp_path / "l"), startup)
    r.start()
    assert ran.wait(10)
    r.elect()
    r.elect()  # the periodic leader check does not repeat it
    assert calls == [1]


def test_startup_tasks_run_the_snapshot_catch_up_before_priming_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(inprocess, "_catchup", lambda: order.append("catchup"))
    monkeypatch.setattr(inprocess, "_quotes", lambda: order.append("quotes"))
    inprocess.startup_tasks()
    assert order == ["catchup", "quotes"]


def test_a_second_instance_serves_without_jobs_and_takes_over_later(
    runners: list[InProcessScheduler], tmp_path: Path
) -> None:
    path = tmp_path / "l"
    first = _make(runners, FileLeaderLock(path))
    second = _make(runners, FileLeaderLock(path))
    assert first.start() is True
    assert second.start() is False
    assert _job_ids(second) == set()  # a standby has no jobs, only the election thread
    first.shutdown()  # the old instance of a rolling deploy exits
    second.elect()  # the next leader check
    assert second.is_leader and _job_ids(second) >= EXPECTED_JOBS


def test_shutdown_releases_the_lock_and_stops_the_scheduler(
    runners: list[InProcessScheduler], tmp_path: Path
) -> None:
    path = tmp_path / "l"
    r = _make(runners, FileLeaderLock(path))
    r.start()
    sched = r.scheduler
    r.shutdown()
    assert sched is not None and not sched.running
    other = FileLeaderLock(path)
    assert other.acquire() is True
    other.release()


def test_a_lost_lock_stops_the_jobs(runners: list[InProcessScheduler]) -> None:
    class Flaky:
        held = True

        def acquire(self) -> bool:
            return self.held

        def still_held(self) -> bool:
            return self.held

        def release(self) -> None:
            self.held = False

    lock = Flaky()
    r = _make(runners, lock)
    r.start()
    assert _job_ids(r) >= EXPECTED_JOBS
    lock.held = False
    r.elect()
    assert not r.is_leader
    assert not EXPECTED_JOBS & _job_ids(r)
    lock.held = True
    r.elect()  # and it comes back when the lock is regained
    assert r.is_leader and _job_ids(r) >= EXPECTED_JOBS


def test_the_postgres_scheduler_lock_end_to_end(
    runners: list[InProcessScheduler],
    pg_url: str,  # noqa: F811
) -> None:
    a = _make(runners, PostgresLeaderLock(pg_url, 99))
    b = _make(runners, PostgresLeaderLock(pg_url, 99))
    assert a.start() is True
    assert b.start() is False
    a.shutdown()
    b.elect()
    assert b.is_leader


# --- the API process ---------------------------------------------------------------------------


def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[], LeaderLock]:
    """Enable the in-process scheduler; returns a factory for a rival contender on the same lock
    (a file lock on SQLite, the advisory lock when the suite runs on Postgres)."""
    monkeypatch.setenv("SCHEDULER_IN_PROCESS", "true")
    monkeypatch.setenv("SCHEDULER_LOCK_PATH", str(tmp_path / "api.lock"))
    monkeypatch.setattr(inprocess, "_catchup", lambda: None)  # no network in tests
    monkeypatch.setattr(inprocess, "_quotes", lambda: None)
    get_settings.cache_clear()
    return lambda: make_leader_lock(get_settings())


def test_the_api_starts_the_scheduler_when_enabled(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import create_app

    rival = _api(monkeypatch, tmp_path)
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        runner = app.state.scheduler
        assert runner is not None and runner.is_leader
        assert _job_ids(runner) >= EXPECTED_JOBS
        assert rival().acquire() is False  # held while the API runs
    probe = rival()
    assert probe.acquire() is True  # released at shutdown
    probe.release()


def test_the_api_still_starts_when_the_lock_is_taken(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import create_app

    other = _api(monkeypatch, tmp_path)()
    assert other.acquire()
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        runner = app.state.scheduler
        assert runner is not None and not runner.is_leader
        assert EXPECTED_JOBS.isdisjoint(_job_ids(runner))
    other.release()


def test_the_api_starts_when_the_scheduler_cannot(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import create_app

    _api(monkeypatch, tmp_path)

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("no lock for you")

    monkeypatch.setattr(FileLeaderLock, "acquire", boom)
    monkeypatch.setattr(PostgresLeaderLock, "acquire", boom)
    with TestClient(create_app()) as c:
        assert c.get("/api/health").status_code == 200


def test_the_scheduler_is_off_by_default(env: None) -> None:
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert app.state.scheduler is None


# --- alert dedupe lives in the database ----------------------------------------------------------


def test_an_alert_claimed_by_another_process_does_not_fire_twice(db: Session) -> None:
    user = User(email="a@x.co", password_hash="h", telegram_chat_id="1")
    db.add(user)
    db.flush()
    assert user.id is not None
    db.add(PriceAlert(user_id=user.id, symbol="AAPL", op="above", price=100.0))
    db.add(PriceQuote(symbol="AAPL", price=105.0, currency="USD"))
    db.commit()

    real_exec = db.exec
    stolen = {"done": False}

    def exec_then_lose_the_race(stmt: Any, *a: Any, **k: Any) -> Any:
        rows = real_exec(stmt, *a, **k).all()
        if not stolen["done"] and any(isinstance(r, PriceAlert) for r in rows):
            stolen["done"] = True
            with new_session() as other:  # a second process fires it first
                other.execute(update(PriceAlert).values(active=False))
                other.commit()
        return SimpleNamespace(all=lambda: rows)

    sent: list[str] = []
    db.exec = exec_then_lose_the_race  # type: ignore[method-assign,assignment]
    made = check_price_alerts(db, sender=lambda _chat, text: sent.append(text) or True)
    assert made == [] and sent == []


def test_an_alert_fires_exactly_once_across_runs(db: Session) -> None:
    user = User(email="b@x.co", password_hash="h")
    db.add(user)
    db.flush()
    assert user.id is not None
    db.add(PriceAlert(user_id=user.id, symbol="AAPL", op="below", price=100.0))
    db.add(PriceQuote(symbol="AAPL", price=90.0, currency="USD"))
    db.commit()
    sent: list[str] = []
    for _ in range(3):
        check_price_alerts(db, sender=lambda _c, t: sent.append(t) or True)
    assert len(sent) == 1
    alert = db.exec(PriceAlert.__table__.select()).first()  # type: ignore[attr-defined]
    assert alert is not None and alert.triggered_at is not None
