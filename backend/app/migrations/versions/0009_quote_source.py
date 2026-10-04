"""Price quote provenance: `price_quote.source`, `basis` and `flag` (block 2.1).

Expand-only: two columns with server defaults and a nullable one. The previous release never reads
or writes them (its rows read as a live Yahoo quote, which is what it stored).

Revision ID: 0009_quote_source
Revises: 0008_screenshot_update
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_quote_source"
down_revision: str | None = "0008_screenshot_update"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "price_quote",
        sa.Column("source", sa.String(), nullable=False, server_default="yfinance"),
    )
    op.add_column(
        "price_quote", sa.Column("basis", sa.String(), nullable=False, server_default="live")
    )
    op.add_column("price_quote", sa.Column("flag", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("price_quote") as batch:
        batch.drop_column("flag")
        batch.drop_column("basis")
        batch.drop_column("source")
