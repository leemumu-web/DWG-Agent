# Beijing Timezone Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将容器、MySQL 业务墙上时间、FastAPI 时间响应、前端显示和 Celery 业务调度统一为 `Asia/Shanghai`，并在可回滚的短维护窗内把现有 UTC `DATETIME` 数据转换为北京时间。

**Architecture:** 新增唯一的业务时钟模块，并在 ORM 装载边界为 MySQL 返回的无时区 `DATETIME` 补上 `Asia/Shanghai`；所有业务当前时间改用该模块，MySQL 每条连接强制 `+08:00`。独立 Alembic 数据迁移只在维护窗内把既有 `DATETIME` 增加 8 小时，发布工具负责上传完成门槛、备份、分层重建和回滚证据。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PyMySQL、Celery、MySQL 8、Docker Compose、React/TypeScript、Playwright、Bash。

---

## 文件结构

- Create: `backend/app/platform/time.py` — 唯一业务时区、当前时间和无时区值本地化函数。
- Modify: `backend/app/platform/database/base.py` — ORM load/refresh 时给 MySQL `DATETIME` 补 `+08:00`。
- Modify: `backend/app/platform/database/mixins.py` — 公共创建/更新时间使用业务时钟。
- Modify: `backend/app/platform/database/session.py` — MySQL 新连接固定 `+08:00`，健康检查验证会话时区。
- Modify: `backend/app/**.py` 中当前使用 `datetime.now(UTC)` 的文件 — 改用 `business_now()`；绝对时刻逻辑仍用 aware datetime/epoch。
- Modify: `backend/app/platform/messaging/celery_app.py` — 调度时区读取 `BUSINESS_TIMEZONE`，保留 `enable_utc=True`。
- Modify: `frontend/src/shared/components/ui.tsx` — 所有公共时间格式化显式指定 `Asia/Shanghai`。
- Create: `backend/migrations/versions/a4c8e1f2b730_convert_datetime_to_beijing.py` — 一次性历史数据转换。
- Create: `backend/tests/infrastructure/test_business_time.py` — 业务时钟、ORM 和 API 偏移测试。
- Create: `backend/tests/infrastructure/test_beijing_time_migration.py` — 数据迁移列发现、SQL 和幂等测试。
- Modify: `backend/tests/infrastructure/test_db_session.py` — MySQL 会话时区测试。
- Modify: `backend/tests/infrastructure/test_compose.py` — Compose、镜像 zoneinfo 和 MySQL 参数契约测试。
- Modify: `backend/tests/infrastructure/test_server_release.py` — 发布包和维护窗命令的静态契约测试。
- Modify: `compose.yaml`, `.env.docker.example`, `backend/Dockerfile`, `frontend/Dockerfile` — 容器时区和 zoneinfo。
- Create: `scripts/release/server-timezone-migrate.sh` — 生产前置门槛、备份、切换和回滚入口。
- Modify: `scripts/release.sh`, `scripts/release/server-deploy.sh` — 将维护脚本纳入离线发布并安装。
- Modify: `docs/reference/configuration.md`, `scripts/release/README.md` — 明确北京时间存储和维护流程。

### Task 1: 建立业务时钟和 ORM 时区边界

**Files:**
- Create: `backend/app/platform/time.py`
- Modify: `backend/app/platform/database/base.py`
- Modify: `backend/app/platform/database/mixins.py`
- Test: `backend/tests/infrastructure/test_business_time.py`

- [ ] **Step 1: 写业务时钟失败测试**

```python
from datetime import datetime

from app.platform.time import BUSINESS_TIMEZONE, as_business_time, business_now


def test_business_now_is_explicit_beijing_time():
    value = business_now()
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 8 * 3600
    assert value.tzname() == "CST"


def test_naive_database_value_is_interpreted_as_beijing_wall_time():
    value = as_business_time(datetime(2026, 8, 1, 0, 30, 0))
    assert value.tzinfo == BUSINESS_TIMEZONE
    assert value.isoformat() == "2026-08-01T00:30:00+08:00"
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_business_time.py`

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'app.platform.time'`。

- [ ] **Step 3: 实现最小业务时钟**

```python
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)
MYSQL_TIME_ZONE = "+08:00"


def business_now() -> datetime:
    return datetime.now(BUSINESS_TIMEZONE)


def as_business_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BUSINESS_TIMEZONE)
    return value.astimezone(BUSINESS_TIMEZONE)
