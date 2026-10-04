"""Persist Yahoo's own currency on `security` (`yahoo_currency`, nullable).

Expand-only: a nullable column, so the previous release keeps working on this schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("security") as batch:
        batch.add_column(sa.Column("yahoo_currency", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("security") as batch:
        batch.drop_column("yahoo_currency")
