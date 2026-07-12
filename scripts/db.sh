#!/usr/bin/env bash
# DWG-Agent — MySQL 运行数据库入口
# 用法: bash scripts/db.sh <start|setup-user|init|migrate|migration-test|check|status|shell|logs>
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ROOT_ENV_FILE="${DWG_ROOT_ENV_FILE:-$PROJECT_ROOT/.env}"
ENV_FILE="${DWG_ENV_FILE:-$PROJECT_ROOT/backend/.env}"
MYSQL_CLIENT="${MYSQL_CLIENT:-}"

usage() {
    cat <<'EOF'
DWG-Agent MySQL helper

Commands:
  start         启动本机 MySQL/MariaDB，并验证 backend/.env 应用凭据可登录
  setup-user    根据 backend/.env 创建/更新 dwg_agent 库、dwg_user 用户和授权
  init          执行 alembic upgrade head + app.db.init_db，补齐迁移与种子数据
  migrate       执行 alembic upgrade head，修复已存在 MySQL schema 漂移
  migration-test
                创建临时 MySQL schema，从空库执行 alembic upgrade head + 种子兼容性并验证表结构
  check         非破坏性检查：配置一致性、MySQL URL、应用凭据、schema、SQLite 退出状态
  status        打印数据库状态与诊断摘要
  shell         使用 backend/.env 中的应用凭据进入 mariadb/mysql shell
  logs          查看 mysql/mariadb systemd 日志
  tables        列出所有表及行数
  backup [file] 导出全库到指定文件（默认 dwg_agent_<timestamp>.sql.gz）
  restore <file>
                从备份文件恢复数据库（需确认）
  reset         删除并重建数据库 + 迁移 + 种子（开发专用，需确认）
  clean         清理残留迁移测试库 + 退役 var/app.db 遗留文件
  reap-storage  回收软删除文件的存储对象（--dry-run 仅预览）
  history       显示 Alembic 迁移历史
  downgrade <rev>
                回滚到指定 Alembic 版本（如 -1 回退一个迁移）
  revision <msg> 创建新的 Alembic 迁移文件

SQLite policy:
  运行入口不再接受 sqlite:// DATABASE_URL。pytest 可显式使用 SQLite test double，
  但 .env / backend/.env 必须指向 mysql+pymysql://。
EOF
}

pick_mysql_client() {
    if [ -n "$MYSQL_CLIENT" ]; then
        command -v "$MYSQL_CLIENT" >/dev/null || { err "找不到 MySQL 客户端: $MYSQL_CLIENT"; return 1; }
        return 0
    fi
    if command -v mariadb >/dev/null 2>&1; then
        MYSQL_CLIENT=mariadb
    elif command -v mysql >/dev/null 2>&1; then
        MYSQL_CLIENT=mysql
    else
        err "找不到 mariadb/mysql 客户端"
        return 1
    fi
}

load_db_config() {
    if [ ! -f "$ENV_FILE" ]; then
        err "数据库环境文件不存在: $ENV_FILE"
        return 2
    fi
    mapfile -t DB_PARTS < <(python - "$ENV_FILE" <<'PY'
from pathlib import Path
from urllib.parse import unquote, urlsplit
import sys

path = Path(sys.argv[1])
value = ""
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line.startswith("DATABASE_URL="):
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
url = urlsplit(value)
username = unquote(url.username or "")
password = unquote(url.password or "")
host = url.hostname or ""
port = str(url.port or 3306)
database = url.path.lstrip("/")
if username:
    auth = username + (":***" if password else "") + "@"
else:
    auth = ""
masked = f"{url.scheme}://{auth}{host}:{port}/{database}" if url.scheme else ""
print(url.scheme)
print(host)
print(port)
print(username)
print(password)
print(database)
print(masked)
PY
)
    DB_SCHEME="${DB_PARTS[0]:-}"
    DB_HOST="${DB_PARTS[1]:-}"
    DB_PORT="${DB_PARTS[2]:-3306}"
    DB_USER="${DB_PARTS[3]:-}"
    DB_PASSWORD="${DB_PARTS[4]:-}"
    DB_NAME="${DB_PARTS[5]:-}"
    DB_MASKED_URL="${DB_PARTS[6]:-}"
}

