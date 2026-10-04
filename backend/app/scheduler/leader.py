"""Leader lock: of all instances sharing a database, exactly one runs the scheduled jobs.

- Postgres: a session-level `pg_try_advisory_lock` held on a dedicated connection. The server drops
  the lock when that connection (or the process) dies, so a crashed leader never blocks a successor.
- SQLite: an exclusive `fcntl.flock` on a lock file next to the database. The kernel drops it when
  the process exits.

`acquire()` never blocks. The in-process scheduler polls it so a standby instance (for example the
new one during a rolling deploy) takes over when the old leader goes away.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import tempfile
from pathlib import Path
from typing import IO, Protocol

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db import is_postgres, normalize_database_url

log = logging.getLogger("scheduler.leader")


class LeaderLock(Protocol):
    def acquire(self) -> bool:
        """Try to become leader without blocking. True if this object holds the lock afterwards."""

    def still_held(self) -> bool:
        """True while this object still holds the lock (False after a lost connection)."""

    def release(self) -> None:
        """Give the lock up (a no-op when it is not held)."""


class FileLeaderLock:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._fh: IO[str] | None = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        fh = open(self.path, "a+")  # noqa: SIM115  (kept open for as long as we lead)
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # BlockingIOError: someone else leads
            fh.close()
            return False
        self._fh = fh
        return True

    def still_held(self) -> bool:
        return self._fh is not None

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


class PostgresLeaderLock:
    def __init__(self, url: str, key: int, prepare_threshold: int | None = None) -> None:
        self.url = normalize_database_url(url)
        self.key = key
        self._prepare_threshold = prepare_threshold
        self._conn: Connection | None = None

    def acquire(self) -> bool:
        if self._conn is not None:
            return self.still_held()
        engine = create_engine(
            self.url,
            poolclass=NullPool,  # a dedicated connection that lives as long as leadership
            connect_args={"prepare_threshold": self._prepare_threshold},
        )
        conn: Connection | None = None
        try:
            conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}).scalar()
        except Exception as exc:
            log.warning("leader lock: cannot reach the database (%s)", type(exc).__name__)
            got = False
        if not got:
            if conn is not None:
                conn.close()
            engine.dispose()
            return False
        self._conn = conn
        return True

    def still_held(self) -> bool:
        if self._conn is None:
            return False
        try:
            self._conn.execute(text("SELECT 1"))
            return True
        except Exception:
            # The connection is gone, so the server already dropped the advisory lock.
            self._discard()
            return False

    def release(self) -> None:
        if self._conn is None:
            return
        with contextlib.suppress(Exception):  # closing the connection releases it anyway
            self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
        self._discard()

    def _discard(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def default_lock_path(database_url: str) -> Path:
    db = make_url(database_url).database
    if db and db != ":memory:":
        return Path(str(db) + ".scheduler.lock")
    return Path(tempfile.gettempdir()) / "portfolio-scheduler.lock"


def make_leader_lock(settings: Settings | None = None) -> LeaderLock:
    s = settings or get_settings()
    if is_postgres(s.scheduler_lock_database_url or s.database_url):
        return PostgresLeaderLock(
            s.scheduler_lock_database_url or s.database_url, s.scheduler_lock_id
        )
    path = s.scheduler_lock_path or str(default_lock_path(s.database_url))
    return FileLeaderLock(path)
