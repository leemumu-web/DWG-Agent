#!/usr/bin/env bash
# DWG-Agent — 仅启动前端 (开发模式)
# 推荐使用 scripts/start-dev.sh 一键启动前后端
set -euo pipefail
source "$(dirname "$0")/lib.sh"

cd "$PROJECT_ROOT/frontend"
# 开发模式确保直连后端
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