require_mysql_url() {
    load_db_config
    if [[ "$DB_SCHEME" != mysql* ]]; then
        err "$ENV_FILE DATABASE_URL 当前不是 mysql+pymysql://，运行入口已优雅退出 SQLite"
        echo "  当前 scheme: ${DB_SCHEME:-empty}"
        echo "  修复: 将 .env 与 backend/.env 的 DATABASE_URL 改为 mysql+pymysql://dwg_user:...@127.0.0.1:3306/dwg_agent"
        return 2
    fi
    if [ -z "$DB_HOST" ] || [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
        err "DATABASE_URL 缺少 host/user/database: $DB_MASKED_URL"
        return 2
    fi
}

ensure_env_consistency() {
    local keys=(DATABASE_URL MYSQL_HOST MYSQL_PORT MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD)
    if [ ! -f "$ROOT_ENV_FILE" ]; then
        err "根 .env 不存在: $ROOT_ENV_FILE"
        return 2
    fi
    if [ ! -f "$ENV_FILE" ]; then
        err "backend .env 不存在: $ENV_FILE"
        return 2
    fi
    local mismatch=false key root_value backend_value
    for key in "${keys[@]}"; do
        root_value="$(env_value "$ROOT_ENV_FILE" "$key")"
        backend_value="$(env_value "$ENV_FILE" "$key")"
        if [ "$root_value" != "$backend_value" ]; then
            err ".env 与 backend/.env 的 $key 不一致"
            mismatch=true
        fi
    done
    if $mismatch; then
        echo "  修复: 以一个文件为准同步 .env 与 backend/.env 后重试"
        return 2
    fi
    ok ".env 与 backend/.env 数据库配置一致"
}

mysql_app() {
    MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" "$@"
}

check_app_credentials() {
    pick_mysql_client
    if MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" -e "SELECT 1" >/dev/null 2>&1; then
        ok "MySQL 应用凭据可登录 ($DB_USER@$DB_HOST:$DB_PORT/$DB_NAME)"
        return 0
    fi
    err "MySQL 应用凭据无法登录 ($DB_MASKED_URL)"
    echo "  修复: bash scripts/db.sh setup-user"
    return 1
}

schema_check() {
    local count admin_count
    count="$(mysql_app -N -e "SHOW TABLES" 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${count:-0}" -ge 15 ]; then
        ok "MySQL schema 已就绪 ($count 张表)"
    else
        err "MySQL schema 表数量不足: ${count:-0}"
        echo "  修复: bash scripts/db.sh init"
        return 1
    fi
    admin_count="$(mysql_app -N -e "SELECT COUNT(*) FROM sys_users WHERE username='admin'" 2>/dev/null || echo 0)"
    if [ "${admin_count:-0}" -ge 1 ]; then
        ok "super_admin 种子用户存在"
    else
        err "super_admin 种子用户不存在"
        return 1
    fi
    timestamp_schema_check
}

timestamp_schema_check() {
    local tables=(project_members drawing_versions review_records agent_run_steps)
    local columns=(created_at updated_at)
    local missing=() table column found
    for table in "${tables[@]}"; do
        for column in "${columns[@]}"; do
            found="$(
                mysql_app -N -B -e "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '$table' AND COLUMN_NAME = '$column';" 2>/dev/null || echo 0
            )"
            if [ "${found:-0}" -lt 1 ]; then
                missing+=("$table.$column")
            fi
        done
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        err "MySQL schema 与 SQLAlchemy TimestampMixin 不一致: ${missing[*]}"
        echo "  修复: bash scripts/db.sh migrate"
        return 1
    fi
    ok "TimestampMixin 时间列已同步"
}

sqlite_fd_check() {
    local found=false pid
    while read -r pid; do
        [ -n "$pid" ] || continue
        if ls -l "/proc/$pid/fd" 2>/dev/null | grep -q 'app\.db'; then
            warn "进程 $pid 仍持有 SQLite app.db 文件句柄；请重启后端以完全退出 SQLite"
            found=true
        fi
    done < <(pgrep -f 'uvicorn app.main:app' || true)
    if ! $found; then
        ok "未发现运行中后端持有 SQLite app.db 文件句柄"
    fi
}

