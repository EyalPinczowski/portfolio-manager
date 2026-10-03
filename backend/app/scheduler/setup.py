"""The scheduled jobs and how they are registered (no process management here)."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.db import new_session
from app.providers.registry import get_providers
from app.scheduler import jobs

log = logging.getLogger("scheduler")


def _quotes() -> None:
    with new_session() as db:
        n = jobs.run_quotes_cycle(db, get_providers().quotes)
        log.info("quotes cycle stored %d quotes", n)


def _snapshots() -> None:
    with new_session() as db:
        log.info("daily snapshots: %d", jobs.run_daily_snapshots(db))


def _catchup() -> None:
    with new_session() as db:
        log.info("catch-up snapshots: %d", jobs.run_catchup_snapshots(db))


def _scores() -> None:
    with new_session() as db:
        log.info("score refresh: %d", jobs.run_score_refresh(db, get_providers().history))


def _purge_drafts() -> None:
    with new_session() as db:
        log.info("purged %d expired import drafts", jobs.run_draft_purge(db))


def _purge_sessions() -> None:
    with new_session() as db:
        log.info("purged %d expired sessions", jobs.run_session_purge(db))


def register_jobs(sched: BaseScheduler, settings: Settings | None = None) -> None:
    """Add the recurring jobs (shared by `python -m app.scheduler` and the in-process scheduler)."""
    s = settings or get_settings()
    sched.add_job(
        _quotes,
        IntervalTrigger(minutes=s.quotes_interval_minutes),
        id="quotes",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.scheduler_misfire_grace_seconds,
    )
    sched.add_job(
        _snapshots,
        CronTrigger(
            hour=s.snapshot_hour, minute=s.snapshot_minute, timezone=ZoneInfo(s.scheduler_timezone)
        ),
        id="daily_snapshot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.snapshot_misfire_grace_seconds,
    )
    sched.add_job(
        _scores,
        IntervalTrigger(minutes=s.scores_interval_minutes),
        id="scores",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.scheduler_misfire_grace_seconds,
    )
    sched.add_job(
        _purge_drafts,
        IntervalTrigger(minutes=s.draft_purge_interval_minutes),
        id="purge_drafts",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.scheduler_misfire_grace_seconds,
    )
    sched.add_job(
        _purge_sessions,
        IntervalTrigger(minutes=s.session_purge_interval_minutes),
        id="purge_sessions",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.scheduler_misfire_grace_seconds,
    )


JOB_IDS = ("quotes", "daily_snapshot", "scores", "purge_drafts", "purge_sessions")


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    sched = BlockingScheduler(timezone=ZoneInfo(s.scheduler_timezone))
    register_jobs(sched, s)
    return sched
