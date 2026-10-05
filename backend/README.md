# Holdwise: backend (Phase 1)

FastAPI + SQLModel (SQLite or Postgres, Alembic migrations) API; the APScheduler jobs run as a
separate process or inside the API (`SCHEDULER_IN_PROCESS=true`). See `../docs/phase-1-spec.md`
for the contract. Not financial advice.

## Setup

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env            # set SECRET_KEY; local dev needs ENV=dev and COOKIE_SECURE=false
```

`ENV=production` is the default: it disables `/api/docs` and the API refuses to start unless
`COOKIE_SECURE=true` and `SECRET_KEY` is a real secret (32+ chars). `uv sync --frozen --extra dev` uses
`uv.lock`; add `--extra postgres` for the Postgres driver.

Tesseract (optional, only for server-side screenshot reading): `apt install tesseract-ocr tesseract-ocr-heb`.
Without it the server OCR route answers 503 "use on-device reading" (the browser reads the screenshot
with tesseract.js and sends only the rows). A Gemini key does not help: sending an image to a third
party needs Tesseract's word boxes for redaction first.

## Run

```bash
.venv/bin/python -m app.cli create-admin --email you@example.com   # prompts for the password
.venv/bin/python -m app.cli create-invite                           # prints a single-use invite code
.venv/bin/uvicorn app.main:app --reload                             # API on :8000, OpenAPI at /api/openapi.json
.venv/bin/python -m app.scheduler                                   # quotes / snapshots / scores (separate process)
.venv/bin/python scripts/dump_openapi.py                            # writes backend/openapi.json
```

Run the API with a single worker (the login rate limiter is in memory).

## Database, migrations and the scheduler

The schema is owned by Alembic (`app/migrations/`, one hand-reviewed initial revision). The app never
calls `create_all` (only the tests do).

| `ENV` | `AUTO_MIGRATE` default | what startup does |
|---|---|---|
| `dev` | `true` | runs `alembic upgrade head` |
| `production` | `false` | checks the schema is at head and refuses to start otherwise |

Run the release step yourself with `python -m app.cli migrate` (or `alembic upgrade head` from
`backend/`, which reads `DATABASE_URL`). Set `AUTO_MIGRATE=true` to migrate at startup instead.
A database created earlier by `create_all` has no `alembic_version`: delete a throwaway dev database,
or `alembic stamp head` if it matches the current models. After changing a model:
`alembic revision --autogenerate -m "what changed"`, then **read and edit the file** before committing.

**In-process scheduler** (`SCHEDULER_IN_PROCESS=true`): the API process runs the same jobs as
`python -m app.scheduler`, after a catch-up snapshot and a quote refresh at start-up. Only the instance
holding the **leader lock** runs jobs (Postgres `pg_try_advisory_lock`; on SQLite an `fcntl` lock file
next to the database, or `SCHEDULER_LOCK_PATH`). Any other instance serves the API only and retries the
lock every `SCHEDULER_LEADER_CHECK_SECONDS`, so it takes over when the old one exits (a rolling deploy).
Do not also run `python -m app.scheduler` against the same database.

### Database URLs

SQLite (dev, home server): `DATABASE_URL=sqlite:///./portfolio.db`

Postgres needs the driver: `uv sync --frozen --extra postgres`. `postgres://` and `postgresql://` are
accepted and rewritten to the psycopg 3 driver. URL-encode special characters in the password. TLS is
chosen in the URL (`?sslmode=require`). Supabase forms (from its docs; check the project's *Connect* page, they
change, so verify at deploy time):

| Use | `DATABASE_URL` | notes |
|---|---|---|
| Session pooler (recommended on Render: IPv4, advisory locks work) | `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require` | nothing else to set |
| Direct connection | `postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require` | IPv6 only on the free plan; Render free cannot reach it |
| Transaction pooler | `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require` | also set `DATABASE_PREPARE_THRESHOLD=none` (no prepared statements) and `SCHEDULER_LOCK_DATABASE_URL=<the session pooler URL>` (advisory locks need a session) |

Pool (Postgres only, sized for a 512 MB host and a small connection limit): `DB_POOL_SIZE=3`,
`DB_MAX_OVERFLOW=2`, `DB_POOL_PRE_PING=true`, `DB_POOL_RECYCLE_SECONDS=1800`, `DB_POOL_TIMEOUT_SECONDS=30`.
The API therefore uses at most 5 connections, plus 1 for the leader lock.

## Memory probe (512 MB hosts)

```bash
.venv/bin/python scripts/memprobe.py            # exits 1 if the peak RSS is above --limit-mb (default 400)
```

Starts the API with the in-process scheduler, signs up and logs in 8 users at once, uploads a
3000x6000 screenshot, posts rows, reads the summary, sends a pixel bomb and a 25 MP image, and prints
RSS after each stage and the `VmHWM` peak. Linux only.

## Checks

```bash
.venv/bin/pytest                    # fixtures only; live tests are skipped (pytest -m live to run them)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app
# the same suite on Postgres (empties that database around every test: use a throwaway one):
DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/test .venv/bin/pytest
```

## Layout

`app/auth` sessions, CSRF, argon2id, rate limit | `app/providers` data sources (agorot normalised here) |
`app/signals` indicators, technical, patterns | `app/scoring` combine, risk, score cards |
`app/portfolio` valuation, TWR, x-ray, heat map | `app/importer` screenshot pipeline |
`app/alerts` price alerts + Telegram | `app/scheduler` jobs + market calendars | `app/data` securities seed.

All weights, thresholds and intervals live in `app/config.py` (env overrides, see `.env.example`).

## Docker

```bash
docker build -t pm-backend .
docker run -p 8000:8000 -v pm-data:/data --env-file .env pm-backend
docker run -v pm-data:/data --env-file .env pm-backend python -m app.scheduler
```

`Dockerfile.slim` is the 512 MB single-service image (Render + Supabase): no Tesseract, the Postgres
driver, one worker, in-process scheduler, non-root, `MALLOC_ARENA_MAX=2`. It runs
`python -m app.cli migrate` and then uvicorn on `$PORT`:

```bash
docker build -f Dockerfile.slim -t pm-api .
docker run -p 8000:8000 -e ENV=production -e COOKIE_SECURE=true -e SECRET_KEY=... -e DATABASE_URL=... pm-api
```
