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

# 端口占用检查
port_free() { ! ss -tlnp "sport = :$1" 2>/dev/null | grep -q ":$1"; }

# 进程按 PID 文件杀
kill_by_pidfile() {
    local pidfile="$1" label="${2:-process}"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        kill "$(cat "$pidfile")" 2>/dev/null && ok "$label 已停止" || true
        rm -f "$pidfile"
    fi
}

# 等待端口就绪
wait_port() {
    local host="$1" port="$2" timeout="${3:-30}" label="${4:-$host:$port}"
    local waited=0
    while [ $waited -lt "$timeout" ]; do
        if ss -tlnp "sport = :$port" 2>/dev/null | grep -q ":$port"; then
            ok "$label 就绪 (${waited}s)"
            return 0
        fi
        sleep 1; waited=$((waited + 1))
    done
    err "$label 超时未就绪"
    return 1
}

# 确保 systemd 服务运行（兼容多种命名: redis/valkey, mysql/mariadb）
ensure_service() {
    local port="$1"; shift
    local names=("$@")
    if ! port_free "$port"; then
        return 0  # already running
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
    err "无法启动 ${names[0]}，请手动启动"; return 1
}
