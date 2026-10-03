"""Append-only guards for the paper-trading tables (registered when `app.models` is imported).

`PaperCall` may change only its resolution fields, and only once; `BacktestRun` never changes.
Two layers here (a `before_update` mapper event for ORM flushes and a `do_orm_execute` check that
refuses bulk `UPDATE` statements) plus, on Postgres, the trigger installed by revision 0003.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
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
    if execute_state.is_update and mapper is not None and mapper.class_ in (PaperCall, BacktestRun):
        raise AppendOnlyError(f"{mapper.class_.__tablename__}: bulk UPDATE is not allowed")
