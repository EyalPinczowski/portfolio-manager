"""Run with `python -m app.scheduler` (a separate process from the API)."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import get_engine, init_db, new_session
from app.providers.registry import get_providers
from app.scheduler import jobs
from app.securities import seed_securities

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


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    sched = BlockingScheduler(timezone=ZoneInfo(s.scheduler_timezone))
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
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db(get_engine())
    with new_session() as db:
        seed_securities(db)
    _catchup()
    sched = build_scheduler()
    # Prime quotes right away; the interval trigger takes over afterwards.
    _quotes()
    log.info("scheduler started")
    sched.start()


if __name__ == "__main__":
    main()
