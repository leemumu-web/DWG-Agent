# DWG-Agent 平台 -- 数据库设计与运维

> **受众:** 数据库管理员、平台运维人员、后端开发者
> **最后更新:** 2026-07-11
> **范围:** 引擎配置、表目录、实体关系、迁移管理、种子数据、备份策略

---

## 1. 引擎配置

### 1.1 运行时引擎: MySQL 8.x

生产/运行时数据库引擎为 MySQL 8.x，通过 `mysql+pymysql://` 访问。

**配置** (`backend/app/platform/database/session.py`):

```python
pool_args = {
    "pool_recycle": settings.db_pool_recycle_seconds,
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_pool_max_overflow,
    "pool_timeout": settings.db_pool_timeout_seconds,
    "pool_use_lifo": True,
}
engine_kwargs = {"pool_pre_ping": True}
if settings.sqlalchemy_database_url.startswith("mysql"):
    engine_kwargs.update(pool_args)
engine = create_engine(settings.sqlalchemy_database_url, **engine_kwargs)
```

| 参数 | 值 | 说明 |
|---|---|---|
| `DB_POOL_SIZE` | 2 | 每个进程的持久池容量；连接按需延迟创建。 |
| `DB_POOL_MAX_OVERFLOW` | 2 | 每进程突发容量；默认最多同时检出 4 个连接。 |
| `DB_POOL_TIMEOUT_SECONDS` | 30秒 | 等待应用池连接的最长时间。 |
| `DB_POOL_RECYCLE_SECONDS` | 3600秒 | 在常见 MySQL 空闲超时前回收连接。 |
| `pool_pre_ping` | True | 每次使用前检测连接的存活状态。每次检出增加一次额外查询，但可以消除 `MySQL server has gone away` 错误。 |

**各部署模式下的连接数:**

| 模式 | 进程数 | 默认应用 engine 上限 |
|---|---|---|
| 本地 API | 1 | 4 |
| Compose API (gunicorn `-w 4`) | 4 | 16 |
| 每个 Celery 执行进程 | 1 | 4 |

这些是上限，不是预先打开的最小连接数。容量规划还要计入每个 Celery 父/子进程、Kombu/result backend、迁移和运维 session；应通过四个 `DB_POOL_*` 参数调优，而不是硬编码更大的池。

### 1.2 测试隔离: SQLite 内存数据库

Pytest 使用 SQLite 配合 `StaticPool` 实现完整的测试隔离:

```python
# Per conftest.py
engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
```

**每个测试连接设置的 SQLite pragma:**

| Pragma | 值 | 用途 |
|---|---|---|
| `foreign_keys` | ON | 强制外键约束（SQLite 默认关闭） |

该 pragma 通过测试 conftest 中的 `@event.listens_for(engine, "connect")` 处理器应用。不会影响运行时的 MySQL 引擎。

**为什么只设置 `foreign_keys`？** 测试数据库使用内存 SQLite 配合 `StaticPool`，仅提供一个连接。在此配置下：

- `journal_mode=WAL` 无意义 -- WAL 在写入期间允许并发读取，但单个连接没有并发。
- `busy_timeout` 无意义 -- 只有一个连接，永远不会有锁争用。

这两个 pragma 仅对基于文件的、带连接池的 SQLite 有意义。此处有意省略。

### 1.3 MySQL 与 SQLite 类型映射

迁移中的 `_pk_type()` 辅助函数平滑处理了类型差异:

```python
def _pk_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")
```

- **MySQL:** 主键为 `BIGINT`（8字节，有符号）。
- **SQLite:** `.with_variant(sa.Integer(), "sqlite")` 告知 SQLAlchemy 在与 SQLite 通信时使用 `INTEGER`。这是必要的，因为 SQLite 的 `INTEGER` 亲和性处理自增的方式与 MySQL 的 `BIGINT AUTO_INCREMENT` 不同。
- **DECIMAL 列:** `analysis_results` 中的 `confidence DECIMAL(5,4)` 两个引擎均可正确处理。

### 1.4 数据库 URL 组装

`settings.mysql_url` 属性从组件字段组装 DSN（规范第 18 节）:

```python
mysql_url = f"mysql+pymysql://{user_part}@{host}:{port}/{database}"
```

这使得 Docker Compose 可以覆盖单个组件（例如 `MYSQL_HOST=mysql`），而不需要重新构造完整的 URL。

---

## 2. 完整表目录

Alembic/SQLAlchemy 管理 **42 张模型表**。空库执行 `alembic upgrade head` 后另有 `alembic_version`，因此迁移基础是 43 张表。Celery 按 broker/result 实际使用按需创建 8 张运行时表：`kombu_queue`、`kombu_message`、`celery_taskmeta`、`celery_tasksetmeta`、`message_id_sequence`、`queue_id_sequence`、`task_id_sequence`、`taskset_id_sequence`。全部 runtime 表都存在时最多为 **51 张表**。

不能把 51 当成每个时刻的固定表数：只运行 Alembic、尚未初始化 Celery channel/backend 的 schema 只有 43 张；Kombu broker 与 result backend 又可能分阶段建表。Alembic autogenerate 排除全部 8 张 Celery 自有表，Celery 升级也不经过应用 migration。

### 2.1 身份与访问管理 (IAM) -- 6 张表

#### `sys_users`

核心用户身份表。通过 `deleted_at` 时间戳支持软删除。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 代理主键 |
| `username` | VARCHAR(64) | UNIQUE, NOT NULL, INDEXED | 登录标识符（工号或用户名） |
| `employee_no` | VARCHAR(64) | NULLABLE | 公司工号（仅元数据） |
| `real_name` | VARCHAR(64) | NOT NULL | 显示名称 |
| `email` | VARCHAR(128) | NULLABLE | 联系邮箱 |
| `password_hash` | VARCHAR(255) | NOT NULL | 密码的 Argon2id 哈希值 |
| `password_algo` | VARCHAR(32) | NOT NULL, DEFAULT 'argon2id' | 算法标签，便于未来迁移 |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'active', INDEXED | `active` / `disabled` / `deleted` |
| `last_login_at` | DATETIME | NULLABLE | 上次成功登录的时间戳 |
| `password_changed_at` | DATETIME | NULLABLE | 在此时刻或之前签发的 Token 均被拒绝 |
| `deleted_at` | DATETIME | NULLABLE, INDEXED | 软删除时间戳（NULL = 未删除） |
| `created_at` | DATETIME | NOT NULL | 记录创建时间戳 |
| `updated_at` | DATETIME | NOT NULL | 记录最后修改时间戳 |

