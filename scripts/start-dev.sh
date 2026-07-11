#!/usr/bin/env bash
# DWG-Agent — 开发模式启动（后端 + Vite HMR，不走 Nginx）
# 用法: bash scripts/start-dev.sh
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  DWG-Agent 开发模式${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"

# 0. 前置检查
step "前置检查"
require_env_files
if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    info "安装前端依赖..."
    (cd "$PROJECT_ROOT/frontend" && npm ci)
fi
ok "前置检查通过"

# 1. 基础设施
step "基础设施"
ensure_db_ready

# 2. Celery workers
step "Celery workers"
start_all_workers

# 3. 后端
step "后端 (${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT})"
if port_free "$LOCAL_BACKEND_PORT"; then
    info "启动后端..."
    cd "$PROJECT_ROOT/backend"
    uv run uvicorn app.main:app --reload --host "$LOCAL_BACKEND_HOST" --port "$LOCAL_BACKEND_PORT" &
    echo $! > /tmp/dwg-agent-backend.pid
    wait_port "$LOCAL_BACKEND_HOST" "$LOCAL_BACKEND_PORT" 30 "后端"
else
    ok "后端已运行"
fi

# 4. 前端
step "前端 Vite Dev Server"
cd "$PROJECT_ROOT/frontend"

if ! port_free 5173; then
    ok "Vite 已运行 (:5173)"
else
    # 确保 .env 是直连模式（dev server 不走 Nginx）
    if grep -q '^VITE_API_BASE_URL=$' .env 2>/dev/null; then
        warn ".env 是 Nginx 模式，开发模式需要直连后端"
        info "已临时设置 VITE_API_BASE_URL=http://${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT}"
        VITE_API_BASE_URL="http://${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT}" npm run dev &
    else
        npm run dev &
    fi
    FRONTEND_PID=$!
    echo $FRONTEND_PID > /tmp/dwg-agent-frontend.pid
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  开发模式已启动${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  前端: ${BLUE}http://127.0.0.1:5173${NC}      (Vite HMR)"
echo -e "  后端: ${BLUE}http://${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT}${NC}      (FastAPI --reload)"
echo -e "  Docs: ${DIM}http://${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT}/docs${NC}"
echo ""
print_admin_credentials
echo ""
echo -e "  停止: ${YELLOW}bash scripts/stop-all.sh${NC}"

wait
