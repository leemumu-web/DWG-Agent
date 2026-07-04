# DWG-Agent 企业级 CAD 智能处理平台技术规范

> 版本：v2.0（与 Stage 1 实现对齐）  
> 适用范围：公司内部 Ubuntu 主服务器 + MySQL + React 前端 + FastAPI 后端 + Agent + DWG/DXF 双轨处理 + 中望 CAD 高精度处理  
> 规范目标：定义 DWG 文件智能处理平台的技术方向、核心架构和关键边界。详细实现文档见 `docs/` 目录。

---

## 0. 文档定位

本技术规范定义 DWG-Agent 平台的**技术方向、核心架构和关键工程边界**。
详细实现细节见对应文档：

| 领域 | 详细文档 |
|------|---------|
| 系统架构与分层 | `docs/architecture.md` |
| API 参考（64 端点） | `docs/api.md` |
| 数据库设计（17 表） | `docs/database.md` |
| 部署与运维 | `docs/deployment.md` |
| 开发工作流 | `docs/development.md` |
| 安全架构 | `docs/security.md` |
| 分阶段路线 | `docs/roadmap.md` |

本规范不再重复 `docs/` 中已有的详细表结构、API 参数、部署步骤等内容，只保留架构决策和工程边界。

---

## 1. 总体设计结论

最终系统采用：

```text
React SPA
  + Nginx Gateway
  + FastAPI RESTful Backend
  + MySQL Metadata Database
  + MinIO / NAS File Storage
  + Redis Cache / Memory / Progress
  + Celery Async Workers
  + LangGraph Agent Orchestrator
  + MCP Tool Layer
  + Python DXF Processing Worker
  + Windows C# ZWCAD Worker
  + Docker Compose Deployment
```

核心原则：

```text
前端只负责交互展示；
FastAPI 只负责业务 API、权限、元数据、任务调度；
MySQL 只存结构化业务数据；
MinIO/NAS 存 DWG、DXF、JSON、PNG、PDF、Excel 等文件；
Redis 存短期状态、会话记忆、任务进度和缓存；
Celery Worker 执行耗时任务；
Agent 负责自然语言任务理解、工具编排和结果解释；
DXF Worker 负责低精度开源处理；
Windows CAD Worker 负责中望 CAD 高精度处理；
Docker Compose 管理 Ubuntu 主服务器上的平台基础服务；
中望 CAD 不强行放入 Linux Docker，而是运行在独立 Windows 节点。
```

---

## 2. 系统物理拓扑结构

### 2.1 生产部署拓扑

```text
┌─────────────────────────────────────────────────────────────┐
│                       公司内部用户                           │
│  Browser: Chrome / Edge                                      │
│  Access: https://dwg-agent.company.local                     │
└─────────────────────────────┬───────────────────────────────┘
                              │ HTTPS
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Ubuntu 主服务器                           │
│                                                             │
│  Docker Compose Network                                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Nginx Container                                       │  │
│  │ - React 静态资源托管                                   │  │
│  │ - HTTPS/TLS                                           │  │
│  │ - /api/v1/* 反向代理到 backend-api                     │  │
│  │ - 上传大小限制、访问日志、限流                           │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          │ internal network                  │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │ FastAPI Backend API Container                          │  │
│  │ - RESTful API                                          │  │
│  │ - Auth / RBAC                                          │  │
│  │ - Project / File / Drawing / Job / Review / Audit      │  │
│  │ - Agent Run API                                        │  │
│  │ - Celery 任务投递                                       │  │
│  └───────┬──────────────┬──────────────┬─────────────────┘  │
│          │              │              │                    │
│          ▼              ▼              ▼                    │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────────┐  │
│  │ MySQL       │  │ Redis       │  │ MinIO / NAS           │  │
│  │ 元数据       │  │ 缓存/记忆/进度│  │ DWG/DXF/结果文件       │  │
│  └─────┬──────┘  └─────┬──────┘  └───────────┬───────────┘  │
│        │               │                     │              │
│        └───────────────┼─────────────────────┘              │
│                        ▼                                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Celery Worker Containers                              │  │
│  │ - worker-agent: Agent 编排                             │  │
│  │ - worker-dxf: DWG→DXF、DXF 解析                         │  │
│  │ - worker-report: 报告生成                               │  │
│  │ - worker-cad-dispatch: CAD 任务派发和状态同步             │  │
│  └───────────────────────┬───────────────────────────────┘  │
└──────────────────────────┼──────────────────────────────────┘
                           │ 内网 HTTP / HTTPS / API Key / mTLS
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Windows CAD Worker 节点                   │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ C# ASP.NET Core Worker Service                         │  │
│  │ - 主动拉取 CAD 任务                                     │  │
│  │ - 下载 DWG 到本地 sandbox                               │  │
│  │ - 调用中望 CAD API / 插件                                │  │
│  │ - 导出 JSON / PNG / 报告中间件                           │  │
│  │ - 上传结果到 MinIO                                      │  │
│  │ - 回写任务状态到 FastAPI                                 │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          ▼                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 中望 CAD / CAD .NET API / C# Plugin                    │  │
│  │ - 原生 DWG 打开                                         │  │
│  │ - 高精度尺寸/构件/块/文本/图层读取                       │  │
│  │ - 专业图纸对象处理                                      │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据流拓扑

```text
用户浏览器
  ↓ 上传 DWG
Nginx
  ↓ 反向代理
FastAPI Backend
  ↓ 写入文件元数据
MySQL
  ↓ 保存文件本体
MinIO / NAS
  ↓ 创建任务记录
MySQL.jobs
  ↓ 投递异步任务
Redis / RabbitMQ Broker
  ↓ Worker 拉取任务
Celery Worker
  ↓ 按 Agent 决策选择管线
DXF Worker 或 CAD Worker
  ↓ 生成结果文件
MinIO / NAS
  ↓ 写入结果索引和状态
MySQL.analysis_results / jobs
  ↓ 前端查询或 SSE 推送
React 任务详情页
```

---

## 3. 逻辑分层架构

系统按照六层组织：

```text
客户端交互层
  React SPA / TypeScript / Vite

网关层
  Nginx / HTTPS / Reverse Proxy / Upload Limit

平台 API 层
  FastAPI / RESTful API / Auth / RBAC / Resource Management

智能编排层
  LangGraph Agent / MCP Tool Adapter / Redis Memory

异步执行层
  Celery Workers / DXF Pipeline / CAD Dispatch / Report Generation

数据与基础设施层
  MySQL / Redis / MinIO / Docker Volumes / Logs / Monitoring
