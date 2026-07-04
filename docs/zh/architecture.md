# DWG-Agent 平台 -- 架构文档

> **目标读者：** 接手维护或扩展此代码库的高级工程师。
> **阶段：** 阶段 1（平台骨架）。阶段 2-6 已规划但尚未启动。
> **规范权威来源：** `DWG-Agent企业平台技术规范.md`（仓库根目录，v2.0, 25 章, 1317 行）。

---

## 1. 系统概述

DWG-Agent 是一个面向企业内部使用的企业级 CAD 智能处理平台。它接受 DWG 图纸上传，管理项目/图纸/文件（具备完整的 RBAC 权限控制），并最终通过 LLM Agent 将自然语言任务路由到两个处理流水线：低精度 Linux DXF 流水线（Python/ezdxf）和高精度 Windows CAD Worker 流水线（C#/ZWCAD API）。

在阶段 1，平台提供了完整的 RESTful API、身份认证/RBAC、项目/文件/图纸/作业生命周期管理、带 DWG 验证的文件上传以及审计日志——即在实际 CAD 处理流水线上线之前，用户上传和管理 DWG 文件所需的一切功能。

```
DWG-Agent 平台（阶段 1）
═══════════════════════════════════════════════════
  用户 → React SPA → Nginx → FastAPI → MySQL（元数据）
                                    → 本地文件系统（文件）
                                    → Redis/Valkey（缓存/内存/黑名单）
```

---

## 2. 物理拓扑

### 2.1 目标生产拓扑（规范第 2 章）

规范定义了一个双节点部署方案：所有 Linux 服务通过 Docker Compose 容器化，另外有一个独立的 Windows 节点用于 CAD 处理：

```
┌─────────────────────────────────────────────────────────────┐
│                    Ubuntu 主服务器                            │
│                                                             │
│  Docker Compose 网络                                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Nginx :80/:443                                        │  │
│  │  - React 静态资源托管                                   │  │
│  │  - /api/v1/* → backend-api:8000                       │  │
│  │  - 速率限制, 上传大小上限                               │  │
│  └───────────────────┬───────────────────────────────────┘  │
│                      │                                      │
│  ┌───────────────────▼──────────────────────────────────┐  │
│  │ FastAPI 后端 :8000                                   │  │
│  │  - RESTful API, 认证/RBAC                             │  │
│  │  - 项目/文件/图纸/作业/审核/审计                       │  │
│  │  - Celery 任务分发                                    │  │
│  └───────┬────────────┬──────────────┬──────────────────┘  │
│          │            │              │                      │
│          ▼            ▼              ▼                      │
│  ┌────────────┐ ┌────────────┐ ┌───────────────────────┐   │
│  │ MySQL :3306│ │ Redis:6379 │ │ MinIO :9000            │   │
│  │ 元数据     │ │ 缓存/      │ │ DWG/DXF/结果文件        │   │
│  │            │ │ 内存/       │ │                        │   │
│  │            │ │ 进度        │ │                        │   │
│  └────────────┘ └─────┬──────┘ └───────────┬───────────┘   │
│                       │                    │                │
│  ┌────────────────────┼────────────────────┘                │
│  │  Celery Workers    │                                     │
│  │  - worker-agent    │                                     │
│  │  - worker-dxf      │                                     │
│  │  - worker-report   │                                     │
│  │  - worker-cad-dispatch                                    │
│  └────────────────────┘                                     │
└──────────────────────────┼──────────────────────────────────┘
                           │ 内部网络（API Key / mTLS）
┌──────────────────────────▼──────────────────────────────────┐
│                 Windows CAD Worker 节点                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ C# ASP.NET Core Worker Service                         │  │
│  │  - 轮询 GET /api/v1/internal/cad-worker/jobs/next     │  │
│  │  - 下载 DWG 到本地沙箱                                 │  │
│  │  - 调用 ZWCAD API / C# 插件                            │  │
│  │  - 导出 JSON/PNG/报告                                  │  │
│  │  - 上传结果到 MinIO                                    │  │
│  │  - PATCH 作业状态回 FastAPI                            │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          ▼                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ZWCAD / CAD .NET API / C# 插件                        │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 当前开发拓扑（阶段 1）

当前实现将所有服务运行在单台 Linux 开发机器上。Docker Compose 配置已编写并验证，但尚未用于生产环境。本地开发工作流如下：

```
浏览器 (localhost:5173)
  │ Vite 开发代理 → localhost:8000
  ▼
FastAPI (localhost:8000)
  │ SQLAlchemy 2.x 同步
  ▼
MySQL 8.x (localhost:3306)
  │
Redis/Valkey 9.1 (localhost:6379, systemd)
  │
Celery worker-report（report 队列, 本地 pidfile）
  │
本地文件系统 (backend/var/storage/)
```

**与规范的关键差异：** 本地开发默认仍使用本地文件系统，且没有 Windows CAD 节点。Docker 部署默认使用 MinIO，并启动 `worker-report` 用于阶段 1 的模拟任务；Agent/DXF workers 和 Flower 仍放在 `workers` / `monitoring` profiles 之后。

---

## 3. 逻辑分层架构

代码库遵循规范第 6 章定义的六层架构。以下是每一层的内容：其目录、职责、明确不做的事情以及实现状态。

### 3.1 层次映射

```
┌──────────────────────────────────────────────────────────────┐
│ 1. API 层                 app/api/v1/         11 个模块       │
│    路由, 参数解析, 认证依赖, 响应包装                          │
│    不做: 包含业务逻辑, 数据库查询, 文件 I/O                     │
├──────────────────────────────────────────────────────────────┤
│ 2. Schema 层              app/schemas/         10 个模块      │
│    Pydantic v2 请求/响应验证                                   │
│    不做: 包含业务规则, 数据库访问                               │
├──────────────────────────────────────────────────────────────┤
│ 3. Service 层             app/services/        12 个模块      │
│    业务逻辑编排, 跨领域工作流                                   │
│    不做: 依赖 FastAPI Request, 执行原始 SQL                   │
├──────────────────────────────────────────────────────────────┤
│ 4. Repository 层          app/repositories/   占位符          │
│    数据库读写封装（未来抽取）                                   │
│    不做: 处理业务规则（当前不适用）                              │
├──────────────────────────────────────────────────────────────┤
│ 5. Model 层               app/models/          10 个模块      │
│    SQLAlchemy 2.x ORM 模型（17 张表）                          │
│    不做: 包含业务逻辑, 验证（那是 schema 的职责）                │
├──────────────────────────────────────────────────────────────┤
│ 6. Core / 基础设施         app/core/            8 个模块       │
│    配置, 安全, 权限, 异常, Redis, 日志, 验证器                  │
│    不做: 包含领域逻辑                                          │
└──────────────────────────────────────────────────────────────┘

横向（跨层）:
┌──────────────────────────────────────────────────────────────┐
│ Agent 层          app/agents/          3 个桩     （阶段 2）   │
│ MCP 层            app/mcp_client/      2 个桩     （阶段 2）   │
│ Worker 层         app/workers/         celery_app + report 任务│
│                                        + agent/dxf/cad 桩      │
│ Storage 层        app/storage/          本地开发 + MinIO       │
│                                         Docker 后端             │
│ Integration 层    app/integrations/zwcad/ 2 个桩 （阶段 4）    │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 各层详述

#### API 层 -- `app/api/v1/`（11 个路由模块, /api/v1 下 63 个 + 1 个 health = 共 64 个端点）