```

- [ ] **Step 4: 让公共 ORM 时间列使用 `business_now`**

```python
from app.platform.time import business_now


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=business_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=business_now, onupdate=business_now, nullable=False
    )
```

- [ ] **Step 5: 在 ORM load/refresh 边界补齐时区**

在 `backend/app/platform/database/base.py` 为 `Base` 注册 `load` 和 `refresh` 事件。遍历 mapper 的 column attributes；遇到 naive `datetime` 时用 `attributes.set_committed_value(target, key, as_business_time(value))`，避免把只读本地化标成待更新字段。

```python
def _localize_loaded_datetimes(target: object) -> None:
    mapper = inspect(target).mapper
    for prop in mapper.column_attrs:
        value = getattr(target, prop.key, None)
        if isinstance(value, datetime) and value.tzinfo is None:
            attributes.set_committed_value(target, prop.key, as_business_time(value))
```

- [ ] **Step 6: 增加 ORM 往返和 Pydantic 序列化测试**

测试用 SQLite 写入并重新加载一个 `Project`，断言 `created_at.utcoffset()` 为 8 小时，`ProjectRead.model_validate(row).model_dump_json()` 包含 `+08:00`，并断言本地化操作不会让 session 将记录判定为 modified。

- [ ] **Step 7: 运行测试**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_business_time.py`

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add backend/app/platform/time.py backend/app/platform/database/base.py backend/app/platform/database/mixins.py backend/tests/infrastructure/test_business_time.py
git commit -m "feat: establish Beijing business time boundary"
```

### Task 2: 强制 MySQL 会话时区并保持绝对时刻正确

**Files:**
- Modify: `backend/app/platform/database/session.py`
- Modify: `backend/tests/infrastructure/test_db_session.py`
- Modify: `backend/app/platform/security/tokens.py`
- Modify: `backend/app/modules/identity/authentication.py`
- Test: `backend/tests/security/test_security_boundaries.py`
- Test: `backend/tests/security/test_adversarial_auth.py`

- [ ] **Step 1: 写 MySQL connection hook 失败测试**

```python
def test_mysql_connection_hook_sets_beijing_session_timezone():
    connection = RecordingDbApiConnection()
    session_module._configure_mysql_timezone(connection, None)
    assert connection.statements == ["SET time_zone = '+08:00'"]
```

`RecordingDbApiConnection.cursor()` 返回 context-manager cursor，`execute()` 只把 SQL 追加到 `statements`。

- [ ] **Step 2: 运行单测并确认 helper 尚不存在**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_db_session.py -k timezone`

Expected: FAIL，错误指向缺少 `_configure_mysql_timezone`。

- [ ] **Step 3: 实现连接 hook 和 fail-closed 健康检查**

```python
def _configure_mysql_timezone(dbapi_connection, _connection_record) -> None:
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET time_zone = '{MYSQL_TIME_ZONE}'")


if settings.sqlalchemy_database_url.startswith("mysql"):
    event.listen(engine, "connect", _configure_mysql_timezone)
```

`db_health()` 在 MySQL 分支执行 `SELECT @@session.time_zone`；结果不是 `+08:00` 时返回 error，不把数据库报告为 ready。

- [ ] **Step 4: 用北京时间 aware datetime 保存 token blacklist 时间**

`datetime.fromtimestamp(exp, tz=UTC)` 改为 `datetime.fromtimestamp(exp, tz=BUSINESS_TIMEZONE)`；所有 epoch 比较继续调用 `.timestamp()`，有效期秒数不变。

- [ ] **Step 5: 增加 token 绝对有效期回归测试**

固定 epoch `1785528000`，断言北京时间和 UTC 表示的 `.timestamp()` 完全相等；密码修改和 blacklist 到期判断在切换前后结果一致。

- [ ] **Step 6: 运行数据库和安全测试**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_db_session.py tests/security/test_security_boundaries.py tests/security/test_adversarial_auth.py`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/platform/database/session.py backend/tests/infrastructure/test_db_session.py backend/app/platform/security/tokens.py backend/app/modules/identity/authentication.py backend/tests/security
git commit -m "feat: enforce Beijing MySQL sessions"
```

### Task 3: 将业务当前时间和 API 输出统一为北京时间