```

各层职责如下：

| 层级 | 职责 | 不应承担的职责 |
|---|---|---|
| React 前端 | 登录、上传、任务展示、结果预览、审核、管理员后台 | 不解析 DWG，不直接访问 MySQL/MinIO，不绕过后端权限 |
| Nginx | 静态资源、HTTPS、反向代理、上传大小限制、访问日志 | 不写业务逻辑 |
| FastAPI | RESTful API、认证、权限、元数据、任务创建、审计 | 不跑长耗时 CAD 任务，不保存大文件本体 |
| Agent | 自然语言任务理解、工具选择、步骤解释、报告草稿 | 不做安全边界、权限判断、路径拼接、精确几何判定 |
| Celery Worker | 异步任务执行、DXF 解析、报告生成、CAD 调度 | 不直接暴露给前端 |
| C# CAD Worker | 高精度 CAD API 调用、DWG 原生对象处理 | 不管理业务用户、权限、项目 |
| MySQL | 结构化数据、状态、审计 | 不存大文件本体 |
| MinIO/NAS | DWG、DXF、JSON、报告、预览图 | 不存用户权限逻辑 |
| Redis | 缓存、短期记忆、进度、Broker MVP | 不作为最终业务状态库 |

---

## 4. 技术栈总表

| 模块 | 技术栈 | 说明 |
|---|---|---|
| 前端框架 | React 19 + TypeScript | 企业后台和 Agent 交互界面 |
| 前端构建 | Vite | 开发热更新、生产构建 |
| UI 组件 | Ant Design 6 | 表格、表单、上传、后台管理 |
| 请求管理 | Axios（封装于 `src/api/`） | RESTful API 调用 |
| 数据缓存 | TanStack Query | 列表、分页、任务状态刷新 |
| 前端状态 | Zustand | 当前用户、权限、token（sessionStorage） |
| 网关 | Nginx 1.27-alpine | HTTPS、静态资源、反代、上传限制、限流 |
| 后端语言 | Python 3.12 | 平台 API、Agent API、Worker |
| 包管理 | uv | 依赖锁定（`uv.lock` 已提交） |
| API 框架 | FastAPI | RESTful API、OpenAPI、依赖注入 |
| Schema | Pydantic v2（`from_attributes=True`） | 请求/响应校验、配置模型 |
| 配置 | pydantic-settings（`extra="ignore"`） | `.env` 配置读取，组件字段 + 计算属性 |
| ORM | SQLAlchemy 2.x（同步 session） | MySQL 数据访问 |
| 迁移 | Alembic（3 版本） | 17 表初始 + TimestampMixin 修复 + resource_id 类型修复 |
| 数据库 | MySQL 8.x | 用户、权限、项目、任务、审计（池：`pool_size=10, max_overflow=20, pool_recycle=3600`） |
| 文件存储 | 双后端抽象（LocalFileStorage / MinioStorage） | 本地开发用本地 FS，Docker 部署用 MinIO |
| 缓存 | Valkey 9.x（Redis 兼容） | 短期状态、Agent 记忆、任务进度、token 黑名单 |
| 异步任务 | Celery（Redis broker/result backend） | Stage 1：worker-report 假任务；Stage 2+：agent/dxf/cad 队列 |
| Agent | LangGraph `create_react_agent`（Stage 2） | LLM 自动工具调用 |
| LLM 接入 | langchain-openai `ChatOpenAI`（Stage 2） | DeepSeek / OpenAI-compatible |
| 工具协议 | MCP（Stage 2） | Agent 调用外部工具 |
| DXF 解析 | ezdxf（Stage 3） | DXF 图层、文本、块、几何解析 |
| DWG 转换 | Converter 抽象层（Stage 3） | DWG → DXF |
| 高精度 CAD | C# + 中望 CAD API（Stage 4） | Windows 节点独立运行 |
| 容器 | Docker Compose（9 服务 + profiles） | Ubuntu 主服务编排 |
| 监控 | Flower / Nginx access log / 结构化日志 | 任务、服务、队列监控 |
| 审计 | `audit_logs` 表 | 30+ 操作类型全覆盖 |

---

## 5. 前端技术规范

### 5.1 技术栈

React 19 + TypeScript + Vite + Ant Design 6 + TanStack Query + Zustand。

### 5.2 目录结构（概要）

```
frontend/src/
├── api/          ← 12 个 API 客户端模块（11 资源 + client.ts）
├── app/          ← router, layout, providers
├── features/     ← 10 个页面模块（auth, dashboard, users, projects, files, drawings, jobs, reviews, profile, admin）
├── components/   ← 8 个共享组件（FileUpload, PermissionGuard 已实现，其余 Stage 2+ 占位）
├── stores/       ← Zustand（auth.store.ts）
└── types/        ← 9 个 TypeScript 类型文件
```

详细目录和开发工作流见 `docs/development.md`。

### 5.3 前端页面（10 页）

| 路由 | 页面 | 权限 |
|---|---|---|
| `/login` | 登录页 | 公开 |
| `/dashboard` | 工作台 | 已登录 |
| `/projects` / `/:id` | 项目列表/详情 | 项目成员 |
| `/drawings/:id` | 图纸详情 | 项目成员 |
| `/files` | 文件管理 | 已登录 |
| `/jobs` / `/:id` | 任务列表/详情 | 已登录 |
| `/reviews` | 待复核列表 | reviewer / admin |
| `/admin/users` / `/admin/roles` / `/admin/audit-logs` | 管理后台 | super_admin / admin / auditor |
| `/profile` | 个人中心 | 已登录 |

### 5.4 前端交互要求

1. 所有 API 请求通过 `src/api/` 封装，禁止组件中直接 `fetch()`。
2. API 基地址来自 `VITE_API_BASE_URL`（开发时指向 `http://127.0.0.1:8000`，Docker 下为空走 Nginx 代理）。
3. 权限控制分三层：路由级、菜单级、组件/按钮级。前端权限仅用于 UX 优化，最终权限由后端决定。
4. Token 存 `sessionStorage`（非 `localStorage`），Axios 拦截器自动注入 `Authorization` header。

---

## 6. 后端平台技术规范

### 6.1 后端目录结构（概要）

```
backend/
├── pyproject.toml + uv.lock + .python-version
├── alembic.ini + migrations/versions/（3 版本）
├── Dockerfile（多阶段构建，非 root，HEALTHCHECK）
├── tests/（24 文件，432 测试）
└── app/
    ├── main.py              ← FastAPI app, lifespan, CORS, 4 异常处理器
    ├── api/v1/              ← 11 路由模块 + router.py 组装
    ├── core/                ← config, security, permissions, exceptions, redis_client, logger, validators, constants
    ├── db/                  ← base, session（引擎 + 池配置 + WAL pragma）, init_db（种子数据）
    ├── models/              ← 10 ORM 模型文件（17 表，TimestampMixin）
    ├── schemas/             ← 10 Pydantic v2 模块
    ├── services/            ← 12 业务服务（auth, user, job, project, file, drawing, review, agent, storage, audit, redis_memory, cache_service）
    ├── workers/             ← celery_app（真实） + tasks_report（Stage 1 假任务） + agent/dxf/cad 占位
    ├── storage/             ← base + local_storage（开发） + minio_storage（Docker 部署）
    ├── agents/              ← 3 占位（Stage 2）
    ├── mcp_client/          ← 2 占位（Stage 2）
    ├── integrations/zwcad/  ← 2 占位（Stage 4）
    ├── repositories/        ← 空占位
    └── utils/               ← path_utils, file_hash, time_utils
```

