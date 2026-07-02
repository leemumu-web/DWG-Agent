#!/usr/bin/env bash
# DWG-Agent — 一键停止全栈
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo -e "${RED}══════════════════════════════════════════════════════${NC}"
echo -e "${RED}  DWG-Agent 停止服务${NC}"
echo -e "${RED}══════════════════════════════════════════════════════${NC}"

# 1. Nginx
step "1/3 Nginx"
NGINX_CONF="$PROJECT_ROOT/infra/nginx/nginx.local.conf"
NGINX_PIDFILE="$PROJECT_ROOT/infra/nginx/logs/nginx.pid"
if [ -f "$NGINX_PIDFILE" ] && kill -0 "$(cat "$NGINX_PIDFILE")" 2>/dev/null; then
    sudo nginx -c "$NGINX_CONF" -s quit 2>/dev/null && ok "Nginx 已停止" || warn "Nginx 停止失败"
else
    sudo nginx -c "$NGINX_CONF" -s quit 2>/dev/null && ok "Nginx 已停止" || ok "Nginx 未运行"
fi

# 2. Backend
step "2/3 后端"
kill_by_pidfile /tmp/dwg-agent-backend.pid "后端 (uvicorn)"
if ! port_free 8000; then
    warn "端口 8000 仍被占用，强制释放..."
    sudo fuser -k 8000/tcp 2>/dev/null && ok "已释放 :8000" || true
else
    ok "后端 :8000 已释放"
fi

# 3. Infrastructure (optional - ask)
step "3/3 MySQL + Redis"
echo -e "  MySQL:  $(port_free 3306 && echo -e "${DIM}未运行${NC}" || echo -e "${GREEN}运行中${NC}")"
echo -e "  Redis: $(port_free 6379 && echo -e "${DIM}未运行${NC}" || echo -e "${GREEN}运行中${NC}")"
echo ""
echo -e "  ${YELLOW}MySQL/Redis 通常保持运行，不予停止。${NC}"
echo -e "  ${DIM}如需停止: sudo systemctl stop mariadb redis${NC}"

echo ""
echo -e "${GREEN}  前端 + 后端 + Nginx 已停止${NC}"
