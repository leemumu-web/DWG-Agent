#!/usr/bin/env bash
# DWG-Agent — 初始化/重建数据库
# 用法: bash scripts/init-db.sh
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo -e "${BLUE}初始化数据库...${NC}"
cd "$PROJECT_ROOT/backend"
uv run python -m app.db.init_db
ok "数据库初始化完成"
