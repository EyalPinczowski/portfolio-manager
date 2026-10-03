"""Append-only triggers for `paper_call` and `backtest_run` on SQLite and Postgres.

Expand-only (triggers are not part of the schema the previous release reads, and it never updates
or deletes these rows): UPDATE of `paper_call` only in the resolution fields and only once, no
DELETE of a global call (the gate's track record), no UPDATE or DELETE of a `backtest_run`.
A user's own paper calls go when the account is deleted (a privacy duty): Postgres checks that the
owner row is gone, SQLite lets any private call be deleted at the database level (a trigger must not
mention the `user` table, or a later table rebuild of `user` fails); the SQLAlchemy guard refuses
every explicit `DELETE` anyway. Revision 0003 installed the UPDATE trigger on Postgres only.

Revision ID: 0007_paper_append_only_triggers
Revises: 0006_user_device_nonce
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_paper_append_only_triggers"
down_revision: str | None = "0006_user_device_nonce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# every column of paper_call that is not a resolution field
FIXED_COLUMNS = (
    "id", "created_at", "user_id", "is_global", "symbol", "side", "horizon", "entry", "stop",
    "targets", "explanation", "model_hash", "prompt_hash", "weights_hash",
)  # fmt: skip

PG_FUNCTIONS = (
    """
    CREATE OR REPLACE FUNCTION paper_call_no_delete() RETURNS trigger AS $$
    BEGIN
      -- the cascade of an account deletion arrives after the owner row is gone
      IF OLD.user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "user" WHERE id = OLD.user_id) THEN
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'paper_call is append-only: rows cannot be deleted';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION backtest_run_immutable() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'backtest_run is immutable: rows cannot be updated or deleted';
    END;
    $$ LANGUAGE plpgsql
    """,
)
PG_TRIGGERS = (
    "CREATE TRIGGER paper_call_no_delete BEFORE DELETE ON paper_call "
    "FOR EACH ROW EXECUTE FUNCTION paper_call_no_delete()",
    "CREATE TRIGGER backtest_run_immutable BEFORE UPDATE OR DELETE ON backtest_run "
    "FOR EACH ROW EXECUTE FUNCTION backtest_run_immutable()",
)


def _sqlite_statements() -> list[str]:
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in FIXED_COLUMNS)
    return [
        f"""
        CREATE TRIGGER paper_call_append_only BEFORE UPDATE ON paper_call
        WHEN OLD.resolved_at IS NOT NULL OR {changed}
        BEGIN
          SELECT RAISE(ABORT, 'paper_call is append-only: only the resolution fields may change, once (final)');
        END
        """,
        """
        CREATE TRIGGER paper_call_no_delete BEFORE DELETE ON paper_call
        WHEN OLD.user_id IS NULL
        BEGIN
          SELECT RAISE(ABORT, 'paper_call is append-only: rows cannot be deleted');
        END
        """,
        """
        CREATE TRIGGER backtest_run_no_update BEFORE UPDATE ON backtest_run
        BEGIN
          SELECT RAISE(ABORT, 'backtest_run is immutable: rows cannot be updated');
        END
        """,
        """
        CREATE TRIGGER backtest_run_no_delete BEFORE DELETE ON backtest_run
        BEGIN
          SELECT RAISE(ABORT, 'backtest_run is immutable: rows cannot be deleted');
        END
        """,
    ]


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for statement in (*PG_FUNCTIONS, *PG_TRIGGERS):
            op.execute(statement)
    elif dialect == "sqlite":
        for statement in _sqlite_statements():
            op.execute(statement)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS backtest_run_immutable ON backtest_run")
        op.execute("DROP TRIGGER IF EXISTS paper_call_no_delete ON paper_call")
        op.execute("DROP FUNCTION IF EXISTS backtest_run_immutable()")
        op.execute("DROP FUNCTION IF EXISTS paper_call_no_delete()")
    elif dialect == "sqlite":
        for name in (
            "paper_call_append_only",
            "paper_call_no_delete",
            "backtest_run_no_update",
            "backtest_run_no_delete",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
