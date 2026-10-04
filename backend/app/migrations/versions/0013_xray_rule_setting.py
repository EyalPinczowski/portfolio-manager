"""Toggleable X-ray rules per portfolio.

Expand-only: one new table the previous release never touches. Only rows the user changed exist;
no rows means every rule on with its default threshold. Cascades when the portfolio is deleted.

Revision ID: 0013_xray_rule_setting
Revises: 0012_user_settings_telegram_admin
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_xray_rule_setting"
down_revision: str | None = "0012_user_settings_telegram_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "xray_rule_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("threshold_pct", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "rule", name="uq_xray_rule_portfolio_rule"),
    )
    op.create_index(
        op.f("ix_xray_rule_setting_portfolio_id"), "xray_rule_setting", ["portfolio_id"]
    )


def downgrade() -> None:
    op.drop_table("xray_rule_setting")
