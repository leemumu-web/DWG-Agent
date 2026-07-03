#!/usr/bin/env bash
# DWG-Agent — 初始化数据库
# 用法: bash scripts/init_db.sh
set -euo pipefail
source "$(dirname "$0")/lib.sh"

exec bash "$PROJECT_ROOT/scripts/db.sh" init