| 模块 | 端点 | 规范章节 | 状态 |
|--------|-----------|-------------|--------|
| `auth_api.py` | POST sessions, DELETE sessions, POST refresh, GET me, PATCH password | 7.5 | 已完成 |
| `users_api.py` | GET/POST users, GET/PATCH/DELETE user, POST/DELETE user roles, password-reset/enable/disable requests | 7.6 | 已完成 |
| `roles_api.py` | GET/POST roles, GET permissions, PUT role permissions | 7.6 | 已完成 |
| `projects_api.py` | GET/POST projects, GET/PATCH/DELETE project, GET/POST members, PATCH/DELETE member | 7.7 | 已完成 |
| `files_api.py` | POST files, GET files, GET/DELETE file, GET download-url, GET download | 7.8 | 已完成 |
| `drawings_api.py` | GET/POST drawings, GET/PATCH/DELETE drawing, GET/POST versions, GET preview | 7.9 | 已完成 |
| `jobs_api.py` | GET/POST jobs, GET job, POST cancel/retry, GET steps/logs/results | 7.10 | 已完成 |
| `agent_runs_api.py` | POST agent-runs, GET agent-run, GET agent-run steps, GET agent-tools | 7.11 | 已完成（503 拦截） |
| `results_api.py` | GET result, GET download-url, POST review, GET review history | 7.12 | 已完成 |
| `reviews_api.py` | GET pending reviews | 7.12 | 已完成 |
| `audit_logs_api.py` | GET audit-logs, GET audit-log | 7.13 | 已完成 |

**职责:**
- 通过 FastAPI 依赖注入解析路径/查询/请求体参数
- 通过 `CurrentUser` 依赖应用身份认证（所有业务端点）
- 通过 `require_roles`、`require_project_member` 等应用 RBAC 检查
- 将所有响应包装为 `{data, meta}` 或 `{error, meta}` 信封格式
- 通过 `main.py` 中的异常处理器将领域异常映射到 HTTP 状态码

**不负责:**
- 包含业务逻辑 -- 委托给 service 层
- 执行原始 SQL 查询
- 直接访问文件系统
- 直接导入 `app.models`（输入输出使用 schemas）

**路由组装** (`router.py`)：所有 11 个路由模块在 `main.py` 中组装为单一的 `api_router`，挂载在 `/api/v1` 路径下。

#### Schema 层 -- `app/schemas/`（10 个 Pydantic v2 模块）

所有 schema 使用 `model_config = ConfigDict(from_attributes=True)` 以支持 ORM 模式反序列化。

**职责:**
- 验证请求体、查询参数和路径参数
- 定义响应结构
- 在 API 层和 service 层之间提供类型安全的 I/O 边界

**不负责:**
- 包含业务规则
- 访问数据库
- 执行副作用

#### Service 层 -- `app/services/`（12 个模块, ~1191 行）

| Service | 职责 | 关键依赖 |
|---------|---------------|-----------------|
| `project_service.py` | 项目 CRUD, 成员管理, 角色分配 | `Project`/`ProjectMember` 模型, `audit_service` |
| `file_service.py` | 文件元数据管理, 权限检查 | `StoredFile` 模型 |
| `drawing_service.py` | 图纸 CRUD, 版本管理（自动递增 version_no） | `Drawing`/`DrawingVersion` 模型 |
| `review_service.py` | 审核提交, 批准/拒绝决策 | `ReviewRecord` 模型 |
| `agent_service.py` | Agent 运行编排（阶段 2 桩） | `AgentRun` 模型 |
| `auth_service.py` | 带时间安全用户查找的登录, JWT 签发, 令牌黑名单 | `security.py`, `redis_client`, `User` 模型 |
| `user_service.py` | 用户 CRUD, 个人信息, 原子状态转换, 软删除 | `User` 模型, `audit_service` |
| `job_service.py` | 作业创建, Celery 桩分发, 状态生命周期更新 | `Job`/`JobStep` 模型 |
| `storage_service.py` | 文件保存/检索/删除, DWG 文件头验证, SHA-256 哈希, 下载 URL 签名 | `path_utils.py`, `file_hash.py`, `StoredFile` 模型 |
| `audit_service.py` | 结构化审计追踪写入（谁, 什么, 资源, 变更前后, IP, UA） | `AuditLog` 模型 |
| `redis_memory.py` | Agent 会话记忆存储 (`agent:memory:{session_id}`, JSON 列表, TTL=7200s, 最多 20 条消息) | `redis_client` |
| `cache_service.py` | 通用键值缓存 (`cache:{namespace}:{key}`, Redis 不可用时优雅降级) | `redis_client` |

**职责:**
- 跨模型、schema 和外部服务编排业务工作流
- 强制执行业务规则（状态转换、数据级权限检查）
- 协调事务边界

**不负责:**
- 依赖 `fastapi.Request` 或 `fastapi.Response`（可接受：`UploadFile` 和其他 Starlette 数据类型；`Request` 仅在 `TYPE_CHECKING` 块中用于类型提示）
- 包含路由层逻辑（参数提取、HTTP 响应构造）
- 执行原始 SQL（使用 SQLAlchemy ORM）

#### Model 层 -- `app/models/`（10 个文件, 17 张表, 401 行）

所有模型继承自 `Base`（SQLAlchemy `DeclarativeBase`）和 `TimestampMixin`（提供 `created_at`、`updated_at`）。

| 文件 | 表 | 规范章节 |
|------|--------|-------------|
| `user.py` | `sys_users` | 9.2 |
| `role.py` | `sys_roles`, `sys_permissions`, `sys_user_roles`, `sys_role_permissions` | 8.3, 9.2 |
| `project.py` | `projects`, `project_members` | 9.2 |
| `file.py` | `files` | 9.2 |
| `drawing.py` | `drawings`, `drawing_versions` | 9.2 |
| `job.py` | `jobs`, `job_steps` | 9.2 |
| `result.py` | `analysis_results`, `review_records` | 9.2 |
| `agent_run.py` | `agent_runs`, `agent_run_steps` | 9.2 |
| `audit_log.py` | `audit_logs` | 9.2 |

**职责:**
- 定义表结构、列、类型、约束、关系
- 提供 ORM 级级联和延迟加载配置

**不负责:**
- 包含业务逻辑
- 定义验证规则（那是 schema 层的职责）
- 了解 HTTP 或 API 关注点

#### Core 层 -- `app/core/`（8 个模块, ~388 行）

| 模块 | 职责 |
|--------|---------------|
| `config.py` | 来自 `.env` 的 pydantic-settings, URL 计算属性, 功能开关 |
| `security.py` | 密码哈希（Argon2id, 通过 `pwdlib`）, JWT 创建/解码（HS256, jti 声明） |
| `permissions.py` | `app/api/deps` 的规范导入接口（权限检查函数） |
| `exceptions.py` | `AppHTTPException` 基类 + 工厂函数 (`not_found`, `forbidden`, `service_unavailable`) |
| `redis_client.py` | 带 hiredis 的延迟初始化同步 Redis 客户端, 不可用时优雅降级 |
| `constants.py` | 文件大小限制, 允许的扩展名, 用户状态常量 |
| `logger.py` | 日志配置辅助工具 |
| `validators.py` | 按资源排序的列白名单验证（防止通过 sort_by 参数注入 SQL） |

**职责:**
- 提供其他每一层都依赖的基础设施
- 集中管理配置、安全原语、错误类型