**Files:**
- Modify: all files returned by `rg -l 'datetime\.now\(UTC\)|\butcnow\(' backend/app`
- Modify: `backend/app/modules/operations/daily_archive/planning.py`
- Modify: `backend/app/modules/workflows/batch_exports.py`
- Modify: `backend/app/modules/workflows/retention.py`
- Modify: `backend/app/modules/operations/control_plane/service.py`
- Modify: `backend/app/modules/dxf_splitting/presentation.py`
- Modify: `backend/app/modules/remnant_inventory/export.py`
- Modify: `backend/app/platform/storage/local.py`
- Modify: `frontend/src/shared/components/ui.tsx`
- Test: `backend/tests/infrastructure/test_business_time.py`
- Test: `backend/tests/contracts/test_frontend_contract.py`
- Test: `backend/tests/operations/test_daily_archive.py`
- Test: `backend/tests/workflows/test_workflow_retention.py`

- [ ] **Step 1: 写 API 和时间格式化失败测试**

在 `test_business_time.py` 创建 Project 后通过 API 读取，断言 `created_at` 匹配 `+08:00`。在前端架构检查中断言公共 `fmtDateTime` 的 options 含 `timeZone: 'Asia/Shanghai'`。

- [ ] **Step 2: 运行测试并确认当前输出缺少显式偏移/固定时区**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_business_time.py tests/contracts/test_frontend_contract.py -k time`

Expected: FAIL，分别指出 API 无 `+08:00` 或格式化器未固定 `Asia/Shanghai`。

- [ ] **Step 3: 机械替换业务当前时间调用**

对 `rg -l 'datetime\.now\(UTC\)|\butcnow\(' backend/app` 返回的文件逐个使用 `apply_patch`：导入 `business_now`，把 `datetime.now(UTC)` 和持久化用途的 `utcnow()` 改为 `business_now()`。完成后运行：

```bash
rg -n 'datetime\.now\(UTC\)|\butcnow\(' backend/app
```

Expected: 仅允许兼容导出名称或文档文字；生产调用为零。

- [ ] **Step 4: 修正本地日边界和 naive datetime 辅助函数**

- `daily_archive/planning.py` 的上海自然日查询边界直接返回带 `BUSINESS_TIMEZONE` 的 start/end，不再转回 UTC 墙上时间。
- `_as_utc` 类 helper 改为调用 `as_business_time`，并同步改成反映语义的名称。
- 文件系统 `mtime` 用 `datetime.fromtimestamp(value, BUSINESS_TIMEZONE)`。
- Excel 导出仍写无时区单元格，但在移除 tzinfo 前先 `as_business_time(value)`。

- [ ] **Step 5: 固定前端显示时区**

```typescript
return new Date(v).toLocaleString('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});
```

- [ ] **Step 6: 运行 focused tests 和静态检查**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_business_time.py tests/operations/test_daily_archive.py tests/workflows/test_workflow_retention.py tests/security`

Run: `cd frontend && npm run build`

Expected: 全部 PASS，TypeScript/Vite 构建成功。

- [ ] **Step 7: 提交**

```bash
git add backend/app backend/tests frontend/src
git commit -m "feat: use Beijing business timestamps"
```

### Task 4: 配置所有容器和 Celery 的北京时间运行环境

**Files:**
- Modify: `compose.yaml`
- Modify: `.env.docker.example`
- Modify: `backend/Dockerfile`
- Modify: `frontend/Dockerfile`
- Modify: `backend/app/platform/messaging/celery_app.py`
- Modify: `backend/tests/infrastructure/test_compose.py`
- Modify: `backend/tests/infrastructure/test_celery_minio_deployment.py`

- [ ] **Step 1: 写 Compose 和 Celery 失败测试**

测试要求：

```python
assert "TZ=Asia/Shanghai" in env_example
assert "--default-time-zone=+08:00" in mysql["command"]
assert nginx["environment"]["TZ"] == "Asia/Shanghai"
assert "tzdata" in backend_dockerfile
assert "tzdata" in frontend_dockerfile
assert 'timezone=settings.business_timezone' in celery_source
assert "enable_utc=True" in celery_source
```

- [ ] **Step 2: 运行并确认测试失败**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_compose.py tests/infrastructure/test_celery_minio_deployment.py -k 'timezone or tzdata'`

Expected: FAIL，指出 TZ、MySQL 参数、zoneinfo 和 Celery 配置缺失。

- [ ] **Step 3: 修改运行配置**

- `.env.docker.example` 增加 `TZ=Asia/Shanghai`。
- `compose.yaml` 的 Nginx 单独增加 `environment: {TZ: Asia/Shanghai}`；其他服务通过既有 `.env.docker` 获取 TZ。
- MySQL command 增加 `--default-time-zone=+08:00`。
- 后端 Debian runtime 安装 `tzdata`；前端 Alpine runtime 在 `USER nginx` 前以 root 执行 `apk add --no-cache tzdata`。
- Celery 设置 `timezone=settings.business_timezone, enable_utc=True`。

- [ ] **Step 4: 验证 Compose 渲染**

Run: `docker compose --env-file .env.docker config --format json | jq -r '.services | to_entries[] | [.key,.value.environment.TZ] | @tsv'`

Expected: 最终 16 个服务（包含 dispatcher）均输出 `Asia/Shanghai`；任何空值都视为失败。

- [ ] **Step 5: 运行测试**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_compose.py tests/infrastructure/test_celery_minio_deployment.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add compose.yaml .env.docker.example backend/Dockerfile frontend/Dockerfile backend/app/platform/messaging/celery_app.py backend/tests/infrastructure
git commit -m "feat: run production containers on Beijing time"
```

### Task 5: 增加一次性 MySQL `DATETIME` 数据迁移

**Files:**
- Create: `backend/migrations/versions/a4c8e1f2b730_convert_datetime_to_beijing.py`
- Create: `backend/tests/infrastructure/test_beijing_time_migration.py`

- [ ] **Step 1: 写迁移失败测试**

测试动态列发现只接受合法 MySQL identifier，按表把多个列合并为一条 UPDATE，并生成：

```sql
UPDATE `projects`
SET `created_at` = DATE_ADD(`created_at`, INTERVAL 8 HOUR),
    `updated_at` = DATE_ADD(`updated_at`, INTERVAL 8 HOUR)
WHERE `created_at` IS NOT NULL OR `updated_at` IS NOT NULL
```

同时断言 revision/down_revision 分别为 `a4c8e1f2b730`/`d1e7f3a9c520`。

- [ ] **Step 2: 运行并确认迁移文件不存在**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_beijing_time_migration.py`

Expected: FAIL，指出迁移模块不存在。

- [ ] **Step 3: 实现 fail-closed 列发现和升级**

迁移从 `information_schema.COLUMNS` 查询当前 schema 的全部 `DATA_TYPE='datetime'` 列，按 table 分组；生产 preflight 必须确认迁移前仍为已审计的 129 列，数量漂移时拒绝自动迁移。table/column 必须匹配 `^[A-Za-z0-9_]+$`，否则抛 `RuntimeError`。MySQL upgrade 对每张表执行一条 `DATE_ADD(..., INTERVAL 8 HOUR)`；非 MySQL dialect 明确 no-op，便于 SQLite 测试链运行。

- [ ] **Step 4: 实现受控 downgrade**

Downgrade 使用相同列集执行 `DATE_SUB(..., INTERVAL 8 HOUR)`；生产回滚仍以备份恢复为准，脚本不得自动调用 downgrade。

- [ ] **Step 5: 测试幂等边界**

通过 Alembic revision 机制断言数据库已在 `a4c8e1f2b730` 时再次 `upgrade head` 不执行 UPDATE；测试不得直接二次调用 `upgrade()` 来模拟 Alembic。

- [ ] **Step 6: 运行迁移相关测试和空库迁移**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_beijing_time_migration.py tests/infrastructure/test_migrations.py`

Run: `bash scripts/db.sh migration-test`

Expected: PASS；空 MySQL 从 base 升到 `a4c8e1f2b730` 成功。

- [ ] **Step 7: 提交**

```bash
git add backend/migrations/versions/a4c8e1f2b730_convert_datetime_to_beijing.py backend/tests/infrastructure/test_beijing_time_migration.py
git commit -m "feat: migrate stored datetimes to Beijing time"
```

### Task 6: 增加生产维护窗、备份和回滚工具

**Files:**
- Create: `scripts/release/server-timezone-migrate.sh`
- Modify: `scripts/release.sh`
- Modify: `scripts/release/server-deploy.sh`
- Modify: `backend/tests/infrastructure/test_server_release.py`

- [ ] **Step 1: 写发布工具失败测试**

静态契约测试要求维护脚本包含：

- `preflight`、`migrate`、`rollback` 三个显式子命令。
- 检查 `workflow_input_dwg_folders.import`、`workflow_input_excel.import`、活动 `file_transfers`、活动 jobs。
- 检查 Docker data-root 和备份目录可用空间。
- Nginx 使用 `docker compose stop -t 180 nginx` 优雅停止后再次核对在途事务。
- `mysqldump --single-transaction --routines --triggers --events`。
- `gzip -t`、SHA-256、临时 schema 恢复和核心表计数比对。
- 依赖顺序 MySQL/MinIO → backend-api → dispatcher → workers → Nginx。
- 不出现 `docker compose down -v`、`docker volume rm`、`DROP DATABASE $MYSQL_DATABASE`。

- [ ] **Step 2: 运行并确认测试失败**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_server_release.py -k timezone`

Expected: FAIL，指出维护脚本和发布打包入口不存在。

- [ ] **Step 3: 实现 `preflight`**

`preflight TARGET_DIR` 输出但不修改：发布版本、旧版 15/最终版 16 服务健康、两个当前 workflow 的 Excel/DWG item 数、非终态 transfer/job/outbox/upload-session 数、MySQL/MinIO 计数与字节数、Docker root/备份盘余量。任一 workflow 尚无 Excel 或 DWG、任何传输/任务仍活动、任何容器不健康都返回非零。

- [ ] **Step 4: 实现备份与恢复验证**

`migrate` 用 `date +%Y%m%d-%H%M%S` 生成备份目录（例如 `$TARGET_DIR/backups/timezone-20260801-010000/`），并保存旧 compose、RELEASE、MySQL gzip dump、dump SHA-256、迁移前 TSV 清单。备份目录权限 `0700`，dump `0600`。创建固定前缀加随机后缀的临时 schema，完整恢复 dump 并比较 `sys_users/projects/files/file_transfers/workflow_runs/jobs/audit_logs` 计数，随后只删除该临时 schema。

- [ ] **Step 5: 实现分层切换**

1. 优雅停止 Nginx，等待在途请求结束。
2. 重跑 preflight 的非终态检查。
3. 停止 backend 和 workers。
4. 创建并验证备份。
5. 用新 Compose 强制重建 MySQL/MinIO并等待健康。
6. 启动 backend；其 `alembic upgrade head` 完成数据迁移。
7. 读取 Alembic head、`@@session.time_zone`、`NOW()-UTC_TIMESTAMP()` 后才启动 workers。
8. 最后启动 Nginx并执行只读 HTTP/DB/MinIO 验证。

- [ ] **Step 6: 实现显式 rollback**

`rollback TARGET_DIR BACKUP_DIR` 只接受位于 `$TARGET_DIR/backups/timezone-*` 的绝对目录，校验 dump SHA-256 后恢复旧 compose、重建 UTC MySQL、恢复 dump，再按旧发布分层启动。目标路径、备份文件或 checksum 任一不匹配立即拒绝。

- [ ] **Step 7: 将脚本纳入离线包**

`scripts/release.sh` 安装 `server-timezone-migrate.sh` 到 payload；`server-deploy.sh install` 再以 `0755` 安装到服务器的 `scripts/`。不把生产备份或 `.env.docker` 打进发布包。

- [ ] **Step 8: 运行发布工具测试**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_server_release.py`

Expected: PASS。

- [ ] **Step 9: 提交**

```bash
git add scripts/release/server-timezone-migrate.sh scripts/release.sh scripts/release/server-deploy.sh backend/tests/infrastructure/test_server_release.py
git commit -m "feat: add guarded Beijing timezone cutover"
```

### Task 7: 文档、全量验证与发布构建

**Files:**
- Modify: `docs/reference/configuration.md`
- Modify: `scripts/release/README.md`
- Modify: `docs/verification/current.md`

- [ ] **Step 1: 更新配置和维护说明**

记录 `TZ=Asia/Shanghai`、MySQL `+08:00`、API `+08:00`、Celery 本地调度/UTC 消息边界，以及禁止在活动上传期间运行迁移。写明备份目录和 rollback 命令的精确形式。

- [ ] **Step 2: 运行格式和 focused gates**

Run: `cd backend && uv run ruff check app tests migrations`

Run: `cd backend && uv run pytest -q tests/infrastructure tests/security tests/operations/test_daily_archive.py tests/workflows/test_workflow_retention.py`

Run: `cd frontend && npm run build`

Expected: 全部 PASS，无 warning/error。

- [ ] **Step 3: 运行仓库发布门槛**

Run: `bash scripts/verify.sh full`

Expected: `FAIL=0` 且没有被误报为 PASS 的 blocked gate。

- [ ] **Step 4: 构建本次离线发布包**

确认 `GPG_RECIPIENT` 指向现有可解密的发布密钥后执行：

```bash
bash scripts/release.sh bundle --recipient "$GPG_RECIPIENT" --output releases --version server-production-20260801-r37
```

验证 `releases/dwg-agent-server-production-20260801-r37.tar.gz.gpg` 可解、外层 SHA-256 和 images manifest；不得把 `.env.docker` 或数据库备份写入发布包。

- [ ] **Step 5: 提交文档**

```bash
git add docs/reference/configuration.md scripts/release/README.md docs/verification/current.md
git commit -m "docs: record Beijing timezone operations"
```

### Task 8: 等待上传完成并建立生产基线

**Files:**
- No repository changes.

- [ ] **Step 1: 每 20–30 秒只读观察上传状态**

观察两个 workflow，直到每个输入批次同时有 Excel 和至少一个 DWG，相关 import audit 已成功，且 `file_transfers` 无 `prepared/in_progress/failed/compensation_required`。

- [ ] **Step 2: 验证对象存储一致性**

逐个已登记文件比较 MySQL `size_bytes` 与 MinIO stat；对两个项目的输入对象流式计算 SHA-256 并与 ledger 比对。不得下载到根分区形成重复大文件。

- [ ] **Step 3: 记录基线**

保存项目、用户、文件、传输、workflow、job、audit、MinIO 对象数量与字节数，15 容器状态、Docker data-root、MySQL volume 与备份盘余量。基线放入迁移备份目录，不写入 Git。

- [ ] **Step 4: 执行远端 preflight**

Run: `/opt/dwg-agent/server/scripts/server-timezone-migrate.sh preflight /opt/dwg-agent/server`

Expected: `PASS`，并明确显示两个 workflow 输入完整、活动写入为 0、MySQL `DATETIME` 列为 129、磁盘门槛满足。

### Task 9: 安装发布并执行短维护窗迁移

**Files:**
- Remote `/opt/dwg-agent/server` only; preserve `.env.docker` and named volumes.

- [ ] **Step 1: 通过 Tailscale 传输并校验发布包**

使用断点续传/压缩友好的现有传输路径；远端核对 SHA-256，失败不得进入 install。

- [ ] **Step 2: 安装新发布但不重启当前栈**

Run: `/opt/dwg-agent/server/scripts/server-deploy.sh install /opt/dwg-agent/incoming/dwg-agent-server-production-20260801-r37.tar.gz.gpg /opt/dwg-agent/server`

Expected: 镜像加载和 manifest 校验通过，现有 `.env.docker` mode 0600 且内容被保留，当前容器仍运行旧实例。

- [ ] **Step 3: 在 `.env.docker` 增加唯一 TZ 配置**

以不打印其他密钥的方式写入或替换 `TZ=Asia/Shanghai`；随后验证文件权限仍为 0600，且只输出 `TZ` 这一项。

- [ ] **Step 4: 运行 guarded migrate**

Run: `/opt/dwg-agent/server/scripts/server-timezone-migrate.sh migrate /opt/dwg-agent/server`

Expected: preflight、优雅停写、备份恢复验证、MySQL/MinIO、backend、workers、Nginx 全部逐层通过；任何阶段失败立即停止且不恢复写入。

- [ ] **Step 5: 验证时间和数据**

- 容器 `date`、Python `business_now()`、MySQL `NOW()` 均为 `+08:00`，相差小于 2 秒。
- `TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())` 接近 28800。
- 新 MySQL 连接 `@@session.time_zone='+08:00'`。
- Alembic head 为 `a4c8e1f2b730`。
- API Project/File/Workflow 时间字符串含 `+08:00`。
- 两个项目、用户、文件和对象数量/字节数与基线一致。
- 15 个容器 healthy，主页、`/nginx-health`、`/health/ready` 为 HTTP 200。

- [ ] **Step 6: 观察 10 分钟并清理临时痕迹**

持续观察容器 restart/OOM、MySQL 错误、worker heartbeat、失败 transfer/job 和磁盘增长。删除远端传输临时包、临时恢复 schema 和一次性探针输出；保留加密发布包、迁移前数据库备份、SHA-256 与基线清单。

- [ ] **Step 7: 最终 Git 状态和发布记录**

确认 Git 只保留既有预期未跟踪目录；记录最终 release、提交、验证命令、备份路径和是否执行 rollback。不得把生产项目名称、凭据或数据库 dump 提交到仓库。
