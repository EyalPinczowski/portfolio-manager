"""User settings, Telegram link codes, one chat per account, admin disable flag, audit log.

Expand-only: three new tables the previous release never touches, one nullable column on `user`
(`disabled_at`, null = active) and a unique index on `user.telegram_chat_id` (nullable, so unlinked
users do not collide; a chat id can already be linked only once because the old code never wrote
duplicates). No backfill: a user without a `user_settings` row has the config defaults.

Revision ID: 0012_user_settings_telegram_admin
Revises: 0011_portfolio_expected_return
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_user_settings_telegram_admin"
down_revision: str | None = "0011_portfolio_expected_return"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(), nullable=False),
        sa.Column("main_currency", sa.String(), nullable=False),
        sa.Column("number_format", sa.String(), nullable=False),
        sa.Column("week_start_day", sa.String(), nullable=False),
        sa.Column("price_alerts_enabled", sa.Boolean(), nullable=False),
        sa.Column("weekly_review_enabled", sa.Boolean(), nullable=False),
        sa.Column("weekly_review_day", sa.String(), nullable=False),
        sa.Column("weekly_review_time", sa.String(), nullable=False),
        sa.Column("quiet_hours", sa.JSON(), nullable=True),
        sa.Column("idea_alerts", sa.JSON(), nullable=True),
        sa.Column("last_weekly_review_week", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "telegram_link_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_telegram_link_code_user_id"), "telegram_link_code", ["user_id"])
    op.create_index(
        op.f("ix_telegram_link_code_code_hash"), "telegram_link_code", ["code_hash"], unique=True
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_created_at"), "audit_log", ["created_at"])
    op.add_column("user", sa.Column("disabled_at", sa.DateTime(), nullable=True))
    op.create_index("uq_user_telegram_chat_id", "user", ["telegram_chat_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_user_telegram_chat_id", table_name="user")
    with op.batch_alter_table("user") as batch:
        batch.drop_column("disabled_at")
    op.drop_table("audit_log")
    op.drop_table("telegram_link_code")
    op.drop_table("user_settings")
