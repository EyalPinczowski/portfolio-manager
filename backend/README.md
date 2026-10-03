# Portfolio Manager: backend (Phase 1)

FastAPI + SQLModel (SQLite, WAL) API and a separate APScheduler process. See `../docs/phase-1-spec.md`
for the contract. Not financial advice.

## Setup

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env            # set SECRET_KEY; COOKIE_SECURE=true behind HTTPS
```

Tesseract (optional but needed for the free OCR path): `apt install tesseract-ocr tesseract-ocr-heb`.
Without it, uploads return 503 unless `GEMINI_API_KEY` is set.

## Run

```bash
.venv/bin/python -m app.cli create-admin --email you@example.com   # prompts for the password
.venv/bin/python -m app.cli create-invite                           # prints a single-use invite code
.venv/bin/uvicorn app.main:app --reload                             # API on :8000, OpenAPI at /api/openapi.json
.venv/bin/python -m app.scheduler                                   # quotes / snapshots / scores (separate process)
.venv/bin/python scripts/dump_openapi.py                            # writes backend/openapi.json
```

Run the API with a single worker (the login rate limiter is in memory).

## Checks

```bash
.venv/bin/pytest                    # fixtures only; live tests are skipped (pytest -m live to run them)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app
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
