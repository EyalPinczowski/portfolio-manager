"""Ask-my-portfolio chat history: `ask_conversation` and `ask_message`.

Expand-only: two new tables the previous release never touches. Rows are scoped to a user; only the
question, the answer, the cited tool names and the tools called are stored, never a raw tool
payload. Old conversations are purged by the scheduler after `ask_history_retention_days`.

Revision ID: 0016_ask_history
Revises: 0015_doc_chunk
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_ask_history"
down_revision: str | None = "0015_doc_chunk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ask_conversation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ask_conversation_user_id", "ask_conversation", ["user_id"])
    op.create_table(
        "ask_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("cites", sa.JSON(), nullable=False),
        sa.Column("tools_called", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("declined", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["ask_conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ask_message_conversation_id", "ask_message", ["conversation_id"])
    op.create_index("ix_ask_message_user_id", "ask_message", ["user_id"])


def downgrade() -> None:
    op.drop_table("ask_message")
    op.drop_table("ask_conversation")
