#!/usr/bin/env bash
# DWG-Agent — 一键启动全栈
# 用法: bash scripts/start-all.sh              # 生产模式（Nginx 统一入口 :8080）
#       bash scripts/start-all.sh --rebuild     # 强制重建前端
#       bash scripts/start-all.sh --restart-backend  # 安全重载本项目后端
set -euo pipefail
source "$(dirname "$0")/lib.sh"

REBUILD=false
RESTART_BACKEND=false
for arg in "$@"; do
    case "$arg" in
        --rebuild) REBUILD=true ;;
        --restart-backend) RESTART_BACKEND=true ;;
        *) err "未知参数: $arg"; exit 2 ;;
    esac
done

echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  DWG-Agent 一键启动${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"

# ── 0. Pre-flight ──────────────────────────────────────────────
require_env_files

# ── 1. MySQL ───────────────────────────────────────────────────
step "1/5 MySQL"
ensure_db_ready

# ── 2. Celery Workers ──────────────────────────────────────────
step "2/5 Celery workers"
start_all_workers

# ── 3. Backend ─────────────────────────────────────────────────
step "3/5 后端 FastAPI"
if $RESTART_BACKEND; then
    restart_owned_backend
fi
if port_free "$LOCAL_BACKEND_PORT"; then
    info "启动后端 (${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT})..."
    start_local_backend
else
    BACKEND_PID="$(owned_backend_pid 2>/dev/null || true)"
    if [ -n "$BACKEND_PID" ] && backend_runtime_stale "$BACKEND_PID"; then
        warn "后端已运行，但运行代码已过期 (pid=${BACKEND_PID})"
        echo "  修复: bash scripts/start-all.sh --restart-backend"
    else
        ok "后端已运行 (:${LOCAL_BACKEND_PORT})"
    fi
fi

# ── 4. Frontend ────────────────────────────────────────────────
step "4/5 前端 React"
FRONTEND_DIST="$PROJECT_ROOT/frontend/dist"
NEED_BUILD=false

if [ ! -f "$FRONTEND_DIST/index.html" ]; then
    info "前端尚未构建"; NEED_BUILD=true
elif $REBUILD; then
    info "--rebuild 指定，强制重建"; NEED_BUILD=true
elif frontend_dist_stale; then
    info "前端构建产物已过期"; NEED_BUILD=true
else
    ok "前端构建产物为最新"
fi

if $NEED_BUILD; then
    info "构建前端..."
    cd "$PROJECT_ROOT/frontend"
    npm ci --silent
    npm run build 2>&1 | tail -3
    ok "前端构建完成"
fi

# ── 5. Nginx ───────────────────────────────────────────────────
step "5/5 Nginx 网关"
NGINX_CONF="$PROJECT_ROOT/infra/gateway/nginx/nginx.local.conf"
NGINX_PIDFILE="$PROJECT_ROOT/infra/gateway/nginx/logs/nginx.pid"

# 检查是否已有本项目的 nginx 在运行。master 通常属于 root，读取
# /proc 不需要 root 凭据，更适合无交互运维脚本。
NGINX_PID="$(cat "$NGINX_PIDFILE" 2>/dev/null || true)"
if [ -f "$NGINX_PIDFILE" ] && process_exists "$NGINX_PID"; then
    ok "Nginx 已运行 (:8080)"
else
    # 端口被占但不是我们的 → 报错退出，让用户自行处理
    if ! port_free 8080; then
        err "端口 8080 已被占用，请先释放: sudo nginx -c $NGINX_CONF -s quit"
        exit 1
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
echo -e "  前端:    ${BLUE}http://localhost:8080${NC}"
echo -e "  API 文档: ${BLUE}http://localhost:8080/docs${NC}"
echo -e "  Health:  ${BLUE}http://localhost:8080/health${NC}"
echo -e "  后端直达: ${DIM}http://${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT}${NC}"
echo ""
print_admin_credentials
echo ""
echo -e "  停止:  ${YELLOW}bash scripts/stop-all.sh${NC}"
echo -e "  状态:  ${YELLOW}bash scripts/status.sh${NC}"
echo ""
