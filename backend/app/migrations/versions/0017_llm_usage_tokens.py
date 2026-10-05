"""`llm_usage`: real token split and cache hits.

Expand-only: four new integer columns with a default of 0, so the previous release (which never
reads or writes them) keeps working against the new schema.

Revision ID: 0017_llm_usage_tokens
Revises: 0016_ask_history
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_llm_usage_tokens"
down_revision: str | None = "0016_ask_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("tokens_in", "tokens_out", "tokens_cached", "cache_hits")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "llm_usage",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_usage") as batch:
        for name in reversed(_COLUMNS):
            batch.drop_column(name)