**索引:** `ix_sys_users_username` (UNIQUE), `ix_sys_users_status`, `ix_sys_users_deleted_at`

**重要说明:** `status = 'deleted'` 的用户被 `get_user_or_404()` 视为不存在。其 `deleted_at` 时间戳会被设置，但行记录会保留以确保引用完整性（审计日志、文件所有权、作业历史）。

#### `sys_roles`

全局角色定义。系统角色为种子数据，受保护。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 代理主键 |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL, INDEXED | 机器可读的角色代码（如 `super_admin`） |
| `name` | VARCHAR(64) | NOT NULL | 人类可读的显示名称 |
| `description` | VARCHAR(255) | NULLABLE | 角色用途描述 |
| `is_system` | BOOLEAN | NOT NULL, DEFAULT FALSE | 若为 TRUE，则该角色为种子数据，不应删除 |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**种子角色:** `super_admin`, `admin`, `engineer`, `reviewer`, `operator`, `viewer`, `auditor`

#### `sys_permissions`

原子权限定义，以 资源+操作 对的形式存在。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 代理主键 |
| `code` | VARCHAR(128) | UNIQUE, NOT NULL, INDEXED | 机器可读的代码（如 `users:read`） |
| `resource` | VARCHAR(64) | NOT NULL | 资源命名空间 (`users`, `files`, `jobs` 等) |
| `action` | VARCHAR(64) | NOT NULL | 操作类型 (`read`, `write`) |
| `name` | VARCHAR(128) | NOT NULL | 人类可读的描述 |

**8 个种子权限**（完整边界见[安全指南](../guides/security.md)）。

#### `sys_user_roles`

用户与其全局角色之间的多对多关联表。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `user_id` | BIGINT | PK (复合), FK → `sys_users.id` | |
| `role_id` | BIGINT | PK (复合), FK → `sys_roles.id` | |
| `created_at` | DATETIME | NOT NULL, DEFAULT NOW() | 分配时间戳 |

**主键:** `(user_id, role_id)` -- 防止重复分配同一角色。

#### `sys_role_permissions`

角色与其权限之间的多对多关联表。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `role_id` | BIGINT | PK (复合), FK → `sys_roles.id` | |
| `permission_id` | BIGINT | PK (复合), FK → `sys_permissions.id` | |

**主键:** `(role_id, permission_id)` -- 防止重复授予同一权限。

#### `token_blacklist`

持久化 JWT 撤销记录。后续登出操作会顺带删除已过期记录。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `jti` | VARCHAR(36) | PK | JWT 唯一标识 |
| `expires_at` | DATETIME | NOT NULL, INDEXED | Token 到期时间；超过该时间的记录不再产生撤销效果 |

### 2.2 项目与成员 -- 2 张表

#### `projects`

