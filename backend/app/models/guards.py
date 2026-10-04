"""Append-only guards for the paper-trading tables (registered when `app.models` is imported).

`PaperCall` may change only its resolution fields, and only once; `BacktestRun` never changes; neither
can be deleted (a user's own calls go only when the user account is deleted).

Layers: (1) `before_update` / `do_orm_execute` for the ORM; (2) `before_cursor_execute` on every
engine, which reads the SQL text, so Core statements, raw `text()` SQL, bulk deletes and
`INSERT OR REPLACE` / upsert are refused on SQLite and Postgres alike; (3) database triggers
(revisions 0003 and 0007) that also stop a client which bypasses SQLAlchemy.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.orm.attributes import instance_state

from app.models.tables import BacktestRun, PaperCall

RESOLUTION_FIELDS = frozenset({"resolved_at", "outcome", "outcome_price", "benchmark_returns"})


class AppendOnlyError(Exception):
    """An UPDATE that the append-only paper-trading tables do not allow."""


@event.listens_for(PaperCall, "before_update")
def _paper_call_guard(_mapper: Any, _connection: Any, target: PaperCall) -> None:
    state = instance_state(target)
    changed = {attr.key for attr in state.attrs if attr.history.has_changes()}
    illegal = sorted(changed - RESOLUTION_FIELDS)
    if illegal:
        raise AppendOnlyError(
            f"paper_call is append-only: {', '.join(illegal)} cannot change after insert"
        )
    if changed:
        hist = state.attrs.resolved_at.history
        was = hist.deleted or hist.unchanged  # the value the row had when it was loaded
        if was and was[0] is not None:
            raise AppendOnlyError("paper_call is already resolved; a resolution is final")


@event.listens_for(BacktestRun, "before_update")
def _backtest_guard(_mapper: Any, _connection: Any, target: BacktestRun) -> None:
    state = instance_state(target)
    if any(attr.history.has_changes() for attr in state.attrs):
        raise AppendOnlyError("backtest_run is immutable: record a new run instead")


@event.listens_for(Session, "do_orm_execute")
def _no_bulk_updates(execute_state: ORMExecuteState) -> None:
    mapper = execute_state.bind_mapper
    if (
        (execute_state.is_update or execute_state.is_delete)
        and mapper is not None
        and mapper.class_ in (PaperCall, BacktestRun)
    ):
        raise AppendOnlyError(f"{mapper.class_.__tablename__}: bulk UPDATE/DELETE is not allowed")


# ---------------------------------------------------------------- SQL text guard (every engine)
_TABLES = r'"?(?P<table>paper_call|backtest_run)"?'
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_UPDATE = re.compile(rf"\bupdate\s+(?:only\s+)?{_TABLES}(?![\w.])", re.IGNORECASE)
_DELETE = re.compile(rf"\bdelete\s+from\s+(?:only\s+)?{_TABLES}(?![\w.])", re.IGNORECASE)
_REPLACE = re.compile(rf"\b(?:insert\s+or\s+replace|replace)\s+into\s+{_TABLES}(?![\w.])", re.I)
_UPSERT = re.compile(
    rf"\binsert\s+into\s+{_TABLES}(?![\w.]).*?\bon\s+conflict\b.*?\bdo\s+update\b",
    re.IGNORECASE | re.DOTALL,
)
_SET = re.compile(r"\bset\b(?P<set>.*?)(?:\bwhere\b|\breturning\b|\bfrom\b|$)", re.I | re.DOTALL)
_COLUMN = re.compile(r'"?(\w+)"?\s*=')


def forbidden_sql(statement: str) -> str | None:
    """Why `statement` may not run against the append-only tables (None when it may)."""
    sql = " ".join(_COMMENTS.sub(" ", statement).split())
    if (m := _DELETE.search(sql)) is not None:
        return f"{m.group('table')}: DELETE is not allowed"
    if (m := _REPLACE.search(sql) or _UPSERT.search(sql)) is not None:
        return f"{m.group('table')}: replacing or upserting rows is not allowed"
    for m in _UPDATE.finditer(sql):
        table = m.group("table")
        if table == "backtest_run":
            return "backtest_run is immutable: record a new run instead"
        tail = _SET.search(sql[m.end() :])
        columns = set(_COLUMN.findall(tail.group("set"))) if tail else set()
        illegal = sorted(columns - RESOLUTION_FIELDS)
        if not columns or illegal:
            return "paper_call is append-only: only the resolution fields may change" + (
                f" ({', '.join(illegal)})" if illegal else ""
            )
    return None


@event.listens_for(Engine, "before_cursor_execute")
def _no_destructive_sql(
    _conn: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _many: bool
) -> None:
    if "paper_call" not in statement and "backtest_run" not in statement:
        return
    reason = forbidden_sql(statement)
    if reason is not None:
        raise AppendOnlyError(reason)
