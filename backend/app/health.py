"""`/api/health`: liveness plus the scheduler state, with at most one cheap DB read per 10 seconds.

Render and UptimeRobot hit this every few minutes, so the DB read is cached (`HealthProbe`). The
read is one SELECT of three MAX() values (the newest quote, portfolio snapshot and paper-call resolution), so a
stalled scheduler is visible from outside: `last_quotes_at` stops moving while the API still looks
healthy. A database error never fails the endpoint; the last known values are kept.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select

from app.db import new_session
from app.models import PaperCall, PortfolioSnapshot, PriceQuote

log = logging.getLogger(__name__)

SchedulerState = Literal["leader", "standby", "unavailable", "external"]


class HealthOut(BaseModel):
    status: Literal["ok"]
    # leader / standby: the in-process scheduler; unavailable: it is enabled but cannot run (for
    # example the leader lock file cannot be opened); external: jobs run in `python -m app.scheduler`.
    scheduler: SchedulerState
    leader: bool  # this process runs the jobs
    last_quotes_at: datetime | None  # newest stored quote (UTC); None if there is none yet
    last_snapshot_at: date | None  # newest portfolio snapshot day
    last_paper_resolve_at: datetime | None  # newest paper-call resolution (UTC); None if none yet


class HealthProbe:
    def __init__(
        self, ttl_seconds: float = 10.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._at: float | None = None
        self._value: tuple[datetime | None, date | None, datetime | None] = (None, None, None)

    def read(self) -> tuple[datetime | None, date | None, datetime | None]:
        """The last quote and snapshot times, at most one DB read per `ttl` seconds.

        The lock is a try-lock: while one request is reading (a hanging database), every other
        request returns the last known value at once instead of queueing a worker thread behind the
        read. The health endpoint therefore always answers, whatever the database is doing."""
        if not self._lock.acquire(blocking=False):
            return self._value
        try:
            now = self._clock()
            if self._at is not None and now - self._at < self._ttl:
                return self._value
            self._at = now  # also on failure: a broken DB is not hammered every request
            try:
                with new_session() as db:
                    row = db.exec(  # type: ignore[call-overload]
                        select(
                            select(func.max(PriceQuote.as_of)).scalar_subquery(),
                            select(func.max(PortfolioSnapshot.date)).scalar_subquery(),
                            select(func.max(PaperCall.resolved_at)).scalar_subquery(),
                        )
                    ).one()
                quotes_at = row[0].replace(tzinfo=UTC) if row[0] is not None else None
                resolved_at = row[2].replace(tzinfo=UTC) if row[2] is not None else None
                self._value = (quotes_at, row[1], resolved_at)
            except Exception as exc:
                log.warning("health: could not read last quote/snapshot (%s)", type(exc).__name__)
            return self._value
        finally:
            self._lock.release()


class _HasState(Protocol):
    @property
    def state(self) -> Literal["leader", "standby", "unavailable"]: ...


def scheduler_state(runner: _HasState | None, in_process: bool) -> tuple[SchedulerState, bool]:
    """(state, is_leader) for the API process."""
    if not in_process:
        return "external", False
    if runner is None:
        return "unavailable", False
    state = runner.state
    return state, state == "leader"