**不负责:**
- 包含领域逻辑
- 了解请求/响应结构

#### Agent 层 -- `app/agents/`（3 个文件, 全部为桩）

三个文件均为桩/占位符：`agent_factory.py` 和 `tool_registry.py` 是单行 docstring 桩；`prompts.py` 包含一个 `SYSTEM_PROMPT` 常量占位符。目标：阶段 2。

规范参考：第 11 章（Agent 技术规范）。实现时：
- `agent_factory.py` 将使用 `ChatOpenAI` 模型创建 LangGraph `create_react_agent`
- `prompts.py` 将包含 CAD 任务分解的系统提示词
- `tool_registry.py` 将 MCP 工具适配为 LangChain 工具格式

功能开关：`AGENT_ENABLED`（当前为 `false` -- `/api/v1/agent-runs` 返回 503）

#### MCP 层 -- `app/mcp_client/`（2 个文件, 全部为桩）

规范参考：第 12 章（MCP 工具层）。实现时：
- `cad_mcp_client.py` 将管理与 CAD 工具服务器的 stdio MCP 连接（`connect()`、`disconnect()`、`list_tools()`、`call_tool()`）
- `mcp_tool_adapter.py` 将 MCP 工具定义转换为 LangChain 兼容的可调用对象

来自规范的关键设计约束（第 11.4 节）：MCP 连接失败绝不能导致服务崩溃；工具不可用时 agent-runs 返回 503。

#### Worker 层 -- `app/workers/`（5 个文件）

`celery_app.py` 定义了真实的 Celery 应用，使用配置中的 Redis broker/result backend。`tasks_report.py` 注册了阶段 1 中正常作业创建所使用的 `run_stub_job` 任务。`tasks_agent.py`、`tasks_dxf.py` 和 `tasks_cad.py` 仍为占位符，因为具体的 Agent/DXF/CAD 处理被有意推迟。

规范参考：第 13 章（Celery 设计）。在阶段 1，作业是异步 Celery 模拟任务；模拟任务仅证明分发、状态转换、结果文件创建以及审计/审核管道可用。

未来阶段将向 `agent`、`dxf` 和 `cad` 队列添加真实工作，同时保持相同的任务状态机。

#### Storage 层 -- `app/storage/`（3 个文件）

`base.py` 定义了 `AbstractStorageBackend`；`local_storage.py` 为本地开发实现了文件系统存储；`minio_storage.py` 实现了 Docker 部署使用的 S3 兼容 MinIO 后端。`storage_service.py` 执行 DWG 验证、哈希和元数据创建，然后通过选定的后端写入字节。

规范参考：第 10 章（文件存储）。定义了四个存储桶：`dwg-original`、`dwg-derived`、`dwg-reports`、`dwg-temp`。

#### Integration 层 -- `app/integrations/zwcad/`（2 个文件, 全部为桩）

`client.py` 和 `schemas.py` 均为单行桩。目标：阶段 4。

规范参考：第 15 章（ZWCAD 高精度流水线）。实现时，该层将通过内部 HTTP API 与 Windows CAD Worker 节点通信，使用 API Key 或 mTLS 认证。

#### Repository 层 -- `app/repositories/`（仅有空的 `__init__.py`）

预留的占位符，用于将来从 service 中抽取数据库读写模式。在阶段 1，service 通过 SQLAlchemy session 直接访问模型。

---

## 4. 组件依赖关系图

```
                    ┌──────────────────┐
                    │   FastAPI main.py │
                    │  (lifespan, CORS,  │
                    │   异常处理器)       │
                    └────────┬─────────┘
                             │ 挂载
                    ┌────────▼─────────┐
                    │  api/v1/router.py │
                    │  (11 个路由模块)   │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
       │  schemas/   │ │ api/deps │ │  services/  │
       │ (Pydantic)  │ │(认证,    │ │ (业务        │
       │             │ │ RBAC)    │ │  逻辑)       │
       └─────────────┘ └────┬─────┘ └──────┬──────┘
                            │              │
                            │       ┌──────┼──────┐
                            │       │      │      │
                     ┌──────▼───────▼─┐ ┌──▼──────▼──┐
                     │   models/      │ │  core/     │
                     │ (SQLAlchemy)   │ │ (config,   │
                     │ 17 张表        │ │  security, │
                     └───────┬────────┘ │  redis,    │
                             │          │  exceptions│
                     ┌───────▼────────┐ └──────┬─────┘
                     │    MySQL 8.x   │        │
                     │  (运行时数据库)  │  ┌─────▼──────┐
                     └────────────────┘  │ Valkey 9.1 │
                                         │  (Redis)   │
                                         └────────────┘

未来（阶段 2-4）在以下分隔线后添加:
- - - - - - - - - - - - - - - - - - - - - - - -
┌──────────┐  ┌──────────┐  ┌─────────────────┐
│ agents/  │  │mcp_client│  │ workers/        │
│ (LangGr) │──│/ (MCP)   │  │ (Celery 真实,    │
│          │  │          │  │  任务 阶段 2+)   │
└──────────┘  └──────────┘  └─────────────────┘
                                   │
┌──────────────────────────────────┼──────────┐
│ storage/ (本地开发,              │          │
│   MinIO Docker 后端)             │          │
│ integrations/zwcad/ (C# Worker)  │          │
└──────────────────────────────────────────────┘
```

### 依赖规则

1. **API → Schemas → Services → Models → DB**（严格的自上而下调用链）
2. **Core 层无处不在** -- 每一层都可以从 `app.core.*` 导入
3. **Schema 绝不直接导入 Model** -- 它定义的是结构，不是查询
4. **Service 绝不导入 FastAPI Request/Response** -- 它返回领域对象
5. **Worker 任务调用 Service** -- 绝不重复业务逻辑（规范第 6.2.4 节）
6. **Agent 代码**不能直接访问数据库或文件系统（规范第 6.2.5 节）

---

## 5. 数据流

### 5.1 请求生命周期（阶段 1）

```
HTTP 请求
  │
  ▼
Nginx（未来）/ Vite 代理（开发环境）
  │
  ▼
FastAPI main.py
  ├── X-Request-ID 中间件（生成或透传）
  ├── CORS 中间件
  ▼
api/v1/router.py
  ├── 路由匹配 (/api/v1/{resource})
  ▼
路由处理函数
  ├── FastAPI 参数解析（路径/查询/请求体 → Pydantic schema）
  ├── 认证依赖（CurrentUser → JWT 验证 → 数据库用户查找）
  ├── RBAC 依赖（角色检查 / 项目成员检查）
  ▼
Service 层
  ├── 业务逻辑编排
  ├── 通过 SQLAlchemy session 进行数据库查询（由 get_db 依赖注入）
  ├── 审计日志写入（audit_service）
  ▼
响应构造
  ├── 成功: {"data": {...}, "meta": {"request_id": ..., "timestamp": ...}}
  ├── 列表: 添加 {"pagination": {"page": ..., "page_size": ..., "total": ...}}
  ├── 错误: {"error": {"code": ..., "message": ..., "details": {}}, "meta": {...}}
  ▼
异常处理器 (main.py)
  ├── AppHTTPException → 结构化错误响应
  ├── StarletteHTTPException → 通用 HTTP 错误
  ├── RequestValidationError → 422 带字段级错误信息
  └── Exception → 500（记录 traceback, 除非 DEBUG=true 否则绝不泄露给客户端）
```

### 5.2 认证流程

