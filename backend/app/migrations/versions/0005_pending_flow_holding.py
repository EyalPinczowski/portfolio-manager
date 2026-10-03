"""Key `pending_buy` markers by holding: `transaction.holding_id` + a unique partial index.

Expand-only: a new nullable column that the previous release ignores, and a partial unique index
(one outstanding marker per holding; ordinary transactions have no `holding_id`, so they are not
affected). Existing markers get their `holding_id` from (portfolio, symbol); duplicate markers (the
race the Phase 2.0 diff review found) are reduced to the oldest one first. A marker carries no flow,
so removing the extra ones changes no money figure.

Revision ID: 0005_pending_flow_holding
Revises: 0004_llm_usage
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_pending_flow_holding"
down_revision: str | None = "0004_llm_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WHERE = sa.text("type = 'pending_buy'")


def upgrade() -> None:
    op.add_column("transaction", sa.Column("holding_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE "transaction" SET holding_id = (
            SELECT h.id FROM holding h
            WHERE h.portfolio_id = "transaction".portfolio_id AND h.symbol = "transaction".symbol
        ) WHERE type = 'pending_buy'
        """
    )
    op.execute(
        """
        DELETE FROM "transaction" WHERE type = 'pending_buy' AND holding_id IS NOT NULL
        AND id NOT IN (
            SELECT MIN(id) FROM "transaction"
            WHERE type = 'pending_buy' AND holding_id IS NOT NULL GROUP BY holding_id
        )
        """
    )
    op.create_index(
        "uq_transaction_pending_holding",
        "transaction",
        ["holding_id"],
        unique=True,
        sqlite_where=_WHERE,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_transaction_pending_holding", table_name="transaction")
    with op.batch_alter_table("transaction") as batch:
        batch.drop_column("holding_id")
