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
for worker in "report:report" "dxf:dxf" "dxf2dwg:dxf2dwg" "dxf2excel:dxf2excel" "excel_final:excel-final"; do
    queue="${worker%%:*}"
    label="${worker#*:}"
    mapfile -t worker_pids < <(celery_worker_pids "$queue" "$label")
    if ((${#worker_pids[@]} > 0)); then
        ok "Celery worker-${label} — pid(s) ${worker_pids[*]}"
    else
        warn "Celery worker-${label} — 未运行"
        ALL_OK=false
    fi
done

# 2. Backend
step "后端"
if check_port "$LOCAL_BACKEND_PORT" "FastAPI"; then
    HEALTH=$(curl -s "http://${LOCAL_BACKEND_HOST}:${LOCAL_BACKEND_PORT}/health/ready" 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"status":"ok"'; then
        ok "健康检查: ok"
    else
        warn "就绪检查: MySQL 不可达或后端无响应"
        ALL_OK=false
    fi
else
    ALL_OK=false
fi

# 3. Nginx
step "网关"
if check_port 8080 "Nginx"; then
    API_TEST=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health 2>/dev/null || echo "000")
    if [ "$API_TEST" = "200" ]; then
        ok "API 反向代理正常 (GET /health → $API_TEST)"
    else
        warn "API 反代异常 (HTTP $API_TEST)"
        ALL_OK=false
    fi

    SPA_TEST=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ 2>/dev/null || echo "000")
    if [ "$SPA_TEST" = "200" ]; then
        ok "SPA 静态托管正常 (GET / → $SPA_TEST)"
    else
        warn "SPA 异常 (HTTP $SPA_TEST)"
        ALL_OK=false
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
