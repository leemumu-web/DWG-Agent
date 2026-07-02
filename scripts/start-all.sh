#!/usr/bin/env bash
# DWG-Agent — 一键启动全栈
# 用法: bash scripts/start-all.sh              # 生产模式（Nginx 统一入口 :8080）
#       bash scripts/start-all.sh --rebuild     # 强制重建前端
set -euo pipefail
source "$(dirname "$0")/lib.sh"

REBUILD=false; [ "${1:-}" = "--rebuild" ] && REBUILD=true

echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  DWG-Agent 一键启动${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"

# ── 1. MySQL ───────────────────────────────────────────────────
step "1/5 MySQL"
if port_free 3306; then
    warn "MySQL 未运行，尝试启动..."
    sudo systemctl start mariadb 2>/dev/null && ok "MariaDB 已启动" || err "MariaDB 启动失败，请手动启动"
else
    ok "MySQL 已运行 (:3306)"
fi

# ── 2. Redis ───────────────────────────────────────────────────
step "2/5 Redis"
if port_free 6379; then
    warn "Redis 未运行，尝试启动..."
    sudo systemctl start redis 2>/dev/null && ok "Redis 已启动" || err "Redis 启动失败，请手动启动"
else
    ok "Redis 已运行 (:6379)"
fi

# ── 3. Backend ─────────────────────────────────────────────────
step "3/5 后端 FastAPI"
if port_free 8000; then
    info "启动后端 (127.0.0.1:8000)..."
    cd "$PROJECT_ROOT/backend"
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &
    BACKEND_PID=$!
    echo $BACKEND_PID > /tmp/dwg-agent-backend.pid
    wait_port 127.0.0.1 8000 30 "后端 :8000"
else
    ok "后端已运行 (:8000)"
fi

# ── 4. Frontend ────────────────────────────────────────────────
step "4/5 前端 React"
FRONTEND_DIST="$PROJECT_ROOT/frontend/dist"
NEED_BUILD=false

if [ ! -f "$FRONTEND_DIST/index.html" ]; then
    info "前端尚未构建"; NEED_BUILD=true
elif $REBUILD; then
    info "--rebuild 指定，强制重建"; NEED_BUILD=true
else
    ok "前端已有构建产物"
fi

if $NEED_BUILD; then
    info "构建前端..."
    cd "$PROJECT_ROOT/frontend"
    npm ci --silent 2>/dev/null || true
    npm run build 2>&1 | tail -3
    ok "前端构建完成"
fi

# ── 5. Nginx ───────────────────────────────────────────────────
step "5/5 Nginx 网关"
NGINX_CONF="$PROJECT_ROOT/infra/nginx/nginx.local.conf"
NGINX_PIDFILE="$PROJECT_ROOT/infra/nginx/logs/nginx.pid"

# 检查是否已有本项目的 nginx 在运行
if [ -f "$NGINX_PIDFILE" ] && sudo kill -0 "$(cat "$NGINX_PIDFILE")" 2>/dev/null; then
    ok "Nginx 已运行 (:8080)"
else
    # 端口被占但不是我们的 → 先停
    if ! port_free 8080; then
        warn "端口 8080 被占用，尝试释放..."
        sudo fuser -k 8080/tcp 2>/dev/null || true
        sleep 1
    fi
    info "启动 Nginx (:8080)..."
    sudo nginx -c "$NGINX_CONF"
    sleep 1
    if ! port_free 8080; then
        ok "Nginx 已启动 (:8080)"
    else
        err "Nginx 启动失败，请检查: sudo nginx -t -c $NGINX_CONF"
        exit 1
    fi
fi

# ── Summary ────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  全栈启动完成${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  前端:  ${BLUE}http://localhost:8080${NC}"
echo -e "  API:   ${BLUE}http://localhost:8080/api/v1${NC}"
echo -e "  Health:${BLUE}http://localhost:8080/health${NC}"
echo -e "  后端直达: ${DIM}http://127.0.0.1:8000${NC}"
echo ""
echo -e "  登录:  ${YELLOW}admin / admin123456${NC}"
echo -e "  停止:  ${YELLOW}bash scripts/stop-all.sh${NC}"
echo -e "  状态:  ${YELLOW}bash scripts/status.sh${NC}"
echo ""
