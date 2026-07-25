#!/usr/bin/env bash
# DWG-Agent — 一键启动全栈
# 用法: bash scripts/start-all.sh  # 按当前代码重启全部受管服务（Nginx :8080）
# 兼容旧调用者保留 --rebuild / --restart-backend；当前启动已默认执行二者。
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/database.sh"
source "$(dirname "$0")/lib/local_stack.sh"
source "$(dirname "$0")/lib/cad_worker.sh"

for arg in "$@"; do
    case "$arg" in
        --rebuild|--restart-backend) ;;
        *) err "未知参数: $arg"; exit 2 ;;
    esac
done

echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  DWG-Agent 一键启动${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"

# ── 0. Pre-flight ──────────────────────────────────────────────
require_env_files

# ── 1. Replace old managed runtime and sync locked dependencies ─
step "1/7 清理旧运行并更新环境"
COMPOSE_CONTAINER_IDS=""
if command -v docker >/dev/null 2>&1 \
    && docker info >/dev/null 2>&1 \
    && [ -f "$PROJECT_ROOT/.env.docker" ]; then
    COMPOSE_CONTAINER_IDS="$(docker compose --project-directory "$PROJECT_ROOT" \
        --env-file "$PROJECT_ROOT/.env.docker" --profile workers \
        ps --all -q 2>/dev/null || true)"
fi
if [ -n "$COMPOSE_CONTAINER_IDS" ]; then
    info "停止本项目现有 Compose 实例..."
    bash "$PROJECT_ROOT/scripts/docker.sh" down
fi
bash "$PROJECT_ROOT/scripts/stop-all.sh"

info "同步后端锁定依赖..."
(
    cd "$PROJECT_ROOT/backend"
    uv sync --frozen
)
ok "后端运行环境已更新"

# ── 2. MySQL ───────────────────────────────────────────────────
step "2/7 MySQL"
ensure_db_ready

# ── 3. Celery Workers ──────────────────────────────────────────
step "3/7 Celery workers"
start_all_workers

# ── 4. Backend ─────────────────────────────────────────────────
step "4/7 后端 FastAPI"
if port_free "$LOCAL_BACKEND_PORT"; then
    info "启动后端 (${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT})..."
    start_local_backend
else
    err "端口 ${LOCAL_BACKEND_PORT} 仍被非本项目进程占用，拒绝覆盖"
    exit 1
fi

# ── 5. Frontend ────────────────────────────────────────────────
step "5/7 前端 React"
info "按当前代码重新安装锁定依赖并构建前端..."
(
    cd "$PROJECT_ROOT/frontend"
    npm ci --silent
    npm run build 2>&1 | tail -3
)
ok "前端构建完成"

# ── 6. Nginx ───────────────────────────────────────────────────
step "6/7 Nginx 网关"
NGINX_CONF="$PROJECT_ROOT/infra/gateway/nginx/nginx.local.conf"
NGINX_PIDFILE="$PROJECT_ROOT/infra/gateway/nginx/logs/nginx.pid"
NGINX_CLIENT_BODY_DIR="$PROJECT_ROOT/infra/gateway/nginx/logs/client-body"
mkdir -p "$NGINX_CLIENT_BODY_DIR"

# 检查是否已有本项目的 nginx 在运行。当前本地配置以仓库用户启动；
# 只读 /proc 的归属检查同样兼容升级前由 root 启动的历史进程。
NGINX_PID="$(cat "$NGINX_PIDFILE" 2>/dev/null || true)"
if [ -f "$NGINX_PIDFILE" ] && process_exists "$NGINX_PID"; then
    ok "Nginx 已运行 (:8080)"
else
    # 端口被占但不是我们的 → 报错退出，让用户自行处理
    if ! port_free 8080; then
        err "端口 8080 已被占用，请先释放: nginx -c $NGINX_CONF -s quit"
        exit 1
    fi
    info "启动 Nginx (:8080)..."
    nginx -c "$NGINX_CONF"
    sleep 1
    if ! port_free 8080; then
        ok "Nginx 已启动 (:8080)"
    else
        err "Nginx 启动失败，请检查: nginx -t -c $NGINX_CONF"
        exit 1
    fi
fi

# ── 7. Final readiness gate ────────────────────────────────────
step "7/7 全栈就绪验证"
if ! bash "$PROJECT_ROOT/scripts/status.sh"; then
    err "全栈启动后验证失败；请按上方诊断处理后重试"
    exit 1
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
