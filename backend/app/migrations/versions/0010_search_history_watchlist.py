"""Per-user search history and watchlist for Analyze a stock.

Expand-only: two new tables the previous release never touches. History keeps the symbol and the
time only (no analysis output). Both cascade when the user is deleted.

Revision ID: 0010_search_history_watchlist
Revises: 0009_quote_source
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_search_history_watchlist"
down_revision: str | None = "0009_quote_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("searched_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "symbol", name="uq_search_history_user_symbol"),
    )
    op.create_index(op.f("ix_search_history_user_id"), "search_history", ["user_id"])
    op.create_table(
        "watchlist_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )
    op.create_index(op.f("ix_watchlist_item_user_id"), "watchlist_item", ["user_id"])


def downgrade() -> None:
    op.drop_table("watchlist_item")
    op.drop_table("search_history")