start_cmd() {
    ensure_env_consistency
    require_mysql_url
    pick_mysql_client
    info "MySQL DATABASE_URL: $DB_MASKED_URL"
    ensure_service 3306 mysql mariadb
    wait_port 127.0.0.1 3306 30 "MySQL :3306"
    check_app_credentials
}

setup_user_cmd() {
    ensure_env_consistency
    require_mysql_url
    pick_mysql_client
    ensure_service 3306 mysql mariadb
    ensure_sudo || return 2
    if [[ -z "$DB_PASSWORD" || "$DB_PASSWORD" == CHANGE_ME* || "$DB_PASSWORD" == change-me-* ]]; then
        err "MYSQL_PASSWORD 仍是空值或占位值，请先在 .env/backend/.env 写入本机真实密码"
        return 2
    fi
    info "创建/更新 MySQL 库、用户和授权（不删除数据）..."
    DB_NAME_ENV="$DB_NAME" DB_USER_ENV="$DB_USER" DB_PASSWORD_ENV="$DB_PASSWORD" python <<'PY' | sudo mariadb
import os

def q_ident(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"

def q_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

db = os.environ["DB_NAME_ENV"]
user = os.environ["DB_USER_ENV"]
password = os.environ["DB_PASSWORD_ENV"]
for host in ("127.0.0.1", "localhost"):
    print(f"CREATE DATABASE IF NOT EXISTS {q_ident(db)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    print(f"CREATE USER IF NOT EXISTS {q_str(user)}@{q_str(host)} IDENTIFIED BY {q_str(password)};")
    print(f"ALTER USER {q_str(user)}@{q_str(host)} IDENTIFIED BY {q_str(password)};")
    print(f"GRANT ALL PRIVILEGES ON {q_ident(db)}.* TO {q_str(user)}@{q_str(host)};")
print("FLUSH PRIVILEGES;")
PY
    check_app_credentials
}

init_cmd() {
    start_cmd
    info "执行数据库迁移..."
    run_alembic_upgrade
    info "写入种子数据..."
    (cd "$PROJECT_ROOT/backend" && uv run python -m app.db.init_db)
    ok "数据库初始化完成"
}

migrate_cmd() {
    start_cmd
    run_alembic_upgrade
    ok "Alembic 迁移完成"
}

migration_test_db_url() {
    local database="$1"
    python - "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" "$database" <<'PY'
from urllib.parse import quote
import sys

user, password, host, port, database = sys.argv[1:6]
print(f"mysql+pymysql://{user}:{quote(password, safe='')}@{host}:{port}/{database}")
PY
}

migration_test_cmd() {
    start_cmd
    ensure_sudo || return 2
    MIGRATION_TEST_DB="dwg_agent_migration_test_$$"
    if [[ ! "$MIGRATION_TEST_DB" =~ ^[A-Za-z0-9_]+$ ]]; then
        err "临时库名不安全: $MIGRATION_TEST_DB"
        return 2
    fi

    # Drop any orphaned migration-test databases from previous crashed runs
    # before creating a new one (SIGKILL, power loss, etc. can skip the EXIT trap).
    sudo mariadb -N -e "SHOW DATABASES LIKE 'dwg_agent_migration_test_%';" 2>/dev/null | while read -r orphan; do
        [[ "$orphan" =~ ^dwg_agent_migration_test_[0-9]+$ ]] || continue
        warn "清理残留迁移测试库: $orphan"
        sudo mariadb -e "DROP DATABASE IF EXISTS \`$orphan\`;" >/dev/null 2>&1 || true
    done

    cleanup_migration_test() {
        if [[ "${MIGRATION_TEST_DB:-}" =~ ^[A-Za-z0-9_]+$ ]]; then
            sudo mariadb -e "DROP DATABASE IF EXISTS $MIGRATION_TEST_DB;" >/dev/null 2>&1 || true
        fi
    }
    trap cleanup_migration_test EXIT

    info "创建临时 MySQL schema: $MIGRATION_TEST_DB"
    sudo mariadb <<SQL
CREATE DATABASE $MIGRATION_TEST_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON $MIGRATION_TEST_DB.* TO '$DB_USER'@'127.0.0.1';
GRANT ALL PRIVILEGES ON $MIGRATION_TEST_DB.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL

    local test_database_url
    test_database_url="$(migration_test_db_url "$MIGRATION_TEST_DB")"
    info "从空 MySQL schema 执行 Alembic 全链路..."
    (cd "$PROJECT_ROOT/backend" && DATABASE_URL="$test_database_url" uv run alembic upgrade head)
    info "验证临时 schema 表结构..."
    (cd "$PROJECT_ROOT/backend" && DATABASE_URL="$test_database_url" uv run python - <<'PY'
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings

expected_tables = {
    "agent_memory",
    "agent_run_steps",
    "agent_runs",
    "analysis_results",
    "audit_logs",
    "drawing_versions",
    "drawings",
    "excel_final_batches",
    "excel_final_components",
    "excel_final_parts",
    "file_transfers",
    "files",
    "job_steps",
    "jobs",
    "project_members",
    "projects",
    "review_records",
    "sys_permissions",
    "sys_role_permissions",
    "sys_roles",
    "sys_user_roles",
    "sys_users",
    "storage_scan_findings",
    "storage_scan_runs",
    "token_blacklist",
    "workflow_artifacts",
    "workflow_runs",
    "workflow_stage_runs",
}
timestamp_tables = (
    "project_members",
    "drawing_versions",
    "review_records",
    "agent_run_steps",
)

expected_columns = {
    "files": {"deleted_at"},
    "jobs": {"progress_data", "attempt"},
    "job_steps": {"attempt"},
    "sys_users": {"password_changed_at"},
}
expected_bigint_columns = {
    "excel_final_batches": {"id", "job_id", "file_id"},
    "excel_final_parts": {"id", "batch_id"},
    "excel_final_components": {"id", "batch_id"},
}

engine = create_engine(settings.sqlalchemy_database_url)
with engine.connect() as conn:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    missing = sorted(expected_tables - tables)
    if missing:
        raise SystemExit(f"missing tables: {missing}")
    if version != "9c4e7b1a2d60":
        raise SystemExit(f"unexpected Alembic head: {version}")
    for table in timestamp_tables:
        columns = {column["name"] for column in inspector.get_columns(table)}
        missing_columns = {"created_at", "updated_at"} - columns
        if missing_columns:
            raise SystemExit(f"{table} missing {sorted(missing_columns)}")
    for table, required_columns in expected_columns.items():
        columns = {column["name"] for column in inspector.get_columns(table)}
        missing_columns = required_columns - columns
        if missing_columns:
            raise SystemExit(f"{table} missing {sorted(missing_columns)}")
    for table, required_columns in expected_bigint_columns.items():
        columns = {
            column["name"]: str(column["type"]).upper()
            for column in inspector.get_columns(table)
        }
        wrong_types = {
            column: columns.get(column)
            for column in required_columns
            if not columns.get(column, "").startswith("BIGINT")
        }
        if wrong_types:
            raise SystemExit(f"{table} identifier types are not BIGINT: {wrong_types}")
    batch_fks = {
        tuple(foreign_key["constrained_columns"]): (
            foreign_key["referred_table"],
            foreign_key.get("options", {}).get("ondelete"),
        )
        for foreign_key in inspector.get_foreign_keys("excel_final_batches")
    }
    if batch_fks.get(("job_id",)) != ("jobs", "CASCADE"):
        raise SystemExit(f"invalid Excel Final job FK: {batch_fks.get(('job_id',))}")
    if batch_fks.get(("file_id",)) != ("files", "SET NULL"):
        raise SystemExit(f"invalid Excel Final file FK: {batch_fks.get(('file_id',))}")
    unique_columns = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("excel_final_batches")
    }
    if ("job_id",) not in unique_columns:
        raise SystemExit("excel_final_batches.job_id is not unique")
    file_unique_columns = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("files")
    }
    if ("bucket", "storage_key") not in file_unique_columns:
        raise SystemExit("files storage location is not unique")
print(f"Alembic head: {version}; business tables: {len(expected_tables)}")
PY
    )
    info "验证种子数据兼容性..."
    (cd "$PROJECT_ROOT/backend" && DATABASE_URL="$test_database_url" uv run python -m app.db.init_db) || {
        err "种子数据不兼容 — 迁移可能新增 NOT NULL 列或重命名列但未同步更新 init_db"
        cleanup_migration_test
        trap - EXIT
        return 2
    }
    cleanup_migration_test
    trap - EXIT
    ok "临时 MySQL schema 迁移 + 种子数据验证通过并已清理"
}