项目容器，用于组织图纸、文件和作业。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL, INDEXED | 简短项目代码（如 `PRJ-2026-001`） |
| `name` | VARCHAR(128) | NOT NULL | 项目显示名称 |
| `description` | TEXT | NULLABLE | 项目详情 |
| `owner_id` | BIGINT | FK → `sys_users.id` | 项目所有者（通常是创建者） |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'active' | `active` / `archived` / `deleted` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_projects_code` (UNIQUE)

#### `project_members`

项目级成员关系，在项目范围内具有基于角色的访问控制。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `project_id` | BIGINT | NOT NULL, FK → `projects.id` | |
| `user_id` | BIGINT | NOT NULL, FK → `sys_users.id` | |
| `project_role` | VARCHAR(64) | NOT NULL | `project_owner` / `project_engineer` / `project_reviewer` / `project_viewer` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**唯一约束:** `uq_project_member` 建立在 `(project_id, user_id)` 上 -- 每个用户在每个项目中只能有一个角色。

### 2.3 文件管理与存储账本 -- 4 张表

#### `files`

文件元数据存储。实际文件字节数据存储在存储层（本地文件系统或 MinIO），而非数据库中。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `bucket` | VARCHAR(128) | NOT NULL | 存储桶名称 (`dwg-original` 等) |
| `storage_key` | VARCHAR(512) | NOT NULL, INDEXED | 桶内路径 (`uploads/{uuid}.dwg`) |
| `original_name` | VARCHAR(255) | NOT NULL | 用户提供的文件名（仅用于显示） |
| `file_ext` | VARCHAR(32) | NOT NULL | 小写扩展名，含点号 (`.dwg`) |
| `content_type` | VARCHAR(128) | NULLABLE | 上传时或检测得到的 MIME 类型 |
| `size_bytes` | BIGINT | NOT NULL | 文件大小（字节） |
| `sha256` | VARCHAR(64) | NOT NULL, INDEXED | SHA-256 十六进制摘要（64 个字符） |
| `md5` | VARCHAR(32) | NULLABLE | MD5 十六进制摘要（32 个字符） |
| `batch_name` | VARCHAR(128) | NULLABLE, INDEXED | 多文件 DXF/Excel 上传的批次分组标签（如 ZIP 主名）。由迁移 `53cd59adf848` 添加 |
| `uploaded_by` | BIGINT | FK → `sys_users.id` | 上传者用户 ID |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'available' | `available` / `deleted` |
| `deleted_at` | DATETIME | NULLABLE, INDEXED | 软删除时间；恢复时清空 |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**唯一约束:** `uq_files_bucket_storage_key` 建立在 `(bucket, storage_key)` 上，禁止一个对象位置对应两条登记。

**索引:** `ix_files_sha256`, `ix_files_storage_key`, `ix_files_batch_name`, `ix_files_status_deleted_at`

**批量上传:** `batch_name` 将一起上传的文件分组（带 `batch_name` 查询参数的单文件上传，或以主名解压的 `.zip`），使 DXF→Excel 管道以及批量下载/删除端点能够一次性操作整组文件。未分组上传时其值为 `NULL`。

#### `file_transfers`

跨 MySQL 与对象存储的持久化流转账本。上传、ZIP 条目、生成文件、DXF 预览生成/失效、下载、预览出库、ZIP 出库、软删除、恢复、补登记与永久清理均写入该表；`audit_logs` 仍记录谁执行了业务动作，但不替代字节级状态机。

| 列组 | 关键列 | 说明 |
|---|---|---|
| 身份 | `transfer_uid`, `request_id`, `idempotency_key` | UUID 流水标识、请求关联和幂等键 |
| 类型 | `direction`, `operation`, `status` | `inbound/outbound/internal`；状态为 `prepared/in_progress/succeeded/failed/cancelled/compensation_required` |
| 关联 | `file_id`, `batch_ref`, `actor_user_id` | 文件、批次和操作人；批量流水可不绑定单个文件 |
| 位置 | `bucket`, `storage_key`, `original_name` | 对象位置与显示名 |
| 结算 | `expected_bytes`, `transferred_bytes`, `started_at`, `finished_at` | 预期/实际字节与生命周期 |
| 失败 | `error_code`, `error_message` | 对象写入、流中断、元数据回滚和补偿失败原因 |

`(actor_user_id, operation, idempotency_key)` 唯一约束阻止同一操作重复执行。MySQL 运行时先以独立短事务持久化意图；对象写成功而业务事务回滚时补偿删除对象，删除失败或永久清理后元数据提交失败时写 `compensation_required`。

#### `storage_scan_runs`

一致性扫描运行记录。保存 backend、可选 bucket 范围、排队/运行/终态、操作人、开始/结束时间，以及登记、对象、正常、软删除保留、对象缺失、未登记、大小不符和错误计数。扫描任务由 report worker 异步执行，不在总览请求中全量枚举对象。

#### `storage_scan_findings`

只保存异常和软删除保留项，不复制全部正常对象。每行包含 `run_id`、`finding_type`、bucket/key、可选 file ID、DB/对象大小、对象修改时间、处置状态/动作/操作人/时间。`(run_id, finding_type, bucket, storage_key)` 唯一；`(run_id, resolution_status)` 支持前端快速筛选待处置与已处置项。

### 2.4 图纸与版本 -- 2 张表

#### `drawings`

逻辑图纸记录，支持版本跟踪。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `project_id` | BIGINT | NOT NULL, FK → `projects.id`, INDEXED | 所属项目 |
| `drawing_no` | VARCHAR(128) | NULLABLE | 图号（如 `A-001`） |
| `title` | VARCHAR(255) | NULLABLE | 图纸标题 |
| `discipline` | VARCHAR(64) | NULLABLE | 工程专业代码 |
| `current_version_id` | BIGINT | FK → `drawing_versions.id` | 指向最新版本 |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'active' | `active` / `archived` / `deleted` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_drawings_project_id`

**循环 FK 说明:** `current_version_id` 引用 `drawing_versions.id`，而 `drawing_versions.drawing_id` 引用 `drawings.id`。迁移通过以下方式处理：先创建不带该 FK 的 `drawings` 表，然后创建 `drawing_versions`，最后在两表都存在后通过 `op.create_foreign_key()` 添加循环 FK。降级迁移则按相反顺序执行。

#### `drawing_versions`

应用层追加的版本记录。每次上传新的 DWG 修订版都会创建新行；API 没有修改已有版本的路由。数据库没有使用 trigger 或密码学 seal 让行物理不可变。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `drawing_id` | BIGINT | NOT NULL, FK → `drawings.id` | 父图纸 |
| `file_id` | BIGINT | NOT NULL, FK → `files.id` | 该版本的实际文件 |
| `version_no` | INT | NOT NULL | 单调递增的版本号 |
| `source` | VARCHAR(64) | NULLABLE | 上传来源标签 |
| `created_by` | BIGINT | FK → `sys_users.id` | 上传该版本的用户 |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

### 2.5 作业处理 -- 2 张表

#### `jobs`