详细目录和开发工作流见 `docs/development.md`。

### 6.2 后端分层要求

| 层 | 目录 | 职责 | 禁止 |
|---|---|---|---|
| API 层 | `app/api/v1` | 路由、参数解析、权限依赖、响应封装 | 业务逻辑、直接 DB 查询 |
| Schema 层 | `app/schemas` | Pydantic v2 请求/响应模型 | 业务规则、DB 访问 |
| Service 层 | `app/services` | 业务流程编排 | 依赖 FastAPI Request |
| Repository 层 | `app/repositories` | DB 读写封装（未来提取） | 业务规则 |
| Model 层 | `app/models` | SQLAlchemy ORM 表模型 | 业务逻辑、校验 |
| Agent 层 | `app/agents` | LangGraph Agent + 工具注册（Stage 2） | 直接访问 DB/文件系统 |
| Worker 层 | `app/workers` | Celery 任务定义 | 复制业务逻辑 |
| Storage 层 | `app/storage` | 本地/MinIO 存储抽象 | — |
| Core 层 | `app/core` | 配置、安全、异常、日志、Redis、权限 | 领域逻辑 |

### 6.3 关键工程决策

1. **同步 API + Celery 异步边界**：FastAPI 使用同步 SQLAlchemy session 和同步 Redis。长耗时任务跨越 Celery 边界执行。
2. **MySQL 运行时 + SQLite 测试隔离**：生产用 MySQL 8.x（`pool_size=10, max_overflow=20, pool_recycle=3600`），测试用 SQLite `:memory:` + `StaticPool` 实现每测试隔离。
3. **组件字段配置模式**：配置使用 `mysql_host`/`mysql_port`/`mysql_user`/`mysql_password` 等组件字段 + 计算属性 `mysql_url`/`redis_url`/`celery_broker_url`，而非单一 `DATABASE_URL` 字符串。
4. **特性开关**：`AGENT_ENABLED`、`DXF_PIPELINE_ENABLED`、`CAD_WORKER_ENABLED` 三个布尔开关，默认 `false`。禁用时返回 503（错误码 `AGENT_DISABLED`）。

---

## 7. RESTful API 规范

### 7.1 总原则

- 所有接口统一在 `/api/v1` 下。
- 资源名使用复数名词，复合名用 kebab-case（如 `agent-runs`、`audit-logs`）。
- 使用 HTTP Method 表达动作，不使用 `/getUser` 这类动词接口。
- 使用 HTTP Status Code 表达结果（200/201/202/204），不统一包装为 `200 + code:0`。
- 所有业务端点必须鉴权（`current_user: CurrentUser`，无 `= None` 默认值）。
- 公开端点仅：`POST /auth/sessions`（登录）、`POST /auth/tokens/refresh`、`GET /health`。

### 7.2 HTTP 状态码

| 状态码 | 语义 | 使用场景 |
|---|---|---|
| 200 | 成功 | GET、PATCH、PUT 成功返回数据 |
| 201 | 已创建 | POST 创建资源成功 |
| 202 | 已接受 | 异步操作（创建任务、Agent run、取消/重试） |
| 204 | 无内容 | DELETE、登出成功 |
| 400 | 请求错误 | 参数组合不合法 |
| 401 | 未认证 | 无/过期/黑名单 token |
| 403 | 无权限 | 已认证但越权 |
| 404 | 不存在 | 资源不存在（或无权访问） |
| 409 | 冲突 | 用户名重复、状态不允许操作 |
| 413 | 文件过大 | 上传超过 `MAX_UPLOAD_SIZE_MB`（默认 512MB） |
| 415 | 类型不支持 | 非 `.dwg` 文件 |
| 422 | 校验失败 | Pydantic 校验失败 |
| 429 | 请求过多 | 登录失败限流 |
| 503 | 不可用 | Agent 未启用、MCP 不可用、CAD Worker 不可达 |
| 500 | 服务异常 | 未预期异常（`DEBUG=false` 时不泄露 traceback） |

### 7.3 响应格式

**成功：** `{"data": {...}, "meta": {"request_id": "...", "timestamp": "..."}}`  
**列表：** 增加 `"pagination": {"page": 1, "page_size": 20, "total": 120, "total_pages": 6}`  
**错误：** `{"error": {"code": "ERROR_CODE", "message": "...", "details": {}}, "meta": {...}}`

### 7.4 API 端点总览（11 模块，64 端点）

| 模块 | 端点 | 关键功能 |
|---|---|---|
| Auth（5） | sessions, tokens/refresh, me, password | 登录/登出/刷新/改密，access token + HttpOnly refresh cookie |
| Users（11） | CRUD + roles + password-reset + disable/enable | 软删除，super_admin 保护，自更新 `PATCH /users/me` |
| Roles（4） | CRUD roles + permissions | 7 全局角色，8 权限 |
| Projects（9） | CRUD + member 管理 | 4 项目角色，级联 active 状态检查 |
| Files（6） | upload, list, download-url, download | DWG 校验（header/size/hash），HMAC 签名下载 URL（TTL=300s） |
| Drawings（8） | CRUD + version + preview | 版本号自增，项目隔离 |
| Jobs（9） | create, cancel, retry, steps, logs, results | 状态机：pending→queued→running→succeeded/failed/cancelled |
| Results（4） | detail, download-url, review | 复核 decision：approved/rejected/needs_revision |
| Reviews（1） | pending list | 按项目成员过滤 |
| Audit（2） | list + detail | super_admin / auditor 专属 |
| Agent（4） | agent-runs CRUD, agent-tools | Stage 1 均返回 503（`AGENT_ENABLED=false`） |
| Health（1） | GET /health | 公开，返回 `{"data": {"status": "ok"}}` |

完整端点参数、请求/响应示例见 `docs/api.md`。

---

## 8. 用户、认证与权限设计

### 8.1 账号体系

- `username`：内部账号/工号，pattern `^[a-zA-Z0-9_.@-]+$`
- `real_name`：真实姓名，拒绝 HTML 标签
- `email`：企业邮箱
- `password_hash`：Argon2id（`pwdlib.PasswordHash.recommended()`，m=65536, t=3, p=4）
- `status`：`active` / `disabled` / `deleted`（软删除，保留审计引用）

