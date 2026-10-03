# syntax=docker/dockerfile:1
# Multi-arch friendly (amd64 + arm64): every dependency ships manylinux/aarch64 wheels.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract with Hebrew + English language data for the screenshot importer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-heb tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv/backend
COPY pyproject.toml ./
COPY app ./app
RUN uv pip install --system --no-cache .

# Non-root runtime user; /data holds the SQLite file (mount a volume there).
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
USER app
ENV DATABASE_URL=sqlite:////data/portfolio.db
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

# API (single worker: the login rate limiter is in-memory).
# The scheduler uses the same image:  command: ["python", "-m", "app.scheduler"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
