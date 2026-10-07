"""The scheduled jobs and how they are registered (no process management here)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.db import new_session
from app.providers.registry import get_providers
from app.scheduler import jobs
from app.scheduler.calendars import any_equity_session_today

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


def _equity_day() -> bool:
    """Non-quote market jobs have nothing new on weekends and market holidays."""
    if any_equity_session_today():
        return True
    log.info("no US/TASE session today: skipping market-data job")
    return False


def _scores() -> None:
    if not _equity_day():
        return
    with new_session() as db:
        log.info("score refresh: %d", jobs.run_score_refresh(db, get_providers().history))


def _universe() -> None:
    if not _equity_day():
        return
    with new_session() as db:
        p = get_providers()
        log.info("universe refresh: %d", jobs.run_universe_score_refresh(db, p.history, p.quotes))


def _purge_drafts() -> None:
    with new_session() as db:
        log.info("purged %d expired import drafts", jobs.run_draft_purge(db))


def _purge_ask_history() -> None:
    with new_session() as db:
        log.info("purged %d expired ask conversations", jobs.run_ask_history_purge(db))


def _purge_sessions() -> None:
    with new_session() as db:
        log.info("purged %d expired sessions", jobs.run_session_purge(db))


def _weekly_review() -> None:
    with new_session() as db:
        log.info("weekly reviews sent: %d", jobs.run_weekly_review_job(db, get_providers().history))


def _paper_resolve() -> None:
    with new_session() as db:
        log.info(
            "paper calls resolved: %d", jobs.run_paper_resolve_job(db, get_providers().history)
        )


def _tase_directory() -> None:
    with new_session() as db:
        log.info("tase directory rows: %d", jobs.run_tase_directory_refresh(db))


def tase_directory_if_empty() -> None:
    """Called once per leadership (startup): a first fill when a key exists and the table is empty."""
    with new_session() as db:
        n = jobs.run_tase_directory_if_empty(db)
        if n:
            log.info("tase directory first fill: %d", n)


def interval_trigger(minutes: int, slot: int, s: Settings) -> IntervalTrigger:
    """An interval trigger whose first run is offset by `slot` * `scheduler_job_offset_seconds` and
    that jitters every run, so heavy jobs never start in the same minute (0.1 CPU hosts)."""
    tz = ZoneInfo(s.scheduler_timezone)
    first = datetime.now(tz) + timedelta(
        minutes=minutes, seconds=slot * s.scheduler_job_offset_seconds
    )
    return IntervalTrigger(
        minutes=minutes,
        start_date=first,
        jitter=s.scheduler_job_jitter_seconds or None,
        timezone=tz,
    )


def register_jobs(sched: BaseScheduler, settings: Settings | None = None) -> None:
    """Add the recurring jobs (shared by `python -m app.scheduler` and the in-process scheduler)."""
    s = settings or get_settings()
    sched.add_job(
        _quotes,
        interval_trigger(s.quotes_interval_minutes, 0, s),
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
    if s.scheduler_universe_enabled:  # off in the 512 MB slim image: something else runs it
        sched.add_job(
            _universe,
            interval_trigger(s.universe_refresh_interval_minutes, 2, s),
            id="universe_scores",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=s.scheduler_misfire_grace_seconds,
        )
    sched.add_job(
        _scores,
        interval_trigger(s.scores_interval_minutes, 1, s),
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
        _purge_ask_history,
        IntervalTrigger(minutes=s.ask_history_purge_interval_minutes),
        id="purge_ask_history",
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
    sched.add_job(
        _weekly_review,
        interval_trigger(s.weekly_review_check_interval_minutes, 3, s),
        id="weekly_review",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.scheduler_misfire_grace_seconds,
    )
    sched.add_job(
        _tase_directory,
        CronTrigger(
            hour=s.tase_directory_refresh_hour,
            minute=s.tase_directory_refresh_minute,
            timezone=ZoneInfo(s.scheduler_timezone),
        ),
        id="tase_directory",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.snapshot_misfire_grace_seconds,
    )
    sched.add_job(
        _paper_resolve,
        interval_trigger(s.paper_resolve_interval_minutes, 4, s),
        id="paper_resolve",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=s.scheduler_misfire_grace_seconds,
    )


JOB_IDS = (
    "quotes",
    "daily_snapshot",
    "scores",
    "universe_scores",
    "purge_drafts",
    "purge_sessions",
    "purge_ask_history",
    "weekly_review",
    "paper_resolve",
    "tase_directory",
)


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    sched = BlockingScheduler(timezone=ZoneInfo(s.scheduler_timezone))
    register_jobs(sched, s)
    return sched
