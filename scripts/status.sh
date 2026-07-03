#!/usr/bin/env bash
# DWG-Agent — 全栈健康检查
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  DWG-Agent 状态检查${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"

ALL_OK=true

# 1. Infrastructure
step "基础设施"
bash "$PROJECT_ROOT/scripts/db.sh" status || ALL_OK=false
check_port 6379 "Redis"   || ALL_OK=false

# 2. Backend
step "后端"
if check_port 8000 "FastAPI"; then
    HEALTH=$(curl -s http://127.0.0.1:8000/health 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"status":"ok"'; then
        ok "健康检查: ok"
    else
        warn "健康检查: 无响应"
    fi
else
    ALL_OK=false
fi

# 3. Nginx
step "网关"
if check_port 8080 "Nginx"; then
    LOGIN_TEST=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8080/api/v1/auth/sessions \
        -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"SuperAdminPass1"}' 2>/dev/null || echo "000")
    if [ "$LOGIN_TEST" = "200" ] || [ "$LOGIN_TEST" = "201" ]; then
        ok "API 反向代理正常 (POST /auth/sessions → $LOGIN_TEST)"
    else
        warn "API 反代异常 (HTTP $LOGIN_TEST)"
    fi

    SPA_TEST=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ 2>/dev/null || echo "000")
    if [ "$SPA_TEST" = "200" ]; then
        ok "SPA 静态托管正常 (GET / → $SPA_TEST)"
    else
        warn "SPA 异常 (HTTP $SPA_TEST)"
    fi
else
    ALL_OK=false
fi

# 4. Summary
echo ""
if $ALL_OK; then
    echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  全部正常${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
else
    echo -e "${YELLOW}══════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  部分服务未运行，执行: bash scripts/start-all.sh${NC}"
    echo -e "${YELLOW}══════════════════════════════════════════════════════${NC}"
fi
