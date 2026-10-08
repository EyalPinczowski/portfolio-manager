"""`import_draft.broker_total`: the broker's own portfolio total (ILS) read from the screen header.

Expand-only: one new nullable column that the previous release never reads. A number only, never
the screen text.

Revision ID: 0023_import_draft_broker_total
Revises: 0022_tase_directory
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_import_draft_broker_total"
down_revision: str | None = "0022_tase_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_draft", sa.Column("broker_total", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("import_draft") as batch:
        batch.drop_column("broker_total")
