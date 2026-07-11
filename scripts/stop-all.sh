#!/usr/bin/env bash
# DWG-Agent — 一键停止全栈
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo -e "${RED}══════════════════════════════════════════════════════${NC}"
echo -e "${RED}  DWG-Agent 停止服务${NC}"
echo -e "${RED}══════════════════════════════════════════════════════${NC}"

# 1. Nginx
step "1/4 Nginx"
NGINX_CONF="$PROJECT_ROOT/infra/nginx/nginx.local.conf"
NGINX_PIDFILE="$PROJECT_ROOT/infra/nginx/logs/nginx.pid"
if [ -f "$NGINX_PIDFILE" ] && kill -0 "$(cat "$NGINX_PIDFILE")" 2>/dev/null; then
    sudo nginx -c "$NGINX_CONF" -s quit 2>/dev/null && ok "Nginx 已停止" || warn "Nginx 停止失败"
else
    sudo nginx -c "$NGINX_CONF" -s quit 2>/dev/null && ok "Nginx 已停止" || ok "Nginx 未运行"
fi

# 2. Frontend (Vite dev server)
step "2/4 前端"
kill_by_pidfile /tmp/dwg-agent-frontend.pid "Vite dev server"

# 3. Backend
step "3/4 后端"
kill_by_pidfile /tmp/dwg-agent-backend.pid "后端 (uvicorn)"
if ! port_free "$LOCAL_BACKEND_PORT"; then
    warn "端口 ${LOCAL_BACKEND_PORT} 仍被占用；未执行强制 kill，请确认是否为外部启动的后端进程"
    echo -e "  ${DIM}诊断: ss -ltnp 'sport = :${LOCAL_BACKEND_PORT}'${NC}"
else
    ok "后端 :${LOCAL_BACKEND_PORT} 已释放"
fi

# 4. Celery Workers
step "4/4 Celery workers"
stop_celery_worker report report || true
stop_celery_worker dxf dxf || true
stop_celery_worker dxf2dwg dxf2dwg || true
stop_celery_worker dxf2excel dxf2excel || true
stop_celery_worker excel_final excel-final || true
echo -e "  MySQL:  $(port_free 3306 && echo -e "${DIM}未运行${NC}" || echo -e "${GREEN}运行中${NC}")"
echo ""
echo -e "  ${YELLOW}MySQL 通常保持运行，不予停止。${NC}"
echo -e "  ${DIM}数据库诊断: bash scripts/db.sh status | bash scripts/db.sh logs${NC}"

echo ""
echo -e "${GREEN}  前端 + 后端 + Celery + Nginx 已停止${NC}"
