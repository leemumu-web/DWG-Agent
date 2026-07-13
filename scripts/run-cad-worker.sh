#!/usr/bin/env bash
# Run one CAD Celery worker behind a queue-scoped persistent Xvfb server.
set -euo pipefail

usage() {
    echo "用法: $0 <queue> <concurrency> <node-name> <display>" >&2
    exit 2
}

[ "$#" -eq 4 ] || usage
queue="$1"
concurrency="$2"
node_name="$3"
display="$4"

case "$queue" in
    dxf|dxf2dwg) ;;
    *) echo "不支持的 CAD 队列: $queue" >&2; exit 2 ;;
esac
[[ "$concurrency" =~ ^[1-9][0-9]*$ ]] || {
    echo "concurrency 必须是正整数: $concurrency" >&2
    exit 2
}
[[ "$display" =~ ^:[0-9]+$ ]] || {
    echo "display 必须使用 :N 格式: $display" >&2
    exit 2
}

display_number="${display#:}"
x_socket="/tmp/.X11-unix/X${display_number}"
display_lock="/tmp/.X${display_number}-lock"
pid_file="/tmp/dwg-celery-${queue}.pid"
xvfb_pid=""
celery_pid=""

cleanup() {
    trap - EXIT INT TERM
    if [ -n "$celery_pid" ] && kill -0 "$celery_pid" 2>/dev/null; then
        kill -TERM "$celery_pid" 2>/dev/null || true
        wait "$celery_pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
    if [ -n "$xvfb_pid" ]; then
        if kill -0 "$xvfb_pid" 2>/dev/null; then
            kill -TERM "$xvfb_pid" 2>/dev/null || true
            wait "$xvfb_pid" 2>/dev/null || true
        fi
        rm -f "$x_socket" "$display_lock"
    fi
}
trap cleanup EXIT INT TERM

wait_for_x_socket() {
    local waited=0
    while [ "$waited" -lt 100 ]; do
        if [ -S "$x_socket" ] && kill -0 "$xvfb_pid" 2>/dev/null; then
            return 0
        fi
        if ! kill -0 "$xvfb_pid" 2>/dev/null; then
            echo "Xvfb 在 DISPLAY=${display} 就绪前退出" >&2
            return 1
        fi
        sleep 0.1
        waited=$((waited + 1))
    done
    echo "等待 Xvfb DISPLAY=${display} 超时" >&2
    return 1
}

if [ -e "$x_socket" ] || [ -e "$display_lock" ]; then
    display_owner=""
    if [ -r "$display_lock" ]; then
        display_owner="$(tr -d '[:space:]' <"$display_lock")"
    fi
    if [[ "$display_owner" =~ ^[1-9][0-9]*$ ]] && kill -0 "$display_owner" 2>/dev/null; then
        echo "DISPLAY=${display} 已被进程 ${display_owner} 占用" >&2
        exit 1
    fi
    echo "清理 DISPLAY=${display} 的失效 Xvfb lock/socket" >&2
    rm -f "$x_socket" "$display_lock"
fi

Xvfb "$display" -screen 0 1024x768x24 -nolisten tcp >/tmp/dwg-xvfb-${queue}.log 2>&1 &
xvfb_pid=$!
wait_for_x_socket
export DISPLAY="$display"

if [ -x .venv/bin/celery ]; then
    celery_cmd=(.venv/bin/celery)
elif command -v celery >/dev/null 2>&1; then
    celery_cmd=(celery)
else
    celery_cmd=(uv run celery)
fi

"${celery_cmd[@]}" \
    -A app.workers.celery_app:celery_app worker \
    -Q "$queue" -n "$node_name" \
    --concurrency="$concurrency" --prefetch-multiplier=1 --loglevel=INFO &
celery_pid=$!
echo "$celery_pid" >"$pid_file"
wait "$celery_pid"