run_alembic_upgrade() {
    info "执行 Alembic 迁移..."
    (cd "$PROJECT_ROOT/backend" && uv run alembic upgrade head)
}

# ── backup ────────────────────────────────────────────────────
backup_cmd() {
    local outfile="${1:-dwg_agent_$(date +%Y%m%d_%H%M%S).sql.gz}"
    start_cmd
    pick_mysql_client
    info "备份 $DB_NAME → $outfile"
    MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" \
        --single-transaction --routines --triggers --events --skip-column-statistics \
        | gzip > "$outfile"
    local size
    size="$(du -h "$outfile" | cut -f1)"
    ok "备份完成: $outfile ($size)"
}

# ── restore ───────────────────────────────────────────────────
restore_cmd() {
    local infile="${1:-}"
    [ -n "$infile" ] || { err "用法: bash scripts/db.sh restore <文件>"; return 2; }
    [ -f "$infile" ] || { err "文件不存在: $infile"; return 2; }
    start_cmd
    pick_mysql_client

    echo ""
    echo -e "  ${RED}即将覆盖数据库 $DB_NAME 的全部数据！${NC}"
    echo -n "  输入 yes 确认: "
    read -r confirm
    [ "$confirm" = "yes" ] || { ok "已取消"; return 0; }

    info "恢复 $infile → $DB_NAME"
    if file "$infile" | grep -q "gzip"; then
        gunzip < "$infile" | MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME"
    else
        MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" < "$infile"
    fi
    ok "恢复完成"
}

