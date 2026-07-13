#!/usr/bin/env bash
# DWG-Agent — 共享函数库
# 用法: source "$(dirname "$0")/lib.sh"

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_BACKEND_HOST="${LOCAL_BACKEND_HOST:-127.0.0.1}"
LOCAL_BACKEND_PORT="${LOCAL_BACKEND_PORT:-8010}"
export PROJECT_ROOT LOCAL_BACKEND_HOST LOCAL_BACKEND_PORT

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[34m'; DIM='\033[2m'; NC='\033[0m'

# CAD 转换 worker 默认值可由启动进程环境覆盖；配置文件示例记录同名键。
DXF_WORKER_CONCURRENCY="${DXF_WORKER_CONCURRENCY:-8}"
DXF2DWG_WORKER_CONCURRENCY="${DXF2DWG_WORKER_CONCURRENCY:-8}"
DXF_WORKER_DISPLAY="${DXF_WORKER_DISPLAY:-:91}"
DXF2DWG_WORKER_DISPLAY="${DXF2DWG_WORKER_DISPLAY:-:92}"

# Celery worker 单一事实来源：queue|concurrency|slug|optional-display
# start-all / start-dev / stop-all / status 全部从这里派生，避免各脚本各写一份列表。
WORKER_SPECS=(
    "report|1|report|"
    "dxf|${DXF_WORKER_CONCURRENCY}|dxf|${DXF_WORKER_DISPLAY}"
    "dxf2dwg|${DXF2DWG_WORKER_CONCURRENCY}|dxf2dwg|${DXF2DWG_WORKER_DISPLAY}"
    "dxf2excel|1|dxf2excel|"
    "excel_final|1|excel-final|"
)

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "${BLUE}▶${NC} $1"; }
step() { echo -e "\n${BLUE}── $1 ──${NC}"; }

port_free() { ! ss -tlnp "sport = :$1" 2>/dev/null | grep -q ":$1"; }

# 确保 sudo 可用后再执行需要 root 的 mariadb 操作。
# 无 TTY 且凭据未缓存时快速失败，而不是永久挂在密码提示上（CI/cron/测试场景）。
ensure_sudo() {
    if sudo -n true 2>/dev/null; then
        return 0
    fi
    if [ -t 0 ]; then
        sudo -v || { err "sudo 鉴权失败"; return 1; }
        return 0
    fi
    err "需要 sudo 权限但当前无终端可输入密码（非交互环境）"
    echo "  修复: 先运行 'sudo -v' 缓存凭据，或为 mariadb 配置 NOPASSWD"
    return 1
}

# 从 .env 风格文件读取单个键值（保留原始值，不去引号）。
env_value() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 0
    awk -v key="$key" '
        BEGIN { FS="=" }
        $1 == key { sub(/^[^=]*=/, ""); print; exit }
    ' "$file"
}

# 启动前置：确认 .env 与 backend/.env 存在，否则直接退出。
require_env_files() {
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        err ".env 不存在，请从 .env.example 复制并配置"
        exit 1
    fi
    if [ ! -f "$PROJECT_ROOT/backend/.env" ]; then
        err "backend/.env 不存在，请从 .env.example 复制并配置"
        exit 1
    fi
}

# 启动 MySQL 并在 schema 缺失时初始化 + 种子。
ensure_db_ready() {
    bash "$PROJECT_ROOT/scripts/db.sh" start
    if bash "$PROJECT_ROOT/scripts/db.sh" check >/dev/null 2>&1; then
        ok "MySQL 已就绪，跳过初始化"
    else
        info "MySQL 需要初始化..."
        bash "$PROJECT_ROOT/scripts/db.sh" init
    fi
}

# 打印管理员登录凭据。这些值来自 .env，首次 db init 时写入数据库 sys_users。
print_admin_credentials() {
    local user pass
    user="$(env_value "$PROJECT_ROOT/.env" SUPER_ADMIN_USERNAME)"
    pass="$(env_value "$PROJECT_ROOT/.env" SUPER_ADMIN_PASSWORD)"
    echo -e "  管理员账号: ${YELLOW}${user:-未配置}${NC}"
    echo -e "  管理员密码: ${YELLOW}${pass:-未配置}${NC}"
    echo -e "  ${DIM}（取自 .env 的 SUPER_ADMIN_USERNAME/PASSWORD；首次 db init 写入数据库，之后改 .env 不影响已建账号）${NC}"
}

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

celery_worker_pattern() {
    local queue="$1" slug="$2"
    printf '%s' "[c]elery.*-A app\\.workers\\.celery_app(:celery_app)? worker.*-Q ${queue}( |$).*-n ${slug}-local@"
}

celery_worker_pids() {
    local queue="$1" slug="$2" pattern
    pattern="$(celery_worker_pattern "$queue" "$slug")"
    pgrep -f "$pattern" 2>/dev/null || true
}

stop_celery_worker() {
    local queue="$1" slug="${2:-${1//_/-}}"
    local label="worker-${slug}"
    local pidfile="/tmp/dwg-agent-${label}.pid"
    local pattern
    pattern="$(celery_worker_pattern "$queue" "$slug")"

    if ! celery_worker_pids "$queue" "$slug" | grep -q .; then
        rm -f "$pidfile"
        ok "Celery ${label} 未运行"
        return 0
    fi

    pkill -TERM -f "$pattern" 2>/dev/null || true
    for _ in $(seq 1 15); do
        if ! celery_worker_pids "$queue" "$slug" | grep -q .; then
            rm -f "$pidfile"
            ok "Celery ${label} 已停止"
            return 0
        fi
        sleep 1
    done
    warn "Celery ${label} 未在 15 秒内退出；未执行强制 kill"
    return 1
}

start_celery_worker() {
    local queue="$1" concurrency="$2" slug="${3:-${1//_/-}}" display="${4:-}"
    local label="worker-${slug}"
    local pidfile="/tmp/dwg-agent-${label}.pid"
    local logfile="/tmp/dwg-agent-${label}.log"
    local node="${slug}-local@$(hostname)"

    if pidfile_running "$pidfile"; then
        ok "Celery ${label} 已运行"
        return 0
    fi

    local -a discovered_pids
    mapfile -t discovered_pids < <(celery_worker_pids "$queue" "$slug")
    if [ "${#discovered_pids[@]}" -gt 0 ]; then
        echo "${discovered_pids[0]}" > "$pidfile"
        warn "Celery ${label} 已存在但 pidfile 缺失；已恢复进程跟踪，跳过重复启动"
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

    if [ -n "$display" ]; then
        nohup setsid "$PROJECT_ROOT/scripts/run-cad-worker.sh" \
            "$queue" "$concurrency" "${slug}-local@%h" "$display" \
            >"$logfile" 2>&1 </dev/null &
    else
        nohup setsid "${celery_cmd[@]}" \
            -A app.workers.celery_app:celery_app worker \
            -Q "$queue" -n "${slug}-local@%h" \
            --concurrency="$concurrency" --loglevel=INFO \
            >"$logfile" 2>&1 </dev/null &
    fi
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

# 遍历 WORKER_SPECS 启动/停止全部 Celery worker。
start_all_workers() {
    local spec queue concurrency slug display
    for spec in "${WORKER_SPECS[@]}"; do
        IFS='|' read -r queue concurrency slug display <<<"$spec"
        start_celery_worker "$queue" "$concurrency" "$slug" "$display"
    done
}

stop_all_workers() {
    local spec queue concurrency slug display
    for spec in "${WORKER_SPECS[@]}"; do
        IFS='|' read -r queue concurrency slug display <<<"$spec"
        stop_celery_worker "$queue" "$slug" || true
    done
}

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