```
POST /api/v1/auth/sessions {username, password}
  │
  ▼
auth_service.authenticate_user(db, username, password)
  ├── SELECT User WHERE username = ?
  ├── 用户不存在或状态 != active:
  │   └── 使用 DUMMY_HASH 进行 Argon2id 验证（恒定时间, 防止时间侧信道攻击）
  │       → 返回 None → 401
  ├── 用户存在且状态为 active:
  │   ├── Argon2id verify(password, stored_hash)
  │   ├── 匹配: 更新 last_login_at, 返回 user
  │   └── 不匹配: 返回 None → 401
  ▼
build_login_token(user) + build_refresh_token(user)
  ├── JWT HS256, sub=user.id, jti=UUID4, exp=now+30min（访问令牌） / +14d（刷新令牌）
  ▼
响应: {access_token, refresh_token, token_type, expires_in, user}
```

### 5.3 登出 / 令牌黑名单

```
DELETE /api/v1/auth/sessions/current
  │
  ▼
从当前访问令牌中提取 jti（不验证直接解码）
  │
  ▼
Redis SETEX "blacklist:jti:{jti}" TTL=(exp - now) value="1"
  ├── TTL 匹配令牌的剩余有效期 → 键自动清理
  ├── Redis 不可用 → 记录 warning, 跳过（降级模式）
  ▼
认证依赖在每次认证请求上检查 is_token_blacklisted(jti)
  ├── Redis 命中 → 401
  └── Redis 未命中 / 不可用 → 允许（为可用性而选择故障开放）
```

### 5.4 文件上传流程

```
POST /api/v1/files (multipart/form-data, file 字段)
  │
  ▼
路由处理函数
  ├── 认证: CurrentUser
  ├── 验证: 文件扩展名 (.dwg)
  ├── 验证: 文件大小（max_upload_size_mb 设置）
  ▼
storage_service.save_uploaded_file(db, user, file)
  ├── 计算 SHA-256 哈希
  ├── 读取前 6 字节 → 验证 DWG 魔术文件头 (AC1012–AC1032)
  ├── 验证最小 1024 字节
  ├── ensure_within_root(storage_root, target_path) → 路径遍历防护
  ├── 通过 StorageBackend 写入文件（本地开发 / MinIO Docker）
  ├── INSERT INTO files (bucket, storage_key, original_name, sha256, size, ...)
  ├── 写入审计日志 (FILE_UPLOADED)
  ▼
响应: {data: {id, original_name, file_ext, size_bytes, sha256, storage_key, status}, meta: ...}
```

### 5.5 下载 URL 流程

```
GET /api/v1/files/{file_id}/download-url
  │
  ▼
认证 + 文件所有权 / 项目成员检查
  │
  ▼
file_service.build_signed_download_url(file_id)
  ├── HMAC-SHA256(file_id + exp_timestamp, secret) → 签名
  ├── URL = /api/v1/files/{file_id}/download?expires={ts}&signature={sig}
  ├── TTL = 300 秒
  ▼
GET /api/v1/files/{file_id}/download?expires={ts}&signature={sig}
  ├── 验证 expires 未过期
  ├── 重新计算 HMAC → 与 sig 比较（恒定时间）
  └── 流式返回文件, 带 Content-Disposition 头
```

### 5.6 作业生命周期（阶段 1 -- Celery 模拟任务）

```
POST /api/v1/jobs {drawing_id, task_type, precision_level, params}
  │
  ▼
job_service.create_job(db, user, data)
  ├── 验证图纸存在且用户具有项目访问权限
  ├── INSERT INTO jobs (status="queued", ...)
  ├── 写入审计日志 (JOB_CREATED)
  ▼
job_service.enqueue_stub_job(job_id)
  │
  ▼
Celery worker-report 消费 app.workers.tasks_report.run_stub_job
  ├── status → "running"
  ├── INSERT INTO job_steps (step_name="dispatch_stub_worker")
  ├── 通过 StorageBackend 写入 JSON 结果文件（本地或 MinIO）
  ├── INSERT INTO analysis_results
  ├── INSERT INTO job_steps (step_name="write_stub_result")
  └── status → "succeeded"
```

任务体是有意模拟的；Agent/DXF/CAD 处理保持推迟。

---

## 6. 关键架构决策

### 6.1 同步 API + Celery Worker 边界

**决策：** FastAPI 请求处理函数使用 SQLAlchemy 2.x 同步 session 和同步 Redis 客户端。作业执行跨越显式的 Celery 边界，在 worker 进程中运行。

**原因：** API 操作保持短小简单，而即使是阶段 1 的模拟作业也遵循生产级的任务分发模式。这使请求延迟有上限，避免在 FastAPI 内运行长耗时的 CAD 工作。

**权衡：** 在极高并发（>200 req/s）下，同步模型将需要更多 gunicorn workers。规范的 Docker Compose 配置使用 `--workers 4` 和 `--timeout 120`。目前这已超出阶段 1 的需求。

### 6.2 MySQL 运行时 + SQLite 测试隔离

**决策：** 运行时使用 MySQL 8.x，连接串为 `mysql+pymysql://`。测试使用内存 SQLite，通过 `StaticPool`。

**运行时使用 MySQL 的原因：**
- 规范第 4 章要求生产环境使用 MySQL 8.x
- 企业环境：现有的 MySQL 运维知识、备份工具、监控
- 所需功能：行级锁 (`SELECT FOR UPDATE`)、正确的并发、连接池

**测试使用 SQLite 的原因：**
- 零设置 -- CI/开发环境无需外部 MySQL 服务器依赖
- `StaticPool` 确保完全隔离（每个测试获得自己的内存数据库）
- WAL 模式 + `foreign_keys=ON` + `busy_timeout=5000` 按连接应用
- 432 个测试运行具有快速的收集和执行速度

**MySQL 连接池：** `pool_recycle=3600`（在 MySQL 默认 `wait_timeout` 28800s 之前回收），`pool_size=10`，`max_overflow=20`。仅在 `database_url` 以 `mysql` 开头时应用。

### 6.3 时间侧信道防御

**决策：** 当用户不存在或处于非活跃状态时，`authenticate_user()` 仍然使用具有相同参数（m=65536, t=3, p=4）的预先计算的虚拟哈希执行 Argon2id 哈希验证。

```python
_DUMMY_VERIFY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHRzb21lc2FsdHNhbHQ$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
```

**原因：** 如果没有这个机制，不存在的用户会立即返回（快），而有效用户会触发 Argon2id 验证（慢）。攻击者可以通过测量响应时间差异来枚举有效用户名。虚拟哈希无论何种情况都消耗同等的 CPU 时间。

### 6.4 基于 jti 的 JWT 令牌黑名单

**决策：** JWT 包含 `jti`（JWT ID）声明（UUID4）。登出时，`jti` 被存储到 Redis 中，TTL 匹配令牌的剩余有效期。认证依赖在每次请求时检查黑名单。

**原因：** JWT 本质上是无状态的 -- 没有黑名单就无法"撤销"它们。替代方案（短生命周期令牌 + 频繁刷新）会带来更差的用户体验。基于 jti 的黑名单配合 Redis TTL 提供了：
- 即时登出（下次请求即拒绝令牌）
- 无永久存储增长（键随令牌过期自动过期）
- 优雅降级（Redis 宕机 = 故障开放，令牌仍然有效）

**权衡：** 每次认证请求都需要一次 Redis `EXISTS` 调用。这会增加约 0.1ms 延迟。对于内部企业平台来说可以接受。