DWG 图纸的异步处理作业。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `project_id` | BIGINT | NULLABLE, FK → `projects.id`, INDEXED | 项目范围 |
| `drawing_id` | BIGINT | NULLABLE, FK → `drawings.id`, INDEXED | 目标图纸 |
| `created_by` | BIGINT | NULLABLE, FK → `sys_users.id` | 作业提交者 |
| `task_type` | VARCHAR(64) | NOT NULL | 任务代码: `convert_dwg_to_dxf` / `convert_dxf_to_dwg` / `extract_dxf_to_excel` |
| `request_key` | VARCHAR(128) | NULLABLE | API 作用域后的逻辑请求幂等键；旧任务和不要求幂等的通用任务为 NULL |
| `precision_level` | VARCHAR(32) | NOT NULL | `normal` / `high`（决定管道路由） |
| `pipeline` | VARCHAR(64) | NULLABLE | 分配的管道: `local_stub` / `dxf_open_source` / `dxf2dwg_open_source` / `dxf2excel` / `zwcad_worker` |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'queued', INDEXED | `pending` → `queued` → `running` → `succeeded`/`failed`/`cancelled` |
| `attempt` | INT | NOT NULL, DEFAULT 1 | 当前执行代次；重试被接受时原子递增 |
| `priority` | INT | NOT NULL, DEFAULT 0 | 越高越紧急 |
| `progress` | INT | NOT NULL, DEFAULT 0 | 0-100 百分比 |
| `params_json` | JSON | NULLABLE | 任务特定参数 |
| `error_code` | VARCHAR(64) | NULLABLE | 失败时的机器可读错误代码 |
| `error_message` | TEXT | NULLABLE | 人类可读的错误描述 |
| `progress_data` | JSON | NULLABLE | 最新的持久化 SSE 载荷（消息、步骤和结果元数据） |
| `created_at` | DATETIME | NOT NULL | |
| `started_at` | DATETIME | NULLABLE | Worker 接收作业的时间 |
| `finished_at` | DATETIME | NULLABLE | 作业达到终态的时间 |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_jobs_project_id`, `ix_jobs_drawing_id`, `ix_jobs_status`

**唯一约束:** `uq_jobs_actor_task_request_key` 建立在 `(created_by, task_type, request_key)` 上。认证 Excel Final 请求的 `created_by` 与 `request_key` 非空，唯一约束是多进程竞态的最终边界；多个 NULL 仍允许旧任务和非幂等任务共存。相同键若参数不同由 service 返回 409，不能静默指向另一输入文件。

**作业生命周期状态:** `pending`（已创建，尚未入队）→ `queued`（已进入 MySQL 支撑的 Celery 队列）→ `running`（Worker 正在执行）→ `succeeded` / `failed` / `cancelled`。中间状态: `waiting_cad_worker`, `validating`, `need_review`。

#### `job_steps`

作业内部的细粒度执行步骤。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `job_id` | BIGINT | NOT NULL, FK → `jobs.id`, INDEXED | 父作业 |
| `attempt` | INT | NOT NULL, DEFAULT 1 | 生成该步骤的执行代次 |
| `step_name` | VARCHAR(128) | NOT NULL | 人类可读的步骤标签 |
| `worker_name` | VARCHAR(128) | NULLABLE | 执行该步骤的 Celery worker 主机名 |
| `status` | VARCHAR(32) | NOT NULL | `pending` / `running` / `succeeded` / `failed` |
| `input_json` | JSON | NULLABLE | 步骤输入参数 |
| `output_json` | JSON | NULLABLE | 步骤输出数据 |
| `error_message` | TEXT | NULLABLE | 步骤失败时的错误详情 |
| `started_at` | DATETIME | NULLABLE | |
| `finished_at` | DATETIME | NULLABLE | |

**索引:** `ix_job_steps_job_id`, `ix_job_steps_job_id_attempt`

### 2.6 Agent 执行 -- 3 张表

#### `agent_runs`

LLM Agent 执行会话记录。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `session_id` | VARCHAR(128) | NOT NULL, INDEXED | 客户端提供的会话标识符 |
| `user_id` | BIGINT | NOT NULL, FK → `sys_users.id` | 发起该 agent 运行的用户 |
| `project_id` | BIGINT | FK → `projects.id` | 项目上下文 |
| `drawing_id` | BIGINT | FK → `drawings.id` | 图纸上下文 |
| `file_id` | BIGINT | FK → `files.id` | 输入文件上下文 |
| `task` | TEXT | NOT NULL | 自然语言任务描述 |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'queued' | `queued` / `running` / `succeeded` / `failed` |
| `answer` | TEXT | NULLABLE | LLM 最终响应文本 |
| `output_file_id` | BIGINT | FK → `files.id` | Agent 产生的结果文件（如果有） |
| `history_count` | INT | NOT NULL, DEFAULT 0 | 本次会话中的对话轮次数量 |
| `created_at` | DATETIME | NOT NULL | |
| `started_at` | DATETIME | NULLABLE | |
| `finished_at` | DATETIME | NULLABLE | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_agent_runs_session_id`

#### `agent_run_steps`

Agent 运行中的单个工具调用和推理步骤。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `agent_run_id` | BIGINT | NOT NULL, FK → `agent_runs.id`, INDEXED | |
| `step_type` | VARCHAR(64) | NOT NULL | `tool_call` / `thought` / `observation` / `answer` |
| `title` | VARCHAR(255) | NULLABLE | 人类可读的步骤摘要 |
| `tool_name` | VARCHAR(128) | NULLABLE | 调用的 MCP 工具名称 |
| `arguments_json` | JSON | NULLABLE | 工具调用参数 |
| `content` | TEXT | NULLABLE | 步骤输出或推理内容 |
| `status` | VARCHAR(32) | NOT NULL | `success` / `error` / `skipped` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_agent_run_steps_agent_run_id`

#### `agent_memory`

每个 Agent 会话一行。`messages` 保存有界 JSON 对话历史；读取时以 `updated_at` 和 `AGENT_MEMORY_TTL` 判断过期，并在调用方事务中删除过期行。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `session_id` | VARCHAR(128) | PK | 稳定的 Agent 会话标识 |
| `messages` | JSON | NOT NULL | 最近最多 `AGENT_MAX_MESSAGES` 条消息 |
| `created_at` | DATETIME | NOT NULL | 创建时间 |
| `updated_at` | DATETIME | NOT NULL | 最后写入时间，用于 TTL 判断 |

### 2.7 结果与审查 -- 2 张表

#### `analysis_results`

处理作业的输出 -- 结构化分析数据。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `job_id` | BIGINT | NOT NULL, FK → `jobs.id`, INDEXED | 来源作业 |
| `drawing_id` | BIGINT | FK → `drawings.id`, INDEXED | 来源图纸 |
| `result_type` | VARCHAR(64) | NOT NULL | 结果类型（如 `layer_list`, `entity_count`） |
| `result_json` | JSON | NULLABLE | 结构化结果数据 |
| `confidence` | DECIMAL(5,4) | NULLABLE | 算法置信度 0.0000-1.0000 |
| `result_file_id` | BIGINT | FK → `files.id` | 输出文件（Excel, PDF 等） |
| `algorithm_version` | VARCHAR(64) | NULLABLE | 处理算法的版本 |
| `tool_version` | VARCHAR(64) | NULLABLE | 处理工具的版本 |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'succeeded' | `succeeded`（初始）/ `need_review` / `approved` / `rejected` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_analysis_results_job_id`, `ix_analysis_results_drawing_id`

#### `review_records`

