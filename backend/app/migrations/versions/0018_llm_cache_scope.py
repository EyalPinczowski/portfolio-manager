"""`llm_cache.scope`: which scope ("global" or "user:<id>") an answer was cached under.

Expand-only: one nullable column plus an index, so the previous release (which never reads or
writes it) keeps working. Lets account deletion remove a user's cached answers.

Revision ID: 0018_llm_cache_scope
Revises: 0017_llm_usage_tokens
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_llm_cache_scope"
down_revision: str | None = "0017_llm_usage_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_cache", sa.Column("scope", sa.String(), nullable=True))
    op.create_index(op.f("ix_llm_cache_scope"), "llm_cache", ["scope"])


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_cache_scope"), table_name="llm_cache")
    with op.batch_alter_table("llm_cache") as batch:
        batch.drop_column("scope")
