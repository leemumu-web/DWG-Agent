#!/usr/bin/env bash
# DWG-Agent — 开发模式启动（后端 + Vite HMR，不走 Nginx）
# 用法: bash scripts/start-dev.sh
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  DWG-Agent 开发模式${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"

# 1. 基础设施
step "基础设施"
check_port() { port_free "$1" && warn "$2 (:${1}) 未运行" || ok "$2 (:${1})"; }
check_port 3306 "MySQL"
check_port 6379 "Redis"

# 2. 后端
step "后端 (127.0.0.1:8000)"
if port_free 8000; then
    info "启动后端..."
    cd "$PROJECT_ROOT/backend"
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
    echo $! > /tmp/dwg-agent-backend.pid
    wait_port 127.0.0.1 8000 30 "后端"
else
    ok "后端已运行"
fi

# 3. 前端
step "前端 Vite Dev Server"
cd "$PROJECT_ROOT/frontend"

# 确保 .env 是直连模式（dev server 不走 Nginx）
if grep -q '^VITE_API_BASE_URL=$' .env 2>/dev/null; then
    warn ".env 是 Nginx 模式，开发模式需要直连后端"
    info "已临时设置 VITE_API_BASE_URL=http://127.0.0.1:8000"
    VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev &
else
    npm run dev &
fi
FRONTEND_PID=$!
echo $FRONTEND_PID > /tmp/dwg-agent-frontend.pid

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  开发模式已启动${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  前端: ${BLUE}http://127.0.0.1:5173${NC}   (Vite HMR)"
echo -e "  API:  ${BLUE}http://127.0.0.1:8000${NC}   (FastAPI --reload)"
echo -e "  Docs: ${DIM}http://127.0.0.1:8000/docs${NC}"
echo ""
echo -e "  停止: ${YELLOW}bash scripts/stop-all.sh${NC}"

wait
