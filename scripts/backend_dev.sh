#!/usr/bin/env bash
# DWG-Agent — 仅启动后端 (开发模式)
# 推荐使用 scripts/start-dev.sh 一键启动前后端
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if port_free 8000; then
    info "启动后端 (127.0.0.1:8000)..."
    cd "$PROJECT_ROOT/backend"
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
else
    err "端口 8000 已被占用"; exit 1
fi
