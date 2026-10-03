# Database migrations: expand, then contract

Status: policy, Phase 2.0-B (2026-10-03). Tools: Alembic (`backend/app/migrations/`), applied by `python -m app.cli migrate` (the Docker image runs it before uvicorn) or `AUTO_MIGRATE=true` in dev.

## Why a policy
Each release migrates the database before (or while) its new code starts. If that release turns out bad, the previous image is started again, **against the already-migrated database**. Without a rule that image either crash-loops ("schema is at X, the app needs Y") or breaks on a dropped column. The rule below makes the rollback safe, and the app checks it at startup.

## The rule: expand, then contract
A schema change that removes or reshapes something ships in **two releases**:

1. **Expand (release N).** Only additive changes that old code ignores: new tables, new nullable columns or columns with a server default, new indexes, new enum values. The new code writes both the old and the new shape if needed. Release N-1 still works on this schema, so rolling back from N to N-1 is safe.
2. **Contract (release N+k, later).** Drops, renames, `NOT NULL` on a populated column, type changes. Ship it only after release N has been running long enough that nobody will roll back past it, and the code no longer reads the old shape.

Never in one release: drop or rename a column the previous release still uses, add a `NOT NULL` column without a default, change a column's type in place.

## Naming
Revision ids are numbered: `0003`, optionally with a suffix, `0003_add_paper_calls`. A **contract** revision must have `contract` in its id: `0007_contract_drop_old_column`. The id is the only thing an older build can read about a revision it has never seen, so the convention carries the policy.

## What the app does ("DB ahead")
At startup (`prepare_database`) and in `python -m app.cli migrate`, the build compares the database revision with its own head:

| Database revision | Result |
|---|---|
| equal to head | start |
| older (known to this build) | production refuses to start until `alembic upgrade head` ran; dev with `AUTO_MIGRATE` upgrades |
| **newer, unknown to this build** | accepted with a loud warning **when** it is numbered, at most `DB_AHEAD_MAX_REVISIONS` (default 1) above this build's head, and has no `contract` in its id; the build does not touch the schema |
| newer but contract, too far ahead, or not comparable | refused with the reason, so a build never runs on a schema that dropped things it needs |
| listed in `DB_ACCEPTED_AHEAD_REVISIONS` | accepted (operator override, comma-separated ids; use only after reading the migration) |

An older build never downgrades the database. Roll forward again as soon as possible: the warning stays in the log.

## Rollback procedure
1. Redeploy the previous image. The release step (`python -m app.cli migrate`) is a no-op on an accepted "DB ahead" state.
2. If the app refuses to start because the newer revision is a contract migration, do **not** edit `alembic_version`. Restore the pre-deploy backup or deploy the newer release again.
3. Never run `alembic downgrade` in production. Downgrades exist for development and tests only.

## Before you merge a migration
- Is every operation an expand? If not, split the contract part into its own later revision with `contract` in the id.
- It runs on SQLite and on Postgres (`pytest` on both; the schema must equal the SQLModel metadata).
- On SQLite, a batch migration that rebuilds a table (`batch_alter_table`) would cascade-delete child rows through `ON DELETE CASCADE`; `migrations/env.py` turns `PRAGMA foreign_keys` off around the batch and runs `PRAGMA foreign_key_check` (see `tests/test_migrations_data.py`, the "migrate with data" test). Add rows to the affected tables in your own test.
- Long locks on Postgres: create indexes on big tables `CONCURRENTLY` (outside the transaction) when the table can be large.

## Settings
| Env | Default | Meaning |
|---|---|---|
| `DB_AHEAD_MAX_REVISIONS` | `1` | how many numbered revisions ahead an older build tolerates |
| `DB_ACCEPTED_AHEAD_REVISIONS` | empty | revision ids to accept regardless of the rules above |
| `AUTO_MIGRATE` | true in dev, false in production | migrate at API/scheduler start; production uses the release step |
