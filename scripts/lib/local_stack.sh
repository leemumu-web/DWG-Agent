#!/usr/bin/env bash
# Local backend/frontend process ownership and source-staleness checks.

if [ "${DWG_LOCAL_STACK_LIB_LOADED:-0}" = "1" ]; then
    return 0
fi
DWG_LOCAL_STACK_LIB_LOADED=1

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

files_newer_than_epoch() {
    local epoch="$1"
    shift
    local path
    for path in "$@"; do
        [ -e "$path" ] || continue
        if [ -f "$path" ]; then
            [ "$(stat -c %Y "$path")" -gt "$epoch" ] && return 0
        elif find "$path" -type f -newermt "@${epoch}" -print -quit 2>/dev/null | grep -q .; then
            return 0
        fi
    done
    return 1
}

process_start_epoch() {
    local pid="$1" elapsed
    elapsed="$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
    [[ "$elapsed" =~ ^[0-9]+$ ]] || return 1
    echo $(( $(date +%s) - elapsed ))
}

owned_backend_pid() {
    local pid cwd
    if [ -f /tmp/dwg-agent-backend.pid ]; then
        pid="$(cat /tmp/dwg-agent-backend.pid 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
            cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
            if [ "$cwd" = "$PROJECT_ROOT/backend" ]; then
                echo "$pid"
                return 0
            fi
        fi
    fi
    while read -r pid; do
        [ -n "$pid" ] || continue
        cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
        if [ "$cwd" = "$PROJECT_ROOT/backend" ]; then
            echo "$pid"
            return 0
        fi
    done < <(pgrep -f "[u]vicorn app\.main:app.*--port ${LOCAL_BACKEND_PORT}" 2>/dev/null || true)
    return 1
}

backend_runtime_stale() {
    local pid="${1:-}" started
    [ -n "$pid" ] || pid="$(owned_backend_pid 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    started="$(process_start_epoch "$pid")" || return 1
    files_newer_than_epoch "$started" \
        "$PROJECT_ROOT/backend/app" \
        "$PROJECT_ROOT/backend/migrations" \
        "$PROJECT_ROOT/backend/pyproject.toml" \
        "$PROJECT_ROOT/backend/uv.lock"
}

frontend_dist_stale() {
    local dist="$PROJECT_ROOT/frontend/dist/index.html" built
    [ -f "$dist" ] || return 0
    built="$(stat -c %Y "$dist")"
    files_newer_than_epoch "$built" \
        "$PROJECT_ROOT/frontend/src" \
        "$PROJECT_ROOT/frontend/package.json" \
        "$PROJECT_ROOT/frontend/package-lock.json" \
        "$PROJECT_ROOT/frontend/vite.config.ts" \
        "$PROJECT_ROOT/frontend/tsconfig.json"
}

restart_owned_backend() {
    local pid
    pid="$(owned_backend_pid 2>/dev/null || true)"
    if [ -z "$pid" ]; then
        if port_free "$LOCAL_BACKEND_PORT"; then
            ok "本项目后端未运行"
            return 0
        fi
        err "端口 ${LOCAL_BACKEND_PORT} 由非本项目进程占用，拒绝重启"
        return 1
    fi
    info "停止本项目后端 (pid=${pid})..."
    kill -TERM "$pid"
    local waited=0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 20 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        err "后端未在 20 秒内优雅退出；未执行强制终止"
        return 1
    fi
    rm -f /tmp/dwg-agent-backend.pid
    if ! port_free "$LOCAL_BACKEND_PORT"; then
        err "后端进程已退出，但端口 ${LOCAL_BACKEND_PORT} 仍被占用"
        return 1
    fi
    ok "旧后端已停止"
}

start_local_backend() {
    local oldpwd="$PWD" pid
    cd "$PROJECT_ROOT/backend"
    if [ -x .venv/bin/uvicorn ]; then
        nohup setsid .venv/bin/uvicorn app.main:app --host "$LOCAL_BACKEND_HOST" --port "$LOCAL_BACKEND_PORT" >/tmp/dwg-agent-backend.log 2>&1 </dev/null &
    else
        nohup setsid uv run uvicorn app.main:app --host "$LOCAL_BACKEND_HOST" --port "$LOCAL_BACKEND_PORT" >/tmp/dwg-agent-backend.log 2>&1 </dev/null &
    fi
    pid=$!
    echo "$pid" > /tmp/dwg-agent-backend.pid
    cd "$oldpwd"
    wait_port "$LOCAL_BACKEND_HOST" "$LOCAL_BACKEND_PORT" 30 "后端 :${LOCAL_BACKEND_PORT}"
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
