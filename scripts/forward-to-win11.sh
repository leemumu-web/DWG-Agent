#!/usr/bin/env bash
# Manage an SSH remote-forward tunnel from Win11 back to the local DWG-Agent UI.

set -euo pipefail

COMMAND="start"
REMOTE_HOST="${FORWARD_REMOTE_HOST:-win11}"
REMOTE_BIND_ADDRESS="${FORWARD_REMOTE_BIND_ADDRESS:-127.0.0.1}"
REMOTE_PORT="${FORWARD_REMOTE_PORT:-8080}"
LOCAL_ADDRESS="${FORWARD_LOCAL_ADDRESS:-127.0.0.1}"
LOCAL_PORT="${FORWARD_LOCAL_PORT:-8080}"
RUNTIME_DIR="${FORWARD_RUNTIME_DIR:-}"

usage() {
    cat <<'EOF'
Usage:
  bash scripts/forward-to-win11.sh [start|stop|restart|status] [options]
  bash scripts/forward-to-win11.sh --stop [options]

Manage an SSH remote-forward tunnel. With no command, "start" is used.

Defaults:
  SSH host:           win11
  Remote listener:   127.0.0.1:8080
  Local target:      127.0.0.1:8080

Options:
  --host HOST              SSH config host or user@host
  --remote-address ADDRESS Remote bind address on the SSH server
  --remote-port PORT       Remote listening port
  --local-address ADDRESS  Local target address
  --local-port PORT        Local target port
  --runtime-dir DIR        Control socket and lock directory
  -h, --help               Show this help

Environment defaults (command-line options take precedence):
  FORWARD_REMOTE_HOST
  FORWARD_REMOTE_BIND_ADDRESS
  FORWARD_REMOTE_PORT
  FORWARD_LOCAL_ADDRESS
  FORWARD_LOCAL_PORT
  FORWARD_RUNTIME_DIR

Status exits with code 0 when active and code 3 when not running.
EOF
}

die_usage() {
    printf 'Error: %s\n' "$1" >&2
    printf 'Run with --help for usage.\n' >&2
    exit 2
}

require_option_value() {
    local option="$1"
    local value="${2:-}"
    [[ -n "$value" && "$value" != --* ]] || die_usage "$option requires a value"
}

if (($# > 0)); then
    case "$1" in
        start|stop|restart|status)
            COMMAND="$1"
            shift
            ;;
        --stop)
            COMMAND="stop"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            ;;
        *)
            die_usage "unknown command: $1"
            ;;
    esac
fi

while (($# > 0)); do
    case "$1" in
        --host)
            require_option_value "$1" "${2:-}"
            REMOTE_HOST="$2"
            shift 2
            ;;
        --remote-address)
            require_option_value "$1" "${2:-}"
            REMOTE_BIND_ADDRESS="$2"
            shift 2
            ;;
        --remote-port)
            require_option_value "$1" "${2:-}"
            REMOTE_PORT="$2"
            shift 2
            ;;
        --local-address)
            require_option_value "$1" "${2:-}"
            LOCAL_ADDRESS="$2"
            shift 2
            ;;
        --local-port)
            require_option_value "$1" "${2:-}"
            LOCAL_PORT="$2"
            shift 2
            ;;
        --runtime-dir)
            require_option_value "$1" "${2:-}"
            RUNTIME_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            die_usage "unknown option: $1"
            ;;
        *)
            die_usage "unexpected argument: $1"
            ;;
    esac
done

validate_port() {
    local label="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[0-9]{1,5}$ ]] || ((10#$value < 1 || 10#$value > 65535)); then
        die_usage "$label must be an integer from 1 to 65535"
    fi
}

validate_text_value() {
    local label="$1"
    local value="$2"
    [[ -n "$value" ]] || die_usage "$label cannot be empty"
    [[ "$value" != *[$'\t\r\n ']* ]] || die_usage "$label cannot contain whitespace"
}

validate_text_value "remote host" "$REMOTE_HOST"
[[ "$REMOTE_HOST" != -* ]] || die_usage "remote host cannot begin with '-'"
validate_text_value "remote address" "$REMOTE_BIND_ADDRESS"
validate_text_value "local address" "$LOCAL_ADDRESS"
validate_port "remote port" "$REMOTE_PORT"
validate_port "local port" "$LOCAL_PORT"

require_command() {
    local command="$1"
    command -v "$command" >/dev/null 2>&1 || {
        printf 'Error: required command not found: %s\n' "$command" >&2
        exit 1
    }
}

require_command ssh
require_command flock

if [[ -z "$RUNTIME_DIR" ]]; then
    RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}/dwg-agent-forward-${UID}"
fi

mkdir -p -- "$RUNTIME_DIR"
chmod 700 -- "$RUNTIME_DIR"

connection_identity="${REMOTE_HOST}|${REMOTE_BIND_ADDRESS}|${REMOTE_PORT}|${LOCAL_ADDRESS}|${LOCAL_PORT}"
if command -v sha256sum >/dev/null 2>&1; then
    connection_id="$(printf '%s' "$connection_identity" | sha256sum)"
    connection_id="${connection_id%% *}"