### 6.5 原子状态转换

**决策：** 用户状态变更（active ↔ disabled，软删除）使用 `UPDATE WHERE + rowcount` 而非读取-修改-写入：

```python
def transition_user_status(db, user_id, to_status, *, set_deleted_at=False):
    values = {"status": to_status}
    if set_deleted_at:
        values["deleted_at"] = datetime.now(UTC)
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.status != DELETED)
        .values(**values)
    )
    return result.rowcount > 0  # 由调用方决定是否抛出异常
```

**原因：** 防止 SELECT 后 UPDATE 之间的 TOCTOU 竞态条件，即另一个管理员可能同时在切换用户状态。`WHERE status != DELETED` 守卫确保已软删除的用户不能被修改。返回 `bool` 而非抛出异常让调用方自行决定错误语义。这是乐观并发控制中的常见模式。

### 6.6 SELECT FOR UPDATE 写保护

**决策：** `get_user_or_404(db, user_id, for_update=True)` 使用 `SELECT ... FOR UPDATE` 在当前事务中锁定该行。

**原因：** 防止对同一用户行的并发写入（例如角色分配和资料更新同时发生）。`FOR UPDATE` 子句在 MySQL 中获取行级排他锁，在事务提交时释放。

### 6.7 级联项目状态检查

**决策：** `require_active_project()` 嵌入在 `require_project_member()` 内部，而不是在每个路由中独立调用。当项目被删除/归档时，所有基于成员的访问自动返回 404。

**原因：** 每个资源（图纸、文件、作业）都限定在项目范围内。在成员依赖中一次性检查项目状态意味着没有路由可以意外忘记检查。这也意味着删除的项目会级联清理所有下游访问，无需修改代码。

### 6.8 文件安全

**路径遍历防护：** `ensure_within_root(root, candidate)` 解析两个路径并验证候选路径以根路径前缀开头。任何 `../` 或符号链接逃逸都会引发 400 错误。

**DWG 文件头验证：** 上传验证读取前 6 字节并检查 DWG 魔术字节（`AC1012` 到 `AC1032`）。强制执行最小 1024 字节文件大小。

**HMAC 签名的下载 URL：** 下载端点具有时间限制（TTL=300s），附带防止 URL 篡改的 HMAC-SHA256 签名。

**原因：** 这些都是规范要求（第 10.4、19.3 节）。来自外部来源的 DWG 文件是不可信的输入。路径遍历、文件类型伪造和直接文件访问都必须在平台边界处被阻止。

### 6.9 分阶段上线的功能开关

**决策：** `Settings` 中有三个布尔功能开关：`agent_enabled`、`dxf_pipeline_enabled`、`cad_worker_enabled`。全部默认为 `False`。

**原因：** 规范定义了 6 阶段上线计划。功能开关让我们可以在合并代码到主分支的同时保持其黑暗状态，独立测试各个子系统，并进行金丝雀发布。在阶段 1，`agent_enabled=false` 使 `/api/v1/agent-runs` 返回 503，并附带清晰的错误信息 (`AGENT_DISABLED`)。

### 6.10 基于组件字段的配置，而非单体 URL

**决策：** 配置使用组件字段（`mysql_host`、`mysql_port`、`mysql_database`、`mysql_user`、`mysql_password`），配合计算属性 `mysql_url` 和 `redis_url`，而不是单一的 `DATABASE_URL` 字符串。

**原因：** 规范第 18 章定义了此模式。它支持：
- 按组件的 Docker 覆盖（例如 Docker 中用 `MYSQL_HOST=mysql`，开发中用 `127.0.0.1`）
- 密码中特殊字符的 URL 编码（通过 `urllib.parse.quote`）
- `.env` 文件中清晰的关注点分离
- 从相同的 Redis 组件编程组装 Celery broker/result URL

---

## 7. RBAC 模型

### 7.1 Schema（5 张表）

```
sys_users ──< sys_user_roles >── sys_roles ──< sys_role_permissions >── sys_permissions
     │
     └── projects ──< project_members >── sys_users（project_role: owner/engineer/reviewer/viewer）
```

### 7.2 全局角色（7 种）

| 角色 | 范围 | 关键权限 |
|------|-------|----------------|
| `super_admin` | 全局 | 绕过所有权限检查 -- 无需权限检查 |
| `admin` | 全局 | 管理用户、项目, 查看所有作业 |
| `engineer` | 全局 + 项目 | 上传文件、创建作业, 查看项目结果 |
| `reviewer` | 全局 + 项目 | 查看待审核项, 批准/拒绝结果 |
| `operator` | 项目 | 执行分配的任务 |
| `viewer` | 项目 | 项目内只读访问 |
| `auditor` | 全局 | 仅读取审计日志 |

### 7.3 权限层次结构（规范第 8.3 节）

```
是否已认证?
  → 否 → 401
  → 是
用户是否已启用 (status = 'active')?
  → 否 → 403
  → 是
用户是否具有 super_admin 角色?
  → 是 → 全部允许
  → 否
用户是否具有此资源类型的全局 admin 角色?
  → 是 → 允许
  → 否
用户是否为目标项目的成员?
  → 否 → 仅检查全局角色（可能仍有访问权限）
  → 是
用户的项目角色是否允许此操作?
  → 是 → 允许
  → 否 → 403
```

**实现说明：** `super_admin` 绕过所有权限检查。`require_roles()` 依赖首先检查 `super_admin`（短路）。项目级检查通过 `require_project_member()` 进行，该函数还内嵌了 `require_active_project()`。

---

## 8. 数据库 -- 物理 Schema

### 8.1 引擎配置

```python
# MySQL 运行时
engine = create_engine(
    settings.database_url,  # mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent
    pool_pre_ping=True,     # 使用前验证连接
    pool_recycle=3600,      # 在 MySQL wait_timeout 之前回收
    pool_size=10,
    max_overflow=20,
)

# SQLite 测试（通过 conftest.py 按测试隔离）
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
# 按连接 pragmas: WAL 模式, foreign_keys=ON, busy_timeout=5000
```

### 8.2 Session 工厂

