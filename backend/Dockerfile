# syntax=docker/dockerfile:1
# Multi-arch friendly (amd64 + arm64): every dependency ships manylinux/aarch64 wheels.
# Base images are pinned by digest (checked against the registries on 2026-10-03). Refresh them on
# purpose (e.g. Dependabot / Renovate, or: docker buildx imagetools inspect python:3.12-slim) so
# security patches are picked up, then rebuild.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract with Hebrew + English language data for the screenshot importer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-heb tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# uv 0.8.17 (the version that wrote uv.lock), pinned by digest.
COPY --from=ghcr.io/astral-sh/uv:0.8.17@sha256:e4644cb5bd56fdc2c5ea3ee0525d9d21eed1603bccd6a21f887a938be7e85be1 /uv /usr/local/bin/uv

WORKDIR /srv/backend
# Dependencies come from the lockfile (exact, hashed versions); the app is installed on top.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY app ./app
RUN uv pip install --system --no-cache --no-deps .

# Non-root runtime user; /data holds the SQLite file (mount a volume there).
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
USER app
# ENV=production is the config default: the API refuses to start without COOKIE_SECURE=true and a real
# SECRET_KEY (>= 32 chars). Local development: ENV=dev COOKIE_SECURE=false (see docker-compose.yml).
ENV DATABASE_URL=sqlite:////data/portfolio.db
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

# API (single worker: the login rate limiter is in-memory).
# The scheduler uses the same image:  command: ["python", "-m", "app.scheduler"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
