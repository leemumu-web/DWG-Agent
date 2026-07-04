# DWG-Agent 企业级 CAD 智能处理平台 —— 技术报告

> **版本：** v1.0  
> **日期：** 2026-07-04  
> **阶段：** Stage 1 完成，Stage 2 规划中  
> **规范依据：** `DWG-Agent企业平台技术规范.md` (v2.0, 1296 行, 25 节)

---

## 1. 项目概述

DWG-Agent 是一套面向公司内部生产流程的 **CAD 文件智能处理平台**。系统接受 DWG 图纸上传，管理项目/图纸/文件（具备完整 RBAC 权限控制），并最终通过 LLM Agent 将自然语言任务路由到两条处理流水线：

| 流水线 | 精度 | 技术路线 | 运行环境 |
|--------|------|----------|----------|
| DWG → DXF → Python (ezdxf) | 低/中 | 开源解析 | Linux Celery Worker |
| DWG → ZWCAD API (C#) | 高 | 原生 CAD API | Windows 独立节点 |

**当前阶段（Stage 1）：** 平台骨架已完整闭环。RESTful API、认证授权、文件管理、任务生命周期、审计日志全部可用。Agent 子系统、DXF 解析管线、CAD Worker 高精度管线为后续阶段。

**核心工程边界：** 前端不解析 DWG；FastAPI 不跑长任务；MySQL 不存文件本体；Agent 不负责安全边界；Docker 不强行容器化中望 CAD。

---

## 2. 物理架构

### 2.1 目标生产拓扑

```
┌───────────────────────────────────────────────────┐
│              Ubuntu 主服务器 (Docker Compose)        │
│  ┌──────┐  ┌──────────┐  ┌───────┐  ┌─────────┐   │
│  │Nginx │  │FastAPI    │  │MySQL  │  │Valkey   │   │
│  │:80   │→│:8000     │  │:3306 │  │:6379   │   │
│  └──────┘  └────┬─────┘  └───────┘  └─────────┘   │
│                 │ dispatch                          │
│  ┌──────────────▼──────────────────────────────┐   │
│  │ Celery Workers (agent/dxf/report/cad)        │   │
│  └──────────────────────────────────────────────┘   │
│                 │ MinIO :9000 (对象存储)             │
└─────────────────┼──────────────────────────────────┘
                  │ 内网 API Key / mTLS
┌─────────────────▼──────────────────────────────────┐
│         Windows CAD Worker 节点 (Stage 4)           │
│  ASP.NET Core + C# Plugin + ZWCAD API              │
└────────────────────────────────────────────────────┘
```

### 2.2 当前开发拓扑（Stage 1）

开发环境为单机 Linux（Arch Linux + Hyprland，Lenovo ThinkBook 16p G6 IAX，Core Ultra 9 275HX，30GB RAM）：

```
浏览器 (localhost:5173, Vite HMR)
  → FastAPI (localhost:8000, uvicorn --reload)
    → MySQL 8.x (localhost:3306)
    → Valkey 9.1 (localhost:6379, systemd)
    → Celery worker-report (report 队列)
    → 本地文件系统 (backend/var/storage/)
```

Docker Compose 配置（9 服务 + 2 profiles）已编写并验证，尚未用于生产。

---

## 3. 逻辑分层架构（六层）

代码库严格遵循六层架构。每层有明确职责边界，通过依赖注入（FastAPI `Depends`）组装：

```
┌──────────────────────────────────────────────────┐
│ 1. API 层       app/api/v1/      11 模块, 64 端点 │
│    路由、参数解析、认证依赖、响应封装                │
│    禁止: 业务逻辑、直接 DB 查询、文件 I/O            │
├──────────────────────────────────────────────────┤
│ 2. Schema 层    app/schemas/      10 模块          │
│    Pydantic v2 请求/响应校验 (from_attributes=True) │
│    禁止: 业务规则、DB 访问                         │
├──────────────────────────────────────────────────┤
│ 3. Service 层   app/services/     12 模块          │
│    业务编排、跨领域工作流                            │
│    禁止: 依赖 FastAPI Request、直接操作文件系统      │
├──────────────────────────────────────────────────┤
│ 4. Repository 层  app/repositories/  空占位        │
│    DB 读写封装（未来从 Service 提取）                │
├──────────────────────────────────────────────────┤
│ 5. Model 层     app/models/       10 文件, 17 表   │
│    SQLAlchemy 2.x ORM, TimestampMixin              │
│    禁止: 业务逻辑、校验逻辑                         │
├──────────────────────────────────────────────────┤
│ 6. Core 层      app/core/         8 模块           │
│    配置(pydantic-settings)、安全(Argon2id+JWT)、    │
│    权限(RBAC)、异常(AppHTTPException)、Redis客户端   │
└──────────────────────────────────────────────────┘

横向跨层:
  Agent 层 (3 占位, Stage 2)  MCP 层 (2 占位, Stage 2)
  Worker 层 (celery_app + 1 真任务 + 3 占位)
  Storage 层 (base + local + minio)
  Integration 层 (zwcad, 2 占位, Stage 4)
```

---

## 4. 关键技术决策

### 4.1 同步 API + Celery 异步边界

**决策：** FastAPI 使用同步 SQLAlchemy session（`sessionmaker`，`autoflush=False`，`expire_on_commit=False`）和同步 Redis 客户端。所有长耗时操作跨越 Celery 边界在 Worker 进程中执行。

**理由：** API 操作短小简单（毫秒级），CAD 处理可能耗时数分钟。显式的 Celery 边界确保请求延迟可控，即使 Stage 1 的假任务也遵循真实生产形态。在高并发场景（>200 req/s）需要更多 gunicorn workers；当前 4 workers + 120s timeout 已远超 Stage 1 需求。

### 4.2 MySQL 运行时 + SQLite 测试隔离

**决策：** 生产/开发环境使用 MySQL 8.x（`mysql+pymysql://`），测试使用 SQLite `:memory:` + `StaticPool` 实现每测试完全隔离。

**MySQL 连接池配置：**

| 参数 | 值 | 说明 |
|------|-----|------|
| `pool_size` | 10 | 基础持久连接数，匹配 4 gunicorn workers |
| `max_overflow` | 20 | 峰值连接 = pool_size + max_overflow = 30 |
| `pool_recycle` | 3600s | 在 MySQL `wait_timeout`(28800s) 前回收，防止陈旧连接 |
| `pool_pre_ping` | True | 每次检出前测试连接活性，消除 "MySQL server has gone away" |

**测试隔离机理：** `conftest.py` autouse fixture 为每个测试创建新的 SQLite `:memory:` 引擎（`StaticPool` 确保单连接复用），绑定所有表，通过 `app.dependency_overrides` 注入测试 session。测试不接触运行时 MySQL。唯一的 pragma 是 `foreign_keys=ON`（SQLite 默认不强制 FK）。WAL 模式和 `busy_timeout` 在内存数据库中无意义（单连接无并发）。

**MySQL vs SQLite 类型兼容：** 迁移中的 `_pk_type()` helper 输出 `BigInteger().with_variant(Integer(), "sqlite")`，确保 Alembic 迁移对两种引擎均可执行。

### 4.3 组件字段配置模式

**决策：** 配置使用 pydantic-settings（`extra="ignore"`），采用组件字段 + 计算属性模式，而非单体 URL 字符串：

```python
# Settings 字段（从 .env 读取）
mysql_host: str = "127.0.0.1"
mysql_port: int = 3306
mysql_user: str = "dwg_user"
mysql_password: str = ""

# 计算属性
@property
def mysql_url(self) -> str:
    # 自动 URL-encode 特殊字符密码
    ...
@property
def redis_url(self) -> str: ...
@property
def celery_broker_url(self) -> str:  # 自动跟随 REDIS_PASSWORD
@property
def celery_result_backend(self) -> str: ...
```

**理由：** Docker Compose 可分别覆盖单个组件（如 `MYSQL_HOST=mysql`），无需重建完整 URL。Celery broker/result URL 从同一组 Redis 字段派生，消除配置漂移。

### 4.4 特性开关（Feature Flags）

三个布尔开关控制后续阶段功能，默认全部 `false`：

| 开关 | 默认值 | 禁用时行为 | 激活阶段 |
|------|--------|-----------|----------|
| `AGENT_ENABLED` | `false` | 4 个 agent-runs 端点返回 503 `AGENT_DISABLED` | Stage 2 |
| `DXF_PIPELINE_ENABLED` | `false` | DXF 管线选择不可用 | Stage 3 |
| `CAD_WORKER_ENABLED` | `false` | CAD Worker 调度不可用 | Stage 4 |

开关支持分阶段上线和紧急回滚（设回 `false` 即可恢复 503）。

### 4.5 JWT + jti 黑名单 Token 管理

**决策：** 采用双 token 机制——短期 access token（30min）+ 长期 refresh token（14d），登出时通过 Redis 黑名单吊销。

**Token 结构（JWT HS256）：**

```json
{"sub": "1", "username": "admin", "jti": "UUID4", "iat": ..., "exp": ..., "type": "access|refresh"}
```

- `jti`(JWT ID)：UUID4，唯一标识每个 token，用于黑名单
- `type`：区分 access/refresh，`get_current_user` 拒绝 refresh 类型
- access_token 存 `sessionStorage`（非 `localStorage`，缓解 XSS 窃取）
- refresh_token 存 `HttpOnly; SameSite=Lax` Cookie（生产环境加 `Secure`）
- **登出黑名单：** Redis key `blacklist:jti:{jti}`，TTL = token 剩余有效期，自动过期无需清理
- **Redis 不可用降级：** 黑名单写入静默跳过（fail-open for availability），日志记录警告
- **密码变更全设备登出：** token 签发时间早于密码修改时间 → 401 `TOKEN_REVOKED`
- **已知差距：** 未实现 refresh token rotation（计划后续版本）

### 4.6 时序攻击防御（H1 修复）

**决策：** `authenticate_user()` 在用户不存在或已禁用时，仍执行一次完整的 Argon2id 哈希验证——对比预计算的 dummy hash。

```python
_DUMMY_VERIFY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHRzb21lc2FsdHNvbQ$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
# m=65536, t=3, p=4 — 与 PasswordHash.recommended() 参数一致
```

**理由：** 若不使用 dummy hash，不存在用户立即返回（快），存在用户触发 Argon2id（慢，~40x 时间差）。攻击者可通过响应时间差枚举有效用户名。Dummy hash 消耗相同 CPU 时间，消除时序侧信道。

### 4.7 原子操作与乐观并发控制

**决策：** 用户状态变更使用 `UPDATE WHERE + rowcount` 单次操作，避免 SELECT→UPDATE 的 TOCTOU 窗口：

```python
def transition_user_status(db, user_id, to_status, *, set_deleted_at=False) -> bool:
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.status != DELETED)
        .values(**values)
    )
    return result.rowcount > 0  # 调用方决定是否 raise
```

关键保护：
- `WHERE status != DELETED`：已软删除用户不可被修改
- 返回 `bool` 而非 raise：调用方决定错误语义
- `get_user_or_404(for_update=True)`：必要时使用 `SELECT ... FOR UPDATE` 悲观行锁
- `IntegrityError` 捕获：用户名重复被转换为 409 `USERNAME_EXISTS`

### 4.8 级联项目状态检查

**决策：** `require_active_project()` 嵌入在 `require_project_member()` 内部，而非在每个路由中独立调用。项目软删除后，所有成员访问级联返回 404（而非 403）。

**理由：** 图纸、文件、任务等所有资源都限定在项目内。在成员检查中一次性验证项目活跃状态，意味着没有路由能遗漏检查。删除的项目对所有下游访问级联清除。

### 4.9 文件存储双后端抽象

**决策：** `AbstractStorageBackend` 定义统一接口（`save`/`retrieve`/`delete`），通过 `STORAGE_BACKEND=local|minio` 切换：

- `LocalFileStorage`：开发环境，`backend/var/storage/`，相对路径
- `MinioStorage`：Docker 部署，S3 兼容 API，4 个 bucket（`dwg-original`/`dwg-derived`/`dwg-reports`/`dwg-temp`）

存储路径由后端生成（`uploads/{uuid4().hex}{ext}`），`original_name` 仅作展示字段。文件上传经过 5 层校验链：扩展名 → MIME → DWG header（AC1012-AC1032）→ 大小（≥1024B, ≤512MB）→ SHA-256 + MD5 流式哈希。

---

## 5. 实现详情（按子系统）

### 5.1 技术栈（精准版本）

| 层 | 技术 | 版本 |
|----|------|------|
| 前端框架 | React | 19.2.7 |
| UI 组件库 | Ant Design | 6.5.0 |
| 前端构建 | Vite + TypeScript | — |
| 状态管理 | Zustand + TanStack Query | — |
| 后端语言 | Python | 3.12 (`>=3.12,<3.13`) |
| 包管理 | uv | latest (lock 已提交) |
| API 框架 | FastAPI | >=0.115.0 |
| 数据校验 | Pydantic | v2 (`from_attributes=True`) |
| 配置 | pydantic-settings | `extra="ignore"` |
| ORM | SQLAlchemy | >=2.0.30 (同步 session) |
| 密码哈希 | pwdlib (Argon2id) | `PasswordHash.recommended()` |
| 数据库 | MySQL | 8.x |
| 缓存/队列 | Valkey (Redis 兼容) | 9.x |
| 对象存储 | MinIO | latest (S3 兼容) |
| 异步任务 | Celery | Redis broker/result backend |
| 网关 | Nginx | 1.27-alpine (非 root) |
| 容器编排 | Docker Compose | v2, 9 服务 + 2 profiles |
| 代码质量 | ruff | `select = ["E","F","I","UP","B","W"]` |

### 5.2 后端规模

| 指标 | 数值 |
|------|------|
| 后端 Python 代码 | 5,272 行（`app/`） |
| API 路由模块 | 11 个（`app/api/v1/`） |
| API 端点 | 64 个（63 在 `/api/v1` + 1 健康检查 `/health`） |
| Pydantic Schema 模块 | 10 个（513 行） |
| 业务 Service 模块 | 12 个（~1,191 行） |
| ORM Model 文件 | 10 个（401 行, 17 张表） |
| Core 基础模块 | 8 个（438 行） |
| SQLite 测试隔离 | `:memory:` + `StaticPool`, conftest autouse |
| Redis 测试方案 | FakeRedis (419 tests) + Real Redis integration (13 tests) |

### 5.3 前端规模

| 指标 | 数值 |
|------|------|
| 页面模块 | 10 个（features/） |
| API 客户端模块 | 12 个（`src/api/`, 11 + client.ts） |
| 共享组件 | 8 个（2 已实现：FileUpload, PermissionGuard；6 Stage 2+ 占位） |
| TypeScript 类型文件 | 9 个 |
| Token 存储 | `sessionStorage`（非 `localStorage`） |
| 权限控制 | 三层：路由级、菜单级、组件/按钮级 |

### 5.4 API 端点总览

| 模块 | 端点数 | 核心功能 |
|------|--------|----------|
| Auth | 5 | 登录/登出/刷新/自查询/改密 |
| Users | 11 | CRUD + 角色分配 + 密码重置 + 禁用/启用 |
| Roles | 4 | CRUD roles + 权限分配 |
| Projects | 9 | CRUD + 成员管理（4 项目角色） |
| Files | 6 | 上传（DWG 校验）/ 列表 / 下载（HMAC 签名 URL, TTL=300s）/ 删除 |
| Drawings | 8 | CRUD + 版本管理（version_no 自增）/ 预览 |
| Jobs | 9 | 创建 / 取消 / 重试 / 步骤 / 日志 / SSE(占位) / 结果 |
| Results | 4 | 详情 / 下载 / 提交复核 / 复核历史 |
| Reviews | 1 | 待复核列表 |
| Audit | 2 | 列表（最近 200）/ 详情（super_admin + auditor 专属） |
| Agent | 4 | agent-runs CRUD + agent-tools（均返回 503 `AGENT_DISABLED`） |

统一响应格式：`{"data": ..., "meta": {"request_id": ..., "timestamp": ...}}`。列表增加 `"pagination": {"page", "page_size", "total", "total_pages"}`。错误：`{"error": {"code": "ERROR_CODE", "message": ..., "details": {}}, "meta": {...}}`。

完整端点参考见 `docs/api.md`。

### 5.5 数据库设计

#### 引擎配置

```python
engine = create_engine(
    settings.mysql_url,  # mysql+pymysql://dwg_user@host:3306/dwg_agent
    pool_pre_ping=True, pool_recycle=3600, pool_size=10, max_overflow=20,
)
```

#### 表目录（17 张表，3 次 Alembic 迁移）

| # | 表 | 用途 |
|---|-----|------|
| 1 | `sys_users` | 用户身份（username UNIQUE, Argon2id password_hash, status, soft-delete） |
| 2 | `sys_roles` | 角色定义（code UNIQUE, is_system 保护） |
| 3 | `sys_permissions` | 权限定义（resource + action, 8 条种子数据） |
| 4 | `sys_user_roles` | 用户↔角色 M2M（Table() 关联） |
| 5 | `sys_role_permissions` | 角色↔权限 M2M（Table() 关联） |
| 6 | `projects` | 项目容器（code UNIQUE, owner_id, status） |
| 7 | `project_members` | 项目成员（project_role, UNIQUE(project_id, user_id)） |
| 8 | `files` | 文件元数据（storage_key, sha256, bucket, status） |
| 9 | `drawings` | 图纸记录（current_version_id 循环 FK） |
| 10 | `drawing_versions` | 版本记录（version_no 自增） |
| 11 | `jobs` | 处理任务（task_type, pipeline, status, params_json, error_code） |
| 12 | `job_steps` | 任务步骤（step_name, worker_name, input/output_json） |
| 13 | `agent_runs` | Agent 执行记录（session_id, task, status, answer） |
| 14 | `agent_run_steps` | Agent 步骤（step_type, tool_name, arguments_json） |
| 15 | `analysis_results` | 分析结果（result_type, result_json, confidence DECIMAL(5,4)） |
| 16 | `review_records` | 复核记录（decision: approved/rejected/needs_revision） |
| 17 | `audit_logs` | 审计日志（action, resource_type, before/after_json, ip_address） |

所有 FK 使用默认 `NO ACTION`（MySQL RESTRICT），禁用级联删除。应用层通过软删除（`deleted_at` + `status = 'deleted'`）保留审计引用完整性。

`drawings.current_version_id` → `drawing_versions.id` 形成循环 FK。迁移通过延迟 FK 创建处理：先建 `drawings`（不含此 FK），再建 `drawing_versions`，最后通过 `op.create_foreign_key()` 加回。

**Alembic 迁移历史：**
1. `40452ddd24e7` — 初始：创建全部 17 张表
2. `b8f9e7d6c5a4` — TimestampMixin 修复：为遗漏列回填 `created_at`/`updated_at`
3. `c3d2e1f0a9b8` — resource_id 类型修复：`audit_logs.resource_id` 从 `Integer` 改为 `BigInteger`

`scripts/db.sh migration-test` 通过创建临时数据库→全量迁移→回滚→重新迁移来验证端到端迁移链。

### 5.6 认证与授权（RBAC）

#### 密码安全

- 哈希：Argon2id, `m=65536, t=3, p=4`（`pwdlib.PasswordHash.recommended()`）
- 最小长度：12 字符
- 复杂度：必须包含大写字母 + 小写字母 + 数字
- 常见密码黑名单：16 个常见/已泄露密码被拒绝
- 管理员重置密码：写入审计日志

#### 全局角色（7 个，`is_system=True` 种子保护）

| 角色 | 能力 |
|------|------|
| `super_admin` | 绕过所有权限检查 |
| `admin` | 用户管理、全局项目查看 |
| `engineer` | 文件上传、任务创建（项目内） |
| `reviewer` | 审核分析结果 |
| `operator` | 执行分配任务 |
| `viewer` | 只读（项目内） |
| `auditor` | 审计日志只读 |

#### 项目角色（4 个）

| 角色 | 能力 |
|------|------|
| `project_owner` | 完全控制：成员、文件、图纸、任务、结果 |
| `project_engineer` | 上传、创建任务、查看结果 |
| `project_reviewer` | 审核结果 |
| `project_viewer` | 只读 |

#### 权限裁决顺序

```
已认证？ → 否 → 401
用户活跃(status=active)？ → 否 → 401
super_admin？ → 是 → 全部允许
admin 全局权限？ → 是 → 允许
项目成员？ → 否 → 403
项目角色允许此操作？ → 否 → 403
→ 允许
```

#### 关键保护

- `require_project_member()` 内嵌 `require_active_project()`：软删除项目 → 所有成员 404
- 非 `super_admin` 不能管理 `super_admin` 账户
- 不能删除/禁用自己
- 角色分配：只有 `super_admin` 可以授予 `super_admin` 角色

### 5.7 Celery 异步任务

**Celery 应用：** Redis broker + result backend，queue routing，测试 eager mode。

**当前实现：**

| 任务文件 | 状态 | 说明 |
|----------|------|------|
| `tasks_report.py` | **已实现** | `run_stub_job`：queued→running→succeeded，写入 job_steps + analysis_results |
| `tasks_agent.py` | 占位 | Stage 2 |
| `tasks_dxf.py` | 占位 | Stage 3 |
| `tasks_cad.py` | 占位 | Stage 4 |

**任务状态机：**

```
pending → queued → running → validating → need_review → succeeded
  │         │         │            │
  │         │         │            └──→ waiting_cad_worker → validating → ...
  │         │         │
  └─ (auto)          ├──→ cancelled (仅 queued/running 可取消)
                     └──→ failed (仅 running/validating 可失败)
                              │
                              └──→ retry → queued (仅 failed/cancelled 可重试)
```

Stage 1 的 Celery worker-report 任务通过 `scripts/start-dev.sh` 自动启动（本地 pidfile），Docker Compose 中作为默认服务运行。

### 5.8 文件上传安全

**校验链（按顺序执行）：**
1. 扩展名白名单：仅 `.dwg`（`ALLOWED_UPLOAD_EXTENSIONS = {".dwg"}`）
2. MIME 类型检查：8 种 DWG 相关 MIME 类型
3. DWG 文件头验证：首 6 字节必须匹配 `AC1012`–`AC1032`（AutoCAD R13–2018+）
4. 大小强制：最小 1,024 字节，最大 `MAX_UPLOAD_SIZE_MB`（默认 512 MiB）
5. 流式哈希：SHA-256 + MD5 在分块读取时计算

**下载安全：**
- HMAC-SHA256 签名 URL，TTL = 300 秒
- 签名 = `hmac.new(secret, f"{file_id}:{expires}", sha256).hexdigest()`
- 下载端点额外要求认证（URL 非独立 capability token，defense-in-depth）
- 下载前校验：上传者/管理员/项目成员

**路径穿越防护：** `ensure_within_root(root, candidate)` 解析两个路径为绝对路径，检查候选路径是否以根路径为前缀。任何 `../` 或符号链接逃逸 → 400 `INVALID_STORAGE_PATH`。

---

## 6. 安全架构

### 6.1 渗透测试修复情况

经过多轮安全审计，**18 项发现中 12 项已修复，6 项为已知设计取舍：**

| ID | 发现 | 严重程度 | 状态 | 修复方式 |
|----|------|----------|------|----------|
| H1 | 登录时序侧信道（40x 时间差） | **Critical** | ✅ | Dummy Argon2id hash，用户不存在时同样执行完整验证 |
| H6 | 用户名注入（空格/Unicode） | **High** | ✅ | Pattern `^[a-zA-Z0-9_.@-]+$` |
| BUG-1 | UserCreate 批量分配 role_codes | **High** | ✅ | 移除字段，独立 RBAC 端点 |
| BUG-2 | 弱密码策略 | **High** | ✅ | 12 字符 + 大写+小写+数字 + 常见密码黑名单 |
| BUG-3 | real_name HTML 注入 | **Medium** | ✅ | HTML 标签正则拒绝 `<[a-zA-Z/]` |
| BUG-4 | 健康端点信息泄露 | **Low** | ✅ | 简化为 `{"status":"ok"}` |
| BUG-5 | DWG 大小校验不足 | **Medium** | ✅ | 1024 字节最小值 + header 验证 |
| BUG-6 | 竞态条件导致 500 traceback 泄露 | **Medium** | ✅ | IntegrityError → 409, catch-all 返回通用消息 |
| BUG-7 | 软删除级联 — 已删除项目在文件列表中可见 | **Medium** | ✅ | `require_active_project()` 嵌入 `require_project_member()` |
| BUG-8 | task_type 无校验 | **Low** | ✅ | Pattern `^[a-z][a-z0-9_]+$` |
| BUG-9 | 重试无状态守卫 | **Medium** | ✅ | 仅 `failed`/`cancelled` 可重试 |
| BUG-12 | 无自更新端点 | **Low** | ✅ | `PATCH /users/me` 添加 |
| BUG-10 | 纳秒级 TOCTOU | — | 设计取舍 | 风险可忽略 |
| BUG-11 | 无法复现 | — | 设计取舍 | 已归档监控 |
| BUG-13/14 | 参数不存在于当前 API | — | 设计取舍 | — |
| C1/C2 | JWT 密钥强度 / 端口暴露 | — | 部署层面 | 生产部署清单覆盖 |

### 6.2 已知安全差距

| 差距 | 当前缓解 | 计划 |
|------|----------|------|
| 无 refresh token rotation | 短期 access token 限制暴露窗口 | 后续实现 OAuth 2.0 标准 rotation |
| 无登录限流 | Nginx `limit_req_zone` 可配置 | 生产部署时启用 |
| 审计日志无限增长 | — | 定期归档策略 |
| 签名 URL 非独立 capability token | 下载端点额外认证 | defense-in-depth，评估是否需改为真正自包含 |

### 6.3 审计日志覆盖

30+ 操作类型全部写入 `audit_logs` 表（不可变，无 API 修改/删除）：登录/登出、用户 CRUD、角色权限变更、项目变更、成员变更、文件上传/删除/下载、任务创建/取消/重试、结果审核。每条记录包含操作者、IP、User-Agent、操作前后快照（`before_json`/`after_json`）。

---

## 7. Redis / Valkey 基础设施

### 7.1 服务端

Valkey 9.1（Redis 兼容分支）。本地开发通过 systemd `redis.service` 管理，无密码。Docker 部署使用 `valkey/valkey:9.0-alpine` + AOF + `requirepass` + maxmemory 256mb（`infra/redis/redis.conf`）。

### 7.2 客户端（`app/core/redis_client.py`, 80 行）

- 同步 `redis-py` 5.x + `hiredis` 解析器
- 惰性初始化：`get_redis()` 首次调用时创建连接池
- Redis 不可用时返回 `None`（不崩溃），所有调用方处理此情况
- `close_redis()` 在 FastAPI shutdown 时调用（lifespan）

### 7.3 使用模式

| 服务 | Key 模式 | 数据 | TTL | 当前状态 |
|------|----------|------|-----|----------|
| Token 黑名单 | `blacklist:jti:{jti}` | "1" | token 剩余有效期 | **活跃** |
| Agent 记忆 | `agent:memory:{session_id}` | JSON 消息列表 | 7,200s | 基础设施就绪（已测试，运行时未调用） |
| 缓存 | `cache:{namespace}:{key}` | 任意 | 可变 | 基础设施就绪（已测试，运行时未调用） |
| Celery broker | `redis://.../0` | 任务消息 | — | **活跃** |
| Celery results | `redis://.../1` | 任务结果 | — | **活跃** |

### 7.4 双层测试验证

| 层 | 方案 | 覆盖 |
|----|------|------|
| FakeRedis | `fakeredis[lua]`, conftest autouse monkeypatch | 419 tests, 零外部依赖 |
| Real Redis | `test_redis_real.py`, 真实 Valkey 集成 | 13 tests, Redis 不可用时 `pytest.skip` |

---

## 8. 测试体系

### 8.1 测试规模

```bash
cd backend
uv run ruff check app tests    # All checks passed (0 errors)
uv run pytest -q               # 432 passed, 0 failed (61s)
```

### 8.2 测试架构

**三层隔离：**
1. **数据库隔离：** 每个测试独立 SQLite `:memory:` + `StaticPool`，`conftest.py` autouse fixture 自动建表，覆盖 `get_db` 依赖
2. **Redis 隔离：** FakeRedis autouse monkeypatch（见 7.4）
3. **HTTP 层：** `fastapi.testclient.TestClient` 进程内测试，不产生真实 HTTP 请求

**无外部依赖：** 全部 432 测试在任何安装了 Python 3.12 依赖的环境运行，不需要 MySQL 或 Redis 服务器。

### 8.3 测试文件分类（24 文件）

| 类别 | 文件 | 覆盖焦点 |
|------|------|----------|
| API 回归 | `test_api_regressions.py` | 64 端点返回正确状态码和格式 |
| 安全边界 | `test_security_boundaries.py`, `test_rbac_deep.py` | 认证强制、RBAC 强制、路径穿越防御、跨项目隔离 |
| Token 生命周期 | `test_token_lifecycle.py` | 登录、刷新、黑名单、过期、jti 验证 |
| Redis 栈 | `test_redis_client.py`, `test_redis_memory.py`, `test_cache_service.py`, `test_redis_real.py` | 客户端初始化、记忆 TTL、缓存降级、真实集成 |
| 配置 | `test_config.py` | MySQL/Redis URL 组装、组件字段、feature flags |
| DB session | `test_db_session.py` | 引擎创建、健康检查、WAL pragmas |
| 边界条件 | `test_edge_cases.py`, `test_rigorous.py`, `test_deep_verify.py` | 并发操作、大负载、Unicode、null 处理 |
| Service 层 | `test_service_layer.py` | 业务逻辑单元测试（user, file, project, auth） |
| Stage 1 边界 | `test_stage1_boundaries.py` | Agent 503、Celery 假任务、feature flag 门控 |
| 端到端 | `test_smoke_flow.py`, `test_job_lifecycle.py` | 注册→登录→上传→任务→结果完整流程 |
| 部署 | `test_celery_minio_deployment.py` | Celery worker 健康、MinIO bucket 操作 |
| 安全修复回归 | `test_cross_audit_fixes.py` | 渗透测试 bug 回归（31 test functions） |
| 脚本 | `test_scripts.py` | Shell 脚本语法、lib.sh 函数、db.sh 操作 |
| 迁移 | `test_migrations.py` | Alembic 版本数、表存在性 |
| Compose | `test_compose.py` | YAML 解析、服务数、必需服务存在性 |

### 8.4 质量门

```bash
cd backend
uv run ruff check app tests    # 必须 0 错误（规则集: E, F, I, UP, B, W）
uv run pytest -q               # 必须全部通过（当前 432）
```

前置提交检查：ruff 0 错误 + 432 passed + `npx tsc --noEmit`（前端类型检查）+ `npm run build`（前端构建）。

---

## 9. 部署与运维

### 9.1 Docker Compose 架构

**9 个服务，2 个 profiles：**

| 服务 | 镜像 | Profile | 健康检查 |
|------|------|---------|----------|
| `nginx` | `nginxinc/nginx-unprivileged:1.27-alpine` | — | depends_on backend-api healthy |
| `backend-api` | 自构建（`backend/Dockerfile`） | — | `curl /health` every 10s |
| `mysql` | `oracle/mysql-community-server:8.4` | — | `mysqladmin ping` every 10s |
| `redis` | `valkey/valkey:9.0-alpine` | — | `redis-cli ping` every 10s |
| `minio` | `minio/minio:latest` | — | `curl /minio/health/live` |
| `worker-report` | 自构建 | —（默认） | `celery inspect ping` every 10s |
| `worker-agent` | 自构建 | `workers` | `celery inspect ping` every 10s |
| `worker-dxf` | 自构建 | `workers` | `celery inspect ping` every 10s |
| `flower` | 自构建 | `monitoring` | `curl :5555` every 10s |

```bash
docker compose up -d                                          # 核心服务
docker compose --profile workers --profile monitoring up -d   # 全量
```

**网络：** `public`（Nginx :80/:443 对外）+ `internal`（`internal: true`，后端服务不对外暴露）。

**卷（持久化）：** `mysql_data`, `redis_data`, `minio_data`，`docker compose down` 不删除，`-v` 完全重置。

### 9.2 Dockerfile（后端）

- 多阶段构建，基础镜像 `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
- 非 root 用户 `appuser`（uid 1000）
- `HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -f http://localhost:8000/health || exit 1`
- CMD: `alembic upgrade head && exec gunicorn --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120`

### 9.3 本地开发脚本（6 个）

| 脚本 | 功能 |
|------|------|
| `lib.sh` | 共享函数：端口检测、进程管理、彩色输出、环境加载 |
| `db.sh` | MySQL 管理（10 个子命令）：启动、建库建用户、初始化、迁移、迁移测试、状态检查、Shell |
| `start-dev.sh` | 一键启动：MySQL + Redis + Celery worker + 后端 uvicorn + 前端 Vite HMR |
| `start-all.sh` | 全栈启动（含 Nginx 网关，`--rebuild` 强制前端构建） |
| `stop-all.sh` | 优雅停止：Nginx → 后端 → Celery worker |
| `status.sh` | 健康聚合：MySQL + Redis + Celery + 后端 + Nginx 状态一览 |

### 9.4 基础设施验证

`infra/verify.sh` 执行 6 段全面检查：Nginx 配置语法、Docker Compose 服务/镜像/卷/健康检查、Dockerfile 多阶段/非 root/HEALTHCHECK、MySQL 集成（17 表/TimestampMixin/种子数据/用户权限）、文件完整性、死代码检查。

### 9.5 配置管理

**两套环境模板：**
- `.env.example`（本地开发，已提交）
- `.env.docker.example`（Docker 部署，已提交）

**8 个 `CHANGE_ME_*` 占位符必须在部署前替换**（MySQL/Redis/MinIO 密码、JWT 密钥、Super Admin 密码）。

禁止提交 `.env` / `.env.docker`（已 gitignore）。

---

## 10. 代码规范

### 10.1 Python

- `from __future__ import annotations` 在每个 `.py` 文件首行（`__init__.py` 和占位文件除外）
- 类型提示：`X | None`（非 `Optional[X]`），`list[X]`（非 `List[X]`）
- 导入：`from collections.abc import Callable`（非 `from typing import Callable`）
- ruff 规则集：`E, F, I, UP, B, W`，排除 `B008, E501, UP037`，行长限制 100
- 禁止 `assert False`（ruff B011），使用 `raise AssertionError("message")`
- 所有业务端点：`current_user: CurrentUser`（无 `= None` 默认值）
- 业务错误使用 `AppHTTPException`（来自 `app.core.exceptions`），不用裸 `HTTPException`
- 文件路径必须经过 `app/utils/path_utils.py` 的 `ensure_within_root()` 校验
- 依赖添加必须通过 `uv add` / `uv remove`（`uv.lock` 已提交，不可手改 `pyproject.toml`）

### 10.2 TypeScript (前端)

- 所有依赖精确版本，禁止 `"latest"`（`package-lock.json` 已提交）
- API 调用全部通过 `src/api/` 封装（`client.ts` Axios 实例自动注入 Authorization）
- 禁止 `any` 类型；API 响应必须定义 TypeScript 接口
- API 基地址：开发时为空（Vite proxy）或指向 `http://127.0.0.1:8000`；Docker 为空（Nginx 反代）
- Token 存储：`sessionStorage`（非 `localStorage`）

---

## 11. 当前状态与路线图

### Stage 1：平台骨架闭环 —— ✅ 已完成

**交付清单：**
- [x] RESTful API 全闭环（64 端点，11 路由模块，统一响应格式）
- [x] 7 全局角色 + 4 项目角色 RBAC，5 表权限模型
- [x] JWT 双 token 认证（access 30min + refresh 14d）+ jti 黑名单
- [x] Argon2id 密码哈希（12 字符、复杂度、黑名单）+ 时序攻击防御
- [x] DWG 文件上传（5 层校验链）+ HMAC 签名下载
- [x] 任务生命周期管理（queued→running→succeeded，含 job_steps）
- [x] Celery worker-report 假任务（验证 dispatch→状态变迁→结果写入→审计全链路）
- [x] 12 项安全渗透发现修复（18 项中 12 项已修复）
- [x] 审计日志不可变（30+ 操作类型，`before_json`/`after_json` 快照）
- [x] 双存储后端（LocalFileStorage + MinioStorage，`STORAGE_BACKEND` 切换）
- [x] 特性开关体系（3 个 flag，支持分阶段上线和紧急回滚）
- [x] Docker Compose 9 服务 + 2 profiles（workers + monitoring）
- [x] 3 次 Alembic 迁移（初始 17 表 + TimestampMixin 修复 + resource_id 类型修复）
- [x] 种子数据自举（7 角色 + 8 权限 + 1 Super Admin，幂等）
- [x] React 19 前端（10 页面、12 API 客户端模块、三层权限控制）
- [x] 432 测试（24 文件，pytest + FakeRedis + SQLite 隔离，零外部依赖）
- [x] 6 个开发运维脚本（lib.sh / db.sh / start-dev.sh / start-all.sh / stop-all.sh / status.sh）
- [x] `infra/verify.sh` 基础设施全面验证（6 段检查，Nginx/Compose/Dockerfile/MySQL/文件/死代码）
- [x] ruff 0 错误，全部测试通过

### Stage 2：Agent 子系统 —— 下一阶段

**核心交付：**
- LangGraph `create_react_agent` + DeepSeek LLM (`temperature=0`)
- MCP Client（`connect/disconnect/list_tools/call_tool`，失败返回 503 不崩溃）
- MCP-to-LangChain 工具适配（工具名/描述/参数从 MCP 定义自动派生）
- Celery agent 任务（Redis memory 读取历史 → Agent 执行 → 提取 answer + tool steps → 写回记忆）
- `/api/v1/agent-runs` 从 503 变为实时执行
- AgentSteps 前端组件（工具调用、推理步骤、最终回答）

**已就绪的基础设施（Stage 1 已完成，无需重复建设）：**
- Redis 记忆服务（`agent:memory:{session_id}`, TTL=7200s, max 20 msgs, 已测试）
- Redis 客户端（惰性初始化, 不可用时安全降级, 已测试）
- 缓存服务（`cache:{namespace}:{key}`, 已测试）
- Celery 应用（Redis broker/result backend, queue routing）
- Agent 端点（4 个，资源模型已定义，行为从 503 切换为真实执行）
- LLM 配置字段（`MODEL_NAME/MODEL_API_KEY/MODEL_BASE_URL`）
- MCP 配置字段（`MCP_CAD_COMMAND/MCP_CAD_ARGS`）
- 前端 AgentRun Schema（TypeScript 类型已定义）

### Stage 3-6 概要

| Stage | 名称 | 关键交付 | 预估工期 |
|-------|------|----------|----------|
| 3 | DXF 管线 | DWG Converter 抽象 + ezdxf 解析 Worker + entities.json + 低置信度复核 | 2-3 周 |
| 4 | Windows CAD Worker | ASP.NET Core + ZWCAD API + pull 任务派发 + CAD 崩溃恢复 | 3-4 周 |
| 5 | 业务算法 | LaR 识别 + 构件比对 + 材料表提取 + 报告生成 + 批量任务 | 4-6 周 |
| 6 | 生产增强 | RabbitMQ(可选) + Prometheus/Grafana + Loki + CI/CD + 多 CAD Worker | 持续 |

---

## 12. 关键经验总结

### 安全设计优先

时序攻击防御（H1 dummy hash）和原子操作模式（`UPDATE WHERE + rowcount`）在 Stage 1 早期即实现，避免了后期重构。12/18 渗透发现已在当前阶段修复。

### 测试隔离的价值

SQLite `:memory:` + FakeRedis 的双层隔离使得 432 测试在 61 秒内完成，无需外部服务。每测试独立 DB 消除了测试间污染。真实 Redis 集成测试（13 tests）作为安全网自动跳过。

### 显式异步边界

将长耗时操作显式地跨越 Celery 边界（即使在 Stage 1 只有假任务）使得代码架构与生产形态一致，后续阶段只需替换任务体而不改动调用链。

### 配置模式的选择

组件字段 + 计算属性模式（而非单一 URL 字符串）在 Docker 部署时体现优势——只需覆盖 `MYSQL_HOST=mysql` 而非重建整个 DSN。

### 特性开关的灵活性

3 个 boolean flag 实现了代码可合并到主分支但功能保持黑暗状态的能力，支持独立测试和分阶段上线。Agent 端点已在 Stage 1 定义完整资源模型，启用时只需改一行配置。

---

## 附录 A：文件清单

| 类别 | 路径 | 说明 |
|------|------|------|
| 核心规范 | `DWG-Agent企业平台技术规范.md` | v2.0, 1296 行, 25 节 |
| 操作指令 | `CLAUDE.md` | Agent 指令：约定、禁止项、文件映射 |
| 架构文档 | `docs/architecture.md` | 系统架构、分层、数据流、扩展指南 |
| API 参考 | `docs/api.md` | 64 端点参考、认证、错误码 |
| 数据库设计 | `docs/database.md` | 引擎配置、17 表目录、迁移管理、备份策略 |
| 部署运维 | `docs/deployment.md` | 本地开发、Docker 部署、配置、故障排除 |
| 开发指南 | `docs/development.md` | 开发工作流、测试、编码规范、常见陷阱 |
| 安全架构 | `docs/security.md` | 认证、RBAC、渗透修复、生产清单 |
| 路线图 | `docs/roadmap.md` | Stage 1-6 详细交付计划 + 接口规范 |
| 中文文档 | `docs/zh/*.md` | 以上 7 份文档的中文翻译 |
| 部署配置 | `infra/` | nginx、mysql/init.sql、redis/redis.conf、minio、verify.sh |
| 运维脚本 | `scripts/` | lib.sh、db.sh、start-dev.sh、start-all.sh、stop-all.sh、status.sh |
| 后端 | `backend/` | Python 3.12 + FastAPI，5,272 行，432 测试 |
| 前端 | `frontend/` | React 19 + TypeScript + Vite + Ant Design 6 |
| Compose | `compose.yaml` | 9 服务 + 2 profiles, 3 卷, 2 网络 |

## 附录 B：关键命令速查

```bash
# 本地开发
bash scripts/start-dev.sh                     # 一键启动（MySQL+Redis+Celery+后端+前端）
bash scripts/stop-all.sh                      # 停止（Nginx+后端+Celery）
bash scripts/status.sh                        # 健康聚合

# 数据库
bash scripts/db.sh init                       # 完整初始化（建库+迁移+种子）
bash scripts/db.sh migration-test             # 端到端迁移测试
bash scripts/db.sh status                     # 数据库诊断

# 后端测试
cd backend
uv run ruff check app tests                   # 代码质量（必须 0 错误）
uv run pytest -q                              # 全部测试（必须 432 passed）
uv run alembic upgrade head                   # 执行待处理迁移
uv run alembic current                        # 查看当前迁移版本

# Docker
docker compose up -d                          # 核心服务
docker compose --profile workers --profile monitoring up -d  # 全量
docker compose ps                             # 服务状态
bash infra/verify.sh                          # 基础设施全面验证
```
