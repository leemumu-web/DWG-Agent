#!/usr/bin/env bash
# DWG-Agent Platform — 基础设施验证脚本
# 测试: Nginx 配置 / Docker Compose / Dockerfile / MySQL / 环境模板
# 规范参考: docs/architecture/platform-specification.md §2.1 §3 §17.4 §17.5
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PASS=0
FAIL=0
TOTAL=0

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }
dim()   { printf '\033[2m%s\033[0m\n' "$1"; }

pass() { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); green "  ✓ $1"; }
fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); red   "  ✗ $1 — $2"; }

assert_file()  { [ -f "$1" ] && pass "$2" || fail "$2" "missing: $1"; }
assert_grep()  { grep -q "$2" "$1" 2>/dev/null && pass "$3" || fail "$3" "missing: $2"; }
assert_grep_v() { ! grep -q "$2" "$1" 2>/dev/null && pass "$3" || fail "$3" "should not contain: $2"; }
assert_eq()    { [ "$1" = "$2" ] && pass "$3" || fail "$3" "expected '$2', got '$1'"; }
env_keys()     { awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/ {print $1}' "$1" | sort; }
assert_env_keys_match() {
    local reference="$1"
    local candidate="$2"
    local label="$3"
    local missing extra

    missing=$(comm -23 <(env_keys "$reference") <(env_keys "$candidate") | paste -sd ',' -)
    extra=$(comm -13 <(env_keys "$reference") <(env_keys "$candidate") | paste -sd ',' -)

    if [ -z "$missing" ] && [ -z "$extra" ]; then
        pass "$label"
    else
        fail "$label" "missing=[${missing:-none}], extra=[${extra:-none}]"
    fi
}
assert_env_keys_compatible() {
    local reference="$1"
    local candidate="$2"
    local label="$3"
    local missing extra invalid_extra

    missing=$(comm -23 <(env_keys "$reference") <(env_keys "$candidate") | paste -sd ',' -)
    extra=$(comm -13 <(env_keys "$reference") <(env_keys "$candidate") | paste -sd ',' -)
    invalid_extra=$(printf '%s\n' "$extra" | tr ',' '\n' | grep -vxE 'DATABASE_URL|DWG_AGENT_IMAGE|DWG_AGENT_FRONTEND_IMAGE|NODE_IMAGE|HTTP_PORT|^$' | paste -sd ',' - || true)

    if [ -z "$missing" ] && [ -z "$invalid_extra" ]; then
        pass "$label"
    else
        fail "$label" "missing=[${missing:-none}], unsupported_extra=[${invalid_extra:-none}]"
    fi
}

echo "═══════════════════════════════════════════════════════════════"
echo "  DWG-Agent 基础设施验证"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════"

# ── Section 1: Nginx 配置 ──────────────────────────────────────
echo ""
echo "── 1. Nginx 配置 ──"

NGINX_DOCKER="infra/nginx/nginx.conf"
NGINX_LOCAL="infra/nginx/nginx.local.conf"

# 1.1 文件存在
assert_file "$NGINX_DOCKER"  "nginx.conf (Docker) 存在"
assert_file "$NGINX_LOCAL"   "nginx.local.conf (本地) 存在"

# 1.2 语法检查
echo ""
dim "  nginx 语法检查..."
NGINX_TEST_CMD=(nginx -t -c "$(pwd)/$NGINX_LOCAL")
if sudo -n true 2>/dev/null; then
    NGINX_TEST_CMD=(sudo nginx -t -c "$(pwd)/$NGINX_LOCAL")
fi
OUT=$("${NGINX_TEST_CMD[@]}" 2>&1) || true
if echo "$OUT" | grep -q 'syntax is ok'; then
    pass "nginx.local.conf 语法通过"
else
    fail "nginx.local.conf 语法" "$(echo "$OUT" | tail -1)"
fi

# 1.3 Docker nginx.conf: 自包含（无 include conf.d 或 snippets）
assert_grep_v "$NGINX_DOCKER" 'include.*conf\.d'   "nginx.conf 不包含 conf.d include"
assert_grep_v "$NGINX_DOCKER" 'include.*snippets'  "nginx.conf 不包含 snippets include"

# 1.4 关键指令 — upstream
assert_grep "$NGINX_DOCKER" 'upstream backend'         "nginx.conf: upstream backend"
assert_grep "$NGINX_DOCKER" 'server backend-api:8010'  "nginx.conf: upstream → backend-api:8010"
assert_grep "$NGINX_DOCKER" 'keepalive 32'             "nginx.conf: upstream keepalive"

# 1.5 关键指令 — 限流
assert_grep "$NGINX_DOCKER" 'limit_req_zone.*zone=login.*rate=2r/s'   "nginx.conf: 登录限流 2r/s"
assert_grep "$NGINX_DOCKER" 'limit_req_zone.*zone=api.*rate=100r/s'   "nginx.conf: API 限流 100r/s"
assert_grep "$NGINX_DOCKER" 'limit_req_status 429'                    "nginx.conf: 限流返回 429"

# 1.6 关键指令 — 反向代理
assert_grep "$NGINX_DOCKER" 'proxy_pass http://backend'  "nginx.conf: proxy_pass backend"
assert_grep "$NGINX_DOCKER" 'proxy_buffering off'        "nginx.conf: SSE buffering off"
assert_grep "$NGINX_DOCKER" 'proxy_set_header X-Request-ID'  "nginx.conf: X-Request-ID 透传"
assert_grep "$NGINX_DOCKER" 'proxy_read_timeout 120s'    "nginx.conf: timeout 120s"

# 1.7 关键指令 — SPA
assert_grep "$NGINX_DOCKER" 'try_files.*\$uri.*/index\.html'  "nginx.conf: SPA fallback"
assert_grep "$NGINX_DOCKER" 'root /usr/share/nginx/html'       "nginx.conf: SPA root"

# 1.8 关键指令 — 安全
assert_grep "$NGINX_DOCKER" 'X-Frame-Options'            "nginx.conf: X-Frame-Options"
assert_grep "$NGINX_DOCKER" 'X-Content-Type-Options'     "nginx.conf: nosniff"
assert_grep "$NGINX_DOCKER" 'Referrer-Policy'             "nginx.conf: Referrer-Policy"

# 1.9 关键指令 — 上传限制
assert_grep "$NGINX_DOCKER" 'client_max_body_size 512m'  "nginx.conf: 上传 512MB"

# 1.10 关键指令 — server_name
assert_grep "$NGINX_DOCKER"   'dwg-agent\.company\.local'   "nginx.conf: server_name 规范 §2.1"
assert_grep "$NGINX_LOCAL"    'dwg-agent\.company\.local'   "nginx.local.conf: server_name 规范 §2.1"

# 1.11 日志格式
assert_grep "$NGINX_DOCKER" 'log_format extended'        "nginx.conf: extended 日志格式"
assert_grep "$NGINX_DOCKER" 'rid=\$request_id'           "nginx.conf: request_id 日志字段"

# 1.12 健康检查
assert_grep "$NGINX_DOCKER" 'location /health'           "nginx.conf: /health location"
assert_grep "$NGINX_DOCKER" 'access_log off'             "nginx.conf: health access_log off"

# 1.13 本地配置: 8080 端口
assert_grep "$NGINX_LOCAL" 'listen 8080'                 "nginx.local.conf: 监听 8080"
assert_grep "$NGINX_LOCAL" 'server 127.0.0.1:8010'       "nginx.local.conf: upstream 127.0.0.1:8010"

# ── Section 2: Docker Compose ──────────────────────────────────
echo ""
echo "── 2. Docker Compose ──"
COMPOSE_FILE="compose.yaml"

assert_file "$COMPOSE_FILE" "compose.yaml 存在"

# Parse YAML with Python for precise checks
COMPOSE_CHECKS=$(python3 << 'PYEOF'
import yaml

with open("compose.yaml") as f:
    data = yaml.safe_load(f)

svcs = data.get("services", {})
errors = []
workers = {
    "worker-agent": "agent",
    "worker-dxf": "dxf",
    "worker-dxf2dwg": "dxf2dwg",
    "worker-dxf2excel": "dxf2excel",
    "worker-excel-final": "excel_final",
    "worker-report": "report",
}
expected = {"nginx", "backend-api", "mysql", "minio", *workers}

if set(svcs) != expected:
    errors.append(f"服务集合不匹配: expected={sorted(expected)}, actual={sorted(svcs)}")

def healthcheck_cmd(service):
    return " ".join(service.get("healthcheck", {}).get("test", []))

def require_blank(service, keys, label):
    env = service.get("environment", {})
    for key in keys:
        if env.get(key) != "":
            errors.append(f"{label} 应清空敏感环境变量: {key}")

nginx = svcs.get("nginx", {})
if nginx.get("build", {}).get("dockerfile") != "frontend/Dockerfile":
    errors.append("nginx 应由 frontend/Dockerfile 构建 SPA 镜像")
if set(nginx.get("depends_on", {})) != {"backend-api"}:
    errors.append("nginx 应等待 backend-api ready")

for name in expected - {"nginx"}:
    if svcs.get(name, {}).get("env_file") != [".env.docker"]:
        errors.append(f"{name} 应使用 env_file: .env.docker")

backend = svcs.get("backend-api", {})
backend_hc = healthcheck_cmd(backend)
if "/health/ready" not in backend_hc:
    errors.append("backend-api healthcheck 必须验证 MySQL readiness")
if set(backend.get("depends_on", {})) != {"mysql", "minio"}:
    errors.append("backend-api depends_on 应为 mysql + minio")

cad_worker_script = open("scripts/run-cad-worker.sh").read()
for name, queue in workers.items():
    worker = svcs.get(name, {})
    command_value = worker.get("command", "")
    command = str(command_value)
    is_cad_worker = name in {"worker-dxf", "worker-dxf2dwg"}
    if is_cad_worker:
        if not isinstance(command_value, list) or command_value[:2] != ["/app/scripts/run-cad-worker.sh", queue]:
            errors.append(f"{name} 包装脚本或队列名错误")
        if '-A app.workers.celery_app:celery_app worker' not in cad_worker_script:
            errors.append(f"{name} Celery app 路径错误")
        if '-Q "$queue"' not in cad_worker_script:
            errors.append(f"{name} 包装脚本未传递队列")
    else:
        if f"-Q {queue}" not in command:
            errors.append(f"{name} 队列名错误")
        if "app.workers.celery_app:celery_app" not in command:
            errors.append(f"{name} Celery app 路径错误")
    if "uv run celery" in command:
        errors.append(f"{name} 不应依赖 runtime 中的 uv")
    worker_hc = healthcheck_cmd(worker)
    process_probe = (f"/tmp/dwg-celery-{queue}.pid" in worker_hc and "kill -0" in worker_hc) if is_cad_worker else "/proc/1/cmdline" in worker_hc
    if not process_probe or "/tmp/dwg-celery-ready" not in worker_hc or "inspect" in worker_hc:
        errors.append(f"{name} 应使用 ready marker + 进程健康检查，不能使用 Celery remote control")
    if "backend-api" not in worker.get("depends_on", {}):
        errors.append(f"{name} 必须等待迁移完成后的 backend-api")
    require_blank(worker, {"MYSQL_ROOT_PASSWORD", "MINIO_ROOT_PASSWORD"}, name)

if "profiles" in svcs.get("worker-report", {}):
    errors.append("worker-report 应默认启动")
for name in workers.keys() - {"worker-report"}:
    if "workers" not in svcs.get(name, {}).get("profiles", []):
        errors.append(f"{name} 缺少 workers profile")

mysql = svcs.get("mysql", {})
if "mysql/community-server:8.4" not in mysql.get("image", ""):
    errors.append("mysql 镜像不匹配")
mysql_hc = healthcheck_cmd(mysql)
if "$${MYSQL_ROOT_PASSWORD}" not in mysql_hc:
    errors.append("mysql healthcheck 必须在容器内读取 MYSQL_ROOT_PASSWORD")

minio = svcs.get("minio", {})
if "quay.io/minio/minio@sha256:" not in minio.get("image", ""):
    errors.append("minio 默认镜像必须使用 Quay digest")
if "console-address" not in str(minio.get("command", "")):
    errors.append("minio 缺少 console-address")

volumes = set(data.get("volumes", {}))
if volumes != {"app_var", "mysql_data", "minio_data"}:
    errors.append(f"持久卷应为 app_var + mysql_data + minio_data，实际: {sorted(volumes)}")

networks = data.get("networks", {})
if set(networks) != {"public", "internal"}:
    errors.append("网络集合不匹配")
if networks.get("internal", {}).get("internal") is not True:
    errors.append("internal network 未设置 internal: true")

compose_text = open("compose.yaml").read().lower()
for removed in ("redis", "6379", "flower", "inspect ping"):
    if removed in compose_text:
        errors.append(f"compose.yaml 仍包含已移除能力: {removed}")

if errors:
    print("ERRORS:" + "\nERRORS:".join(errors))
else:
    print("ALL_CHECKS_PASSED")
PYEOF
)

if echo "$COMPOSE_CHECKS" | grep -q 'ALL_CHECKS_PASSED'; then
    pass "compose.yaml 全部结构检查通过 (11 services, MySQL-backed runtime)"
else
    while IFS= read -r line; do
        [ -n "$line" ] && fail "compose.yaml" "$line"
    done <<< "$(echo "$COMPOSE_CHECKS" | grep 'ERRORS:')"
fi

# ── Section 3: Dockerfile ──────────────────────────────────────
echo ""
echo "── 3. Dockerfile ──"
DOCKERFILE="backend/Dockerfile"

assert_file "$DOCKERFILE" "Dockerfile 存在"

# 3.1 基础镜像 (multi-stage: builder + runtime)
assert_grep "$DOCKERFILE" 'FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder'  "Dockerfile: FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder"
assert_grep "$DOCKERFILE" 'FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime'  "Dockerfile: FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime"

# 3.2 环境变量
for var in PYTHONDONTWRITEBYTECODE PYTHONUNBUFFERED UV_PROJECT_ENVIRONMENT; do
    assert_grep "$DOCKERFILE" "$var"  "Dockerfile: ENV $var"
done

# 3.3 系统依赖 (runtime: curl+ca-certificates; builder: ca-certificates only)
assert_grep "$DOCKERFILE" 'curl'               "Dockerfile: curl (健康检查)"

# 3.4 uv + 依赖
assert_grep "$DOCKERFILE" 'ghcr.io/astral-sh/uv'   "Dockerfile: uv 安装"
assert_grep "$DOCKERFILE" 'uv sync --frozen --no-dev'  "Dockerfile: uv sync"

# 3.5 应用代码 (build context = 仓库根, 故源路径带 backend/ 前缀)
assert_grep "$DOCKERFILE" 'COPY backend/app ./app'      "Dockerfile: COPY app"
assert_grep "$DOCKERFILE" 'COPY backend/alembic.ini'    "Dockerfile: COPY alembic.ini"
assert_grep "$DOCKERFILE" 'COPY backend/migrations'     "Dockerfile: COPY migrations"

# 3.5.1 editable path 依赖：Stages/ 必须进 builder，否则 uv sync --frozen 失败
assert_grep "$DOCKERFILE" 'COPY Stages/dwg2dxf'         "Dockerfile: COPY Stages/dwg2dxf (editable path 依赖)"
assert_grep "$DOCKERFILE" 'COPY Stages/dxf2dwg'         "Dockerfile: COPY Stages/dxf2dwg (editable path 依赖)"
assert_grep "$DOCKERFILE" 'COPY Stages/dxf2excel'       "Dockerfile: COPY Stages/dxf2excel (editable path 依赖)"
assert_grep "$DOCKERFILE" 'COPY Stages/excel_final'     "Dockerfile: COPY Stages/excel_final (editable path 依赖)"
# 3.5.2 ODA 运行时 + init_db 种子（首次启动可用 admin 登录）
assert_grep "$DOCKERFILE" 'tools/oda'                   "Dockerfile: COPY ODA 二进制"
assert_grep "$DOCKERFILE" 'app.db.init_db'              "Dockerfile: CMD 含 init_db 种子"

# 3.5.3 根 .dockerignore 存在（context=根后排除 Stages/.venv 等膨胀源）
assert_file ".dockerignore" ".dockerignore 存在 (context=仓库根)"

# 3.6 EXPOSE + CMD
assert_grep "$DOCKERFILE" 'EXPOSE 8010'             "Dockerfile: EXPOSE 8010"
assert_grep "$DOCKERFILE" 'gunicorn'                "Dockerfile: CMD gunicorn"
assert_grep "$DOCKERFILE" 'UvicornWorker'           "Dockerfile: UvicornWorker"

# 3.7 生产要求 (§17.5 第4条: 非 root)
assert_grep "$DOCKERFILE" 'USER appuser'   "Dockerfile: 非 root 用户"
assert_grep "$DOCKERFILE" 'HEALTHCHECK'    "Dockerfile: HEALTHCHECK"
assert_grep "$DOCKERFILE" 'STOPSIGNAL'     "Dockerfile: STOPSIGNAL"

# ── Section 4: MySQL 集成 ──────────────────────────────────────
echo ""
echo "── 4. MySQL 集成 ──"

# MariaDB uses unix socket auth for root — use sudo mariadb
MYSQL_AVAILABLE=false
if command -v mariadb &>/dev/null && sudo mariadb -e "SELECT 1" &>/dev/null 2>&1; then
    MYSQL_AVAILABLE=true
    MYSQL_CMD="sudo mariadb"
fi

if $MYSQL_AVAILABLE; then
    # 4.1 数据库存在
    if $MYSQL_CMD -e "USE dwg_agent; SELECT 1" &>/dev/null 2>&1; then
        pass "MySQL: dwg_agent 数据库可访问"
    else
        fail "MySQL" "dwg_agent 数据库不可访问"
    fi

    # 4.2 表存在 (模型使用 sys_ 前缀 + 实际表名)
    TABLES=$($MYSQL_CMD -N -e "SHOW TABLES FROM dwg_agent" 2>/dev/null)
    # 核心用户/权限表 (sys_ 前缀)
    for t in sys_users sys_roles sys_permissions sys_role_permissions sys_user_roles; do
        if echo "$TABLES" | grep -q "^${t}$"; then
            pass "MySQL: 表 $t 存在"
        else
            fail "MySQL" "表 $t 缺失"
        fi
    done
    # 业务表
    for t in projects project_members files drawings drawing_versions jobs job_steps; do
        if echo "$TABLES" | grep -q "^${t}$"; then
            pass "MySQL: 表 $t 存在"
        else
            fail "MySQL" "表 $t 缺失"
        fi
    done
    # 结果/审计/Agent 表
    for t in analysis_results review_records audit_logs agent_runs agent_run_steps agent_memory token_blacklist; do
        if echo "$TABLES" | grep -q "^${t}$"; then
            pass "MySQL: 表 $t 存在"
        else
            fail "MySQL" "表 $t 缺失"
        fi
    done

    TOTAL_TABLES=$(echo "$TABLES" | wc -l)
    if [ "$TOTAL_TABLES" -ge 24 ]; then
        pass "MySQL: 共 $TOTAL_TABLES 张表 (≥24，含任务队列/结果表)"
    else
        fail "MySQL" "表总数不足: $TOTAL_TABLES (期望 ≥24)"
    fi

    # 4.2b TimestampMixin schema drift
    MISSING_TS_COLUMNS=()
    for t in project_members drawing_versions review_records agent_run_steps; do
        for c in created_at updated_at; do
            EXISTS=$($MYSQL_CMD -N -e "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='dwg_agent' AND TABLE_NAME='$t' AND COLUMN_NAME='$c'" 2>/dev/null || echo 0)
            if [ "${EXISTS:-0}" -lt 1 ]; then
                MISSING_TS_COLUMNS+=("$t.$c")
            fi
        done
    done
    if [ "${#MISSING_TS_COLUMNS[@]}" -eq 0 ]; then
        pass "MySQL: TimestampMixin 时间列已同步"
    else
        fail "MySQL" "TimestampMixin 时间列缺失: ${MISSING_TS_COLUMNS[*]}"
    fi

    for tc in jobs.progress_data sys_users.password_changed_at; do
        t="${tc%%.*}"
        c="${tc##*.}"
        EXISTS=$($MYSQL_CMD -N -e "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='dwg_agent' AND TABLE_NAME='$t' AND COLUMN_NAME='$c'" 2>/dev/null || echo 0)
        if [ "${EXISTS:-0}" -ge 1 ]; then
            pass "MySQL: 列 $tc 存在"
        else
            fail "MySQL" "列 $tc 缺失"
        fi
    done

    # 4.3 角色种子
    ROLE_COUNT=$($MYSQL_CMD -N -e "SELECT COUNT(*) FROM dwg_agent.sys_roles" 2>/dev/null)
    if [ "${ROLE_COUNT:-0}" -ge 7 ]; then
        pass "MySQL: 7+ 角色已种子 ($ROLE_COUNT)"
    else
        fail "MySQL" "角色数不足: $ROLE_COUNT (期望 ≥7)"
    fi

    # 4.4 admin 用户
    ADMIN_EXISTS=$($MYSQL_CMD -N -e "SELECT COUNT(*) FROM dwg_agent.sys_users WHERE username='admin'" 2>/dev/null)
    if [ "${ADMIN_EXISTS:-0}" -ge 1 ]; then
        pass "MySQL: super_admin 用户已创建"
    else
        fail "MySQL" "admin 用户不存在"
    fi

    # 4.5 dwg_user 权限
    GRANTS=$($MYSQL_CMD -N -e "SHOW GRANTS FOR 'dwg_user'@'127.0.0.1'" 2>/dev/null || \
             $MYSQL_CMD -N -e "SHOW GRANTS FOR 'dwg_user'@'localhost'" 2>/dev/null || echo "")
    if echo "$GRANTS" | grep -q 'dwg_agent'; then
        pass "MySQL: dwg_user 有 dwg_agent 权限"
    else
        fail "MySQL" "dwg_user 权限检查失败"
    fi

    # 4.6 应用 DATABASE_URL 凭据可实际登录
    if [ -f .env ]; then
        mapfile -t APP_DB < <(python - <<'PY'
from pathlib import Path
from urllib.parse import unquote, urlsplit

values = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")

database_url = values.get("DATABASE_URL", "")
if database_url:
    url = urlsplit(database_url)
    parts = (
        url.scheme,
        url.hostname or "",
        url.port or 3306,
        unquote(url.username or ""),
        unquote(url.password or ""),
        url.path.lstrip("/"),
    )
else:
    parts = (
        "mysql+pymysql",
        values.get("MYSQL_HOST", ""),
        values.get("MYSQL_PORT", "3306"),
        values.get("MYSQL_USER", ""),
        values.get("MYSQL_PASSWORD", ""),
        values.get("MYSQL_DATABASE", ""),
    )
print(*parts, sep="\n")
PY
)
        APP_DB_SCHEME="${APP_DB[0]:-}"
        APP_DB_HOST="${APP_DB[1]:-}"
        APP_DB_PORT="${APP_DB[2]:-3306}"
        APP_DB_USER="${APP_DB[3]:-}"
        APP_DB_PASSWORD="${APP_DB[4]:-}"
        APP_DB_NAME="${APP_DB[5]:-}"
        if [[ "$APP_DB_SCHEME" == mysql* ]]; then
            if MYSQL_PWD="$APP_DB_PASSWORD" mariadb -h "$APP_DB_HOST" -P "$APP_DB_PORT" -u "$APP_DB_USER" "$APP_DB_NAME" -e "SELECT 1" &>/dev/null; then
                pass "MySQL: .env 有效应用凭据可登录"
            else
                fail "MySQL" ".env 有效应用凭据无法登录"
            fi
        else
            fail "MySQL" ".env 有效数据库配置不是 MySQL"
        fi
    else
        fail "MySQL" ".env 不存在，无法验证应用数据库凭据"
    fi
else
    dim "  MySQL 未运行或不可达 — 跳过集成测试"
    dim "  启动: sudo systemctl start mariadb"
fi

# ── Section 5: 文件完整性 ──────────────────────────────────────
echo ""
echo "── 5. 文件完整性 ──"

REQUIRED_FILES=(
    "compose.yaml"
    "backend/Dockerfile"
    "infra/nginx/nginx.conf"
    "infra/nginx/nginx.local.conf"
    "infra/nginx/.gitignore"
    "infra/nginx/ssl/.gitkeep"
    "infra/mysql/init.sql"
    "scripts/db.sh"
    "scripts/start-all.sh"
    "scripts/start-dev.sh"
    "scripts/status.sh"
    "scripts/stop-all.sh"
    "infra/README.md"
    "infra/nginx/README.md"
    ".env.example"
    ".env.docker.example"
    "docs/guides/deployment.md"
)

for f in "${REQUIRED_FILES[@]}"; do
    assert_file "$f" "$f"
done

# 环境模板一致性：只比较键名，不输出或读取敏感值到日志。
assert_env_keys_match ".env.example" ".env.docker.example" ".env.example 与 .env.docker.example 键名一致"
if [ -f ".env" ]; then
    assert_env_keys_compatible ".env.example" ".env" ".env 包含全部模板键且仅使用合法可选覆盖"
else
    dim "  .env 不存在 — 跳过本机真实 env 键名检查"
fi
if [ -f ".env.docker" ]; then
    assert_env_keys_compatible ".env.docker.example" ".env.docker" ".env.docker 包含全部模板键且仅使用合法可选覆盖"
else
    dim "  .env.docker 不存在 — 跳过 Docker 真实 env 键名检查"
fi

assert_grep ".gitignore" '^\.env$' ".env 已加入 .gitignore"
assert_grep ".gitignore" '^\.env\.docker$' ".env.docker 已加入 .gitignore"
assert_grep_v ".env.docker.example" '\${' ".env.docker.example 不含 Compose 二次插值"

# ── Section 6: 死代码检查 ──────────────────────────────────────
echo ""
echo "── 6. 死代码检查 ──"

# conf.d/ and snippets/ should NOT exist (removed as dead code)
for d in "infra/nginx/conf.d" "infra/nginx/snippets"; do
    if [ ! -d "$d" ]; then
        pass "已清理: $d/ 不存在"
    else
        fail "死代码" "$d/ 目录仍存在，应删除"
    fi
done

# Docker nginx.conf 不应 include 已删除的文件
assert_grep_v "$NGINX_DOCKER" 'include.*conf\.d\|include.*snippets' \
    "nginx.conf 无死 include (conf.d/snippets)"

# ── Summary ────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
printf "  通过: %s / %s" "$PASS" "$TOTAL"
if [ "$FAIL" -gt 0 ]; then
    printf "  \033[31m失败: %s\033[0m" "$FAIL"
fi
echo ""
echo "═══════════════════════════════════════════════════════════════"

exit $FAIL
