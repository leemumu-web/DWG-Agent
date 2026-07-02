# ────────────────────────────────────────────────────────────────
# DWG-Agent Platform — 后端 Dockerfile
# 规范参考: §17.5 后端 Dockerfile 要求
#
# 构建:
#   docker build -t dwg-agent-backend:latest -f backend/Dockerfile backend/
#
# 运行（开发 / SQLite）:
#   docker run --rm -p 8000:8000 \
#     -v $(pwd)/backend/var:/app/var \
#     --env-file .env \
#     dwg-agent-backend:latest
# ────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# ── 系统环境 ──────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# ── 系统依赖 ──────────────────────────────────────────────────
# curl: 健康检查
# build-essential: Python C 扩展编译（hiredis 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── uv 包管理器 ───────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Python 依赖 ───────────────────────────────────────────────
# 先复制依赖清单，利用 Docker layer cache
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ── 应用代码 ──────────────────────────────────────────────────
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

# ── 运行时目录 ────────────────────────────────────────────────
# var/ 用于 SQLite 数据库和本地文件存储（开发模式）
# 生产环境使用 MySQL + MinIO，此目录可挂载为空
RUN mkdir -p /app/var && chmod 755 /app/var

# ── 安全: 非 root 运行 ───────────────────────────────────────
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser \
    && chown -R appuser:appuser /app
USER appuser

# ── 端口暴露 ──────────────────────────────────────────────────
EXPOSE 8000

# ── 默认启动命令（compose.yaml 可覆盖）─────────────────────────
# 开发/单机: uvicorn
# 生产: compose.yaml 使用 gunicorn + uvicorn workers
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