### 8.2 密码安全

1. 最小长度 12 字符，必须包含大写+小写+数字。
2. 拒绝常见弱密码（内置黑名单）。
3. 管理员不能查看用户密码，重置密码写入审计日志。
4. 用户禁用后 **所有 token 立即失效**（密码变更也触发全设备登出）。
5. **时序攻击防御**：用户不存在或已禁用时，仍执行一次完整 Argon2id 验证（对比预计算的 dummy hash），消除用户名枚举的时序侧信道。

### 8.3 Token 策略

- **access_token**：JWT HS256，`sub`=user_id，`jti`=UUID4，过期 30 分钟。前端存 `sessionStorage`。
- **refresh_token**：JWT HS256，过期 14 天。`HttpOnly; SameSite=Lax` Cookie（生产加 `Secure`）。
- **登出黑名单**：`jti` 写入 Redis（`blacklist:jti:{jti}`），TTL = 剩余有效期，自清理。Redis 不可用时降级（fail-open）。
- **密码变更失效**：token 签发时间早于 `password_changed_at` → 401。

### 8.4 RBAC 模型（5 表）

```
sys_users ──< sys_user_roles >── sys_roles ──< sys_role_permissions >── sys_permissions
     │
     └── projects ──< project_members >── sys_users
```

### 8.5 全局角色（7 个，`is_system=True` 保护）

| 角色 | 说明 |
|---|---|
| `super_admin` | 绕过所有权限检查，完全系统访问 |
| `admin` | 用户管理、项目管理、全局项目查看 |
| `engineer` | 上传文件、创建任务、查看所属项目结果 |
| `reviewer` | 审核分析结果 |
| `operator` | 执行分配任务 |
| `viewer` | 只读访问 |
| `auditor` | 查看审计日志 |

### 8.6 项目角色（4 个）

| 角色 | 能力 |
|---|---|
| `project_owner` | 项目管理、成员管理、文件/图纸/任务/结果完全控制 |
| `project_engineer` | 上传文件、创建图纸、提交任务、查看结果 |
| `project_reviewer` | 审核分析结果 |
| `project_viewer` | 只读 |

### 8.7 权限判断顺序

```
已认证？ → 否 → 401
用户活跃？ → 否 → 401
super_admin？ → 是 → 允许全部
admin 全局权限？ → 是 → 允许
项目成员？ → 否 → 403
项目角色允许此操作？ → 否 → 403
→ 允许
```

### 8.8 关键实现细节

- **`require_project_member()`** 内嵌 `require_active_project()`：项目软删除后所有成员访问级联返回 404。
- **`super_admin` 保护**：非 super_admin 不能管理 super_admin 账号。
- **自操作保护**：不能删除/禁用自己的账号。
- **原子状态转换**：`transition_user_status()` 使用 `UPDATE WHERE + rowcount`（消除 SELECT→UPDATE 的 TOCTOU 窗口）。
- **`FOR UPDATE`**：`get_user_or_404(for_update=True)` 提供悲观行锁。

---

## 9. MySQL 数据库设计

### 9.1 核心原则

- MySQL 8.x 只保存结构化业务数据，不存文件本体（文件在 MinIO/NAS）。
- 所有表使用 `TimestampMixin`（`created_at` + `updated_at`），用户表额外有 `deleted_at`（软删除）。
- 所有 FK 使用默认 `NO ACTION`（MySQL RESTRICT），禁用级联删除——通过应用层软删除保护审计链。
- 迁移使用 Alembic（当前 3 版本），`_pk_type()` helper 兼容 MySQL `BIGINT` 和 SQLite `INTEGER`。

### 9.2 表总览（17 表）

| # | 表 | 用途 |
|---|---|---|
| 1 | `sys_users` | 用户身份（username UNIQUE, password_hash, status, soft-delete） |
| 2 | `sys_roles` | 角色定义（code UNIQUE, is_system 保护） |
| 3 | `sys_permissions` | 权限定义（resource + action） |
| 4 | `sys_user_roles` | 用户↔角色 M2M |
| 5 | `sys_role_permissions` | 角色↔权限 M2M |
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

详细表结构、列定义、索引和 ER 图见 `docs/database.md`。

### 9.3 连接池配置

```python
engine = create_engine(
    settings.mysql_url,
    pool_pre_ping=True, pool_recycle=3600, pool_size=10, max_overflow=20,
)
```

### 9.4 SQLite 测试隔离

pytest 使用 SQLite `:memory:` + `StaticPool`，每测试完全隔离。`foreign_keys=ON` pragma 由 conftest 自动设置。

---

## 10. 文件存储设计

### 10.1 双后端抽象

```
app/storage/
├── base.py           ← AbstractStorageBackend（save / retrieve / delete）
├── local_storage.py  ← LocalFileStorage（开发环境，backend/var/storage/）
└── minio_storage.py  ← MinioStorage（Docker 部署）
```

通过 `STORAGE_BACKEND=local|minio` 切换。

### 10.2 Bucket 设计（4 个）

| Bucket | 内容 | 规则 |
|---|---|---|
| `dwg-original` | 原始 DWG | 永不覆盖 |
| `dwg-derived` | DXF、JSON、PNG、SVG | 允许重算 |
| `dwg-reports` | Excel、PDF、ZIP | 按需生成 |
| `dwg-temp` | 临时文件 | Worker 生命周期内有效 |

### 10.3 文件安全要求

1. 存储路径由后端生成（`local/{uuid4().hex}{ext}`），不使用用户文件名。
2. `original_name` 仅作展示字段。
3. 所有文件计算 SHA-256 + MD5。
4. 上传校验链：扩展名（`.dwg`） → MIME → DWG 文件头（AC1012-AC1032） → 大小（≥1024 字节，≤512MB） → 流式哈希。
5. 路径穿越防护：`ensure_within_root(root, candidate)` 拒绝 `../` 和符号链接逃逸。
6. 下载：先校验权限（项目成员或全局管理员），再返回 HMAC-SHA256 签名 URL（TTL=300s）。
7. 原始文件只读，不允许覆盖写入。

---

## 11. Agent 技术规范

### 11.1 Agent 定位

Agent 层继承原 Sorting-Agent 规范，但在本平台中定位为：

```text
自然语言任务理解器
工具调用编排器
执行步骤解释器
结果摘要生成器
报告草稿生成器
```

Agent 不负责：

```text
最终权限判断
文件路径拼接
数据库安全写入
高精度几何计算
CAD 原生对象解析
生产结果最终裁定
```

### 11.2 Agent 技术栈

```text
LangGraph create_react_agent
langchain-openai ChatOpenAI
OpenAI-compatible LLM
MCP Client
MCP Tool Adapter
Redis Session Memory
FastAPI Agent API
```

### 11.3 Agent 创建要求

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