# ── reset ─────────────────────────────────────────────────────
reset_cmd() {
    [ "${RESET_CONFIRM:-}" = "yes" ] || {
        echo ""
        echo -e "  ${RED}即将删除数据库 $DB_NAME 并重建！${NC}"
        echo -n "  输入 yes 确认（或设置 RESET_CONFIRM=yes 跳过提示）: "
        read -r confirm
        [ "$confirm" = "yes" ] || { ok "已取消"; return 0; }
    }
    require_mysql_url
    pick_mysql_client

    info "删除数据库 $DB_NAME..."
    MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" \
        -e "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null \
        || { ensure_sudo && sudo mariadb -e "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"; } \
        || { err "重建数据库失败（应用凭据无 DROP 权限且 sudo 不可用）"; return 1; }

    ok "数据库已重建"
    info "执行迁移 + 种子..."
    (cd "$PROJECT_ROOT/backend" && uv run alembic upgrade head && uv run python -m app.db.init_db)
    ok "重置完成: 迁移 + 种子数据已就绪"
}

# ── history ───────────────────────────────────────────────────
history_cmd() {
    (cd "$PROJECT_ROOT/backend" && uv run alembic history)
}

# ── downgrade ─────────────────────────────────────────────────
downgrade_cmd() {
    local rev="${1:--1}"
    info "回滚 Alembic 到: $rev"
    (cd "$PROJECT_ROOT/backend" && uv run alembic downgrade "$rev")
    ok "回滚完成"
}

# ── revision ──────────────────────────────────────────────────
revision_cmd() {
    local msg="${1:-auto}"
    shift 2>/dev/null || true
    info "创建 Alembic 迁移: $msg"
    (cd "$PROJECT_ROOT/backend" && uv run alembic revision --autogenerate -m "$msg")
    ok "迁移文件已创建: migrations/versions/"
}

