"""`portfolio.risk_chosen_at`: when the user last saved a risk level themselves.

Expand-only: one new nullable column that the previous release never reads. Null means the
server default is still in place (the user has not chosen).

Revision ID: 0021_portfolio_risk_chosen_at
Revises: 0020_daily_bar
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_portfolio_risk_chosen_at"
down_revision: str | None = "0020_daily_bar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("portfolio", sa.Column("risk_chosen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("portfolio") as batch:
        batch.drop_column("risk_chosen_at")
