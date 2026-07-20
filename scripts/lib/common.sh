#!/usr/bin/env bash
# Shared paths, output, environment and host-service primitives.

if [ "${DWG_COMMON_LIB_LOADED:-0}" = "1" ]; then
    return 0
fi
DWG_COMMON_LIB_LOADED=1

SCRIPT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_LIB_DIR/../.." && pwd)"
LOCAL_BACKEND_HOST="${LOCAL_BACKEND_HOST:-127.0.0.1}"
LOCAL_BACKEND_PORT="${LOCAL_BACKEND_PORT:-8010}"
export PROJECT_ROOT LOCAL_BACKEND_HOST LOCAL_BACKEND_PORT

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[34m'; DIM='\033[2m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "${BLUE}▶${NC} $1"; }
step() { echo -e "\n${BLUE}── $1 ──${NC}"; }

port_free() { ! ss -tlnp "sport = :$1" 2>/dev/null | grep -q ":$1"; }

process_exists() {
    local pid="$1"
    [[ "$pid" =~ ^[0-9]+$ ]] && [ -d "/proc/${pid}" ]
}

# Ensure sudo is usable without hanging unattended commands on a password prompt.
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

# Read one value from an .env-style file without evaluating shell input.
env_value() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 0
    awk -v key="$key" '
        BEGIN { FS="=" }
        $1 == key { sub(/^[^=]*=/, ""); print; exit }
    ' "$file"
}

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

print_admin_credentials() {
    local user
    user="$(env_value "$PROJECT_ROOT/.env" SUPER_ADMIN_USERNAME)"
    echo -e "  管理员账号: ${YELLOW}${user:-未配置}${NC}"
    echo -e "  ${DIM}管理员密码不会在终端显示；SUPER_ADMIN_PASSWORD 仅在首次 db init 时写入数据库，之后改 .env 不影响已建账号。${NC}"
}

check_port() {
    local port="$1" label="$2"
    if port_free "$port"; then
        warn "$label — 未运行 (:${port})"
        return 1
    fi
    ok "$label — :$port"
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
