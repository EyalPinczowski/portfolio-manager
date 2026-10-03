"""Paper-trading tables: `paper_call` (append-only) and `backtest_run`.

Expand-only: two new tables, nothing the previous release reads. On Postgres a trigger also refuses
any UPDATE of a `paper_call` except its resolution fields (once); SQLite relies on the ORM guard in
`app/models/guards.py`.

Revision ID: 0003_paper_trading
Revises: 0002
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_paper_trading"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGER_FN = """
CREATE FUNCTION paper_call_append_only() RETURNS trigger AS $$
BEGIN
  IF (to_jsonb(NEW) - ARRAY['resolved_at','outcome','outcome_price','benchmark_returns'])
     IS DISTINCT FROM
     (to_jsonb(OLD) - ARRAY['resolved_at','outcome','outcome_price','benchmark_returns']) THEN
    RAISE EXCEPTION 'paper_call is append-only: only the resolution fields may change';
  END IF;
  IF OLD.resolved_at IS NOT NULL THEN
    RAISE EXCEPTION 'paper_call is already resolved; a resolution is final';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.create_table(
        "backtest_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("weights_hash", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_backtest_run_weights_hash"), "backtest_run", ["weights_hash"])
    op.create_index(op.f("ix_backtest_run_created_at"), "backtest_run", ["created_at"])
    op.create_table(
        "paper_call",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("is_global", sa.Boolean(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("horizon", sa.String(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=True),
        sa.Column("targets", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.JSON(), nullable=True),
        sa.Column("model_hash", sa.String(), nullable=False),
        sa.Column("prompt_hash", sa.String(), nullable=False),
        sa.Column("weights_hash", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("outcome_price", sa.Float(), nullable=True),
        sa.Column("benchmark_returns", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "(is_global AND user_id IS NULL) OR (NOT is_global AND user_id IS NOT NULL)",
            name="ck_paper_call_scope",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_paper_call_created_at"), "paper_call", ["created_at"])
    op.create_index(op.f("ix_paper_call_user_id"), "paper_call", ["user_id"])
    op.create_index(op.f("ix_paper_call_symbol"), "paper_call", ["symbol"])
    op.create_index(op.f("ix_paper_call_weights_hash"), "paper_call", ["weights_hash"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(TRIGGER_FN)
        op.execute(
            "CREATE TRIGGER paper_call_append_only BEFORE UPDATE ON paper_call "
            "FOR EACH ROW EXECUTE FUNCTION paper_call_append_only()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS paper_call_append_only ON paper_call")
        op.execute("DROP FUNCTION IF EXISTS paper_call_append_only()")
    op.drop_table("paper_call")
    op.drop_table("backtest_run")