对分析结果的人工审查决定。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `result_id` | BIGINT | NOT NULL, FK → `analysis_results.id`, INDEXED | |
| `reviewer_id` | BIGINT | FK → `sys_users.id` | 执行审查的人 |
| `decision` | VARCHAR(32) | NOT NULL | `approved` / `rejected` / `needs_revision` |
| `comment` | TEXT | NULLABLE | 审查者的备注 |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_review_records_result_id`

### 2.8 审计 -- 1 张表

#### `audit_logs`

安全相关操作的应用审计记录。service 追加写入且不暴露 update/delete API，但数据库不强制物理或密码学不可变。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `actor_user_id` | BIGINT | FK → `sys_users.id` | 执行操作的人（系统操作为 NULL） |
| `action` | VARCHAR(128) | NOT NULL, INDEXED | 操作代码（如 `users.create`） |
| `resource_type` | VARCHAR(64) | NOT NULL | 资源命名空间 |
| `resource_id` | BIGINT | NULLABLE, INDEXED | 受影响的资源 ID |
| `ip_address` | VARCHAR(64) | NULLABLE | 客户端 IP |
| `user_agent` | VARCHAR(512) | NULLABLE | 客户端 User-Agent |
| `before_json` | JSON | NULLABLE | 操作前的资源状态 |
| `after_json` | JSON | NULLABLE | 操作后的资源状态 |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_audit_logs_action`, `ix_audit_logs_resource_id`

**说明:** `resource_id` 是**多态指针**，并非真正的外键 -- 它存储受影响资源的 ID（不限资源类型），因此不存在 FK 约束。迁移 `c3d2e1f0a9b8` 将其类型从 `Integer` 修正为 `BIGINT`，与其他所有 ID 列保持一致。

### 2.9 Excel Final -- 3 张表

- `excel_final_batches` 保存任务的一对一结构化导入。`job_id` 唯一，删除任务时级联删除；可空 `file_id` 指向源文件，源文件删除时置 NULL。
- `excel_final_components` 保存构件级数量与重量汇总；删除批次时按 `batch_id` 级联删除。
- `excel_final_parts` 保存标准化零件行、尺寸、材质、数量、理论/净重/毛重和表面积；按 `batch_id`、`material`、`part_no` 建索引。

三表仅保存解析后的业务数据。源工作簿和生成工作簿的字节仍位于存储层，并通过 `files` 引用。

迁移 `9c4e7b1a2d60` 扩容真实业务标识：零件/构件 `component_no` 为 VARCHAR(512)，`part_no` 与 `profile_spec` 为 VARCHAR(255)，`part_type` 与 `spec` 为 VARCHAR(128)。迁移只扩大列宽，不重写历史 Excel Final 建表迁移，也不管理 Celery/Kombu 表。

### 2.10 通用工作流、生产输入与 DXF 分类 -- 7 张表

- `workflow_runs` 保存项目级流程、类型、状态、当前阶段、整体进度、配置、错误和生命周期时间；按项目/创建者/状态建立索引。
- `workflow_stage_runs` 保存模板生成的有序阶段，`(workflow_run_id, stage_code)` 唯一；可选记录 `job_id` 与 `job_attempt`，同步时只接受匹配 attempt。
- `workflow_artifacts` 保存流程/阶段到 `files` 或 `analysis_results` 的引用和版本字段，不保存对象字节。
- `workflow_input_batches` 保存每个 Linux workflow 唯一的生产输入状态、冻结版本、清单 SHA-256、错误与冻结时间。
- `workflow_input_items` 引用源 DWG/Excel、转换 Job attempt、派生 DXF 和 Drawing；同批次同 `file_id` 唯一。
- `dxf_classification_runs` 按 Job attempt 保存冻结清单摘要、分类器/schema 版本、汇总以及 JSON/CSV 文件引用；`(job_id, job_attempt)` 唯一。
- `dxf_classification_items` 逐图关联 Drawing、来源派生 DXF、分流 DXF、处置、零件类型、输出目录、诊断和证据；`(run_id, source_file_id)` 唯一。

删除 workflow 时阶段和 artifact 级联删除；artifact 的阶段被删除时 `stage_run_id` 置空。数据库没有 CHECK 强制 artifact 至少引用 file/result，也没有版本唯一约束或跨项目一致性约束；公开 API 通过资源权限、非空引用和幂等查重维护当前不变量，并在 Job 成功同步时自动挂接结果。完整状态机和边界见[Linux 生产工作流](../architecture/workflow.md)。

### 2.11 每日归档 -- 1 张表

`daily_archive_runs` 保存指定业务日期和可选 Bucket 范围的非破坏式归档运行。`source_file_ids_json` 与 `source_manifest_sha256` 冻结预检范围，`file_count`/`total_bytes` 及 Bucket/格式 JSON 保存可核对统计；`task_id` 关联 maintenance 投递；`archive_file_id` 和 `manifest_file_id` 分别引用 ZIP 与 JSON 清单的 `files` 行。状态为 `queued/running/succeeded/failed/cancelled`，失败保留稳定错误码、信息和结束时间。

`(actor_user_id, idempotency_key)` 唯一，防止同一浏览器提交重放。`(archive_date, scope_key, status)` 支持活动任务复用和历史查询；`source_manifest_sha256` 索引支持同清单成功结果复用。结果文件删除时外键置 NULL，运行与审计记录保留。文件字节仍由当前 Local/MinIO 后端保存，并通过两个独立 `file_transfers` 结算。

---

## 3. 实体关系总览

### 3.1 核心关系图

