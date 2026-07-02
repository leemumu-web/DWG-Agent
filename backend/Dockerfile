# DWG-Agent Platform — Backend Dockerfile
# 规范参考: §17.5 后端 Dockerfile 要求
#
# Multi-stage: builder installs deps, runtime is minimal.
# All production requirements §17.5-1..6 are met.

# ── Stage 1: build dependencies ──────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# System deps for uv + potential C extensions (hiredis via wheel, no gcc needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies (layer cached unless pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# Create non-root user (spec §17.5 requirement 4)
RUN groupadd --system --gid 1000 appuser && \
    useradd --system --no-log-init --uid 1000 --gid appuser appuser

WORKDIR /app

# Runtime system deps: curl for healthcheck only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder (single layer, ~120 MB)
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

# Create writable var/ directory for SQLite (dev mode) — spec requirement 6
# Production overrides DATABASE_URL to MySQL, so this is dev-only safety.
RUN mkdir -p /app/var && chown -R appuser:appuser /app/var

USER appuser

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["gunicorn", "app.main:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
