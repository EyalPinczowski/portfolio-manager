"""The return a user expects from a portfolio (for the post-mortem).

Expand-only: two nullable columns on `portfolio` that the previous release ignores. Both stay null
until the user sets them; nothing is backfilled or defaulted.

Revision ID: 0011_portfolio_expected_return
Revises: 0010_search_history_watchlist
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_portfolio_expected_return"
down_revision: str | None = "0010_search_history_watchlist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("portfolio", sa.Column("expected_return_pct", sa.Float(), nullable=True))
    op.add_column(
        "portfolio", sa.Column("expected_return_horizon_months", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("portfolio") as batch:
        batch.drop_column("expected_return_horizon_months")
        batch.drop_column("expected_return_pct")
