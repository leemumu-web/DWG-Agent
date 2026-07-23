# DWG-Agent Platform — Backend Dockerfile
# 规范参考: §17.4 / §17.5 后端 Dockerfile 要求
#
# Multi-stage: builder installs deps, runtime is minimal.
#
# 关键设计：build context = 仓库根（见 compose.yaml），因为 backend/pyproject.toml
# 把 dwg-converter / dxf-converter / dxf2excel / steel-dxf-classifier 声明为 editable path 依赖，
# 指向 ../Stages/ 下对应目录（相对 backend/）。这些路径在 ./backend 上下文
# 之外，故必须以仓库根为上下文，把 Stages/ 一并 COPY 进 builder，uv sync --frozen 才能
# 解析 lock 里锁定的 editable 源。
# Excel Final 保持其独立脚本工程布局，由平台通过隔离子进程调用，不作为 Python 包安装。
#
# 运行时：DXF 转换链路（ODA File Converter + xvfb-run）内置进镜像，Stage 1 虽
# DXF_PIPELINE_ENABLED=false 不调用，但 worker-dxf / worker-agent 一旦开启即就绪。
#
# 全部生产要求 §17.5-1..6 满足：多阶段、非 root、HEALTHCHECK、STOPSIGNAL、gunicorn+UvicornWorker。

# ── Stage 1: build dependencies ──────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Builder 系统依赖：仅 ca-certificates。所有 Python 依赖（argon2 / ezdxf /
# pandas / openpyxl）在 CPython 3.12 + manylinux 下均有预编译 wheel，无需 gcc。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends --no-install-suggests \
    ca-certificates

# 先 COPY 依赖清单 + 本地 path 依赖源码，再 uv sync —— 命中 uv.lock 缓存层
# （pyproject.toml / uv.lock / Stages 源码不变则跳过 sync）。
#
# 镜像内布局复刻仓库结构：/app/backend/pyproject.toml + /app/Stages/*。
# 因为 [tool.uv.sources] 的 editable path 相对 pyproject.toml 目录解析为 ../Stages/*，
# pyproject.toml 必须放在 /app/backend/ 下，../Stages/ 才指向 /app/Stages/。
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./backend/
COPY Stages/dwg2dxf ./Stages/dwg2dxf
COPY Stages/dxf2dwg ./Stages/dxf2dwg
COPY Stages/dxf2excel ./Stages/dxf2excel
COPY Stages/steel_dxf_classifier_v1.1.0 ./Stages/steel_dxf_classifier_v1.1.0
COPY Stages/remnant_drawing_reader ./Stages/remnant_drawing_reader

# editable path 依赖相对 backend/ 解析为 ../Stages/* —— 镜像内 /app/Stages/* 正好命中。
WORKDIR /app/backend
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/appuser
ENV PATH="/app/.venv/bin:$PATH"
# Containers do not expose /dev/fuse by default; extract AppImages per run.
ENV APPIMAGE_EXTRACT_AND_RUN=1
# ODA File Converter 定位（check_env.py 优先读 $ODA_HOME）。
ENV ODA_HOME=/app/oda

# Create non-root user (spec §17.5 requirement 4: uid 1000)
RUN groupadd --system --gid 1000 appuser && \
    useradd --system --no-log-init --uid 1000 --gid appuser appuser

WORKDIR /app

# Runtime 系统依赖：
#   curl, ca-certificates — 健康检查
#   xvfb, xauth           — xvfb-run 无头 X 及其授权文件，ODA AppImage 需要 X server
#   libfuse2              — AppImage FUSE 挂载解压（bookworm-slim 默认无）
#   libfontconfig1        — ODA 解包后的 Qt 程序运行时字体依赖
#   fonts-noto-cjk        — DXF 在线预览中文轮廓字体
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends --no-install-suggests \
    curl \
    ca-certificates \
    xvfb \
    xauth \
    libfuse2 \
    libfontconfig1 \
    fonts-noto-cjk

# Copy virtualenv from builder (single layer)
COPY --from=builder /app/.venv /app/.venv

# editable 安装的 .pth 指向 /app/Stages/*，runtime 必须带源码，否则 import 失败。
COPY --from=builder /app/Stages /app/Stages
# 独立 Excel Final Stage 由 excel_processing.stage_runner 在子进程中执行。
COPY Stages/excel_final /app/Stages/excel_final

# Copy application code
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY scripts/run-cad-worker.sh /app/scripts/run-cad-worker.sh

# ODA File Converter 二进制（85 MB AppImage）—— worker-dxf / worker-agent 转换链路用。
# backend-api / worker-report 不调用，但共享镜像，统一内置以简化部署。
COPY --chown=appuser:appuser Stages/dwg2dxf/tools/oda /app/oda

# Create writable directories for runtime artifacts and user-level control/cache files.
RUN mkdir -p /app/var /home/appuser && \
    chmod 0755 /app/scripts/run-cad-worker.sh && \
    chown -R appuser:appuser /app/var /home/appuser /app/oda

USER appuser

EXPOSE 8010

STOPSIGNAL SIGTERM

# start-period 容忍 alembic upgrade + init_db seed + gunicorn worker 启动，避免首轮被判 unhealthy。
HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=40s \
    CMD curl -f http://localhost:8010/health || exit 1

# CMD: 迁移 → 幂等超管/角色种子（init_db 按 code 查重，已存在则跳过）→ gunicorn。
# 规范 §17.4 CMD 字面量保留：alembic upgrade head && ... gunicorn app.main:app
# --bind 0.0.0.0:8010 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120
CMD ["sh", "-c", \
     "alembic upgrade head && python -m app.bootstrap.seed && exec gunicorn app.main:app --bind 0.0.0.0:8010 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --access-logfile - --error-logfile -"]
