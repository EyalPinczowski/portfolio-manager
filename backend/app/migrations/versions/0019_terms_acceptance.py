"""`terms_acceptance`: which terms version each user accepted, and when.

Expand-only: one new table, so the previous release (which never reads it) keeps working. One
row per (user, version) keeps the history; rows go with the user (ON DELETE CASCADE).

Revision ID: 0019_terms_acceptance
Revises: 0018_llm_cache_scope
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_terms_acceptance"
down_revision: str | None = "0018_llm_cache_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "terms_acceptance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_terms_acceptance_user_id", "terms_acceptance", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_terms_acceptance_user_id", table_name="terms_acceptance")
    op.drop_table("terms_acceptance")
