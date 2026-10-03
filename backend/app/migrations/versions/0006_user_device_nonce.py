"""`user.device_nonce`: revocable known-device cookies.

Expand-only: a nullable column the previous release ignores (it never reads or writes it).

Revision ID: 0006_user_device_nonce
Revises: 0005_pending_flow_holding
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_user_device_nonce"
down_revision: str | None = "0005_pending_flow_holding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("device_nonce", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_column("device_nonce")