```python
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,       # 手动刷新控制
    autocommit=False,      # 显式提交
    expire_on_commit=False # 避免提交后延迟加载
)

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 8.3 全部 17 张表

| # | 表 | 主键 | 外键 | 备注 |
|---|-------|-------------|--------------|-------|
| 1 | `sys_users` | id | -- | username UNIQUE, password_hash, status, 软删除 |
| 2 | `sys_roles` | id | -- | code UNIQUE, is_system 标志 |
| 3 | `sys_permissions` | id | -- | code UNIQUE, resource + action |
| 4 | `sys_user_roles` | (user_id, role_id) | users.id, roles.id | 多对多关联 |
| 5 | `sys_role_permissions` | (role_id, permission_id) | roles.id, permissions.id | 多对多关联 |
| 6 | `projects` | id | owner_id → users.id | code UNIQUE, status |
| 7 | `project_members` | id | project_id → projects.id, user_id → users.id | project_role 枚举 |
| 8 | `files` | id | uploaded_by → users.id | storage_key, sha256, status |
| 9 | `drawings` | id | project_id → projects.id | current_version_id 自引用 |
| 10 | `drawing_versions` | id | drawing_id → drawings.id, file_id → files.id, created_by → users.id | version_no |
| 11 | `jobs` | id | project_id, drawing_id, created_by | task_type, precision_level, pipeline, status, params_json |
| 12 | `job_steps` | id | job_id → jobs.id | step_name, worker_name, status, input/output_json |
| 13 | `agent_runs` | id | user_id, project_id, drawing_id, file_id | session_id, task, status, answer, output_file_id |
| 14 | `agent_run_steps` | id | agent_run_id → agent_runs.id | step_type, tool_name, arguments_json, status |
| 15 | `analysis_results` | id | job_id, drawing_id | result_type, result_json, confidence, result_file_id |
| 16 | `review_records` | id | result_id → analysis_results.id, reviewer_id → users.id | decision (approved/rejected), comment |
| 17 | `audit_logs` | id | actor_user_id → users.id | action, resource_type, resource_id, before/after_json, ip_address |

表 1-12 在阶段 1 中处于活跃状态。表 13-14（agent_runs, agent_run_steps）已创建并可查询，但仅在阶段 2 写入。表 15-16（analysis_results, review_records）已创建且部分使用（模拟作业结果写入 analysis_results；审核记录功能可用）。

### 8.4 迁移

`backend/migrations/versions/` 中有两个 Alembic 版本：

1. `40452ddd24e7` -- **initial**：创建全部 17 张表及其列、约束、索引
2. `b8f9e7d6c5a4` -- **add_missing_timestamp_columns**：回填初始迁移中遗漏 `TimestampMixin` 的表的 `created_at`/`updated_at` 列

Alembic 目标为 MySQL：`sqlalchemy.url = mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent`

---

## 9. Redis / Valkey 基础设施

### 9.1 服务器

Valkey 9.1（Redis 兼容分支），通过 systemd 作为 `redis.service` 本地运行。本地开发无密码。Docker 部署使用 `ghcr.io/valkey-io/valkey:9.0-alpine` 并配置 `requirepass`。

### 9.2 客户端 (`app/core/redis_client.py`)

- 同步 `redis-py` 5.x，使用 `hiredis` 解析器以提升性能
- 延迟初始化：`get_redis()` 在首次调用时创建连接池
- Redis 不可用时返回 `None` 而非崩溃（所有调用方均处理此情况）
- `close_redis()` 在 FastAPI 关闭时调用（lifespan）

### 9.3 使用模式

| 服务 | 键模式 | 数据 | TTL | 阶段 |
|---------|-----------|------|-----|-------|
| 令牌黑名单 | `blacklist:jti:{jti}` | "1" | 令牌剩余有效期 | 1（活跃） |
| Agent 记忆 | `agent:memory:{session_id}` | JSON 消息列表 | 7200s | 1（仅基础设施） |
| 缓存 | `cache:{namespace}:{key}` | 任意 | 可变 | 1（仅基础设施） |
| Celery broker | `redis://.../0` | 任务消息 | -- | 2+ |
| Celery results | `redis://.../1` | 任务结果 | -- | 2+ |

### 9.4 测试策略

双层 Redis 测试：
1. **FakeRedis** (`fakeredis[lua]`)：`conftest.py` 中的自动使用 fixture 通过 monkeypatch 将 `get_redis()` 替换为返回 `FakeRedis` 实例。这覆盖了 419 个非真实 Redis 测试（432 总计 - 13 个真实 Redis 专用），零外部依赖。
2. **真实 Redis** (`test_redis_real.py`)：针对实际本地 Valkey 实例的集成测试。Redis 不可达时自动跳过 (`pytest.skip`)。

---

## 10. 存储架构

### 10.1 当前（阶段 1）

文件通过 `AbstractStorageBackend` 存储。本地开发默认使用 `LocalFileStorage`，存储路径为 `backend/var/storage/`；Docker 部署默认使用 `MinioStorage`，地址为 `http://minio:9000`。`storage_service.py` 处理：
- 文件保存（通过选定后端写入字节）
- 文件检索（本地 `FileResponse` 或 MinIO 流式响应）
- 文件删除（删除文件, 软删除数据库记录）

`file_service.py` 处理：
- 下载 URL 生成（HMAC 签名, 300s TTL, 通过 `build_signed_download_url`）
- 文件读取权限检查（项目成员、所有权、全局管理员）

### 10.2 存储后端

`app/storage/` 目录包含：

```
app/storage/
├── base.py           # AbstractStorageBackend ABC + 存储异常
├── local_storage.py  # LocalFileStorage
└── minio_storage.py  # MinioStorage（S3 兼容）
```

存储后端通过 `STORAGE_BACKEND=local|minio` 选择。MinIO 根据规范第 10.2 节使用四个存储桶：
- `dwg-original` -- 原始 DWG 上传（绝不覆盖）
- `dwg-derived` -- DXF, JSON, PNG, SVG 衍生文件
- `dwg-reports` -- Excel, PDF, ZIP 报告
- `dwg-temp` -- 临时 worker 沙箱文件（自动清理）

---

## 11. API 设计约定

### 11.1 URL 结构

```
/api/v1/{resource}                    # 集合
/api/v1/{resource}/{id}               # 单个资源
/api/v1/{resource}/{id}/{subresource} # 嵌套子资源
```

### 11.2 响应信封

所有响应遵循一致的信封格式：

**成功（单个）:**
```json
{"data": {...}, "meta": {"request_id": "...", "timestamp": "..."}}
```

**成功（列表）:**
```json
{"data": [...], "pagination": {"page": 1, "page_size": 20, "total": 120}, "meta": {...}}
```

**错误:**
```json
{"error": {"code": "ERROR_CODE", "message": "Human-readable", "details": {...}}, "meta": {...}}
```

### 11.3 HTTP 状态码使用规范

| 状态码 | 用途 |
|------|-------|
| 200 | 成功读取或更新 |
| 201 | 资源已创建（返回所创建实体的 POST） |
| 202 | 已接受异步处理（作业提交, agent 运行） |
| 204 | 成功但无响应体（删除, 登出） |
| 400 | 客户端语义错误（无效参数组合） |
| 401 | 未认证（缺失/无效/过期/已拉黑的令牌） |
| 403 | 已认证但未授权 |
| 404 | 资源未找到 |
| 409 | 冲突（重复用户名, 无效状态转换） |
| 413 | 上传超过大小限制 |
| 415 | 不支持的文件类型 |
| 422 | Pydantic 验证失败 |
| 429 | 速率限制（登录失败） |
| 500 | 未处理的服务器错误 |
| 503 | 依赖不可用（Agent 禁用, MCP 宕机, CAD Worker 不可达） |

### 11.4 错误码约定

错误码为 `UPPER_SNAKE_CASE` 字符串，可被机器解析且稳定。示例：`NOT_FOUND`、`FORBIDDEN`、`FILE_TYPE_NOT_ALLOWED`、`AGENT_DISABLED`、`INVALID_STORAGE_PATH`。前端代码可以通过 `error.code` 进行判断，而无需解析 `error.message`。

---

## 12. 测试架构

### 12.1 测试基础设施

| 组件 | 技术 | 用途 |
|-----------|-----------|---------|
| 运行器 | pytest | 测试发现和执行 |
| HTTP 客户端 | `fastapi.testclient.TestClient` | 进程内 API 测试 |
| 数据库隔离 | SQLite `:memory:` + `StaticPool` | 每测试隔离数据库 |
| Redis 隔离 | `fakeredis[lua]` 自动使用 monkeypatch | 零依赖 Redis 模拟 |
| Redis 集成 | 真实 Valkey 本地实例 | 集成安全网 (`test_redis_real.py`) |
| Fixtures | `conftest.py` | 数据库设置/拆卸, 认证头, 测试数据工厂 |

