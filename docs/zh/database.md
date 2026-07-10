# DWG-Agent 平台 -- 数据库设计与运维

> **受众:** 数据库管理员、平台运维人员、后端开发者
> **最后更新:** 2026-07-08
> **范围:** 引擎配置、表目录、实体关系、迁移管理、种子数据、备份策略

---

## 1. 引擎配置

### 1.1 运行时引擎: MySQL 8.x

生产/运行时数据库引擎为 MySQL 8.x，通过 `mysql+pymysql://` 访问。

**配置** (`backend/app/db/session.py`):

```python
engine_kwargs = {"pool_pre_ping": True}                       # 始终应用
if settings.database_url.startswith("mysql"):
    engine_kwargs.update({"pool_recycle": 3600, "pool_size": 10, "max_overflow": 20})
engine = create_engine(settings.database_url, **engine_kwargs)
# pool_args（recycle/size/overflow）仅在 DATABASE_URL 以 "mysql" 开头时生效
```

| 参数 | 值 | 说明 |
|---|---|---|
| `pool_size` | 10 | 持久连接的基本数量。适用于 4 个 gunicorn worker 并留有裕量。 |
| `max_overflow` | 20 | 峰值连接数 = pool_size + max_overflow = 30。提供突发容量，同时不会压垮 MySQL 的 `max_connections`。 |
| `pool_recycle` | 3600秒 (1小时) | 在 MySQL 默认的 `wait_timeout`（28800秒）之前回收连接。防止长时间空闲后出现陈旧连接导致错误。 |
| `pool_pre_ping` | True | 每次使用前检测连接的存活状态。每次检出增加一次额外查询，但可以消除 `MySQL server has gone away` 错误。 |

**各部署模式下的连接数:**

| 模式 | Workers | 最小连接数 | 最大连接数 |
|---|---|---|---|
| 本地开发 (uvicorn --reload) | 1 | 10 | 30 |
| Docker (gunicorn -w 4) | 4 | 40 | 120 |
| Docker (gunicorn -w 8) | 8 | 80 | 240 |

确保 MySQL `max_connections` 至少为 150（以 4-worker 部署计），并计入 Celery worker、Alembic 迁移和管理连接的需求。

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

应用 schema 共有 **19 张业务表**：初始迁移创建 17 张，迁移 `1d1696c7e854` 新增 `token_blacklist` 和 `agent_memory`。此外，Celery 的 SQL 传输与结果后端管理 `kombu_queue`、`kombu_message`、`celery_taskmeta`、`celery_tasksetmeta` 四张表，Alembic 管理 `alembic_version`。

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

**8 个种子权限**（完整列表见 security.md 第 2.6 节）。

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

### 2.3 文件管理 -- 1 张表

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
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**索引:** `ix_files_sha256`, `ix_files_storage_key`, `ix_files_batch_name`

**批量上传:** `batch_name` 将一起上传的文件分组（带 `batch_name` 查询参数的单文件上传，或以主名解压的 `.zip`），使 DXF→Excel 管道以及批量下载/删除端点能够一次性操作整组文件。未分组上传时其值为 `NULL`。

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

不可变版本记录。每次为图纸上传新的 DWG 修订版本都会创建一条新的版本行。

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
| `precision_level` | VARCHAR(32) | NOT NULL | `normal` / `high`（决定管道路由） |
| `pipeline` | VARCHAR(64) | NULLABLE | 分配的管道: `local_stub` / `dxf_open_source` / `dxf2dwg_open_source` / `dxf2excel` / `zwcad_worker` |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'queued', INDEXED | `pending` → `queued` → `running` → `succeeded`/`failed`/`cancelled` |
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

**作业生命周期状态:** `pending`（已创建，尚未入队）→ `queued`（已进入 MySQL 支撑的 Celery 队列）→ `running`（Worker 正在执行）→ `succeeded` / `failed` / `cancelled`。中间状态: `waiting_cad_worker`, `validating`, `need_review`。

#### `job_steps`

作业内部的细粒度执行步骤。

| 列 | 类型 | 约束 | 描述 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `job_id` | BIGINT | NOT NULL, FK → `jobs.id`, INDEXED | 父作业 |
| `step_name` | VARCHAR(128) | NOT NULL | 人类可读的步骤标签 |
| `worker_name` | VARCHAR(128) | NULLABLE | 执行该步骤的 Celery worker 主机名 |
| `status` | VARCHAR(32) | NOT NULL | `pending` / `running` / `succeeded` / `failed` |
| `input_json` | JSON | NULLABLE | 步骤输入参数 |
| `output_json` | JSON | NULLABLE | 步骤输出数据 |
| `error_message` | TEXT | NULLABLE | 步骤失败时的错误详情 |
| `started_at` | DATETIME | NULLABLE | |
| `finished_at` | DATETIME | NULLABLE | |

**索引:** `ix_job_steps_job_id`

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

所有重要操作的不可变审计追踪。

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

analysis_results ──< review_records