def create_cad_agent(mcp_client, settings):
    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        temperature=0,
    )
    tools = build_langchain_tools(mcp_client)
    agent = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)
    return agent
```

要求：

1. Agent 必须使用 `create_react_agent`。
2. 工具选择由 LLM 根据工具描述和上下文决定。
3. 平台硬规则不能交给 LLM，例如权限、路径、安全、强制高精度任务路由。
4. `temperature` 默认设为 0，保证稳定性。
5. LLM API Key 必须来自环境变量。

### 11.4 MCP Client 要求

MCP Client 必须实现：

```text
connect()
disconnect()
list_tools() -> list[dict]
call_tool(tool_name, arguments) -> str
```

要求：

1. MCP 连接失败时服务不能崩溃。
2. MCP 不可用时 `/api/v1/agent-runs` 返回 503。
3. MCP stdio 模式下 stdout 只能输出合法 JSON。
4. MCP tool description 必须包含完整参数说明。
5. Agent 代码不能直接使用 pandas/openpyxl/ezdxf 等业务库；这些应在工具服务或 Worker 中使用。

### 11.5 Redis 短期记忆（已实现，待运行时调用）

- Key pattern: `agent:memory:{session_id}`
- 数据类型: JSON list
- TTL: `REDIS_MEMORY_TTL=7200`（2 小时）
- 最大消息数: `REDIS_MAX_MESSAGES=20`
- 实现文件: `app/services/redis_memory.py`（78 行，已通过测试验证）
- 缓存服务: `app/services/cache_service.py`（84 行，`cache:{namespace}:{key}` 模式，Redis 不可用时安全降级）
- Stage 1 状态: 基础设施就绪，已测试但未在请求路径中调用

---

## 12. MCP 工具层与 CAD 工具服务

### 12.1 工具分层

系统工具分三类：

| 工具类型 | 示例 | 实现方式 |
|---|---|---|
| 通用文件工具 | 列文件、读元数据、生成报告 | MCP Server / Tool Service |
| DXF 处理工具 | 转 DXF、解析 DXF、提取图层 | Python Worker / MCP 包装 |
| CAD 高精度工具 | 中望 CAD 打开 DWG、测量、提取尺寸 | C# CAD Worker |

### 12.2 推荐工具列表

```text
list_project_files
get_file_metadata
create_processing_job
get_job_status
convert_dwg_to_dxf
parse_dxf_entities
extract_layers
extract_texts
extract_blocks
dispatch_to_zwcad_worker
validate_analysis_result
generate_report
create_review_record
```

### 12.3 CAD 专业能力说明

原规范要求“使用现成 MCP Server，不自定义 MCP Server”。这个原则适用于 Excel、文件系统、通用数据处理等场景。但 DWG/DXF/中望 CAD 属于强专业领域，现成 MCP Server 很难完整覆盖。因此本平台采用以下折中规范：

```text
Agent 应用层不直接写业务工具逻辑；
通用工具优先使用现成 MCP Server；
CAD 专业能力可以封装为内部 Tool Service 或内部 MCP Server；
所有工具仍通过统一 Tool Adapter 暴露给 Agent。
```

---

## 13. 异步任务与 Celery 设计

### 13.1 设计原则

- FastAPI 负责创建任务、查询状态；Celery 负责异步执行。
- 前端通过轮询或 SSE 获取进度。
- 队列: `agent`、`dxf`、`cad`、`report`、`maintenance`。
- Broker/Result Backend: Redis（生产可升级 RabbitMQ）。

### 13.2 当前实现状态

| 组件 | 状态 | 说明 |
|---|---|---|
| `celery_app.py` | **已实现** | Redis broker/result backend，queue routing，eager mode for tests |
| `tasks_report.py` | **已实现** | `run_stub_job`：queued→running→succeeded 假任务，写入 job_steps + analysis_results |
| `tasks_agent.py` | 占位 | Stage 2 |
| `tasks_dxf.py` | 占位 | Stage 3 |
| `tasks_cad.py` | 占位 | Stage 4 |

Celery URL 从 Redis 组件字段自动计算，与 Redis 配置保持同步。

### 13.3 任务状态机

```text
pending → queued → running → validating → need_review → succeeded
  │         │         │            │
  │         │         │            └──→ waiting_cad_worker → validating → ...
  │         │         │
  └── (auto)         ├──→ cancelled（仅 queued/running 可取消）
                     └──→ failed（仅 running/validating 可失败）
                              │
                              └──→ retry → queued（仅 failed/cancelled 可重试）
```

### 13.4 工程要求

1. 任务必须幂等。
2. 失败写入 `jobs.error_code` + `jobs.error_message`。
3. 每个步骤写入 `job_steps`（step_name, worker_name, status, input/output_json）。
4. Worker 日志必须带 `job_id`。
5. 大型中间结果写入 MinIO，MySQL 只记录索引。

---

## 14. DWG/DXF 低精度处理管线

### 14.1 技术栈

```text
DWG Converter 抽象层
ezdxf
Python 几何与规则处理
JSON 结构化输出
Celery dxf queue
```

### 14.2 适用场景

```text
图层提取
文字提取
块引用统计
线段/多段线/圆/圆弧解析
普通材料表文本提取
低精度预览
简单几何关系判断
```

### 14.3 不适用场景

```text
高精度尺寸测量
复杂动态块
代理对象
三维实体
CAD 原生语义依赖强的对象
对法律/生产结果有强精度要求的任务
```

### 14.4 流程

```text
DWG 原始文件
  ↓
下载到 Worker sandbox
  ↓
调用 DWG Converter
  ↓
生成 converted.dxf
  ↓
ezdxf 解析
  ↓
生成 entities.json
  ↓
规则处理
  ↓
生成 result.json
  ↓
上传派生文件
  ↓
写入 analysis_results
  ↓
自动完成或进入 need_review
```

### 14.5 输出 JSON 示例

```json
{
  "source": "dxf",
  "layers": ["0", "DIM", "TEXT", "STEEL"],
  "entities": [
    {
      "type": "TEXT",
      "layer": "TEXT",
      "text": "BH650*300*14*24",
      "position": [120.5, 88.0],
      "rotation": 0
    },
    {
      "type": "LINE",
      "layer": "STEEL",
      "start": [0, 0],
      "end": [1000, 0]
    }
  ]
}
```

---

## 15. 中望 CAD 高精度处理管线

### 15.1 技术栈

```text
Windows Server
中望 CAD
C#
ASP.NET Core Worker Service
CAD .NET API
C# Plugin DLL
本地 sandbox
内网 API Key / mTLS
```

### 15.2 为什么不放入 Ubuntu Docker

中望 CAD / AutoCAD 类软件通常依赖：

```text
Windows 桌面环境；
CAD 软件本体；
许可证服务；
.NET / COM / 插件加载环境；
GUI 或半 GUI 会话；
本机图形和系统 API。
```

因此不应强行放入 Linux Docker。正确方式是：

```text
Ubuntu 主服务器运行平台主体；
Windows 节点运行 CAD Worker 和中望 CAD；
两者通过内网 API 通信。
```

### 15.3 工作模式

推荐 Windows CAD Worker 主动拉任务：

```text
CAD Worker 启动
  ↓
