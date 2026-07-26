#!/usr/bin/env bash
# Celery queue topology and CAD worker/Xvfb lifecycle.

if [ "${DWG_CAD_WORKER_LIB_LOADED:-0}" = "1" ]; then
    return 0
fi
DWG_CAD_WORKER_LIB_LOADED=1

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/local_stack.sh"

DXF_WORKER_CONCURRENCY="${DXF_WORKER_CONCURRENCY:-8}"
DXF2DWG_WORKER_CONCURRENCY="${DXF2DWG_WORKER_CONCURRENCY:-8}"
DXF_WORKER_DISPLAY="${DXF_WORKER_DISPLAY:-:91}"
DXF2DWG_WORKER_DISPLAY="${DXF2DWG_WORKER_DISPLAY:-:92}"
DXF_CLASSIFICATION_WORKER_CONCURRENCY="${DXF_CLASSIFICATION_WORKER_CONCURRENCY:-3}"

if ! [[ "$DXF_CLASSIFICATION_WORKER_CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
    echo "DXF classification concurrency 配置无效: ${DXF_CLASSIFICATION_WORKER_CONCURRENCY}" >&2
    return 2 2>/dev/null || exit 2
fi

# queue|capacity|slug|optional-display is the local worker single source of truth.
WORKER_SPECS=(
    "report|1|report|"
    "dxf_classification|${DXF_CLASSIFICATION_WORKER_CONCURRENCY}|dxf-classification|"
    "dxf_split|1|dxf-split|"
    "dxf|${DXF_WORKER_CONCURRENCY}|dxf|${DXF_WORKER_DISPLAY}"
    "dxf2dwg|${DXF2DWG_WORKER_CONCURRENCY}|dxf2dwg|${DXF2DWG_WORKER_DISPLAY}"
    "dxf2excel|1|dxf2excel|"
    "excel_final|1|excel-final|"
    "maintenance|1|maintenance|"
)

celery_worker_pattern() {
    local queue="$1" slug="$2"
    printf '%s' "[c]elery.*-A app\\.platform\\.messaging\\.celery_app(:celery_app)? worker.*-Q ${queue}( |$).*-n ${slug}-local@"
}

celery_worker_pids() {
    local queue="$1" slug="$2" pattern
    pattern="$(celery_worker_pattern "$queue" "$slug")"
    pgrep -f "$pattern" 2>/dev/null || true
}

celery_worker_parent_pids() {
    local queue="$1" slug="$2" pid parent
    local -a worker_pids
    mapfile -t worker_pids < <(celery_worker_pids "$queue" "$slug")
    for pid in "${worker_pids[@]}"; do
        parent="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
        if ! printf '%s\n' "${worker_pids[@]}" | grep -qx "$parent"; then
            echo "$pid"
        fi
    done
}

worker_pid_is_owned() {
    local pid="$1" queue="$2" slug="$3" args
    args="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    [ -n "$args" ] || return 1
    [[ "$args" == *"$PROJECT_ROOT"* ]] || return 1
    if [[ "$args" == *"run-cad-worker.sh ${queue} "* ]]; then
        return 0
    fi
    [[ "$args" == *"celery"* && "$args" == *"-Q ${queue} "* && "$args" == *"-n ${slug}-local@"* ]]
}

stop_celery_worker() {
    local queue="$1" slug="${2:-${1//_/-}}"
    local label="worker-${slug}"
    local pidfile="/tmp/dwg-agent-${label}.pid"
    local tracked_pid
    tracked_pid="$(cat "$pidfile" 2>/dev/null || true)"

    local -a parent_pids
    mapfile -t parent_pids < <(celery_worker_parent_pids "$queue" "$slug")
    if [[ "$tracked_pid" =~ ^[1-9][0-9]*$ ]] \
        && process_exists "$tracked_pid" \
        && worker_pid_is_owned "$tracked_pid" "$queue" "$slug" \
        && ! printf '%s\n' "${parent_pids[@]}" | grep -qx "$tracked_pid"; then
        parent_pids+=("$tracked_pid")
    fi

    if [ "${#parent_pids[@]}" -eq 0 ]; then
        rm -f "$pidfile"
        ok "Celery ${label} 未运行"
        return 0
    fi

    kill -TERM "${parent_pids[@]}" 2>/dev/null || true
    for _ in $(seq 1 15); do
        local any_running=false pid
        for pid in "${parent_pids[@]}"; do
            process_exists "$pid" && any_running=true
        done
        if ! $any_running && ! celery_worker_pids "$queue" "$slug" | grep -q .; then
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
        local tracked_pid
        tracked_pid="$(cat "$pidfile")"
        if celery_worker_pids "$queue" "$slug" | grep -q .; then
            ok "Celery ${label} 已运行"
            return 0
        fi
        if worker_pid_is_owned "$tracked_pid" "$queue" "$slug"; then
            err "Celery ${label} 的 pidfile 指向旧版或不兼容 Worker；请先执行 stop-all 后重试"
            return 1
        fi
        warn "Celery ${label} 的 pidfile 指向非本项目进程；移除失效跟踪文件"
        rm -f "$pidfile"
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
        DWG_WORKER_QUEUE="$queue" \
        DWG_WORKER_CONCURRENCY="$concurrency" \
        nohup setsid "${celery_cmd[@]}" \
            -A app.platform.messaging.celery_app:celery_app worker \
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
    return 0
}

cad_worker_usage() {
    echo "用法: $0 <queue> <concurrency> <node-name> <display>" >&2
    return 2
}

cad_worker_cleanup() {
    trap - EXIT INT TERM
    if [ -n "${celery_pid:-}" ] && kill -0 "$celery_pid" 2>/dev/null; then
        kill -TERM "$celery_pid" 2>/dev/null || true
        wait "$celery_pid" 2>/dev/null || true
    fi
    [ -n "${pid_file:-}" ] && rm -f "$pid_file"
    if [ -n "${xvfb_pid:-}" ]; then
        if kill -0 "$xvfb_pid" 2>/dev/null; then
            kill -TERM "$xvfb_pid" 2>/dev/null || true
            wait "$xvfb_pid" 2>/dev/null || true
        fi
        rm -f "${x_socket:-}" "${display_lock:-}"
    fi
}

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

cad_worker_main() {
    [ "$#" -eq 4 ] || { cad_worker_usage; return 2; }
    queue="$1"
    concurrency="$2"
    node_name="$3"
    display="$4"

    case "$queue" in
        dxf|dxf2dwg) ;;
        *) echo "不支持的 CAD 队列: $queue" >&2; return 2 ;;
    esac
    [[ "$concurrency" =~ ^[1-9][0-9]*$ ]] || {
        echo "concurrency 必须是正整数: $concurrency" >&2
        return 2
    }
    [[ "$display" =~ ^:[0-9]+$ ]] || {
        echo "display 必须使用 :N 格式: $display" >&2
        return 2
    }

    # The hardened container mounts /tmp as a non-executable tmpfs. AppImage
    # must extract onto the dedicated application volume before ODA starts.
    if [ -n "${TMPDIR:-}" ]; then
        mkdir -p "$TMPDIR"
    fi

    display_number="${display#:}"
    x_socket="/tmp/.X11-unix/X${display_number}"
    display_lock="/tmp/.X${display_number}-lock"
    pid_file="/tmp/dwg-celery-${queue}.pid"
    xvfb_pid=""
    celery_pid=""
    trap cad_worker_cleanup EXIT INT TERM

    if [ -e "$x_socket" ] || [ -e "$display_lock" ]; then
        display_owner=""
        if [ -r "$display_lock" ]; then
            display_owner="$(tr -d '[:space:]' <"$display_lock")"
        fi
        if [[ "$display_owner" =~ ^[1-9][0-9]*$ ]] && kill -0 "$display_owner" 2>/dev/null; then
            echo "DISPLAY=${display} 已被进程 ${display_owner} 占用" >&2
            return 1
        fi
        echo "清理 DISPLAY=${display} 的失效 Xvfb lock/socket" >&2
        rm -f "$x_socket" "$display_lock"
    fi

    Xvfb "$display" -screen 0 1024x768x24 -nolisten tcp >/tmp/dwg-xvfb-${queue}.log 2>&1 &
    xvfb_pid=$!
    wait_for_x_socket
    export DISPLAY="$display"
    export DWG_WORKER_QUEUE="$queue"
    export DWG_WORKER_CONCURRENCY="$concurrency"

    if [ -x .venv/bin/celery ]; then
        celery_cmd=(.venv/bin/celery)
    elif command -v celery >/dev/null 2>&1; then
        celery_cmd=(celery)
    else
        celery_cmd=(uv run celery)
    fi

    "${celery_cmd[@]}" \
        -A app.platform.messaging.celery_app:celery_app worker \
        -Q "$queue" -n "$node_name" \
        --concurrency="$concurrency" --prefetch-multiplier=1 --loglevel=INFO &
    celery_pid=$!
    echo "$celery_pid" >"$pid_file"
    wait "$celery_pid"
}