### 12.2 测试类别（24 个文件, 432 个测试）

| 类别 | 文件 | 焦点 |
|----------|-------|-------|
| API 回归 | `test_api_regressions.py` | 所有 64 个端点返回正确的状态码和结构 |
| 安全边界 | `test_security_boundaries.py`, `test_rbac_deep.py` | 需要认证, RBAC 强制执行, 路径遍历防御 |
| 令牌生命周期 | `test_token_lifecycle.py` | 登录, 刷新, 黑名单, 过期, jti 验证 |
| Redis 栈 | `test_redis_client.py`, `test_redis_memory.py`, `test_cache_service.py`, `test_redis_real.py` | 客户端初始化, 记忆 TTL, 缓存回退, 真实集成 |
| 配置 | `test_config.py` | MySQL/Redis URL 组装, 组件字段, 功能开关 |
| 数据库 session | `test_db_session.py` | 引擎创建, 健康检查, WAL pragmas |
| 边缘情况 | `test_edge_cases.py`, `test_rigorous.py`, `test_deep_verify.py` | 并发操作, 大负载, Unicode, null 处理 |
| Service 层 | `test_service_layer.py` | Service 函数单元测试（用户、文件、项目、认证） |
| 阶段 1 边界 | `test_stage1_boundaries.py` | Agent 503, Celery 模拟任务, 功能开关拦截 |
| 流程测试 | `test_smoke_flow.py`, `test_job_lifecycle.py` | 端到端: 注册 → 登录 → 上传 → 作业 → 结果 |
| Celery/MinIO 部署 | `test_celery_minio_deployment.py` | Celery worker 健康, MinIO 存储桶操作, 端到端作业流水线 |
| 跨审计修复 | `test_cross_audit_fixes.py` | 渗透测试缺陷回归测试（31 个测试函数） |
| 脚本验证 | `test_scripts.py` | Shell 脚本语法, lib.sh 函数, db.sh 操作 |
| 迁移测试 | `test_migrations.py` | Alembic 版本数量, 表存在性 |
| Compose 测试 | `test_compose.py` | YAML 解析, 服务数量, 必需服务存在 |
| 健康检查 | `test_health.py` | `/health` 端点, 数据库健康检查函数 |

### 12.3 测试隔离机制

```python
# conftest.py (简化)
@pytest.fixture(autouse=True)
def _isolate_redis_client(monkeypatch):
    """将真实 Redis 单例替换为 FakeRedis，每个测试独享。"""
    fake = FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._redis_client", fake)
    monkeypatch.setattr("app.core.redis_client._redis_available", True)
    yield
    fake.flushall()
    fake.close()

@pytest.fixture(autouse=True)
def _isolate_test_db(monkeypatch):
    """每个测试使用独立的内存 SQLite 连接。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    app.dependency_overrides[original_get_db] = _override_get_db
    # ... 同时 monkeypatch init_db, job_service, db.session 中的 SessionLocal/engine
    yield
    app.dependency_overrides.clear()
```

---

## 13. 实现状态矩阵

### 阶段 1 -- 已完成

| 组件 | 状态 | 行数 | 测试 | 备注 |
|-----------|--------|-------|-------|-------|
| FastAPI 应用 (main.py) | 已完成 | 125 | 已覆盖 | Lifespan, CORS, X-Request-ID, 4 个异常处理器, /health |
| API 路由 (11 个模块) | 已完成 | 2,008 | 已覆盖 | 全部 64 个端点返回正确信封 |
| Pydantic schemas (10 个模块) | 已完成 | 505 | 已覆盖 | 全部使用 v2 `from_attributes=True` |
| 业务 service (12 个模块) | 已完成 | ~1191 | 已覆盖 | Auth, user, job, project, file, drawing, review, agent, storage, audit, redis_memory, cache |
| SQLAlchemy 模型 (17 张表) | 已完成 | 401 | 已覆盖 | 全部带 TimestampMixin, 关系, 约束 |
| Core 基础设施 (8 个模块) | 已完成 | ~388 | 已覆盖 | Config, security, permissions, exceptions, Redis, logger, constants, validators |
| 数据库 session + 连接池 | 已完成 | -- | 已覆盖 | MySQL 连接池配置, SQLite WAL pragmas, 健康检查 |
| 数据库初始化 + 种子数据 | 已完成 | -- | 已覆盖 | 超级管理员, 7 个角色, 8 个权限 |
| Alembic 迁移 | 已完成 | 2 | 已覆盖 | 初始 17 张表 + TimestampMixin 回填 |
| Redis/Valkey 客户端 | 已完成 | 80 | 已覆盖 | 延迟初始化, 优雅降级, FakeRedis + 真实 |
| 令牌黑名单 | 已完成 | -- | 已覆盖 | 基于 jti, TTL 匹配, 故障开放 |
| 文件上传 + 验证 | 已完成 | -- | 已覆盖 | DWG 文件头, SHA-256, 路径遍历防护, HMAC URL |
| 审计日志 | 已完成 | 44 | 已覆盖 | 结构化审计追踪写入 |
| Docker Compose (9 个服务) | 已完成 | 236 | 已覆盖 | worker-report 默认, Agent/DXF + 监控 profiles |
| Dockerfile (后端) | 已完成 | -- | 已验证 | 多阶段, 非 root, HEALTHCHECK, uv sync |
| Nginx 配置 (Docker + 本地) | 已完成 | -- | 已验证 | 速率限制, 代理, 静态服务 |
| 前端 (React 19 + TS + Vite) | 已完成 | -- | 手动 | 10 个页面功能, 12 个 API 客户端文件（11 个模块 + client.ts）, auth store, router |
| 432 个测试 | 已完成 | -- | -- | 24 个测试文件, 全部通过 |

### 阶段 2 -- 未开始（Agent, MCP, 真实 CAD 处理）

| 组件 | 状态 | 行数 | 备注 |
|-----------|--------|-------|-------|
| LangGraph agent 工厂 | 桩 | 1 | `app/agents/agent_factory.py` |
| 系统提示词 | 桩 | 1 | `app/agents/prompts.py` |
| 工具注册表 | 桩 | 1 | `app/agents/tool_registry.py` |
| MCP CAD 客户端 | 桩 | 1 | `app/mcp_client/cad_mcp_client.py` |
| MCP 工具适配器 | 桩 | 1 | `app/mcp_client/mcp_tool_adapter.py` |
| Celery 应用 | 已完成 | -- | Redis broker/result backend 已配置 |
| Agent 任务 | 桩 | 1 | `app/workers/tasks_agent.py` |
| DXF 任务 | 桩 | 1 | `app/workers/tasks_dxf.py` |
| CAD 分发任务 | 桩 | 1 | `app/workers/tasks_cad.py` |
| Report 任务 | 阶段 1 桩 | -- | `run_stub_job` 创建模拟结果文件 |
| Agent runs API | 真实 (503) | 90 | `AGENT_ENABLED=false` 时返回 503 |
| Redis 记忆运行时 | 仅基础设施 | 78 | 测试已验证, 请求路径中未调用 |
| 缓存运行时 | 仅基础设施 | 84 | 测试已验证, 请求路径中未调用 |

### 阶段 3 -- 未开始（DXF 流水线）