向 Backend 注册心跳
  ↓
GET /api/v1/internal/cad-worker/jobs/next 拉取任务
  ↓
下载 DWG 到本地 sandbox
  ↓
打开中望 CAD
  ↓
加载 C# 插件
  ↓
执行任务指令
  ↓
导出 cad_result.json
  ↓
上传结果文件到 MinIO
  ↓
PATCH 回写任务状态
```

### 15.4 CAD Worker 内部模块

```text
cad-worker/
├── ZwCadWorker.Api/          ASP.NET Core API / Worker Service
├── ZwCadWorker.Core/         任务模型、协议、状态机
├── ZwCadWorker.Plugin/       中望 CAD 插件 DLL
├── ZwCadWorker.Infrastructure/
│   ├── BackendClient.cs
│   ├── MinioClient.cs
│   ├── SandboxManager.cs
│   └── LicenseChecker.cs
└── tests/
```

### 15.5 CAD Worker 要求

1. 任务执行必须有本地锁，同一 CAD 实例不要并发打开多个不兼容任务。
2. 每个任务使用独立 sandbox。
3. CAD 崩溃后必须能自动恢复或明确失败。
4. 许可证不可用时返回明确错误码。
5. 所有结果必须结构化输出 JSON。
6. 所有日志必须带 `job_id`。
7. 回传结果前必须校验文件存在和 JSON schema。
8. 内网通信必须使用 API Key 或 mTLS。

---

## 16. Agent 与确定性调度的边界

系统同时存在两类决策：

### 16.1 确定性平台规则

这些规则不能交给 LLM：

```text
用户是否有权限；
文件路径是否合法；
用户是否能访问项目；
高精度任务是否强制走 CAD Worker；
DXF 转换失败是否 fallback；
任务状态是否允许重试；
结果是否允许下载；
审计日志是否写入。
```

### 16.2 LLM Agent 决策

这些可以交给 LLM Agent：

```text
自然语言任务拆解；
选择合适工具；
组织工具调用顺序；
总结工具返回结果；
解释任务步骤；
生成报告草稿。
```

### 16.3 管线选择建议

```text
用户指定 high → CAD Worker
任务类型为 precise_measurement → CAD Worker
DXF 转换失败 → CAD Worker
DXF 解析置信度 < 0.85 → CAD Worker
包含复杂块、代理对象、3D 实体 → CAD Worker
图层/文字/块统计 → DXF Worker
普通预览和实体抽取 → DXF Worker
```

---

## 17. Docker 与部署规范

### 17.1 容器化原则

- 应容器化的：nginx, backend-api, worker-*, mysql, redis(valkey), minio, flower。
- 不应容器化的：中望 CAD 桌面软件（依赖 Windows GUI、许可证服务、.NET/COM 环境）。

### 17.2 服务总览（9 服务，2 profiles）

| 服务 | 镜像 | Profile | 说明 |
|---|---|---|---|
| `nginx` | `nginxinc/nginx-unprivileged:1.27-alpine` | — | React 静态资源 + `/api/v1/*` 反代 + 限流 |
| `backend-api` | 自构建（`backend/Dockerfile`） | — | gunicorn + uvicorn, 4 workers, HEALTHCHECK `/health` |
| `mysql` | `oracle/mysql-community-server:8.4` | — | `mysqladmin ping` 健康检查 |
| `redis` | `valkey/valkey:9.0-alpine` | — | AOF, requirepass, maxmemory 256mb |
| `minio` | `minio/minio:latest` | — | S3 兼容对象存储 |
| `worker-agent` | 自构建 | `workers` | Celery agent 队列 |
| `worker-dxf` | 自构建 | `workers` | Celery dxf 队列 |
| `worker-report` | 自构建 | —（默认启动） | Stage 1 假任务，Stage 5 报告生成 |
| `flower` | 自构建 | `monitoring` | Celery 监控面板（`:5555`） |

```bash
docker compose up -d                                          # 核心服务（含 worker-report）
docker compose --profile workers --profile monitoring up -d   # 全量
```

### 17.3 网络

- `public`：Nginx 对外（80/443）。
- `internal`（`internal: true`）：所有后端服务，不对外暴露。

### 17.4 Dockerfile 关键特性

- 多阶段构建（uv python3.12-bookworm-slim）。
- 非 root 用户 `appuser`（uid 1000）。
- HEALTHCHECK: `curl -f http://localhost:8000/health`（15s interval, 3s timeout, 5 retries）。
- CMD: `alembic upgrade head && exec gunicorn app.main:app --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120`

完整 `compose.yaml` 和部署步骤见 `docs/deployment.md`。

---

## 18. 配置规范

### 18.1 配置模式

配置使用 **pydantic-settings**（`extra="ignore"`），采用**组件字段 + 计算属性**模式：

```python
mysql_host: str = "127.0.0.1"
mysql_port: int = 3306
mysql_password: str = ""

@property
def mysql_url(self) -> str: ...
@property
def redis_url(self) -> str: ...
@property
def celery_broker_url(self) -> str: ...  # 自动跟随 REDIS_PASSWORD
@property
def celery_result_backend(self) -> str: ...
```

优势：Docker Compose 可分别覆盖各组件（如 `MYSQL_HOST=mysql`），无需重建完整 URL。

### 18.2 关键配置项

| 分类 | 变量 | 默认值 |
|---|---|---|
| App | `APP_ENV`, `DEBUG`, `API_V1_PREFIX=/api/v1` | — |
| MySQL | `MYSQL_HOST/PORT/DATABASE/USER/PASSWORD` | 127.0.0.1:3306 |
| Redis/Valkey | `REDIS_HOST/PORT/DB/PASSWORD` | localhost:6379, 无密码 |
| Redis 记忆 | `REDIS_MEMORY_TTL=7200`, `REDIS_MAX_MESSAGES=20` | — |
| JWT | `JWT_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES=30`, `REFRESH_TOKEN_EXPIRE_DAYS=14` | — |
| 存储 | `STORAGE_BACKEND=local|minio`, `MAX_UPLOAD_SIZE_MB=512` | local |
| MinIO | `MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD` + 4 bucket 名 | — |
| Super Admin | `SUPER_ADMIN_USERNAME/PASSWORD/REAL_NAME` | admin / 系统管理员 |
| 特性开关 | `AGENT_ENABLED/DXF_PIPELINE_ENABLED/CAD_WORKER_ENABLED` | 全部 `false` |
| LLM | `MODEL_NAME/MODEL_API_KEY/MODEL_BASE_URL` | deepseek-chat |
| CORS | `BACKEND_CORS_ORIGINS` | localhost:5173 |

### 18.3 配置要求

1. `.env.example` / `.env.docker.example` 可提交，`.env` / `.env.docker` 不可提交。
2. 所有密钥、密码、API Key 来自环境变量，**禁止硬编码**。
3. 前端只暴露 `VITE_` 前缀变量。
4. 特性开关支持分阶段上线和紧急回滚（设回 `false` 即可恢复 503）。

完整配置参考见 `docs/deployment.md`。

---

## 19. 安全规范

### 19.1 账号安全

- 密码 Argon2id（`pwdlib.PasswordHash.recommended()`），最小 12 字符，大写+小写+数字，弱密码黑名单。
- **时序攻击防御**：用户不存在/已禁用时仍执行完整 Argon2id 验证（dummy hash），消除用户名枚举的时序侧信道。
- 登录限流（Nginx `limit_req_zone`，2 req/s burst 3）。
- 用户禁用/密码变更后所有 token 立即失效。

### 19.2 API 安全

- 所有业务端点强制鉴权（`current_user: CurrentUser`，无 `= None`）。
- 输入校验：username pattern、real_name HTML 标签拒绝、task_type pattern、email 格式。
- 4 层异常处理器，所有 500 不泄露 traceback（`DEBUG=false`）。
- CORS 显式枚举 origins/methods/headers，不设 `*`。
- 健康端点只返回 `{"status": "ok"}`。

### 19.3 文件安全

- 上传校验链：扩展名 → MIME → DWG header（AC1012-AC1032） → 大小（≥1024B, ≤512MB） → SHA-256 + MD5。
- 路径穿越防护：`ensure_within_root()` 拒绝 `../` 和符号链接。
- HMAC-SHA256 签名下载 URL（TTL=300s）+ 权限校验。

### 19.4 Token 安全

- JWT HS256，`jti` (UUID4) 标识，`type` 区分 access/refresh。
- 登出时 `jti` 写入 Redis 黑名单（`blacklist:jti:{jti}`，TTL=剩余有效期，自清理）。
- access_token 存 `sessionStorage`（非 `localStorage`）。
- refresh_token 存 `HttpOnly; SameSite=Lax` Cookie（生产加 `Secure`）。

### 19.5 渗透测试修复（12/18 fixed）

| ID | 发现 | 严重度 | 修复 |
|---|---|---|---|
| H1 | 登录时序侧信道 | Critical | Dummy Argon2id hash |
| H6 | 用户名注入 | High | Pattern `^[a-zA-Z0-9_.@-]+$` |
| BUG-1 | UserCreate 批量分配角色 | High | 移除字段，独立 RBAC 端点 |
| BUG-2 | 弱密码策略 | High | 12 字符 + 复杂度 + 黑名单 |
| BUG-3 | real_name HTML 注入 | Medium | HTML 标签拒绝 |
| BUG-4 | 健康端点信息泄露 | Low | `{"status":"ok"}` |
| BUG-5 | DWG 大小校验不足 | Medium | 1024 字节最小 + header 验证 |
| BUG-6 | 竞态条件 500 traceback | Medium | IntegrityError→409 |
| BUG-7 | 软删除级联泄露 | Medium | require_active_project 嵌入 |
| BUG-8 | task_type 无校验 | Low | Pattern `^[a-z][a-z0-9_]+$` |
| BUG-9 | 重试无状态守卫 | Medium | 仅 failed/cancelled 可重试 |
| BUG-12 | 无自更新端点 | Low | `PATCH /users/me` |

剩余 6 项无法复现或为部署层面关注。详见 `docs/security.md`。

### 19.6 已知差距

| 差距 | 缓解 |
|---|---|
| 无 refresh token rotation | 建议实现 OAuth 2.0 标准 rotation |
| 审计日志无保留策略 | 建议定期归档 |
| 签名下载 URL 非独立 capability token | 下载端点额外校验认证（defense-in-depth） |

---

## 20. 日志、监控与审计

### 20.1 日志要求

- 所有请求自动分配 `X-Request-ID`（传入则透传，否则生成 UUID4）。
- 日志字段：`request_id, user_id, project_id, job_id, agent_run_id, worker_name, duration_ms, error_code`。
- Nginx access log：extended 格式含 `$request_id, $request_time, $upstream_response_time`。

### 20.2 监控

- Docker logs / `docker compose logs -f <service>`
- Nginx access/error log + FastAPI 结构化日志 + Celery worker log
- Flower（`:5555`，via `monitoring` profile）
- 健康检查聚合：`scripts/status.sh`（本地）, `infra/verify.sh`（全面验证）

### 20.3 审计（30+ 操作类型）

登录/登出、用户 CRUD、角色权限变更、项目变更、成员变更、文件上传/删除/下载、任务创建/取消/重试、结果审核。审计日志不可变（无 API 修改/删除）。

### 20.4 生产增强（Stage 6）

Prometheus + Grafana + Loki + OpenTelemetry + Sentry（可选）。

---

## 21. 测试规范

### 21.1 后端测试（当前：24 文件，432 测试）

**技术栈：** pytest + `fastapi.testclient.TestClient`（进程内） + SQLite `:memory:` + `StaticPool` + FakeRedis

**双层 Redis 测试：**
1. FakeRedis（`fakeredis[lua]`）：`conftest.py` autouse fixture monkeypatch，覆盖 419 测试，零外部依赖。
2. Real Redis（`test_redis_real.py`）：真实 Valkey 集成测试，Redis 不可用时自动 `pytest.skip`。

**测试领域：** 登录/token、RBAC 深度验证、文件上传/DWG 校验、任务生命周期、审计日志、Redis 客户端/记忆/缓存、安全边界（时序攻击/路径穿越/HTML 注入/SQL 完整性）、API 回归、配置、Docker Compose 验证、Celery/MinIO 部署验证、迁移测试、Shell 脚本验证。

**质量门：**
```bash
cd backend
uv run ruff check app tests    # 必须 0 错误
uv run pytest -q               # 必须 432 passed
```

### 21.2 前端测试（计划）

Vitest + React Testing Library + Playwright。

### 21.3 CAD Worker 测试（Stage 4 计划）

xUnit/NUnit + 固定 DWG 样本库 + Golden JSON 对比。

---

## 22. 仓库结构

```text
complete_framework/
├── DWG-Agent企业平台技术规范.md   ← 本文件
├── CLAUDE.md / README.md
├── .env.example / .env.docker.example
├── compose.yaml                   ← 9 服务 + 2 profiles
├── backend/                       ← Python 3.12, uv, FastAPI
│   ├── app/ (api/core/db/models/schemas/services/workers/storage/...)
│   ├── tests/ (24 文件, 432 测试) + migrations/versions/ (3 迁移)
├── frontend/                      ← React 19 + TS + Vite + Ant Design 6
├── docs/                          ← 7 份交接文档
├── infra/                         ← nginx/mysql/redis/minio 配置 + verify.sh
├── scripts/                       ← 6 脚本 (lib.sh/db.sh/start-dev.sh/...)
├── agents/                        ← 占位（未来 Agent 定义）
├── cad-worker/                    ← 占位（未来 Windows C# Worker）
└── tests/                         ← 占位（E2E/集成测试）
```

详细目录见 `docs/development.md`。

---

## 23. 分阶段落地路线

### 阶段一：平台骨架闭环 —— ✅ 已完成

**交付物：** RESTful API 全闭环（64 端点，11 路由模块），RBAC（7 全局 + 4 项目角色），JWT 认证（access + refresh + jti 黑名单），DWG 文件上传（header 校验 + SHA-256），任务生命周期（queued→running→succeeded），Celery worker-report 假任务，审计日志（30+ 操作类型），432 测试，Docker Compose 9 服务，React 19 前端（10 页面）。

**验收结果：**
- [x] 用户能登录/刷新/登出
- [x] 管理员能管理用户/角色/权限
- [x] 用户能上传 DWG（header/大小/hash 校验）
- [x] 用户能创建任务（含 job_steps + analysis_results）
- [x] 结果文件能通过签名 URL 下载
- [x] 审计日志全部落库
- [x] 432 测试通过，ruff 0 错误

### 阶段二～六（详见 `docs/roadmap.md`）

| Stage | 名称 | 关键交付物 |
|---|---|---|
| **2** | Agent 子系统 | LangGraph `create_react_agent` + DeepSeek LLM + MCP Client + Redis Memory + AgentSteps UI |
| **3** | DXF 管线 | DWG Converter 抽象 + ezdxf 解析 Worker + entities.json 输出 + 低置信度复核 |
| **4** | Windows CAD Worker | ASP.NET Core Worker Service + ZWCAD API + pull-based 任务派发 + CAD 崩溃恢复 |
| **5** | 业务算法 | LaR 识别 + 构件比对 + 材料表提取 + 报告生成 + 批量任务 |
| **6** | 生产增强 | RabbitMQ（可选）+ Prometheus/Grafana + Loki + CI/CD + 多 CAD Worker 扩展 |

---

## 24. 验收清单

### 24.1 架构验收（Stage 1 已全部通过 ✅）

- [x] 前后端严格分离（React SPA ↔ FastAPI RESTful API）
- [x] API 符合 RESTful 资源规范（复数名词，HTTP method/status 语义）
- [x] 后端不直接执行长耗时任务（Celery 异步边界）
- [x] MySQL 不存文件本体（文件在 storage backend）
- [x] 双存储后端抽象（LocalFileStorage / MinioStorage）
- [x] Celery 任务可异步执行（Redis broker/result backend）
- [x] Docker Compose 可启动主服务（9 服务 + 2 profiles）
- [ ] Agent 使用 LangGraph `create_react_agent`（Stage 2）
- [ ] Windows CAD Worker 与 Ubuntu 主服务解耦（Stage 4）

### 24.2 API 验收（Stage 1 已全部通过 ✅）

- [x] 所有接口在 `/api/v1` 下（64 端点，11 模块）
- [x] 资源名使用复数名词，kebab-case 复合名
- [x] 创建资源返回 201 或 202
- [x] 删除成功返回 204
- [x] 错误响应使用 `error.code`（`UPPER_SNAKE_CASE`）
- [x] Agent 执行使用 `/api/v1/agent-runs`（当前返回 503，错误码 `AGENT_DISABLED`）
- [x] 文件下载使用 HMAC 签名短期 URL（TTL=300s）

### 24.3 安全验收（Stage 1 已全部通过 ✅）

- [x] 密码 Argon2id 哈希，不明文保存
- [x] RBAC 后端强校验（super_admin 绕过 + 项目级权限）
- [x] 文件路径经过 `ensure_within_root()` 安全校验
- [x] 上传校验：扩展名 + MIME + DWG header + 大小 + SHA-256
- [x] 管理员操作写审计日志
- [x] 时序攻击防御（dummy hash）
- [x] `.env` 不入 Git
- [x] Token jti 黑名单 + 密码变更失效
- [ ] CAD Worker 内网认证（Stage 4）

### 24.4 工程验收（Stage 1 已全部通过 ✅）

- [x] `uv sync` 可安装后端依赖，`uv.lock` 已提交
- [x] `npm ci` 可安装前端依赖，`package-lock.json` 已提交
- [x] `uv run ruff check app tests` 0 错误
- [x] `uv run pytest -q` 432 passed
- [x] Alembic 迁移可执行（3 版本，含 migration-test 验证）
- [x] Docker 镜像构建成功（multi-stage, non-root, HEALTHCHECK）
- [x] Worker 日志包含 `job_id`
- [x] README + docs（7 份）完整

---

## 25. 最终结论

DWG-Agent 是一套面向公司内部生产流程的 **CAD 文件智能处理平台**。最终系统由以下子系统构成：

```text
企业账号系统（Argon2id + JWT jti + RBAC 5 表）
+ 项目/图纸/文件管理（17 表，级联权限）
+ RESTful API 后端（64 端点，11 模块）
+ MySQL 元数据（pool_size=10 + pool_recycle=3600）
+ 双后端文件存储（LocalFS / MinIO，4 bucket）
+ Valkey 缓存/记忆/黑名单/进度（lazy init, fail-safe）
+ Celery 异步任务（Redis broker, 4 队列, 1 任务已实现）
+ LangGraph Agent 编排（Stage 2）
+ MCP 工具调用（Stage 2）
+ Python DXF 普通处理（Stage 3）
+ Windows C# 中望 CAD 高精度处理（Stage 4）
+ 人工复核闭环（decision: approved/rejected/needs_revision）
+ 审计日志不可变（30+ 操作类型）
+ Docker Compose 部署（9 服务 + 2 profiles）
```

最重要的工程边界：

```text
前端不解析 DWG；
FastAPI 不跑长任务；
MySQL 不存文件本体；
Agent 不负责安全边界；
Docker 不强行容器化中望 CAD；
CAD Worker 不管理业务权限；
所有正式接口遵守 RESTful API 资源规范。
```

**当前状态（v2.0）：Stage 1 已完成**，主链路 `用户 → 项目 → 文件 → 任务 → Worker → 结果 → 复核 → 审计` 全闭环，432 测试通过。详细实施指南、API 参考和架构文档见 `docs/` 目录。
