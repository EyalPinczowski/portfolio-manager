"""RAG passages (`doc_chunk`): public text for retrieval, never tied to a user.

Expand-only: one new table the previous release never touches. The keyword index (SQLite FTS5 table
or a Postgres GIN expression index) is created by `app.rag.index.ChunkIndex.ensure()` in the index
job, not here: it is not part of the SQLModel metadata, and the schema-equals-metadata test stays
exact. Search simply returns nothing until the index exists.

Revision ID: 0015_doc_chunk
Revises: 0014_fund_holding
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_doc_chunk"
down_revision: str | None = "0014_fund_holding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "doc_chunk",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "text_hash", name="uq_doc_chunk_symbol_hash"),
    )
    op.create_index("ix_doc_chunk_symbol_type", "doc_chunk", ["symbol", "doc_type"])


def downgrade() -> None:
    op.drop_table("doc_chunk")