| 组件 | 状态 | 备注 |
|-----------|--------|-------|
| DWG→DXF 转换器 | 未开始 | 抽象层, 预期使用 ODA File Converter 或 LibreDWG |
| ezdxf 解析 worker | 未开始 | 图层/文字/图块/几何信息提取 |
| entities.json 输出 | 未开始 | 按规范第 14.5 节的结构化 JSON |
| 低置信度 → 审核 | 未开始 | 自动标记置信度 < 0.85 的结果 |

### 阶段 4 -- 未开始（Windows CAD Worker）

| 组件 | 状态 | 备注 |
|-----------|--------|-------|
| ASP.NET Core Worker Service | 未开始 | 任务轮询, 沙箱管理 |
| ZWCAD API 插件 (C#) | 未开始 | 图层/文字/标注/图块提取 |
| ZWCAD 客户端 | 桩 | `app/integrations/zwcad/client.py` |
| ZWCAD schemas | 桩 | `app/integrations/zwcad/schemas.py` |
| CAD Worker 安全 | 未开始 | 进程崩溃恢复, 许可证检查, 每任务沙箱 |

### 阶段 5-6 -- 未开始

业务算法（LaR、材料统计、批量处理）、生产环境加固（RabbitMQ、Prometheus、Grafana、Loki、CI/CD、多节点扩展）。

---

## 14. 功能开关清单

所有开关位于 `app/core/config.py` / `.env` 中：

| 开关 | 默认值 | 阶段 | 为 False 时的影响 |
|------|---------|-------|--------------------|
| `AGENT_ENABLED` | `false` | 2 | `POST /api/v1/agent-runs` → 503 `AGENT_DISABLED` |
| `DXF_PIPELINE_ENABLED` | `false` | 3 | DXF 相关 Celery 任务不处理 |
| `CAD_WORKER_ENABLED` | `false` | 4 | CAD Worker 端点返回 503 |
| `DEBUG` | `true`（开发） | 全部 | 控制 500 响应中的堆栈跟踪；生产环境必须为 `false` |

---

## 15. 目录映射（完整）

```
complete_framework/
├── DWG-Agent企业平台技术规范.md          ← 规范 v2.0 (1317 行, 25 章)
├── README.md
├── .env.example                          ← 本地开发环境模板（已追踪）
├── .env.docker.example                   ← Docker 环境模板（已追踪）
├── compose.yaml                          ← 9 个服务, 3 个卷, 2 个网络
├── CLAUDE.md                             ← 本仓库的 Agent 指令
├── Makefile                              ← 开发快捷命令（install, test, lint, run）
├── image.png                             ← 架构图
├── EXPLORATION_REPORT.md                 ← 初始调研报告
├── FRONTEND_EXPLORATION.md               ← 前端调研报告
├── REINVESTIGATION_REPORT.md             ← 重新调研报告
├── var/                                  ← 运行时数据（gitignore）
│
├── backend/                              ← Python 3.12, uv, FastAPI
│   ├── pyproject.toml                    ← 依赖 + ruff 配置
│   ├── uv.lock                           ← 锁定的依赖（已提交）
│   ├── .python-version                   ← 3.12
│   ├── Dockerfile                        ← 多阶段, 非 root
│   ├── .dockerignore
│   ├── alembic.ini                       ← 目标为 MySQL
│   ├── migrations/versions/              ← 2 个 Alembic 版本
│   ├── tests/                            ← 24 个文件, 432 个测试
│   │   └── conftest.py                   ← FakeRedis 自动使用 + SQLite 隔离
│   ├── var/storage/                      ← 运行时文件存储（gitignore）
│   └── app/
│       ├── main.py                       ← FastAPI 应用, lifespan, 中间件
│       ├── api/v1/                       ← 11 个路由模块
│       │   └── router.py                 ← 中央路由组装
│       ├── schemas/                      ← 10 个 Pydantic v2 模块
│       ├── services/                     ← 12 个业务逻辑模块
│       ├── models/                       ← 10 个 ORM 模型文件（17 张表）
│       ├── core/                         ← 8 个基础设施模块
│       ├── db/                           ← session, base, init_db
│       ├── utils/                        ← path_utils, file_hash, time_utils
│       ├── agents/                       ← 3 个桩（阶段 2）
│       ├── mcp_client/                   ← 2 个桩（阶段 2）
│       ├── workers/                      ← celery_app（真实） + 4 个任务模块（1 个阶段 1 桩, 3 个阶段 2 桩）
│       ├── storage/                      ← 3 个文件（base + 本地开发 + MinIO 部署后端）
│       ├── integrations/zwcad/           ← 2 个桩（阶段 4）
│       └── repositories/                 ← 空占位符
│
├── frontend/                             ← React 19 + TypeScript + Vite
│   ├── package.json                      ← 所有依赖已锁定
│   └── src/
│       ├── api/                          ← 12 个 API 客户端文件（11 个模块 + client.ts）
│       ├── features/                     ← 10 个页面模块
│       ├── components/                   ← 8 个共享组件（2 个真实, 6 个桩）
│       ├── stores/                       ← Zustand auth store
│       ├── types/                        ← 9 个 TypeScript 类型文件
│       └── app/                          ← Router, layout, providers
│
├── docs/                                 ← 7 个文档文件
├── infra/                                ← 部署配置
│   ├── nginx/                            ← Docker + 本地开发配置
│   ├── mysql/init.sql                    ← 数据库 + 用户创建
│   ├── redis/redis.conf                  ← AOF, LRU, maxmemory
│   ├── minio/                            ← 占位符
│   └── verify.sh                         ← 部署验证
├── scripts/                              ← 6 个开发/运维 shell 脚本
├── agents/                               ← 占位符（未来 Agent 定义）
└── cad-worker/                           ← 占位符（未来 Windows C# worker）
```

---

## 16. 扩展指南 -- 各部分关联关系

### 添加新的 API 端点

1. 在 `app/schemas/` 中定义 Pydantic schemas（请求 + 响应）
2. 在 `app/services/` 中实现业务逻辑（如果是新领域，创建新 service）
3. 在 `app/api/v1/` 中创建路由模块（如果是新资源，创建新文件）
4. 在 `app/api/v1/router.py` 中注册
5. 在 `backend/tests/` 中编写测试

### 添加新的数据库表

1. 在 `app/models/` 中定义 SQLAlchemy 模型（使用 `TimestampMixin`）
2. 生成 Alembic 迁移：`cd backend && uv run alembic revision --autogenerate -m "description"`
3. 应用迁移：`uv run alembic upgrade head`
4. 为新资源添加 Pydantic schemas
5. 更新 `app/models/__init__.py` 导出新模型

### 启用 Agent（阶段 2）

1. 实现 `app/agents/agent_factory.py`（LangGraph `create_react_agent`）
2. 实现 `app/agents/prompts.py`（系统提示词）
3. 实现 `app/agents/tool_registry.py`（MCP 到 LangChain 适配器）
4. 实现 `app/mcp_client/cad_mcp_client.py` 和 `mcp_tool_adapter.py`
5. 在现有 Celery 应用基础上添加 Agent 任务实现
6. 启动 Redis 和相关的 Celery worker 队列
7. 在 `.env` 中设置 `AGENT_ENABLED=true`

### 切换存储到 MinIO

1. 设置 `STORAGE_BACKEND=minio`，配置 MinIO 端点 + 凭据
3. 启动 MinIO 容器：`docker compose up minio -d`
4. 如果迁移现有文件，运行迁移以回填存储键

---

*文档版本: 2.0 -- 最后更新于 2026-07-03*
*对应于阶段 1 完成时的代码库（432 个测试, 64 个端点, 17 张表）*
