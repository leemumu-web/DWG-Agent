#!/usr/bin/env bash
# DWG-Agent Platform — 基础设施验证脚本
# 测试: Nginx 配置 / Docker Compose / Dockerfile / MySQL / 环境模板
# 规范参考: DWG-Agent企业平台技术规范.md §2.1 §3 §17.4 §17.5
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
if sudo nginx -t -c "$(pwd)/$NGINX_LOCAL" 2>&1 | grep -q 'successful'; then
    pass "nginx.local.conf 语法通过"
else
    # Retry with stderr capture
    OUT=$(sudo nginx -t -c "$(pwd)/$NGINX_LOCAL" 2>&1) || true
    if echo "$OUT" | grep -q 'syntax is ok'; then
        pass "nginx.local.conf 语法通过"
    else
        fail "nginx.local.conf 语法" "$(echo "$OUT" | tail -1)"
    fi
fi

# 1.3 Docker nginx.conf: 自包含（无 include conf.d 或 snippets）
assert_grep_v "$NGINX_DOCKER" 'include.*conf\.d'   "nginx.conf 不包含 conf.d include"
assert_grep_v "$NGINX_DOCKER" 'include.*snippets'  "nginx.conf 不包含 snippets include"

# 1.4 关键指令 — upstream
assert_grep "$NGINX_DOCKER" 'upstream backend'         "nginx.conf: upstream backend"
assert_grep "$NGINX_DOCKER" 'server backend-api:8000'  "nginx.conf: upstream → backend-api:8000"
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
assert_grep "$NGINX_LOCAL" 'server 127.0.0.1:8000'       "nginx.local.conf: upstream 127.0.0.1:8000"

# ── Section 2: Docker Compose ──────────────────────────────────
echo ""
echo "── 2. Docker Compose ──"
COMPOSE_FILE="compose.yaml"

assert_file "$COMPOSE_FILE" "compose.yaml 存在"