```
sys_users ──< sys_user_roles >── sys_roles ──< sys_role_permissions >── sys_permissions

sys_users ──< projects (owner_id)
sys_users ──< project_members ──> projects
sys_users ──< files (uploaded_by)
sys_users ──< drawing_versions (created_by)
sys_users ──< jobs (created_by)
sys_users ──< agent_runs (user_id)
sys_users ──< review_records (reviewer_id)
sys_users ──< audit_logs (actor_user_id)

projects ──< project_members
projects ──< drawings
projects ──< jobs
projects ──< agent_runs
projects ──< workflow_runs

drawings ──< drawing_versions ──> files
drawings ──< jobs
drawings ──< analysis_results
drawings ──< agent_runs

drawing_versions ──> drawings (current_version_id, 循环 FK)

files ──< drawing_versions
files ──< agent_runs (file_id, output_file_id)
files ──< analysis_results (result_file_id)

jobs ──< job_steps
jobs ──< analysis_results
jobs ──< workflow_stage_runs (可选 job_id + job_attempt)

analysis_results ──< review_records

agent_runs ──< agent_run_steps

workflow_runs ──< workflow_stage_runs
workflow_runs ──< workflow_artifacts
workflow_stage_runs ──< workflow_artifacts
files ──< workflow_artifacts
analysis_results ──< workflow_artifacts
```

### 3.2 外键级联行为

初始 schema 的 FK 使用默认 `NO ACTION`（MySQL 中为 `RESTRICT`）。Excel Final 加固迁移有意为 batch 子项/job 归属使用 `ON DELETE CASCADE`，并为可选源文件使用 `ON DELETE SET NULL`。新迁移必须说明每个非默认删除动作。

这意味着：
- **不能删除上传过文件、创建过作业或拥有项目的用户**，除非先置空或重新分配这些引用。
- **不能删除包含图纸的项目**，除非先归档或重新分配图纸。
- **不能删除包含版本的图纸**，除非先移除版本。
- **软删除** 是应用层的策略：将行标记为 `status = 'deleted'`（用户则为 `deleted_at = NOW()`），而非物理删除。

**设计理念:** 在企业级 CAD 处理平台中，审计追踪和处理历史的引用完整性比删除便利性更为重要。已删除用户的文件仍然是历史产物；已删除项目的审计日志必须保持可追溯。

### 3.3 循环 FK: drawings <-> drawing_versions

`drawings.current_version_id` 引用 `drawing_versions.id`，而 `drawing_versions.drawing_id` 引用 `drawings.id`。这是一个循环依赖，需要谨慎的 DDL 顺序：

**创建顺序（升级）:**
1. 创建 `drawings` 表，**不包含** `current_version_id` FK。
2. 创建 `drawing_versions` 表及其 FK。
3. 在两表都存在后，添加 FK 约束 `fk_drawings_current_version_id_drawing_versions`。

**删除顺序（降级）:**
1. 首先从 `drawings` 中删除循环 FK 约束。
2. 删除 `drawing_versions`。
3. 删除 `drawings`。

这在迁移 `40452ddd24e7` 中已正确处理。

### 3.4 JSON 列

以下表使用 MySQL 原生 JSON 类型（SQLite 回退为 TEXT）:

| 表 | JSON 列 | 典型内容 |
|---|---|---|
| `jobs` | `params_json` | 任务参数（图层过滤器、精度选项） |
| `job_steps` | `input_json`, `output_json` | 步骤 I/O 载荷 |
| `analysis_results` | `result_json` | 结构化分析输出（图层列表、实体计数等） |
| `agent_run_steps` | `arguments_json` | MCP 工具调用参数 |
| `audit_logs` | `before_json`, `after_json` | 审计追踪的资源快照 |
| `workflow_runs` | `config_json` | 流程配置快照；当前没有自动执行器消费 |
| `workflow_stage_runs` | `input_json`, `output_json` | 阶段输入/输出元数据 |
| `workflow_artifacts` | `metadata_json` | 产物补充信息 |

**查询 JSON 列:** MySQL 8.x 支持 `JSON_EXTRACT()`, `->` 和 `->>` 操作符。请使用这些操作符而非字符串匹配，以确保查询的可靠性。

---

## 4. 迁移管理

### 4.1 当前迁移版本

| 版本号 | 描述 | 日期 |
|---|---|---|
| `40452ddd24e7` | 初始迁移 -- 创建全部 17 张业务表，并显式处理循环 FK | 2026-07-03 |
| `b8f9e7d6c5a4` | TimestampMixin 修复 -- 用于缺少 `created_at`/`updated_at` 列的旧 MySQL 数据库的幂等迁移 | 2026-07-03 |
| `c3d2e1f0a9b8` | 修复 `audit_logs.resource_id` 类型 -- `Integer` 改为 `BigInteger`，与其他所有 ID 列保持一致 | 2026-07-04 |
| `53cd59adf848` | 添加 `files.batch_name` VARCHAR(128) 可空列 + 索引 `ix_files_batch_name` -- 支持 DXF/Excel 批量上传 | 2026-07-06 |
| `1d1696c7e854` | 新增 `agent_memory`、`token_blacklist`、`jobs.progress_data` 和 `sys_users.password_changed_at` | 2026-07-10 |
| `3480bd86ddc3` | 新增 Excel Final 批次/构件/零件表 | 2026-07-10 |
| `7f2a9c4e6b10` | 加固 Excel Final 外键和唯一约束 | 2026-07-10 |
| `8c61f4d2a9e7` | 新增 `jobs.attempt`，隔离重试代次 | 2026-07-11 |
| `a74c2e9f1d30` | 新增 `job_steps.attempt` 和 `(job_id, attempt)` 索引 | 2026-07-11 |
| `e4a1c7f2b930` | 新增工作流、顺序阶段和版本化流程产物表 | 2026-07-11 |
| `6d2f8a9c1b40` | 新增文件流转账本、一致性扫描表、文件软删除时间与对象位置唯一约束 | 2026-07-12 |
| `9c4e7b1a2d60` | 扩容 Excel Final 构件号、零件号、类型与规格字段 | 2026-07-13 |
| `d5e8a1c4b720` | 新增 `jobs.request_key` 与用户/任务/请求键唯一约束 | 2026-07-13 |
| `f7a9c2d4e610` | 新增生产输入批次与条目账本 | 2026-07-19 |
| `a9e4c7d2f610` | 新增 DXF 分类 run/item 账本，并为 Linux 流程插入独立分类阶段 | 2026-07-19 |
| `c1e9a4b7d220` | 新增 Worker 活动、控制平面事件与管理员运维消息账本 | 2026-07-19 |
| `e2f4b8c6a130` | 新增每日归档运行账本、冻结源清单与 ZIP/manifest 结果引用 | 2026-07-20 |
| `2b7e91d4c830` | 新增余料材质、导入批次/条目、正式余料与零件六张表 | 2026-07-22 |

