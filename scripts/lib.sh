#!/usr/bin/env bash
# DWG-Agent — 共享函数库
# 用法: source "$(dirname "$0")/lib.sh"

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[34m'; DIM='\033[2m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "${BLUE}▶${NC} $1"; }
step() { echo -e "\n${BLUE}── $1 ──${NC}"; }

port_free() { ! ss -tlnp "sport = :$1" 2>/dev/null | grep -q ":$1"; }

check_port() {
    local port="$1" label="$2"
    if port_free "$port"; then
        warn "$label — 未运行 (:${port})"
        return 1
    fi
    ok "$label — :$port"
}

kill_by_pidfile() {
    local pidfile="$1" label="${2:-process}"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        kill "$(cat "$pidfile")" 2>/dev/null && ok "$label 已停止" || true
        rm -f "$pidfile"
    fi
}

pidfile_running() {
    local pidfile="$1"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        return 0
    fi
    [ -f "$pidfile" ] && rm -f "$pidfile"
    return 1
}

start_celery_worker() {
    local queue="$1" concurrency="$2" slug="${3:-${1//_/-}}"
    local label="worker-${slug}"
    local pidfile="/tmp/dwg-agent-${label}.pid"
    local logfile="/tmp/dwg-agent-${label}.log"
    local node="${slug}-local@$(hostname)"

    if pidfile_running "$pidfile"; then
        ok "Celery ${label} 已运行"
        return 0
    fi

    info "启动 Celery ${label}..."
    local oldpwd="$PWD"
    cd "$PROJECT_ROOT/backend"

    local -a celery_cmd
    if [ -x .venv/bin/celery ]; then
        celery_cmd=(.venv/bin/celery)
    else
        celery_cmd=(uv run celery)
    fi

    nohup setsid "${celery_cmd[@]}" \
        -A app.workers.celery_app:celery_app worker \
        -Q "$queue" -n "${slug}-local@%h" \
        --concurrency="$concurrency" --loglevel=INFO \
        >"$logfile" 2>&1 </dev/null &
    local pid=$!
    echo "$pid" > "$pidfile"

    local ready=false
    for _ in $(seq 1 12); do
        if ! kill -0 "$pid" 2>/dev/null; then
            cd "$oldpwd"
            err "Celery ${label} 启动失败，请检查: $logfile"
            tail -40 "$logfile" 2>/dev/null || true
            rm -f "$pidfile"
            return 1
        fi
        if grep -q "$node" "$logfile" 2>/dev/null; then
            ready=true
            break
        fi
        sleep 1
    done
    cd "$oldpwd"

    if $ready; then
        ok "Celery ${label} 已启动 (concurrency=${concurrency})"
        return 0
    fi

    err "Celery ${label} 未完成启动，请检查: $logfile"
    tail -40 "$logfile" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    rm -f "$pidfile"
    return 1
}

start_report_worker() { start_celery_worker report 1; }
start_dxf_worker() { start_celery_worker dxf 2; }
start_dxf2dwg_worker() { start_celery_worker dxf2dwg 2; }
start_dxf2excel_worker() { start_celery_worker dxf2excel 1; }
start_excel_final_worker() { start_celery_worker excel_final 1 excel-final; }

wait_port() {
    local host="$1" port="$2" timeout="${3:-30}" label="${4:-$host:$port}"
    local waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if ss -tlnp "sport = :$port" 2>/dev/null | grep -q ":$port"; then
            ok "$label 就绪 (${waited}s)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    err "$label 超时未就绪"
    return 1
}

ensure_service() {
    local port="$1"
    shift
    local names=("$@")
    if ! port_free "$port"; then
        return 0
    fi
    warn "${names[0]} (:${port}) 未运行，尝试启动..."
    for name in "${names[@]}"; do
        if systemctl is-active --quiet "$name" 2>/dev/null; then
            ok "$name 已运行"
            return 0
        fi
    done
    for name in "${names[@]}"; do
        if sudo systemctl start "$name" 2>/dev/null; then
            ok "$name 已启动"
            return 0
        fi
    done
    err "无法启动 ${names[0]}，请手动启动"
    return 1
}
