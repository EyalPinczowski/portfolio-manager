"""The scheduler inside the API process (`SCHEDULER_IN_PROCESS=true`), for single-host deployments.

One BackgroundScheduler with a small thread pool. It starts the jobs only while this process holds
the leader lock (`app.scheduler.leader`). Every instance runs a light `leader_check` job: a standby
retries the lock (the old instance of a rolling deploy exits, this one takes over) and the leader
verifies it still holds it (a dropped database connection loses a Postgres advisory lock). Without
the lock the API simply keeps serving.

State that matters lives in the database (price-alert dedupe is an atomic UPDATE; snapshots, score
freshness and quotes are rows), so a restart or a change of leader never loses or repeats work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.scheduler.leader import LeaderLock, make_leader_lock
from app.scheduler.setup import JOB_IDS, _catchup, _quotes, register_jobs

log = logging.getLogger("scheduler")

ELECTION_JOB_ID = "leader_check"
STARTUP_JOB_ID = "startup"


def startup_tasks() -> None:
    """Once per leadership: fill a missed snapshot, then prime the quotes."""
    _catchup()
    _quotes()


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

    @property
    def is_leader(self) -> bool:
        return self._leader

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
        sched.add_job(
            self.elect,
            IntervalTrigger(seconds=s.scheduler_leader_check_seconds),
            id=ELECTION_JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=s.scheduler_leader_check_seconds,
        )
        return self._leader

    def elect(self) -> None:
        """Acquire (standby) or verify (leader) the lock and start or stop the jobs accordingly."""
        with self._mutex:
            sched = self._scheduler
            if sched is None:
                return
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
                    run_date=datetime.now(ZoneInfo(self.settings.scheduler_timezone)),
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