agent_runs ──< agent_run_steps
```

### 3.2 外键级联行为

**所有 FK 使用默认的 `NO ACTION`（MySQL 中为 RESTRICT）。** 迁移中未定义任何 `ON DELETE CASCADE` 或 `ON UPDATE CASCADE` 子句。

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

线性链为 `40452ddd24e7 → b8f9e7d6c5a4 → c3d2e1f0a9b8 → 53cd59adf848 → 1d1696c7e854`；**`1d1696c7e854` 是当前 head。**

### 4.2 如何创建新迁移

```bash
# 1. 修改 app/models/ 中的 SQLAlchemy 模型

# 2. 生成迁移脚本
cd backend
uv run alembic revision --autogenerate -m "变更描述"

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
3. 验证生成的 schema：断言全部 **17 张预期业务表** 均存在，且四张后期回填时间戳列的表（`project_members`、`drawing_versions`、`review_records`、`agent_run_steps`）现已携带 `created_at` 和 `updated_at`。
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

`backend/app/db/init_db.py` 中的 `init_db()` 函数在应用启动时（在 `lifespan` 处理器中）自动调用。该函数是幂等的 -- 如果数据已存在，则跳过插入。

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

种子用户仅在不存在该用户名的用户时创建。如需在更改密码后重新种子化：

```bash
# 1. 删除现有的超级管理员 (MySQL)
mysql -u dwg_user -p dwg_agent -e "DELETE FROM sys_user_roles WHERE user_id IN (SELECT id FROM sys_users WHERE username='admin');"
mysql -u dwg_user -p dwg_agent -e "DELETE FROM sys_users WHERE username='admin';"

# 2. 用新的 SUPER_ADMIN_PASSWORD 更新 .env

# 3. 重启应用 (init_db 在启动时运行)
```

### 5.3 通过脚本手动种子化

```
bash scripts/db.sh init
```

这将运行完整的初始化过程：在需要时创建数据库、运行所有迁移、种子化角色/权限/超级管理员。

---

## 6. 备份策略建议

### 6.1 需要备份的内容

| 组件 | 优先级 | 方法 |
|---|---|---|
| MySQL 数据库 (`dwg_agent`) | **关键** | `mysqldump` --single-transaction |
| 文件存储 (MinIO / 本地 `var/storage/`) | **关键** | `mc mirror` (MinIO) 或 `rsync` (本地) |
| Celery SQL 表及持久化运行状态 | 已包含在 MySQL 备份中 | 使用同一次 `mysqldump`，无需独立状态存储备份 |
| 配置文件 (`.env.docker`, `compose.yaml`) | 高 | Git + 加密备份 |
| Nginx 配置 (`infra/nginx/`) | 中 | Git |

### 6.2 MySQL 备份命令（推荐）

```bash
# 完整逻辑备份（通过 --single-transaction 实现一致性快照）
mysqldump -h 127.0.0.1 -u dwg_user -p \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --set-gtid-purged=OFF \
  dwg_agent | gzip > dwg_agent_$(date +%Y%m%d_%H%M%S).sql.gz
```

### 6.3 推荐的备份计划

| 频率 | 类型 | 保留策略 |
|---|---|---|
| 每日 | 完整 `mysqldump` | 7 天（滚动） |
| 每周 | 完整 `mysqldump` | 4 周（滚动） |
| 每月 | 完整 `mysqldump` | 12 个月 |
| 迁移前 | 手动完整备份 | 直到迁移验证完毕 |

### 6.4 MinIO / 文件存储备份

```bash
# MinIO 镜像到备份位置
mc mirror minio/dwg-original backup/dwg-original --watch
mc mirror minio/dwg-derived backup/dwg-derived --watch
mc mirror minio/dxf-original backup/dxf-original --watch
mc mirror minio/dxf-derived backup/dxf-derived --watch
mc mirror minio/dwg-reports backup/dwg-reports --watch

# 本地存储 rsync
rsync -avz --delete var/storage/ backup@backup-server:/backups/dwg-agent/storage/
```

### 6.5 恢复步骤

```bash
# 1. 停止应用（防止恢复期间写入）
docker compose stop backend-api worker-*

# 2. 恢复 MySQL
gunzip < dwg_agent_20260703_120000.sql.gz | mysql -h 127.0.0.1 -u dwg_user -p dwg_agent

# 3. 恢复文件（MinIO 示例）
mc mirror backup/dwg-original/ minio/dwg-original/ --overwrite

# 4. 验证数据完整性
mysql -u dwg_user -p dwg_agent -e "SELECT COUNT(*) FROM audit_logs; SELECT COUNT(*) FROM files;"

# 5. 重启应用
docker compose up -d
```

### 6.6 时间点恢复（高级）

对于需要 PITR 的生产部署：
- 启用 MySQL 二进制日志（`log_bin = ON` 在 `my.cnf` 中）。
- 将 binlog 与每日转储一起备份。
- 恢复步骤：先恢复最新的完整转储，然后重放 binlog 到所需时间点。

### 6.7 备份验证

- **自动化:** 每周对临时数据库执行一次恢复测试，验证表行数，检查 FK 完整性。
- **手动:** 存储恢复后抽查文件下载 -- 验证恢复后的文件与 `files.sha256` 数据库记录之间的 SHA-256 是否匹配。
