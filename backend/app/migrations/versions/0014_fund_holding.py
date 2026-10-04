"""Israeli fund holdings (GemelNet): the fund number, the user's own entries and a manual value.

Expand-only: one new table the previous release never touches. Cascades when the holding is
deleted. Existing rows are not changed.

Revision ID: 0014_fund_holding
Revises: 0013_xray_rule_setting
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_fund_holding"
down_revision: str | None = "0013_xray_rule_setting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fund_holding",
        sa.Column("holding_id", sa.Integer(), nullable=False),
        sa.Column("fund_id", sa.String(), nullable=False),
        sa.Column("fund_name", sa.String(), nullable=True),
        sa.Column("track", sa.String(), nullable=True),
        sa.Column("manual_value_ils", sa.Float(), nullable=True),
        sa.Column("manual_value_as_of", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["holding_id"], ["holding.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("holding_id"),
    )
    op.create_index(op.f("ix_fund_holding_fund_id"), "fund_holding", ["fund_id"])


def downgrade() -> None:
    op.drop_table("fund_holding")