线性迁移链以 `e2f4b8c6a130 → 2b7e91d4c830` 延伸；**`2b7e91d4c830` 是当前 head。**

### 4.2 如何创建新迁移

```bash
# 1. 修改对应 app/modules/<domain>/ 中由 model registry 装配的 SQLAlchemy 模型

# 2. 生成迁移脚本
cd backend
uv run alembic revision --autogenerate -m "change_description"

# 3. 检查生成的脚本（位于 migrations/versions/）
#    - 确认所有表/列变更是有意的
#    - 检查循环依赖的 FK 顺序
#    - 确保 downgrade() 正确反转 upgrade()

# 4. 针对临时 schema 测试迁移
bash scripts/db.sh migration-test

# 5. 应用到开发数据库
uv run alembic upgrade head
```

### 4.3 如何运行迁移

```bash
# 应用所有待处理的迁移
cd backend && uv run alembic upgrade head

# 应用到指定版本
uv run alembic upgrade 40452ddd24e7

# 回滚一个迁移
uv run alembic downgrade -1

# 回滚到指定版本
uv run alembic downgrade 40452ddd24e7

# 显示当前版本
uv run alembic current

# 显示迁移历史
uv run alembic history
```

### 4.4 CI 验证

`scripts/db.sh migration-test` 命令执行以下操作：

1. 创建一个**临时** MySQL schema（utf8mb4），并授予应用用户访问权限。
2. 通过限定作用域的 `DATABASE_URL`，对该空 schema 运行 `alembic upgrade head`。
3. 验证生成的 schema：断言全部 **42 张预期业务表** 存在，检查当前 Alembic head、attempt 列/索引相关类型、Excel Final 外键/唯一约束、生产输入、DXF 分类、控制平面、每日归档与余料库存账本、文件对象位置唯一约束、流转/扫描表，以及历史表后期回填的时间戳列。
4. 删除临时 schema（出错时也会通过 `EXIT` trap 删除）。

这验证了完整的迁移链能从零重建 schema，且 `TimestampMixin` 列保持一致。（它不执行降级路径。）

### 4.5 编写安全的迁移

**应该做到:**
- 始终编写能反转 `upgrade()` 的 `downgrade()`。
- 使用 `op.create_index()` 和 `op.drop_index()` 并指定显式索引名称。
- 对主键/外键列使用 `_pk_type()`，确保 SQLite 兼容性。
- 在应用到非生产数据库之前，先测试升级和降级。
- 通过延迟 FK 创建来处理循环 FK（参考 `40452ddd24e7`）。

**不应做:**
- 编写假设特定行数或 ID 值的数据迁移。
- 在未针对 MySQL 和 SQLite 语法进行测试的情况下使用原始 SQL。
- 在没有降级路径的情况下删除列（除非可以用默认值重新创建该列）。
- 在没有经过验证的备份的情况下对生产环境运行迁移。

---

## 5. 种子数据

### 5.1 哪些数据会被种子化

`backend/app/bootstrap/seed.py` 中的 `init_db()` 函数在应用启动时（在 `lifespan` 处理器中）自动调用。该函数是幂等的 -- 如果数据已存在，则跳过插入。

**7 个角色:**

> **说明：** 种子数据中 `name` 列为中文名称（如 `super_admin` 对应 "超级管理员"）。

| Code | Name | `is_system` |
|---|---|---|
| `super_admin` | Super Admin | True |
| `admin` | System Admin | True |
| `engineer` | Engineer | True |
| `reviewer` | Reviewer | True |
| `operator` | Operator | True |
| `viewer` | Viewer | True |
| `auditor` | Auditor | True |

**8 个权限:**

| Code | Resource | Action |
|---|---|---|
| `users:read` | users | read |
| `users:write` | users | write |
| `roles:write` | roles | write |
| `projects:write` | projects | write |
| `files:write` | files | write |
| `jobs:write` | jobs | write |
| `reviews:write` | reviews | write |
| `audit_logs:read` | audit_logs | read |

所有 8 个权限在种子数据阶段均被分配给 `super_admin` 角色。

**1 个超级管理员用户:**

| 字段 | 值 |
|---|---|
| `username` | 来自 `SUPER_ADMIN_USERNAME` 环境变量（默认: `admin`） |
| `password_hash` | `SUPER_ADMIN_PASSWORD` 环境变量的 Argon2id 哈希值 |
| `real_name` | 来自 `SUPER_ADMIN_REAL_NAME` 环境变量（默认: "系统管理员"） |
| `status` | `active` |
| `roles` | `[super_admin]` |

### 5.2 更改种子超级管理员

种子用户仅在不存在该用户名的用户时创建。以后修改 `SUPER_ADMIN_PASSWORD` 不会轮换已有账号。已认证时使用 `PATCH /api/v1/auth/password`；有权限的管理员可对允许的目标角色使用 password-reset request 端点。

不要删除种子 row 强制重建：用户可能被项目、文件、Job、版本、复核和审计记录引用。丢失 super-admin 的恢复需要有备份、独立审批且留下记录的受控数据库流程；仓库没有自动化该过程。

### 5.3 通过脚本手动种子化

```
bash scripts/db.sh init
```

这将运行完整的初始化过程：在需要时创建数据库、运行所有迁移、种子化角色/权限/超级管理员。

---

## 6. 数据保护边界

### 6.1 必要恢复集合

