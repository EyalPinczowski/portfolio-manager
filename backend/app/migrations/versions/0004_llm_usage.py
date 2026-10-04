"""LLM foundation tables: `llm_usage` (quota ledger), `llm_bucket` (token bucket), `llm_cache`.

Expand-only: three new tables nothing in the previous release reads.

Revision ID: 0004_llm_usage
Revises: 0003_paper_trading
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_llm_usage"
down_revision: str | None = "0003_paper_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("fallbacks", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "model", "day"),
    )
    op.create_table(
        "llm_bucket",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_table(
        "llm_cache",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("response", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_llm_cache_role"), "llm_cache", ["role"])
    op.create_index(op.f("ix_llm_cache_created_at"), "llm_cache", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_cache")
    op.drop_table("llm_bucket")
    op.drop_table("llm_usage")
