#!/usr/bin/env bash
# DWG-Agent — 全栈健康检查
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/local_stack.sh"
source "$(dirname "$0")/lib/cad_worker.sh"

echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  DWG-Agent 状态检查${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"

ALL_OK=true
COMPOSE_RUNNING_SERVICES=""
if command -v docker >/dev/null 2>&1; then
    COMPOSE_RUNNING_SERVICES="$(
        cd "$PROJECT_ROOT" && dck compose ps --status running --services 2>/dev/null || true
    )"
fi

# 1. Infrastructure
step "基础设施"
bash "$PROJECT_ROOT/scripts/db.sh" status || ALL_OK=false
for spec in "${WORKER_SPECS[@]}"; do
    IFS='|' read -r queue concurrency label _display <<<"$spec"
    mapfile -t worker_pids < <(celery_worker_pids "$queue" "$label")
    if ((${#worker_pids[@]} > 0)); then
        ok "Celery worker-${label} — pid(s) ${worker_pids[*]}"
        if [ "$queue" = "dxf_classification" ]; then
            concurrency_expected="--concurrency=${concurrency}"
            concurrency_ready=false
            mapfile -t parent_pids < <(celery_worker_parent_pids "$queue" "$label")
            for pid in "${parent_pids[@]}"; do
                worker_args="$(ps -o args= -p "$pid" 2>/dev/null || true)"
                if [[ "$worker_args" == *"$concurrency_expected"* ]]; then
                    concurrency_ready=true
                    break
                fi
            done
            if $concurrency_ready; then
                ok "Celery worker-${label} — concurrency=${concurrency}"
            else
                warn "Celery worker-${label} 未按 concurrency=${concurrency} 运行"
                ALL_OK=false
            fi
        fi
        if [ "$queue" = "dxf" ] || [ "$queue" = "dxf2dwg" ]; then
            if grep -qx "worker-${label}" <<<"$COMPOSE_RUNNING_SERVICES"; then
                warn "本地与 Compose 同时消费 ${queue} 队列（worker-${label}）；性能与调度结果不确定"
                ALL_OK=false
            fi
        fi
    else
        warn "Celery worker-${label} — 未运行"
        ALL_OK=false
    fi
done

# 2. Backend
step "后端"
if check_port "$LOCAL_BACKEND_PORT" "FastAPI"; then
    BACKEND_PID="$(owned_backend_pid 2>/dev/null || true)"
    if [ -n "$BACKEND_PID" ]; then
        BACKEND_STARTED="$(process_start_epoch "$BACKEND_PID" 2>/dev/null || true)"
        if [ -n "$BACKEND_STARTED" ]; then
            info "本项目后端 pid=${BACKEND_PID}，启动于 $(date -d "@${BACKEND_STARTED}" '+%F %T')"
        fi
        if backend_runtime_stale "$BACKEND_PID"; then
            warn "运行代码已过期；执行 bash scripts/start-all.sh --restart-backend"
            ALL_OK=false
        else
            ok "运行代码与后端源码时间一致"
        fi
    else
        warn "8010 不是本项目可识别的后端进程"
        ALL_OK=false
    fi
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

if frontend_dist_stale; then
    warn "前端构建产物已过期；执行 bash scripts/start-all.sh --rebuild"
    ALL_OK=false
else
    ok "前端构建产物为最新"
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
    exit 0
else
    echo -e "${YELLOW}══════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  状态异常或运行版本过期，请按上方建议处理${NC}"
    echo -e "${YELLOW}══════════════════════════════════════════════════════${NC}"
    exit 1
fi