else
    require_command cksum
    connection_id="$(printf '%s' "$connection_identity" | cksum)"
    connection_id="${connection_id%% *}"
fi
connection_id="${connection_id:0:16}"

CONTROL_SOCKET="${RUNTIME_DIR}/f-${connection_id}.sock"
LOCK_FILE="${RUNTIME_DIR}/f-${connection_id}.lock"

if ((${#CONTROL_SOCKET} > 100)); then
    printf 'Error: control socket path is too long (%d characters): %s\n' \
        "${#CONTROL_SOCKET}" "$CONTROL_SOCKET" >&2
    exit 1
fi

format_endpoint() {
    local address="$1"
    local port="$2"
    if [[ "$address" == *:* && "$address" != \[*\] ]]; then
        printf '[%s]:%s' "$address" "$port"
    else
        printf '%s:%s' "$address" "$port"
    fi
}

REMOTE_ENDPOINT="$(format_endpoint "$REMOTE_BIND_ADDRESS" "$REMOTE_PORT")"
LOCAL_ENDPOINT="$(format_endpoint "$LOCAL_ADDRESS" "$LOCAL_PORT")"

is_running() {
    ssh -S "$CONTROL_SOCKET" -O check "$REMOTE_HOST" >/dev/null 2>&1
}

local_target_is_listening() {
    local row local_endpoint listening_address listening_port

    while IFS= read -r row; do
        read -r _ _ _ local_endpoint _ <<<"$row"
        [[ -n "${local_endpoint:-}" ]] || continue
        listening_port="${local_endpoint##*:}"
        [[ "$listening_port" == "$LOCAL_PORT" ]] || continue
        listening_address="${local_endpoint%:*}"
        listening_address="${listening_address#[}"
        listening_address="${listening_address%]}"

        if [[ "$listening_address" == "$LOCAL_ADDRESS" \
            || "$listening_address" == "0.0.0.0" \
            || "$listening_address" == "*" \
            || "$listening_address" == "::" \
            || "$LOCAL_ADDRESS" == "localhost" && "$listening_address" == "127.0.0.1" ]]; then
            return 0
        fi
    done < <(ss -H -ltn)

    return 1
}

print_active() {
    printf 'Tunnel active: %s %s -> %s\n' "$REMOTE_HOST" "$REMOTE_ENDPOINT" "$LOCAL_ENDPOINT"
    printf 'On %s, access: http://%s\n' "$REMOTE_HOST" "$REMOTE_ENDPOINT"
}

start_tunnel() {
    require_command ss
    if ! local_target_is_listening; then
        printf 'Error: local target %s is not listening. Start the project with: bash scripts/start-all.sh\n' \
            "$LOCAL_ENDPOINT" >&2
        return 1
    fi

    if is_running; then
        printf 'Tunnel already active.\n'
        print_active
        return 0
    fi

    rm -f -- "$CONTROL_SOCKET"
    printf 'Starting SSH remote forward: %s %s -> %s\n' \
        "$REMOTE_HOST" "$REMOTE_ENDPOINT" "$LOCAL_ENDPOINT"
    ssh -M -S "$CONTROL_SOCKET" -fNT \
        -o ControlPersist=yes \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=5 \
        -R "${REMOTE_ENDPOINT}:${LOCAL_ENDPOINT}" \
        "$REMOTE_HOST" || {
            local status=$?
            rm -f -- "$CONTROL_SOCKET"
            return "$status"
        }

    if ! is_running; then
        rm -f -- "$CONTROL_SOCKET"
        printf 'Error: SSH master did not become active after startup.\n' >&2
        return 1
    fi

    print_active
}

stop_tunnel() {
    if ! is_running; then
        rm -f -- "$CONTROL_SOCKET"
        printf 'No tunnel running for %s %s.\n' "$REMOTE_HOST" "$REMOTE_ENDPOINT"
        return 0
    fi

    ssh -S "$CONTROL_SOCKET" -O exit "$REMOTE_HOST" >/dev/null
    rm -f -- "$CONTROL_SOCKET"
    printf 'Tunnel stopped: %s %s.\n' "$REMOTE_HOST" "$REMOTE_ENDPOINT"
}

status_tunnel() {
    if is_running; then
        print_active
        return 0
    fi

    rm -f -- "$CONTROL_SOCKET"
    printf 'Tunnel not running: %s %s.\n' "$REMOTE_HOST" "$REMOTE_ENDPOINT"
    return 3
}

exec {LOCK_FD}>"$LOCK_FILE"
flock -x "$LOCK_FD"

case "$COMMAND" in
    start)
        start_tunnel
        ;;
    stop)
        stop_tunnel
        ;;
    restart)
        stop_tunnel
        start_tunnel
        ;;
    status)
        status_tunnel
        ;;
esac
