#!/usr/bin/env bash
# DWG-Agent — 生产式本地环境诊断（服务、运行版本、近期 HTTP 异常）
set -euo pipefail
source "$(dirname "$0")/lib.sh"

LOG_ONLY=false
SINCE_MINUTES="${DOCTOR_SINCE_MINUTES:-60}"
ACCESS_LOG="${NGINX_ACCESS_LOG:-$PROJECT_ROOT/infra/gateway/nginx/logs/access.log}"

while (($# > 0)); do
    case "$1" in
        --log-only) LOG_ONLY=true; shift ;;
        --since-minutes)
            [ "$#" -ge 2 ] || { err "--since-minutes 缺少数值"; exit 2; }
            SINCE_MINUTES="$2"; shift 2 ;;
        -h|--help)
            echo "用法: bash scripts/doctor.sh [--log-only] [--since-minutes N]"
            exit 0
            ;;
        *) err "未知参数: $1"; exit 2 ;;
    esac
done

[[ "$SINCE_MINUTES" =~ ^[0-9]+$ ]] || { err "时间窗口必须是非负整数"; exit 2; }

overall=0
if ! $LOG_ONLY; then
    step "服务、依赖与运行版本"
    set +e
    bash "$PROJECT_ROOT/scripts/status.sh"
    status_rc=$?
    set -e
    if [ "$status_rc" -ne 0 ]; then
        overall=1
    fi

    step "Win11 访问隧道"
    set +e
    bash "$PROJECT_ROOT/scripts/forward-to-win11.sh" status
    forward_rc=$?
    set -e
    if [ "$forward_rc" -eq 3 ]; then
        warn "Win11 隧道未运行（仅在需要远程访问时处理）"
    elif [ "$forward_rc" -ne 0 ]; then
        warn "Win11 隧道状态未能确认"
        overall=1
    fi
fi

step "近期 HTTP 状态"
if [ ! -f "$ACCESS_LOG" ]; then
    warn "未检查：Nginx access log 不存在: $ACCESS_LOG"
    exit 2
fi

if [ "$SINCE_MINUTES" -eq 0 ]; then
    since_epoch=0
    info "检查全部日志记录（输出仅含归一化路径）"
else
    since_epoch=$(( $(date +%s) - SINCE_MINUTES * 60 ))
    info "检查最近 ${SINCE_MINUTES} 分钟（输出仅含归一化路径）"
fi

PYTHON_BIN="${DOCTOR_PYTHON:-$PROJECT_ROOT/backend/.venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3 || command -v python)"

set +e
"$PYTHON_BIN" - "$ACCESS_LOG" "$since_epoch" <<'PY'
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

log_path = Path(sys.argv[1])
since_epoch = int(sys.argv[2])
pattern = re.compile(
    r'\[(?P<time>[^]]+)\] "(?P<method>[A-Z]+) (?P<target>\S+) HTTP/[^\"]+" '
    r'(?P<status>\d{3}) \S+ .*?\brid=(?P<rid>[^ ]+)'
)
groups: dict[tuple[int, str, str, bool], dict[str, object]] = defaultdict(
    lambda: {"count": 0, "last": "", "rid": "-"}
)

for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
    match = pattern.search(line)
    if not match:
        continue
    try:
        timestamp = datetime.strptime(match["time"], "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        continue
    if since_epoch and timestamp.timestamp() < since_epoch:
        continue
    status = int(match["status"])
    if status < 400:
        continue
    target = match["target"]
    path = target.split("?", 1)[0][:180]
    is_test_probe = "91001%2C91002%2C91003" in target or "91001,91002,91003" in target
    key = (status, match["method"], path, is_test_probe)
    group = groups[key]
    group["count"] = int(group["count"]) + 1
    group["last"] = match["time"]
    group["rid"] = match["rid"][:64]

app_rows = [(key, value) for key, value in groups.items() if key[0] != 499]
disconnect_rows = [(key, value) for key, value in groups.items() if key[0] == 499]

if not app_rows:
    print("  ✓ 未发现应用 4xx/5xx")
else:
    print("  应用响应：")
    for (status, method, path, is_test), value in sorted(app_rows):
        marker = " [固定测试探针]" if is_test else ""
        hint = ""
        if status == 405:
            hint = " [检查路由与运行代码版本]"
        elif status == 409 and path.endswith("/download-zip"):
            hint = " [检查格式完整性/存储一致性]"
        elif status >= 500:
            hint = " [服务端故障]"
        print(
            f"  - HTTP {status} {value['count']}x {method} {path}"
            f" 最近={value['last']} rid={value['rid']}{marker}{hint}"
        )

if not disconnect_rows:
    print("  ✓ 未发现客户端断开 (499)")
else:
    print("  客户端断开 (499)（非应用主动响应）：")
    for (_status, method, path, is_test), value in sorted(disconnect_rows):
        marker = " [固定测试探针]" if is_test else ""
        print(
            f"  - {value['count']}x {method} {path}"
            f" 最近={value['last']} rid={value['rid']}{marker}"
        )

actionable = any(
    not is_test and (status in {405, 409} or status >= 500)
    for status, _method, _path, is_test in groups
)
raise SystemExit(1 if actionable else 0)
PY
log_rc=$?
set -e

if [ "$log_rc" -ne 0 ]; then
    overall=1
fi

if [ "$overall" -eq 0 ]; then
    ok "诊断完成：未发现需立即处理的运行异常"
else
    warn "诊断完成：发现需处理的异常，请按上方端点与 request ID 定位"
fi
exit "$overall"