| 组件 | 必要内容 | 一致性风险 |
|---|---|---|
| MySQL `dwg_agent` | 42 张模型表、`alembic_version`、实际存在的 Celery runtime 表 | 只恢复 DB 会引用缺失对象或重放 broker row |
| 对象存储 | 每个已配置 original/derived/report/temp/DXF bucket 或 local root | 只恢复 storage 会产生孤儿字节 |
| `hardware_handbook` | schema/data 或独立管理的权威源 | Excel Final 重量查找可能变化或失败 |
| 配置/密钥 | Git 跟踪配置加加密 live value | `.env.docker` 禁止存入 Git |
| 证据 | revision、migration head、timestamp、checksum、恢复结果 | 未测试 dump 不是备份保证 |

### 6.2 本地 MySQL helper

```bash
bash scripts/db.sh backup /secure/path/dwg_agent_$(date +%Y%m%d_%H%M%S).sql.gz
```

该 helper 面向根 `.env` 配置的本地 MySQL endpoint。它不采集 MinIO、`hardware_handbook`、live secret 或协调一致性点。

维护辅助子命令：

- `bash scripts/db.sh clean`——清理 `migration-test` 崩溃残留的 `dwg_agent_migration_test_*` 临时库，并把退役的 SQLite 遗留文件 `var/app.db` 重命名归档（后端仍在运行则跳过）。
- `bash scripts/db.sh reap-storage [--dry-run] [--retention-days N] [--include-orphans]`——回收软删除文件的存储对象（见 §6.5）。

需要 `sudo mariadb` 的子命令（`setup-user`/`migration-test`/`reset`/`clean`）先经 `ensure_sudo` 预检：无 TTY 且 sudo 凭据未缓存时快速失败，而非永久挂在密码提示上（保护 CI/cron/测试）。

### 6.2.1 统一基础设施概览

`GET /api/v1/system/infrastructure`（仅管理员）在一个响应里同时呈现 MySQL、对象存储、磁盘容量和文件目录，便于共同查看：

- 数据库：引擎、业务库名、表数量、探测延迟、每进程连接池预算。
- 存储：后端、探测延迟，每个已配置桶一行，显示「MySQL 登记文件数 vs 实际对象数」。local 与 minio 后端**均**上报对象数（local 后端递归统计磁盘文件），对象数与登记数不一致的桶会被标记差额（疑似孤儿或断链）。
- 容量：local 后端上报存储卷的磁盘 total/used/free 字节；远程后端标记 `status = "unknown"`（容量不可直接测量）。
- 目录：可用文件总数、已登记字节数、Top 文件扩展名。
- 恢复：一致性规则（MySQL 元数据与对象字节是同一恢复集）和显式的 `automated_backup = false` 标志——平台无调度备份，此视图是可观测性手段，不构成备份保证。

桶计数是尽力而为，不枚举对象 key。用于快速健康检查和备份前 sanity check；恢复验证仍按 §6.4。

### 6.3 Compose 备份边界

Compose 不发布 MySQL/MinIO 宿主端口，也没有调度 backup service。`bash scripts/docker.sh backup DIR` 使用 `mysqldump --single-transaction` 导出 `dwg_agent` 与 `hardware_handbook`，并归档对象数据，最后生成 `SHA256SUMS` 和记录备份时间窗的 `BACKUP_WINDOW`。它没有停止 API/worker writer，因此 MySQL 事务快照和对象 tar 并非严格同一时点；业务关键备份应进入维护窗口或通过外部协调机制实现一致恢复点。

对象归档随 `.env.docker` 的 `STORAGE_BACKEND` 分支：`minio` 时归档 MinIO volume；`local` 时改为归档 `app_var/storage` 并发出警告（否则 local 后端对象将无备份）。`restore` 镜像该分支，并在结束时打印 `BACKUP_WINDOW`，提示对备份窗内创建的文件运行 `bash scripts/db.sh reap-storage --include-orphans` 核查一致性。该脚本仍不备份 `.env.docker`、镜像或证书。Compose 不支持 `worker-*` 这样的 service-name wildcard。

准确 Compose 命令和停止/启动顺序维护在[运维指南](../guides/operations.md)。数据库与对象必须作为一个恢复集合。

### 6.4 恢复要求

1. writer 停止时恢复到隔离或维护环境。
2. 从同一个声明恢复点恢复 MySQL 和全部对象 bucket。
3. 验证 `alembic current`、全部必要 table 和手册查询。
4. 把代表性恢复字节与 `files.sha256` 比较。
5. 启动 worker 前检查 queued/running Job 和 Celery broker row；恢复 message 可能重新投递工作。
6. 启动 stack，并执行认证 upload/process/SSE/download 工作流。

仓库当前没有自动 retention、point-in-time recovery 配置、backup encryption、调度 restore test 或 RPO/RTO 测量。这些是生产缺口，不是隐含平台能力。

### 6.5 存储对象回收（reaper）

软删除只把 `files.status` 置为 `deleted`，不删除底层存储对象；崩溃时"已写对象但事务回滚"也会留下无 DB 行的孤儿对象。二者都会让磁盘/桶单调增长，并使 §6.2.1 的登记字节数与真实占用逐渐偏离。

`scripts/storage/reap.py`（经稳定入口 `bash scripts/db.sh reap-storage` 调用）负责回收：

```bash
# 预览（默认 dry-run，不删任何东西）
bash scripts/db.sh reap-storage --retention-days 30
# 实际回收超过保留期的软删除对象
bash scripts/db.sh reap-storage --retention-days 30 --no-dry-run
# 额外扫描无 DB 行的孤儿对象（local 后端；仅报告，不删）
bash scripts/db.sh reap-storage --include-orphans
```

- Phase 1：删除 `status='deleted'` 且 `updated_at` 早于保留期的行的存储对象，成功后再硬删 DB 行；存储删除失败会计入 errors 且**不**孤儿删除 DB 行。
- Phase 2（可选）：枚举磁盘/桶对象，报告无匹配 `storage_key` 的孤儿——出于安全默认不自动删除。
- `--retention-days 0` 需 `REAP_CONFIRM=yes` 才实际执行，避免误删刚软删除的文件。

reaper 是手动/可调度的运维工具，不是自动 GC；平台不保证后台对账。
