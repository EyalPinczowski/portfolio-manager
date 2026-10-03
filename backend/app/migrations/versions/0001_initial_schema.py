"""Initial schema (hand-reviewed autogenerate): every table with its ondelete rules.

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_quote",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_table(
        "security",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("name_en", sa.String(), nullable=False),
        sa.Column("name_he", sa.String(), nullable=False),
        sa.Column("tase_number", sa.String(), nullable=True),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("sector", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=False),
        sa.Column("dual_listing_group", sa.String(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index(
        op.f("ix_security_dual_listing_group"), "security", ["dual_listing_group"], unique=False
    )
    op.create_index(op.f("ix_security_tase_number"), "security", ["tase_number"], unique=False)
    op.create_table(
        "signal_cache",
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("locale", sa.String(), nullable=False),
        sa.Column("disclaimer_accepted_at", sa.DateTime(), nullable=True),
        sa.Column("ocr_consent_at", sa.DateTime(), nullable=True),
        sa.Column("telegram_chat_id", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)
    op.create_table(
        "invite",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("used_by", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["used_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_user_id"), "notification", ["user_id"], unique=False)
    op.create_table(
        "portfolio",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_currency", sa.String(), nullable=False),
        sa.Column("risk_filter", sa.JSON(), nullable=True),
        sa.Column("tracking_started_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_portfolio_owner_id"), "portfolio", ["owner_id"], unique=False)
    op.create_table(
        "price_alert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("op", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_price_alert_user_id"), "price_alert", ["user_id"], unique=False)
    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("csrf_token", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_token_hash"), "session", ["token_hash"], unique=True)
    op.create_index(op.f("ix_session_user_id"), "session", ["user_id"], unique=False)
    op.create_table(
        "holding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=True),
        sa.Column("cost_currency", sa.String(), nullable=False),
        sa.Column("horizon", sa.String(), nullable=True),
        sa.Column("risk_override", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "symbol"),
    )
    op.create_index(op.f("ix_holding_portfolio_id"), "holding", ["portfolio_id"], unique=False)
    op.create_index(op.f("ix_holding_symbol"), "holding", ["symbol"], unique=False)
    op.create_table(
        "holdings_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_holdings_snapshot_portfolio_id"),
        "holdings_snapshot",
        ["portfolio_id"],
        unique=False,
    )
    op.create_table(
        "import_draft",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=True),
        sa.Column("proposed_changes", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_import_draft_portfolio_id"), "import_draft", ["portfolio_id"], unique=False
    )
    op.create_table(
        "portfolio_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("value_ils", sa.Float(), nullable=False),
        sa.Column("value_usd", sa.Float(), nullable=False),
        sa.Column("net_flow_ils", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "date"),
    )
    op.create_index(
        op.f("ix_portfolio_snapshot_portfolio_id"),
        "portfolio_snapshot",
        ["portfolio_id"],
        unique=False,
    )
    op.create_table(
        "transaction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("fx_to_ils", sa.Float(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_transaction_portfolio_id"), "transaction", ["portfolio_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("transaction")
    op.drop_table("portfolio_snapshot")
    op.drop_table("import_draft")
    op.drop_table("holdings_snapshot")
    op.drop_table("holding")
    op.drop_table("session")
    op.drop_table("price_alert")
    op.drop_table("portfolio")
    op.drop_table("notification")
    op.drop_table("invite")
    op.drop_table("user")
    op.drop_table("signal_cache")
    op.drop_table("security")
    op.drop_table("price_quote")