# ── tables ────────────────────────────────────────────────────
tables_cmd() {
    start_cmd
    pick_mysql_client
    info "表统计 ($DB_NAME):"
    echo ""
    printf "  %-30s %8s\n" "TABLE" "ROWS"
    printf "  %-30s %8s\n" "------------------------------" "--------"
    MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" \
        -N -e "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = '$DB_NAME' ORDER BY TABLE_NAME;" 2>/dev/null \
        | while read -r table rows; do printf "  %-30s %8s\n" "$table" "$rows"; done
    local total
    total="$(MYSQL_PWD="$DB_PASSWORD" "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" -N -e "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = '$DB_NAME';" 2>/dev/null)"
    echo "  ──────────────────────────────  ────────"
    printf "  %-30s %8s\n" "$total tables total" ""
}

check_cmd() {
    ensure_env_consistency
    require_mysql_url
    pick_mysql_client
    check_app_credentials
    schema_check
    sqlite_fd_check
}

status_cmd() {
    step "MySQL 运行数据库"
    load_db_config || return 1
    echo "  配置: ${DB_MASKED_URL:-unconfigured}"
    if port_free 3306; then
        err "MySQL :3306 未监听"
        return 1
    fi
    ok "MySQL :3306 正在监听"
    check_cmd
}

shell_cmd() {
    ensure_env_consistency
    require_mysql_url
    pick_mysql_client
    check_app_credentials
    MYSQL_PWD="$DB_PASSWORD" exec "$MYSQL_CLIENT" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME"
}

logs_cmd() {
    local lines="${LINES:-120}"
    if systemctl list-unit-files mariadb.service >/dev/null 2>&1 || systemctl status mariadb >/dev/null 2>&1; then
        sudo journalctl -u mariadb -n "$lines" --no-pager
    elif systemctl list-unit-files mysql.service >/dev/null 2>&1 || systemctl status mysql >/dev/null 2>&1; then
        sudo journalctl -u mysql -n "$lines" --no-pager
    else
        err "找不到 mysql/mariadb systemd unit"
        return 1
    fi
}

clean_cmd() {
    pick_mysql_client
    info "清理残留迁移测试数据库..."
    if ensure_sudo; then
        sudo mariadb -N -e "SHOW DATABASES LIKE 'dwg_agent_migration_test_%';" 2>/dev/null | while read -r orphan; do
            [[ "$orphan" =~ ^dwg_agent_migration_test_[0-9]+$ ]] || continue
            warn "清理: $orphan"
            sudo mariadb -e "DROP DATABASE IF EXISTS \`$orphan\`;" >/dev/null 2>&1 || true
        done
    else
        warn "跳过迁移测试库清理（sudo 不可用）"
    fi

    local appdb="$PROJECT_ROOT/var/app.db"
    if [ -f "$appdb" ]; then
        local pid
        pid="$(pgrep -f 'uvicorn app.main:app' 2>/dev/null | head -1)" || true
        if [ -n "$pid" ]; then
            warn "后端仍运行 (pid=$pid)，跳过 $appdb 清理（可能仍被使用）"
        else
            local backup="$PROJECT_ROOT/var/app.db.retired-$(date +%Y%m%d-%H%M%S)"
            info "退役 SQLite 遗留文件: $appdb → $backup"
            mv "$appdb" "$backup"
            ok "已退役 var/app.db"
        fi
    else
        ok "无 var/app.db 遗留文件"
    fi

    info "提示: 软删除对象回收请用 scripts/db.sh reap-storage"
}

case "${1:-status}" in
    "start")           start_cmd ;;
    "setup-user")      setup_user_cmd ;;
    "init")            init_cmd ;;
    "migrate")         migrate_cmd ;;
    "migration-test")  migration_test_cmd ;;
    "check")           check_cmd ;;
    "status")          status_cmd ;;
    "shell")           shell_cmd ;;
    "logs")            logs_cmd ;;
    "tables")          tables_cmd ;;
    "backup")          backup_cmd "${2:-}" ;;
    "restore")         restore_cmd "${2:-}" ;;
    "reset")           reset_cmd ;;
    "history")         history_cmd ;;
    "downgrade")       downgrade_cmd "${2:--1}" ;;
    "revision")        revision_cmd "${2:-auto}" ;;
    "clean")           clean_cmd ;;
    "reap-storage")
        shift
        (cd "$PROJECT_ROOT/backend" && uv run python ../scripts/reap_storage.py "$@") ;;
    "help"|"--help"|"-h") usage ;;
    *) usage; exit 2 ;;
esac
