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
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    err ".env 不存在，请从 .env.example 复制并配置"
    exit 1
fi
if [ ! -f "$PROJECT_ROOT/backend/.env" ]; then
    err "backend/.env 不存在，请从 .env.example 复制并配置"
    exit 1
fi
if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    info "安装前端依赖..."
    (cd "$PROJECT_ROOT/frontend" && npm ci)
fi
ok "前置检查通过"

# 1. 基础设施
step "基础设施"
bash "$PROJECT_ROOT/scripts/db.sh" start
# Only init if DB needs it (check first to skip expensive migration check)
if bash "$PROJECT_ROOT/scripts/db.sh" check >/dev/null 2>&1; then
    ok "MySQL 已就绪，跳过初始化"
else
    info "MySQL 需要初始化..."
    bash "$PROJECT_ROOT/scripts/db.sh" init
fi
ensure_service 6379 redis valkey

# 2. Celery workers
step "Celery worker-report"
start_report_worker
step "Celery worker-dxf"
start_dxf_worker
step "Celery worker-dxf2dwg"
start_dxf2dwg_worker
step "Celery worker-dxf2excel"
start_dxf2excel_worker

# 3. 后端
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

# 4. 前端
step "前端 Vite Dev Server"
cd "$PROJECT_ROOT/frontend"

if ! port_free 5173; then
    ok "Vite 已运行 (:5173)"
else
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
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  开发模式已启动${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  前端: ${BLUE}http://127.0.0.1:5173${NC}      (Vite HMR)"
echo -e "  后端: ${BLUE}http://127.0.0.1:8000${NC}      (FastAPI --reload)"
echo -e "  Docs: ${DIM}http://127.0.0.1:8000/docs${NC}"
echo ""
echo -e "  停止: ${YELLOW}bash scripts/stop-all.sh${NC}"

wait
