"""`daily_bar`: stored daily OHLCV bars (major units) so history is fetched incrementally.

Expand-only: one new table that the previous release never reads.

Revision ID: 0020_daily_bar
Revises: 0019_terms_acceptance
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_daily_bar"
down_revision: str | None = "0019_terms_acceptance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_bar",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "day"),
    )


def downgrade() -> None:
    op.drop_table("daily_bar")
