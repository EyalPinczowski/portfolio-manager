"""The scheduler inside the API process (`SCHEDULER_IN_PROCESS=true`), for single-host deployments.

One BackgroundScheduler with a small thread pool. It starts the jobs only while this process holds
the leader lock (`app.scheduler.leader`). The election runs in its OWN daemon thread, never on the job
pool: a standby retries the lock (the old instance of a rolling deploy exits, this one takes over) and
the leader verifies it still holds it (a dropped database connection loses a Postgres advisory lock).
Because the election does not share the (single) job worker, a long job cannot delay noticing a lost
lock. Without the lock the API simply keeps serving. If the lock cannot even be opened (a read-only
disk) the error is logged loudly, `state` is "unavailable" (shown in /api/health) and the election
keeps retrying every interval.

State that matters lives in the database (price-alert dedupe is an atomic UPDATE; snapshots, score
freshness and quotes are rows), so a restart or a change of leader never loses or repeats work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings, get_settings
from app.scheduler.leader import LeaderLock, make_leader_lock
from app.scheduler.setup import JOB_IDS, _catchup, _quotes, register_jobs, tase_directory_if_empty

log = logging.getLogger("scheduler")

ELECTION_THREAD_NAME = "scheduler-election"
STARTUP_JOB_ID = "startup"


def startup_tasks() -> None:
    """Once per leadership: fill a missed snapshot, then prime the quotes."""
    _catchup()
    _quotes()
    try:
        tase_directory_if_empty()  # a first fill of the TASE list when a key exists
    except Exception as exc:  # never blocks the other startup work
        log.warning("tase directory first fill failed: %s", type(exc).__name__)


class InProcessScheduler:
    def __init__(
        self,
        lock: LeaderLock | None = None,
        settings: Settings | None = None,
        startup: Callable[[], None] = startup_tasks,
    ) -> None:
        self.settings = settings or get_settings()
        self.lock = lock or make_leader_lock(self.settings)
        self._startup = startup
        self._mutex = threading.Lock()
        self._leader = False
        self._scheduler: BackgroundScheduler | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    @property
    def is_leader(self) -> bool:
        return self._leader

    @property
    def error(self) -> str | None:
        """Type of the last election error (None while elections work)."""
        return self._error

    @property
    def state(self) -> Literal["leader", "standby", "unavailable"]:
        if self._error is not None:
            return "unavailable"
        return "leader" if self._leader else "standby"

    @property
    def scheduler(self) -> BackgroundScheduler | None:
        return self._scheduler

    def start(self) -> bool:
        """Start the scheduler machinery; returns whether this process is the leader."""
        s = self.settings
        sched = BackgroundScheduler(
            timezone=ZoneInfo(s.scheduler_timezone),
            executors={"default": ThreadPoolExecutor(max_workers=s.scheduler_max_workers)},
        )
        self._scheduler = sched
        sched.start()
        self.elect()  # decide now, so the caller (and the tests) can see the outcome
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._election_loop, name=ELECTION_THREAD_NAME, daemon=True
        )
        self._thread.start()
        return self._leader

    def _election_loop(self) -> None:
        interval = max(0.05, float(self.settings.scheduler_leader_check_seconds))
        while not self._stop.wait(interval):
            self.elect()

    def elect(self) -> None:
        """Acquire (standby) or verify (leader) the lock and start or stop the jobs accordingly.
        Never raises: an election error is logged and shown as `state == "unavailable"`."""
        with self._mutex:
            sched = self._scheduler
            if sched is None:
                return
            try:
                self._elect(sched)
            except Exception as exc:
                name = type(exc).__name__
                if self._error != name:  # once per kind, not every interval
                    log.error(
                        "scheduler: unavailable, the leader lock cannot be used (%s); "
                        "the API keeps serving and the election retries every %ss",
                        name,
                        self.settings.scheduler_leader_check_seconds,
                    )
                self._error = name
                if self._leader:  # cannot verify the lock: assume it is lost
                    self._leader = False
                    self._remove_jobs(sched)
                return
            if self._error is not None:
                log.info("scheduler: leader lock is usable again")
            self._error = None

    def _elect(self, sched: BackgroundScheduler) -> None:
        if self._leader:
            if not self.lock.still_held():
                log.warning("leader lock lost: stopping the in-process jobs")
                self._leader = False
                self._remove_jobs(sched)
            return
        if self.lock.acquire():
            log.info("leader lock acquired: starting the in-process jobs")
            self._leader = True
            register_jobs(sched, self.settings)
            sched.add_job(
                self._run_startup,
                "date",
                run_date=datetime.now(ZoneInfo(self.settings.scheduler_timezone))
                + timedelta(seconds=max(0, self.settings.scheduler_start_delay_seconds)),
                id=STARTUP_JOB_ID,
                replace_existing=True,
                misfire_grace_time=self.settings.scheduler_misfire_grace_seconds,
            )
        else:
            log.info("another instance holds the leader lock: serving the API only")

    def _run_startup(self) -> None:
        # The one-shot job removes itself after it ran, so it is never removed by hand (a manual
        # removal can race with the scheduler thread). A process that lost the lock meanwhile skips it.
        if self._leader:
            self._startup()

    @staticmethod
    def _remove_jobs(sched: BackgroundScheduler) -> None:
        for job_id in JOB_IDS:
            if sched.get_job(job_id) is not None:
                sched.remove_job(job_id)

    def shutdown(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._mutex:
            sched, self._scheduler = self._scheduler, None
            self._leader = False
        if sched is not None and sched.running:
            sched.shutdown(wait=False)
        self.lock.release()


def start_in_process_scheduler(settings: Settings | None = None) -> InProcessScheduler | None:
    """Used by the API lifespan. Never raises: a scheduler problem must not stop the API."""
    try:
        runner = InProcessScheduler(settings=settings)
        runner.start()
        return runner
    except Exception as exc:
        log.error("in-process scheduler failed to start: %s", type(exc).__name__)
        return None
