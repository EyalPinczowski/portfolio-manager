"""Update from screenshots: `portfolio.last_screenshot_update_at` and `import_draft.scope`.

Expand-only: a nullable column and a column with a server default, both ignored by the previous
release (it never reads or writes them; its drafts are read as `partial`, which is exactly what it
did not do, but a draft lives at most 24 hours).

Revision ID: 0008_screenshot_update
Revises: 0007_paper_append_only_triggers
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_screenshot_update"
down_revision: str | None = "0007_paper_append_only_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("portfolio", sa.Column("last_screenshot_update_at", sa.DateTime(), nullable=True))
    op.add_column(
        "import_draft",
        sa.Column("scope", sa.String(), nullable=False, server_default="partial"),
    )


def downgrade() -> None:
    with op.batch_alter_table("import_draft") as batch:
        batch.drop_column("scope")
    with op.batch_alter_table("portfolio") as batch:
        batch.drop_column("last_screenshot_update_at")