# Parse YAML with Python for precise checks
COMPOSE_CHECKS=$(python3 << 'PYEOF'
import yaml, sys, json

with open("compose.yaml") as f:
    data = yaml.safe_load(f)

svcs = data.get("services", {})
errors = []
APP_SECRET_KEYS = {
    "JWT_SECRET_KEY",
    "SUPER_ADMIN_PASSWORD",
    "DATABASE_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
}

def require_blank(service, keys, label):
    env = service.get("environment", {})
    for key in keys:
        if env.get(key) != "":
            errors.append(f"{label} 应清空敏感环境变量: {key}")

def require_absent(service, keys, label):
    env = service.get("environment", {})
    for key in keys:
        if key in env:
            errors.append(f"{label} 不应覆盖自身必需环境变量: {key}")

def healthcheck_cmd(service):
    return " ".join(service.get("healthcheck", {}).get("test", []))

# 2.1 服务数量 (spec §17.4: 9 services)
names = list(svcs.keys())
expected = {"nginx","backend-api","worker-agent","worker-dxf","worker-report","mysql","redis","minio","flower"}
extra = set(names) - expected
missing = expected - set(names)
if extra:
    errors.append(f"多余服务: {extra}")
if missing:
    errors.append(f"缺失服务: {missing}")

# 2.2 nginx
nginx = svcs.get("nginx", {})
if nginx.get("image") != "ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine":
    errors.append(f"nginx image: {nginx.get('image')}")
nginx_vols = [v.split(":")[0] for v in nginx.get("volumes", [])]
if "./frontend/dist" not in nginx_vols:
    errors.append("nginx 缺少 frontend/dist 挂载")
if "./infra/nginx/nginx.conf" not in nginx_vols:
    errors.append("nginx 缺少 nginx.conf 挂载")
if len(nginx_vols) != 2:
    errors.append(f"nginx 卷数量应为 2, 实际 {len(nginx_vols)}: {nginx_vols}")
nginx_ports = nginx.get("ports", [])
if "80:8080" not in str(nginx_ports):
    errors.append("nginx 缺少 80:8080")
if "443:8443" not in str(nginx_ports):
    errors.append("nginx 缺少 443:8443")
nginx_deps = list(nginx.get("depends_on", {}).keys())
if "backend-api" not in nginx_deps:
    errors.append(f"nginx depends_on: {nginx_deps}")

# 2.3 所有后端/中间件服务使用 Docker 专用 env_file
for svc_name in [
    "backend-api",
    "worker-agent",
    "worker-dxf",
    "worker-report",
    "mysql",
    "redis",
    "minio",
    "flower",
]:
    if svcs.get(svc_name, {}).get("env_file") != [".env.docker"]:
        errors.append(f"{svc_name} 应使用 env_file: .env.docker")

for svc_name in ["backend-api", "worker-agent", "worker-dxf", "worker-report", "flower"]:
    require_blank(svcs.get(svc_name, {}), {"MYSQL_ROOT_PASSWORD", "MINIO_ROOT_PASSWORD"}, svc_name)

# 2.4 backend-api (CMD in Dockerfile, not overridden in compose — cleaner)
backend = svcs.get("backend-api", {})
# Verify depends_on is complete
backend_deps = list(backend.get("depends_on", {}).keys())
# backend-api deps checked above, now check healthcheck
hc = backend.get("healthcheck", {})
if hc:
    if "curl" not in str(hc.get("test", "")):
        errors.append("backend-api healthcheck 异常")
else:
    # Dockerfile has HEALTHCHECK, compose may defer to it
    pass

# 2.5 worker-agent
wa = svcs.get("worker-agent", {})
wa_cmd = wa.get("command", "")
if "-Q agent" not in str(wa_cmd):
    errors.append("worker-agent 队列名错误")
wa_hc = healthcheck_cmd(wa)
if "/app/.venv/bin/celery" not in wa_hc or "inspect ping" not in wa_hc or "agent@$$HOSTNAME" not in wa_hc:
    errors.append("worker-agent healthcheck 应使用 celery inspect ping")

# 2.6 worker-dxf
wd = svcs.get("worker-dxf", {})
wd_cmd = wd.get("command", "")
if "-Q dxf" not in str(wd_cmd):
    errors.append("worker-dxf 队列名错误")
wd_hc = healthcheck_cmd(wd)
if "/app/.venv/bin/celery" not in wd_hc or "inspect ping" not in wd_hc or "dxf@$$HOSTNAME" not in wd_hc:
    errors.append("worker-dxf healthcheck 应使用 celery inspect ping")

# 2.7 worker-report: Stage 1 Celery fake task worker, default profile
wr = svcs.get("worker-report", {})
wr_cmd = wr.get("command", "")
if "-Q report" not in str(wr_cmd):
    errors.append("worker-report 队列名错误")
if "app.workers.celery_app:celery_app" not in str(wr_cmd):
    errors.append("worker-report Celery app 路径错误")
if "uv run celery" in str(wr_cmd):
    errors.append("worker-report 不应依赖 runtime 镜像中的 uv")
wr_deps = set(wr.get("depends_on", {}).keys())
for dep in {"redis", "mysql", "minio"}:
    if dep not in wr_deps:
        errors.append(f"worker-report 缺少 depends_on: {dep}")
if "profiles" in wr:
    errors.append("worker-report 应默认启动，不应设置 profiles")
wr_hc = healthcheck_cmd(wr)
if "/app/.venv/bin/celery" not in wr_hc or "inspect ping" not in wr_hc or "report@$$HOSTNAME" not in wr_hc:
    errors.append("worker-report healthcheck 应使用 celery inspect ping")

# 2.8 mysql
mysql = svcs.get("mysql", {})
if mysql.get("image") != "container-registry.oracle.com/mysql/community-server:8.4":
    errors.append(f"mysql image: {mysql.get('image')}")
require_blank(
    mysql,
    APP_SECRET_KEYS | {"REDIS_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"},
    "mysql",
)
require_absent(mysql, {"MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"}, "mysql")
mysql_hc = " ".join(mysql.get("healthcheck", {}).get("test", []))
if "$${MYSQL_ROOT_PASSWORD}" not in mysql_hc:
    errors.append("mysql healthcheck 应在容器内读取 MYSQL_ROOT_PASSWORD")
if "${MYSQL_ROOT_PASSWORD:-" in mysql_hc:
    errors.append("mysql healthcheck 不应带根 .env/shell fallback")

# 2.9 redis (uses redis.conf file + env_file password)
redis = svcs.get("redis", {})
if redis.get("image") != "ghcr.io/valkey-io/valkey:9.0-alpine":
    errors.append(f"redis image: {redis.get('image')}")
redis_cmd = str(redis.get("command", ""))
redis_vols = [v.split(":")[0] for v in redis.get("volumes", [])]
# Either inline requirepass or via config file
has_requirepass = "requirepass" in redis_cmd or any("redis.conf" in v for v in redis_vols)
if not has_requirepass:
    errors.append("redis 缺少 requirepass 配置")
require_blank(
    redis,
    APP_SECRET_KEYS
    | {"MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"},
    "redis",
)
require_absent(redis, {"REDIS_PASSWORD"}, "redis")
if "$$REDIS_PASSWORD" not in redis_cmd:
    errors.append("redis command 应在容器内读取 REDIS_PASSWORD")
if "${REDIS_PASSWORD" in redis_cmd:
    errors.append("redis command 不应依赖根 .env/shell 插值")
redis_hc = " ".join(redis.get("healthcheck", {}).get("test", []))
if "$${REDIS_PASSWORD}" not in redis_hc:
    errors.append("redis healthcheck 应在容器内读取 REDIS_PASSWORD")

# 2.10 minio
minio = svcs.get("minio", {})
if minio.get("image") != "quay.io/minio/minio:latest":
    errors.append(f"minio image: {minio.get('image')}")
minio_cmd = minio.get("command", "")
if "console-address" not in str(minio_cmd):
    errors.append("minio 缺少 console-address")
require_blank(
    minio,
    APP_SECRET_KEYS
    | {"MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "REDIS_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"},
    "minio",
)
require_absent(minio, {"MINIO_ROOT_PASSWORD"}, "minio")

# 2.11 flower: no depends_on, no port mapping (spec §17.4)
flower = svcs.get("flower", {})
if flower.get("depends_on"):
    errors.append("flower 不应有 depends_on (规范 §17.4)")
if flower.get("ports"):
    errors.append("flower 不应有 ports (规范 §17.4)")
if "monitoring" not in flower.get("profiles", []):
    errors.append("flower 缺少 profiles: monitoring")
flower_hc = healthcheck_cmd(flower)
if "localhost:5555" not in flower_hc:
    errors.append("flower healthcheck 应探测 localhost:5555")

# 2.12 volumes
vols = list(data.get("volumes", {}).keys())
for v in ["mysql_data", "redis_data", "minio_data"]:
    if v not in vols:
        errors.append(f"缺失 volume: {v}")

# 2.13 networks
nets = data.get("networks", {})
for n in ["public", "internal"]:
    if n not in nets:
        errors.append(f"缺失 network: {n}")
if nets.get("internal", {}).get("internal") != True:
    errors.append("internal network 未设置 internal: true")

# 2.14 无 worker-cad-dispatch (不在 §17.4)
if "worker-cad-dispatch" in names:
    errors.append("worker-cad-dispatch 不应在 compose 中 (规范 §17.4)")

# 2.15 具体 Agent/DXF worker 仍以 profiles 隔离；worker-report 默认启动
for w in ["worker-agent", "worker-dxf"]:
    if "workers" not in svcs[w].get("profiles", []):
        errors.append(f"{w} 缺少 profiles: workers")

if errors:
    print("ERRORS:" + "\nERRORS:".join(errors))
else:
    print("ALL_CHECKS_PASSED")
PYEOF
)

if echo "$COMPOSE_CHECKS" | grep -q 'ALL_CHECKS_PASSED'; then
    pass "compose.yaml 全部结构检查通过 (9 services)"
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

# 3.5 应用代码
assert_grep "$DOCKERFILE" 'COPY app ./app'          "Dockerfile: COPY app"
assert_grep "$DOCKERFILE" 'COPY alembic.ini'        "Dockerfile: COPY alembic.ini"
assert_grep "$DOCKERFILE" 'COPY migrations'         "Dockerfile: COPY migrations"

# 3.6 EXPOSE + CMD
assert_grep "$DOCKERFILE" 'EXPOSE 8000'             "Dockerfile: EXPOSE 8000"
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
    for t in analysis_results review_records audit_logs agent_runs agent_run_steps; do
        if echo "$TABLES" | grep -q "^${t}$"; then
            pass "MySQL: 表 $t 存在"
        else
            fail "MySQL" "表 $t 缺失"
        fi
    done

    TOTAL_TABLES=$(echo "$TABLES" | wc -l)
    if [ "$TOTAL_TABLES" -ge 15 ]; then
        pass "MySQL: 共 $TOTAL_TABLES 张表 (≥15)"
    else
        fail "MySQL" "表总数不足: $TOTAL_TABLES (期望 ≥15)"
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

value = ""
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("DATABASE_URL="):
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
url = urlsplit(value)
print(url.scheme)
print(url.hostname or "")
print(url.port or 3306)
print(unquote(url.username or ""))
print(unquote(url.password or ""))
print(url.path.lstrip("/"))
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
                pass "MySQL: .env DATABASE_URL 应用凭据可登录"
            else
                fail "MySQL" ".env DATABASE_URL 应用凭据无法登录"
            fi
        else
            fail "MySQL" ".env DATABASE_URL 不是 mysql+pymysql URL"
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
    "docs/deployment.md"
)

for f in "${REQUIRED_FILES[@]}"; do
    assert_file "$f" "$f"
done

# 环境模板一致性：只比较键名，不输出或读取敏感值到日志。
assert_env_keys_match ".env.example" ".env.docker.example" ".env.example 与 .env.docker.example 键名一致"
if [ -f ".env" ]; then
    assert_env_keys_match ".env.example" ".env" ".env 与 .env.example 键名一致"
else
    dim "  .env 不存在 — 跳过本机真实 env 键名检查"
fi
if [ -f ".env.docker" ]; then
    assert_env_keys_match ".env.docker.example" ".env.docker" ".env.docker 与 .env.docker.example 键名一致"
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
