"""A SQLite batch migration (table rebuild) must not lose data to foreign-key cascades.

`make_engine` turns `PRAGMA foreign_keys=ON` on. A batch migration of `user` does DROP TABLE, which
cascades to `portfolio` and `session`: verified loss in the Phase 1.5 re-review. `migrations/env.py`
now switches the pragma off around the migration, checks `foreign_key_check`, and switches it back.
The migration here is a test-only revision in a copy of the migrations directory.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, text
from sqlmodel import Session

from app.db import MIGRATIONS_DIR, head_revision, make_engine, run_migrations
from app.models import AuthSession, Holding, Portfolio, User

REBUILD_USER = '''"""test-only: rebuild `user` through a batch migration (DROP TABLE + rename)."""

import sqlalchemy as sa
from alembic import op

revision = "9999"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user", recreate="always") as batch:
        batch.add_column(sa.Column("test_note", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user", recreate="always") as batch:
        batch.drop_column("test_note")
'''

BREAK_FK = '''"""test-only: leaves a dangling reference behind (the foreign_key_check must catch it)."""

from alembic import op

revision = "9999"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('DELETE FROM "user"')  # FKs are off during a migration: portfolios now dangle


def downgrade() -> None:
    pass
'''


def _config_with_test_revision(tmp_path: Path, body: str, engine: Engine) -> AlembicConfig:
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "9999_test_only.py").write_text(body.format(down=head_revision()))
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(scripts))
    return cfg


def _seed(engine: Engine) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as db:
        user = User(email="a@x.co", password_hash="h")
        db.add(user)
        db.flush()
        assert user.id is not None
        p = Portfolio(owner_id=user.id, name="P", tracking_started_at=date(2026, 1, 1))
        db.add(p)
        db.flush()
        assert p.id is not None
        db.add(Holding(portfolio_id=p.id, symbol="AAPL", quantity=3))
        db.add(
            AuthSession(
                user_id=user.id,
                token_hash="t" * 64,
                csrf_token="c",
                created_at=now,
                last_seen_at=now,
                expires_at=datetime(2030, 1, 1),
            )
        )
        db.commit()


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: int(conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() or 0)
            for t in ("user", "portfolio", "session", "holding")
        }


def _upgrade(cfg: AlembicConfig, engine: Engine) -> None:
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        conn.commit()


def _fk_pragma(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0)


def test_batch_migration_of_user_keeps_portfolios_sessions_and_holdings(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    run_migrations(engine)
    _seed(engine)
    assert _counts(engine) == {"user": 1, "portfolio": 1, "session": 1, "holding": 1}

    _upgrade(_config_with_test_revision(tmp_path, REBUILD_USER, engine), engine)

    assert _counts(engine) == {"user": 1, "portfolio": 1, "session": 1, "holding": 1}
    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info("user")')]
        assert "test_note" in cols  # the table really was rebuilt
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()


def test_foreign_keys_are_enforced_again_after_the_migration(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    run_migrations(engine)
    _seed(engine)
    _upgrade(_config_with_test_revision(tmp_path, REBUILD_USER, engine), engine)
    # the pooled connection the migration used is back with the pragma on
    for _ in range(3):
        assert _fk_pragma(engine) == 1
    with engine.begin() as conn:  # and the cascade still works for real deletes
        conn.execute(text('DELETE FROM "user"'))
    assert _counts(engine)["portfolio"] == 0
    engine.dispose()


def test_a_migration_that_leaves_dangling_references_is_rolled_back(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    run_migrations(engine)
    _seed(engine)
    cfg = _config_with_test_revision(tmp_path, BREAK_FK, engine)
    with pytest.raises(RuntimeError, match="foreign_key_check"):
        _upgrade(cfg, engine)
    assert _counts(engine) == {"user": 1, "portfolio": 1, "session": 1, "holding": 1}
    assert _fk_pragma(engine) == 1
    engine.dispose()
