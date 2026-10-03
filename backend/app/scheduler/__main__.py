"""Run with `python -m app.scheduler` (a separate process from the API).

Alternative: `SCHEDULER_IN_PROCESS=true` runs the same jobs inside the API process
(`app.scheduler.inprocess`).
"""

from __future__ import annotations

import logging

from app.db import get_engine, new_session, prepare_database
from app.logging_setup import configure_logging
from app.scheduler.setup import _catchup, _quotes, build_scheduler
from app.securities import seed_securities

log = logging.getLogger("scheduler")

__all__ = ["build_scheduler", "main"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    configure_logging()
    prepare_database(get_engine())
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
