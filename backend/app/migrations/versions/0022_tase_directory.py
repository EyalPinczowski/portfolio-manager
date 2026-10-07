"""`tase_directory`: the TASE Data Hub traded-securities list (public reference data).

Expand-only: one new table that the previous release never reads.

Revision ID: 0022_tase_directory
Revises: 0021_portfolio_risk_chosen_at
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_tase_directory"
down_revision: str | None = "0021_portfolio_risk_chosen_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tase_directory",
        sa.Column("tase_number", sa.String(), nullable=False),
        sa.Column("name_he", sa.String(), nullable=False, server_default=""),
        sa.Column("name_en", sa.String(), nullable=False, server_default=""),
        sa.Column("trading_symbol", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default=""),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tase_number"),
    )


def downgrade() -> None:
    op.drop_table("tase_directory")
