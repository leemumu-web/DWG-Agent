# DWG-Agent 企业级 CAD 智能处理平台技术规范

> 版本：v1.0  
> 适用范围：公司内部 Ubuntu 主服务器 + MySQL + React 前端 + FastAPI 后端 + Agent + DWG/DXF 双轨处理 + 中望 CAD 高精度处理  
> 规范目标：将原有 Sorting-Agent 规范升级为面向生产的 DWG 文件智能处理平台规范，确保前后端分离、RESTful API、Docker 部署、权限安全、任务可追踪、结果可复核、架构可扩展。

---

## 0. 文档定位

本技术文档综合以下设计目标：

1. 建设公司内部 DWG 文件处理平台。
2. 主服务器基于 Ubuntu，核心数据库使用 MySQL。
3. 前端采用 React + TypeScript + Vite。
4. 后端采用 Python + FastAPI，并使用 uv 管理 Python 项目环境。
5. Agent 层参考原有 Sorting-Agent 规范，使用 LangGraph `create_react_agent`、OpenAI-compatible LLM、MCP 工具协议、Redis 短期记忆。
6. 低精度/简单任务走 DWG → DXF → Python 开源处理路线。
7. 高精度/复杂任务走 Windows CAD Worker + C# + 中望 CAD API 路线。
8. 系统必须支持企业账号、RBAC 权限、Super Admin、文件管理、任务管理、审计日志、人工复核。
9. API 必须遵守 RESTful 资源建模规范。
10. 部署必须充分发挥 Docker 容器的环境隔离、依赖固定、服务编排、可迁移和可扩展能力。

本系统不是单纯的 Agent Demo，也不是普通文件上传后台，而是一套面向公司内部生产流程的 **CAD 文件智能处理平台**。

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

| 模块 | 技术栈 | 应用说明 | 生产要求 |
|---|---|---|---|
| 前端框架 | React 18+ | 企业后台和 Agent 交互界面 | 组件化、类型化、禁止硬编码 API 地址 |
| 前端语言 | TypeScript | 类型约束，前后端 Schema 对齐 | 所有 API 响应必须有类型定义 |
| 前端构建 | Vite | 开发热更新、生产构建 | 生产构建产物由 Nginx 托管 |
| UI 组件 | Ant Design / Arco Design | 表格、表单、上传、后台管理 | 统一视觉规范 |
| 请求管理 | Axios / fetch wrapper | RESTful API 调用 | 必须封装在 `src/api/` |
| 数据缓存 | TanStack Query | 列表、分页、任务状态刷新 | 避免组件内手写重复请求逻辑 |
| 前端状态 | Zustand | 当前用户、权限、菜单、临时状态 | 不存长期敏感 token |
| 网关 | Nginx | HTTPS、静态资源、反代、上传限制 | 唯一对外入口 |
| 后端语言 | Python 3.11/3.12 | 平台 API、Agent API、Worker | 使用 uv 管理 |
| 包管理 | uv | Python 依赖锁定和运行 | 必须提交 `uv.lock` |
| API 框架 | FastAPI | RESTful API、OpenAPI、依赖注入 | API 只做短请求和任务调度 |
| Schema | Pydantic v2 | 请求/响应校验、配置模型 | 前后端类型对齐 |
| 配置 | pydantic-settings | `.env` 配置读取 | `.env` 不进 Git |
| ORM | SQLAlchemy 2.x | MySQL 数据访问 | 禁止散落原生 SQL |
| 迁移 | Alembic | 数据库版本迁移 | 所有 schema 变更必须迁移化 |
| 数据库 | MySQL 8.x | 用户、权限、项目、任务、审计 | 开启备份和慢查询日志 |
| 文件存储 | MinIO / NAS | DWG、DXF、JSON、PNG、报告 | 原始文件不可覆盖 |
| 缓存 | Redis | 短期状态、Agent 记忆、任务进度 | 不作为最终状态库 |
| 异步任务 | Celery | DXF、Agent、报告、CAD 调度 | 任务必须幂等、可重试 |
| Broker | Redis / RabbitMQ | Celery 消息代理 | MVP Redis，生产可升级 RabbitMQ |
| Agent | LangGraph `create_react_agent` | LLM 自动工具调用 | 平台硬规则不交给 LLM |
| LLM 接入 | langchain-openai ChatOpenAI | DeepSeek/Qwen/OpenAI-compatible | API Key 不进代码 |
| 工具协议 | MCP | Agent 调用外部工具 | MCP stdio stdout 只能输出 JSON |
| DXF 解析 | ezdxf | DXF 图层、文本、块、几何解析 | 不负责原生 DWG 解析 |
| DWG 转换 | Converter 抽象层 | DWG → DXF | 底层实现可替换 |
| 高精度 CAD | C# + 中望 CAD API | 原生 DWG、高精度尺寸、构件识别 | Windows 节点独立运行 |
| CAD 服务 | ASP.NET Core Worker Service | CAD 任务拉取、执行、回传 | 内网 API Key / mTLS |
| 容器 | Docker Compose | Ubuntu 主服务编排 | CAD GUI 不放 Linux Docker |
| 监控 | Flower / Logs / Prometheus | 任务、服务、队列、资源监控 | 每个 job 有 trace 信息 |
| 审计 | audit_logs | 关键操作追踪 | 用户、文件、任务、审核都留痕 |

---

## 5. 前端技术规范

### 5.1 前端目录结构

```text
frontend/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── app/
│   │   ├── router.tsx
│   │   ├── providers.tsx
│   │   └── layout.tsx
│   ├── api/
│   │   ├── client.ts
│   │   ├── auth.api.ts
│   │   ├── users.api.ts
│   │   ├── roles.api.ts
│   │   ├── projects.api.ts
│   │   ├── files.api.ts
│   │   ├── drawings.api.ts
│   │   ├── jobs.api.ts
│   │   ├── results.api.ts
│   │   ├── reviews.api.ts
│   │   └── agent-runs.api.ts
│   ├── components/
│   │   ├── FileUpload.tsx
│   │   ├── TaskInput.tsx
│   │   ├── AgentSteps.tsx
│   │   ├── ResultPanel.tsx
│   │   ├── DrawingPreview.tsx
│   │   ├── JobTimeline.tsx
│   │   ├── PermissionGuard.tsx
│   │   └── ReviewPanel.tsx
│   ├── features/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── projects/
│   │   ├── files/
│   │   ├── drawings/
│   │   ├── jobs/
│   │   ├── reviews/
│   │   └── admin/
│   ├── hooks/
│   ├── stores/
│   ├── types/
│   │   ├── auth.ts
│   │   ├── user.ts
│   │   ├── project.ts
│   │   ├── file.ts
│   │   ├── drawing.ts
│   │   ├── job.ts
│   │   ├── result.ts
│   │   └── agent.ts
│   └── utils/
├── public/
├── index.html
├── vite.config.ts
├── tsconfig.json
├── package.json
├── package-lock.json
└── .env.example
```

### 5.2 前端页面

| 路由 | 页面 | 权限 |
|---|---|---|
| `/login` | 登录页 | 未登录用户 |
| `/dashboard` | 工作台 | 已登录用户 |
| `/projects` | 项目列表 | 项目成员 / 管理员 |
| `/projects/:projectId` | 项目详情 | 项目成员 |
| `/drawings/:drawingId` | 图纸详情 | 项目成员 |
| `/files` | 文件管理 | 已登录用户 |
| `/jobs` | 任务列表 | 已登录用户 |
| `/jobs/:jobId` | 任务详情 | 任务可见用户 |
| `/reviews` | 待复核列表 | reviewer / admin |
| `/admin/users` | 用户管理 | super_admin / admin |
| `/admin/roles` | 角色权限 | super_admin |
| `/admin/audit-logs` | 审计日志 | super_admin / auditor |
| `/profile` | 个人中心 | 已登录用户 |

### 5.3 前端交互要求

1. 所有 API 请求必须通过 `src/api/` 封装。
2. 组件中禁止直接写 `fetch('/api/...')`。
3. 后端地址必须来自 `VITE_API_BASE_URL`。
4. 前端类型必须和后端 Pydantic Schema 保持一致。
5. 权限控制分三层：
   - 路由级权限；
   - 菜单级权限；
   - 组件/按钮级权限。
6. 前端权限只用于体验优化，最终权限由后端决定。
7. 大文件上传必须展示进度和失败重试。
8. 任务详情页必须展示：
   - 当前状态；
   - 任务步骤；
   - Worker 日志摘要；
   - Agent steps；
   - 结果文件；
   - 审核状态。

---

## 6. 后端平台技术规范

### 6.1 后端目录结构

```text
backend/
├── pyproject.toml
├── uv.lock
├── alembic.ini
├── migrations/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py
│   │       ├── health_api.py
│   │       ├── auth_api.py
│   │       ├── users_api.py
│   │       ├── roles_api.py
│   │       ├── projects_api.py
│   │       ├── files_api.py
│   │       ├── drawings_api.py
│   │       ├── jobs_api.py
│   │       ├── results_api.py
│   │       ├── reviews_api.py
│   │       ├── audit_logs_api.py
│   │       ├── agent_runs_api.py
│   │       └── agent_tools_api.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── permissions.py
│   │   ├── exceptions.py
│   │   ├── logger.py
│   │   └── constants.py
│   ├── db/
│   │   ├── base.py
│   │   ├── session.py
│   │   └── init_db.py
│   ├── models/
│   │   ├── user.py
│   │   ├── role.py
│   │   ├── permission.py
│   │   ├── project.py
│   │   ├── file.py
│   │   ├── drawing.py
│   │   ├── job.py
│   │   ├── result.py
│   │   ├── review.py
│   │   └── audit_log.py
│   ├── schemas/
│   │   ├── auth_schema.py
│   │   ├── user_schema.py
│   │   ├── project_schema.py
│   │   ├── file_schema.py
│   │   ├── drawing_schema.py
│   │   ├── job_schema.py
│   │   ├── result_schema.py
│   │   ├── review_schema.py
│   │   └── agent_schema.py
│   ├── repositories/
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── user_service.py
│   │   ├── project_service.py
│   │   ├── file_service.py
│   │   ├── drawing_service.py
│   │   ├── job_service.py
│   │   ├── review_service.py
│   │   ├── audit_service.py
│   │   └── agent_service.py
│   ├── agents/
│   │   ├── agent_factory.py
│   │   ├── prompts.py
│   │   └── tool_registry.py
│   ├── mcp_client/
│   │   ├── cad_mcp_client.py
│   │   └── mcp_tool_adapter.py
│   ├── workers/
│   │   ├── celery_app.py
│   │   ├── tasks_agent.py
│   │   ├── tasks_dxf.py
│   │   ├── tasks_cad.py
│   │   └── tasks_report.py
│   ├── storage/
│   │   ├── base.py
│   │   ├── local_storage.py
│   │   └── minio_storage.py
│   ├── integrations/
│   │   └── zwcad/
│   │       ├── client.py
│   │       └── schemas.py
│   └── utils/
│       ├── path_utils.py
│       ├── file_hash.py
│       └── time_utils.py
└── tests/
```

### 6.2 后端分层要求

| 层 | 目录 | 职责 |
|---|---|---|
| API 层 | `app/api/v1` | 路由、参数接收、权限依赖、响应封装 |
| Schema 层 | `app/schemas` | Pydantic 请求/响应模型 |
| Service 层 | `app/services` | 业务流程编排 |
| Repository 层 | `app/repositories` | 数据库读写封装 |
| Model 层 | `app/models` | SQLAlchemy ORM 表模型 |
| Agent 层 | `app/agents` | LangGraph Agent 创建和工具注册 |
| MCP 层 | `app/mcp_client` | MCP 连接、工具列表、工具调用 |
| Worker 层 | `app/workers` | Celery 任务定义 |
| Storage 层 | `app/storage` | 本地/MinIO 存储抽象 |
| Integration 层 | `app/integrations` | 外部系统，如中望 CAD Worker |
| Core 层 | `app/core` | 配置、安全、异常、日志、权限 |

要求：

1. API 层不写复杂业务逻辑。
2. Service 层不直接依赖 FastAPI Request。
3. Repository 层不处理业务规则。
4. Worker 任务必须调用 Service，而不是复制业务逻辑。
5. Agent 代码不得直接访问数据库和文件系统，必须通过工具或 Service 边界调用。
6. 文件路径必须经过 `path_utils.py` 校验。

---

## 7. RESTful API 规范

### 7.1 总原则

正式接口统一使用：

```text
/api/v1
```

RESTful API 必须遵守以下约定：

1. 资源名使用复数名词。
2. 使用 HTTP Method 表达动作。
3. 使用 HTTP Status Code 表达请求结果。
4. 不使用 `/getUser`、`/createTask`、`/deleteFile` 这类动词接口。
5. 不把所有响应都包装成 `200 OK + code: 0`。
6. 非资源型动作要谨慎建模为子资源或状态变更。
7. Agent 执行建模为 `agent-runs` 资源，不使用 `/api/agent/run` 作为正式主接口。

### 7.2 HTTP 方法语义

| 方法 | 语义 | 示例 |
|---|---|---|
| GET | 查询资源 | `GET /api/v1/jobs/{job_id}` |
| POST | 创建资源或提交子资源 | `POST /api/v1/jobs` |
| PATCH | 局部更新资源 | `PATCH /api/v1/users/{user_id}` |
| PUT | 整体替换资源，谨慎使用 | `PUT /api/v1/roles/{role_id}/permissions` |
| DELETE | 删除资源或解除关系 | `DELETE /api/v1/project-members/{member_id}` |

### 7.3 HTTP 状态码规范

| 状态码 | 含义 | 使用场景 |
|---|---|---|
| 200 OK | 请求成功 | 查询、更新成功且返回数据 |
| 201 Created | 创建成功 | 创建用户、项目、任务、上传会话 |
| 202 Accepted | 已接收，异步处理中 | 创建耗时任务、Agent run、CAD 任务 |
| 204 No Content | 成功但无响应体 | 删除、登出、取消关系 |
| 400 Bad Request | 请求语义错误 | 参数组合不合法 |
| 401 Unauthorized | 未认证 | 未登录、token 无效 |
| 403 Forbidden | 已认证但无权限 | 越权访问项目/文件 |
| 404 Not Found | 资源不存在 | job_id 不存在 |
| 409 Conflict | 资源冲突 | 用户名重复、任务状态不允许重试 |
| 413 Payload Too Large | 文件过大 | 上传超过限制 |
| 415 Unsupported Media Type | 文件类型不支持 | 上传非 DWG 文件 |
| 422 Unprocessable Entity | 字段校验失败 | Pydantic 校验失败 |
| 429 Too Many Requests | 请求过多 | 登录失败限流 |
| 500 Internal Server Error | 服务异常 | 未预期异常 |
| 503 Service Unavailable | 依赖不可用 | MCP 未连接、CAD Worker 不可用 |

### 7.4 响应格式

成功响应：

```json
{
  "data": {},
  "meta": {
    "request_id": "req_20260702_000001",
    "timestamp": "2026-07-02T10:00:00+08:00"
  }
}
```

分页响应：

```json
{
  "data": [],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 120
  },
  "meta": {
    "request_id": "req_20260702_000002"
  }
}
```

错误响应：

```json
{
  "error": {
    "code": "FILE_TYPE_NOT_ALLOWED",
    "message": "Only DWG files are allowed.",
    "details": {
      "field": "file",
      "allowed_extensions": [".dwg"]
    }
  },
  "meta": {
    "request_id": "req_20260702_000003"
  }
}
```

### 7.5 认证 API

| Method | Path | 说明 | 状态码 |
|---|---|---|---|
| POST | `/api/v1/auth/sessions` | 登录，创建会话 | 201 |
| DELETE | `/api/v1/auth/sessions/current` | 登出当前会话 | 204 |
| POST | `/api/v1/auth/tokens/refresh` | 刷新 access token | 200 |
| GET | `/api/v1/auth/me` | 当前用户信息 | 200 |
| PATCH | `/api/v1/auth/password` | 修改当前用户密码 | 200 |

登录请求：

```json
{
  "username": "10001",
  "password": "********"
}
```

登录响应：

```json
{
  "data": {
    "access_token": "eyJ...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "张三",
      "roles": ["engineer"]
    }
  }
}
```

### 7.6 用户与权限 API

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/v1/users` | 用户列表 |
| POST | `/api/v1/users` | 创建用户 |
| GET | `/api/v1/users/{user_id}` | 用户详情 |
| PATCH | `/api/v1/users/{user_id}` | 修改用户 |
| DELETE | `/api/v1/users/{user_id}` | 软删除用户 |
| POST | `/api/v1/users/{user_id}/roles` | 给用户分配角色 |
| DELETE | `/api/v1/users/{user_id}/roles/{role_id}` | 移除用户角色 |
| POST | `/api/v1/users/{user_id}/password-reset-requests` | 管理员发起密码重置 |
| POST | `/api/v1/users/{user_id}/disable-requests` | 禁用用户 |
| POST | `/api/v1/users/{user_id}/enable-requests` | 启用用户 |
| GET | `/api/v1/roles` | 角色列表 |
| POST | `/api/v1/roles` | 创建角色 |
| GET | `/api/v1/permissions` | 权限列表 |
| PUT | `/api/v1/roles/{role_id}/permissions` | 替换角色权限集合 |

说明：

- 禁用、启用、重置密码这类动作会产生审计记录，因此可以建模为 `*-requests` 子资源。
- 删除用户默认软删除，保留历史任务和审计关系。

### 7.7 项目 API

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/v1/projects` | 项目列表 |
| POST | `/api/v1/projects` | 创建项目 |
| GET | `/api/v1/projects/{project_id}` | 项目详情 |
| PATCH | `/api/v1/projects/{project_id}` | 修改项目 |
| DELETE | `/api/v1/projects/{project_id}` | 归档/软删除项目 |
| GET | `/api/v1/projects/{project_id}/members` | 项目成员 |
| POST | `/api/v1/projects/{project_id}/members` | 添加项目成员 |
| PATCH | `/api/v1/projects/{project_id}/members/{member_id}` | 修改项目成员角色 |
| DELETE | `/api/v1/projects/{project_id}/members/{member_id}` | 移除项目成员 |

### 7.8 文件 API

普通上传：

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/v1/files` | 上传文件 |
| GET | `/api/v1/files` | 文件列表 |
| GET | `/api/v1/files/{file_id}` | 文件详情 |
| DELETE | `/api/v1/files/{file_id}` | 删除文件，默认软删除 |
| GET | `/api/v1/files/{file_id}/download-url` | 获取短期下载 URL |

大文件分片上传：

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/v1/uploads` | 创建上传会话 |
| PUT | `/api/v1/uploads/{upload_id}/parts/{part_number}` | 上传分片 |
| POST | `/api/v1/uploads/{upload_id}/complete` | 完成上传并合并 |
| DELETE | `/api/v1/uploads/{upload_id}` | 取消上传 |

上传响应：

```json
{
  "data": {
    "id": 1001,
    "original_name": "A-001.dwg",
    "file_ext": ".dwg",
    "size_bytes": 12345678,
    "sha256": "...",
    "storage_key": "dwg-original/project/1/drawing/123/v1/source.dwg",
    "status": "available"
  }
}
```

### 7.9 图纸 API

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/v1/drawings` | 图纸列表 |
| POST | `/api/v1/drawings` | 创建图纸记录 |
| GET | `/api/v1/drawings/{drawing_id}` | 图纸详情 |
| PATCH | `/api/v1/drawings/{drawing_id}` | 修改图纸元数据 |
| DELETE | `/api/v1/drawings/{drawing_id}` | 归档图纸 |
| GET | `/api/v1/drawings/{drawing_id}/versions` | 图纸版本列表 |
| POST | `/api/v1/drawings/{drawing_id}/versions` | 上传新版本 |
| GET | `/api/v1/drawings/{drawing_id}/preview` | 获取图纸预览 |

### 7.10 任务 API

| Method | Path | 说明 | 推荐状态码 |
|---|---|---|---|
| GET | `/api/v1/jobs` | 任务列表 | 200 |
| POST | `/api/v1/jobs` | 创建处理任务 | 202 |
| GET | `/api/v1/jobs/{job_id}` | 任务详情 | 200 |
| POST | `/api/v1/jobs/{job_id}/cancellation-requests` | 请求取消任务 | 202 |
| POST | `/api/v1/jobs/{job_id}/retry-requests` | 请求重试任务 | 202 |
| GET | `/api/v1/jobs/{job_id}/steps` | 任务步骤 | 200 |
| GET | `/api/v1/jobs/{job_id}/logs` | 任务日志 | 200 |
| GET | `/api/v1/jobs/{job_id}/events` | SSE 任务事件流 | 200 |
| GET | `/api/v1/jobs/{job_id}/results` | 任务结果 | 200 |

创建任务请求：

```json
{
  "drawing_id": 123,
  "task_type": "extract_layers",
  "precision_level": "normal",
  "params": {
    "include_hidden_layers": false,
    "export_preview": true
  }
}
```

创建任务响应：

```json
{
  "data": {
    "id": 456,
    "status": "queued",
    "pipeline": "dxf_open_source",
    "created_at": "2026-07-02T10:00:00+08:00"
  }
}
```

### 7.11 Agent API

原规范中的 `POST /api/agent/run` 在正式平台中改为 RESTful 资源：

| Method | Path | 说明 | 状态码 |
|---|---|---|---|
| POST | `/api/v1/agent-runs` | 创建一次 Agent 执行 | 202 |
| GET | `/api/v1/agent-runs/{agent_run_id}` | 查询 Agent 执行详情 | 200 |
| GET | `/api/v1/agent-runs/{agent_run_id}/steps` | 查询 Agent 步骤 | 200 |
| GET | `/api/v1/agent-tools` | 查询当前 Agent 可用工具 | 200 |
| GET | `/health` | 服务健康检查 | 200 |

Agent run 请求：

```json
{
  "session_id": "sess_001",
  "task": "帮我提取这张 DWG 里的所有图层和文字",
  "file_id": 1001,
  "context": {
    "project_id": 1,
    "drawing_id": 123
  }
}
```

Agent run 响应：

```json
{
  "data": {
    "id": 9001,
    "session_id": "sess_001",
    "status": "queued",
    "answer": null,
    "history_count": 4,
    "created_at": "2026-07-02T10:00:00+08:00"
  }
}
```

Agent run 完成后的详情响应：

```json
{
  "data": {
    "id": 9001,
    "session_id": "sess_001",
    "status": "succeeded",
    "answer": "已完成图层和文字提取，共发现 18 个图层、326 个文字对象。",
    "steps": [
      {
        "type": "tool_call",
        "title": "parse_dxf_entities",
        "tool_name": "parse_dxf_entities",
        "arguments": {
          "file_id": 1001
        },
        "status": "success"
      }
    ],
    "output_file_id": 2001,
    "history_count": 6
  }
}
```

说明：

- 可以在开发期保留 `/api/agent/run` 作为兼容别名，但正式文档、前端和联调均以 `/api/v1/agent-runs` 为准。
- Agent 执行属于异步资源创建，因此默认返回 `202 Accepted`。
- Agent 工具查询使用 `GET /api/v1/agent-tools`，而不是 `/api/agent/tools`。

### 7.12 结果与复核 API

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/v1/results/{result_id}` | 结果详情 |
| GET | `/api/v1/results/{result_id}/download-url` | 结果文件下载 URL |
| GET | `/api/v1/reviews/pending` | 待复核列表 |
| POST | `/api/v1/results/{result_id}/reviews` | 提交复核记录 |
| GET | `/api/v1/results/{result_id}/reviews` | 复核历史 |

复核请求：

```json
{
  "decision": "approved",
  "comment": "结果与图纸标注一致。"
}
```

### 7.13 审计 API

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/v1/audit-logs` | 审计日志列表 |
| GET | `/api/v1/audit-logs/{audit_log_id}` | 审计日志详情 |

仅允许：

```text
super_admin
auditor
```

访问。

---

## 8. 用户、认证与权限设计

### 8.1 账号体系

系统采用企业内部账号体系：

```text
username：内部账号/工号
real_name：真实姓名
email：企业邮箱
password_hash：密码哈希
status：active / disabled / deleted
```

密码要求：

1. 后端不保存明文密码。
2. 使用 Argon2id 或 bcrypt 哈希。
3. 管理员不能查看用户密码。
4. 管理员重置密码必须写入审计日志。
5. 用户禁用后，refresh token 必须失效。

### 8.2 Token 策略

推荐：

```text
access_token：短期有效，前端内存保存
refresh_token：HttpOnly + Secure + SameSite Cookie
```

不推荐：

```text
长期 token 存 localStorage
```

### 8.3 RBAC 模型

角色表：

```text
sys_users
sys_roles
sys_permissions
sys_user_roles
sys_role_permissions
```

推荐全局角色：

| 角色 | 说明 |
|---|---|
| `super_admin` | 超级管理员，拥有全局管理权限 |
| `admin` | 系统管理员，可管理用户、项目和任务 |
| `engineer` | 工程师，可上传文件、创建任务、查看项目结果 |
| `reviewer` | 复核员，可审核机器处理结果 |
| `operator` | 操作员，可处理分配任务 |
| `viewer` | 只读用户 |
| `auditor` | 审计员，可查看审计日志 |

项目级角色：

| 项目角色 | 说明 |
|---|---|
| `project_owner` | 项目负责人 |
| `project_engineer` | 项目工程师 |
| `project_reviewer` | 项目复核员 |
| `project_viewer` | 项目只读成员 |

权限判断顺序：

```text
是否登录
  ↓
用户是否启用
  ↓
是否具备全局权限
  ↓
是否属于目标项目
  ↓
是否具备项目级资源权限
  ↓
是否允许具体 action
```

---

## 9. MySQL 数据库设计

### 9.1 数据库职责

MySQL 只保存结构化业务数据：

```text
用户
角色
权限
项目
项目成员
文件元数据
图纸版本
任务状态
任务步骤
Agent run 记录
分析结果索引
复核记录
审计日志
```

MySQL 不保存：

```text
DWG 文件本体
DXF 文件本体
PNG/SVG 预览图
Excel/PDF 报告
大型 JSON 文件
```

这些文件应保存到 MinIO / NAS。

### 9.2 核心表

#### sys_users

```text
id BIGINT PK
username VARCHAR(64) UNIQUE NOT NULL
employee_no VARCHAR(64)
real_name VARCHAR(64) NOT NULL
email VARCHAR(128)
password_hash VARCHAR(255) NOT NULL
password_algo VARCHAR(32) NOT NULL
status VARCHAR(32) NOT NULL
last_login_at DATETIME NULL
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
deleted_at DATETIME NULL
```

#### sys_roles

```text
id BIGINT PK
code VARCHAR(64) UNIQUE NOT NULL
name VARCHAR(64) NOT NULL
description VARCHAR(255)
is_system BOOLEAN NOT NULL
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
```

#### sys_permissions

```text
id BIGINT PK
code VARCHAR(128) UNIQUE NOT NULL
resource VARCHAR(64) NOT NULL
action VARCHAR(64) NOT NULL
name VARCHAR(128) NOT NULL
```

#### sys_user_roles

```text
user_id BIGINT FK
role_id BIGINT FK
created_at DATETIME NOT NULL
```

#### projects

```text
id BIGINT PK
code VARCHAR(64) UNIQUE NOT NULL
name VARCHAR(128) NOT NULL
description TEXT
owner_id BIGINT FK
status VARCHAR(32) NOT NULL
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
```

#### project_members

```text
id BIGINT PK
project_id BIGINT FK
user_id BIGINT FK
project_role VARCHAR(64) NOT NULL
created_at DATETIME NOT NULL
```

#### files

```text
id BIGINT PK
bucket VARCHAR(128) NOT NULL
storage_key VARCHAR(512) NOT NULL
original_name VARCHAR(255) NOT NULL
file_ext VARCHAR(32) NOT NULL
content_type VARCHAR(128)
size_bytes BIGINT NOT NULL
sha256 VARCHAR(64) NOT NULL
md5 VARCHAR(32)
uploaded_by BIGINT FK
status VARCHAR(32) NOT NULL
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
```

#### drawings

```text
id BIGINT PK
project_id BIGINT FK
drawing_no VARCHAR(128)
title VARCHAR(255)
discipline VARCHAR(64)
current_version_id BIGINT NULL
status VARCHAR(32) NOT NULL
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
```

#### drawing_versions

```text
id BIGINT PK
drawing_id BIGINT FK
file_id BIGINT FK
version_no INT NOT NULL
source VARCHAR(64)
created_by BIGINT FK
created_at DATETIME NOT NULL
```

#### jobs

```text
id BIGINT PK
project_id BIGINT FK
drawing_id BIGINT FK
created_by BIGINT FK
task_type VARCHAR(64) NOT NULL
precision_level VARCHAR(32) NOT NULL
pipeline VARCHAR(64)
status VARCHAR(32) NOT NULL
priority INT NOT NULL DEFAULT 0
progress INT NOT NULL DEFAULT 0
params_json JSON
error_code VARCHAR(64)
error_message TEXT
created_at DATETIME NOT NULL
started_at DATETIME NULL
finished_at DATETIME NULL
```

#### job_steps

```text
id BIGINT PK
job_id BIGINT FK
step_name VARCHAR(128) NOT NULL
worker_name VARCHAR(128)
status VARCHAR(32) NOT NULL
input_json JSON
output_json JSON
error_message TEXT
started_at DATETIME NULL
finished_at DATETIME NULL
```

#### agent_runs

```text
id BIGINT PK
session_id VARCHAR(128) NOT NULL
user_id BIGINT FK
project_id BIGINT NULL
drawing_id BIGINT NULL
file_id BIGINT NULL
task TEXT NOT NULL
status VARCHAR(32) NOT NULL
answer TEXT NULL
output_file_id BIGINT NULL
history_count INT NOT NULL DEFAULT 0
created_at DATETIME NOT NULL
started_at DATETIME NULL
finished_at DATETIME NULL
```

#### agent_run_steps

```text
id BIGINT PK
agent_run_id BIGINT FK
step_type VARCHAR(64) NOT NULL
title VARCHAR(255)
tool_name VARCHAR(128)
arguments_json JSON
content TEXT
status VARCHAR(32) NOT NULL
created_at DATETIME NOT NULL
```

#### analysis_results

```text
id BIGINT PK
job_id BIGINT FK
drawing_id BIGINT FK
result_type VARCHAR(64) NOT NULL
result_json JSON
confidence DECIMAL(5,4)
result_file_id BIGINT NULL
algorithm_version VARCHAR(64)
tool_version VARCHAR(64)
status VARCHAR(32) NOT NULL
created_at DATETIME NOT NULL
```

#### review_records

```text
id BIGINT PK
result_id BIGINT FK
reviewer_id BIGINT FK
decision VARCHAR(32) NOT NULL
comment TEXT
created_at DATETIME NOT NULL
```

#### audit_logs

```text
id BIGINT PK
actor_user_id BIGINT NULL
action VARCHAR(128) NOT NULL
resource_type VARCHAR(64) NOT NULL
resource_id BIGINT NULL
ip_address VARCHAR(64)
user_agent VARCHAR(512)
before_json JSON
after_json JSON
created_at DATETIME NOT NULL
```

---

## 10. 文件存储设计

### 10.1 存储选型

开发环境可以使用本地目录：

```text
uploads/
outputs/
data/
```

生产环境推荐：

```text
MinIO 或公司 NAS
```

### 10.2 Bucket 设计

```text
dwg-original    原始 DWG
dwg-derived     DXF、JSON、PNG、SVG 等派生文件
dwg-reports     Excel、PDF、ZIP 报告
dwg-temp        临时文件
```

### 10.3 对象路径规范

```text
dwg-original/project/{project_id}/drawing/{drawing_id}/v{version}/source.dwg

dwg-derived/project/{project_id}/drawing/{drawing_id}/job/{job_id}/converted.dxf

dwg-derived/project/{project_id}/drawing/{drawing_id}/job/{job_id}/entities.json

dwg-derived/project/{project_id}/drawing/{drawing_id}/job/{job_id}/preview.png

dwg-reports/project/{project_id}/drawing/{drawing_id}/job/{job_id}/report.xlsx
```

### 10.4 文件安全要求

1. 原始 DWG 永不覆盖。
2. 派生文件允许重算。
3. 所有文件必须计算 SHA-256。
4. 禁止使用用户提供的路径作为存储路径。
5. 文件名只作为展示字段，不作为真实存储路径。
6. 上传必须校验：
   - 文件扩展名；
   - MIME 类型；
   - 文件头；
   - 文件大小；
   - SHA-256。
7. 下载必须先通过后端权限校验，再返回短期下载 URL。
8. Worker 处理文件必须使用 sandbox 目录。
9. 任务结束后清理临时目录。

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

### 11.5 Redis 短期记忆

Redis 保存：

```text
session_id -> messages
```

配置项：

```text
REDIS_MEMORY_TTL=7200
REDIS_MAX_MESSAGES=20
```

执行流程：

```text
读取 session 历史消息
  ↓
拼接当前用户消息
  ↓
agent.ainvoke({"messages": history + [user_msg]})
  ↓
提取 answer 和 tool steps
  ↓
截断历史消息
  ↓
写回 Redis 并设置 TTL
```

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

### 13.1 为什么必须异步

DWG/DXF/CAD 处理具有以下特点：

```text
文件大；
耗时长；
可能失败；
需要重试；
需要记录步骤；
可能调用外部 Windows CAD Worker；
不适合阻塞 HTTP 请求。
```

因此：

```text
FastAPI 负责创建任务；
Celery 负责执行任务；
前端通过任务查询或 SSE 获取进度。
```

### 13.2 队列划分

```text
agent      Agent 执行和工具编排
dxf        DWG→DXF、DXF 解析、低精度处理
cad        CAD Worker 任务派发、状态轮询、回调处理
report     Excel/PDF/ZIP 报告生成
maintenance 临时文件清理、失败任务修复
```

### 13.3 任务状态机

```text
pending
queued
running
waiting_cad_worker
validating
need_review
succeeded
failed
cancelled
```

状态说明：

| 状态 | 含义 |
|---|---|
| `pending` | 任务已创建，尚未入队 |
| `queued` | 已投递到队列 |
| `running` | Worker 正在处理 |
| `waiting_cad_worker` | 等待 Windows CAD Worker 执行或回调 |
| `validating` | 结果校验中 |
| `need_review` | 需要人工复核 |
| `succeeded` | 任务完成 |
| `failed` | 任务失败 |
| `cancelled` | 任务取消 |

### 13.4 任务工程要求

1. 任务必须幂等。
2. 任务必须有超时控制。
3. 失败必须写入 `jobs.error_code` 和 `jobs.error_message`。
4. 每个任务步骤必须写入 `job_steps`。
5. Worker 日志必须带 `job_id`。
6. 可重试任务必须区分：
   - 临时失败；
   - 业务失败；
   - 不可重试失败。
7. 大型中间结果应写入 MinIO，MySQL 只记录索引。

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

### 17.1 Docker 的作用

Docker 在本系统中的作用：

```text
固定运行环境；
隔离服务依赖；
一键部署；
便于回滚；
统一日志；
分离网络边界；
管理数据卷；
支持 Worker 横向扩展；
降低服务器迁移成本。
```

### 17.2 应容器化的服务

```text
nginx
backend-api
worker-agent
worker-dxf
worker-report
worker-cad-dispatch
mysql
redis
minio
flower
prometheus，可选
grafana，可选
```

### 17.3 不应强行容器化的服务

```text
中望 CAD 桌面软件
AutoCAD / ZWCAD GUI
依赖 Windows 桌面会话和许可证的 CAD 插件环境
```

### 17.4 Docker Compose 服务结构

```yaml
services:
  nginx:
    image: nginx:1.27-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html:ro
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      backend-api:
        condition: service_healthy
    networks:
      - public
      - internal
    restart: unless-stopped

  backend-api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: >
      uv run gunicorn app.main:app
      -k uvicorn.workers.UvicornWorker
      --bind 0.0.0.0:8000
      --workers 4
      --timeout 120
    env_file:
      - .env
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 5
    networks:
      - internal
    restart: unless-stopped

  worker-agent:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uv run celery -A app.workers.celery_app worker -Q agent -n agent@%h --concurrency=2
    env_file:
      - .env
    depends_on:
      redis:
        condition: service_healthy
      mysql:
        condition: service_healthy
    networks:
      - internal
    restart: unless-stopped

  worker-dxf:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uv run celery -A app.workers.celery_app worker -Q dxf -n dxf@%h --concurrency=2
    env_file:
      - .env
    depends_on:
      redis:
        condition: service_healthy
      mysql:
        condition: service_healthy
      minio:
        condition: service_healthy
    networks:
      - internal
    restart: unless-stopped

  worker-report:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uv run celery -A app.workers.celery_app worker -Q report -n report@%h --concurrency=2
    env_file:
      - .env
    networks:
      - internal
    restart: unless-stopped

  mysql:
    image: mysql:8.4
    environment:
      MYSQL_DATABASE: dwg_agent
      MYSQL_USER: dwg_user
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
    networks:
      - internal
    restart: unless-stopped

  redis:
    image: redis:7.4-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    networks:
      - internal
    restart: unless-stopped

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio_data:/data
    networks:
      - internal
    restart: unless-stopped

  flower:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uv run celery -A app.workers.celery_app flower --port=5555
    env_file:
      - .env
    networks:
      - internal
    restart: unless-stopped

volumes:
  mysql_data:
  redis_data:
  minio_data:

networks:
  public:
  internal:
    internal: true
```

### 17.5 后端 Dockerfile 要求

```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8000

CMD ["uv", "run", "gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
```

生产要求：

1. 依赖必须在镜像构建阶段安装。
2. 运行容器时不允许临时安装依赖。
3. `.env` 不打入镜像。
4. 不使用 root 用户运行生产服务。
5. API 容器不保存持久文件。
6. 所有持久数据使用 volume 或外部存储。

---

## 18. 配置规范

### 18.1 `.env.example`

```text
# App
APP_ENV=development
DEBUG=false
API_BASE_URL=http://localhost:8000

# MySQL
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_DATABASE=dwg_agent
MYSQL_USER=dwg_user
MYSQL_PASSWORD=
MYSQL_ROOT_PASSWORD=

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_MEMORY_TTL=7200
REDIS_MAX_MESSAGES=20

# Celery
CELERY_BROKER_URL=redis://:password@redis:6379/0
CELERY_RESULT_BACKEND=redis://:password@redis:6379/1

# MinIO
MINIO_ENDPOINT=http://minio:9000
MINIO_ROOT_USER=
MINIO_ROOT_PASSWORD=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET_ORIGINAL=dwg-original
MINIO_BUCKET_DERIVED=dwg-derived
MINIO_BUCKET_REPORTS=dwg-reports
MINIO_BUCKET_TEMP=dwg-temp

# JWT
JWT_SECRET_KEY=
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=14

# LLM
MODEL_NAME=deepseek-chat
MODEL_API_KEY=
MODEL_BASE_URL=https://api.deepseek.com

# MCP
MCP_CAD_COMMAND=uvx
MCP_CAD_ARGS=cad-mcp-server,stdio

# CAD Worker
CAD_WORKER_API_BASE=http://cad-worker.internal:8080
CAD_WORKER_API_KEY=

# Frontend
VITE_API_BASE_URL=http://localhost:8000
```

### 18.2 配置要求

1. `.env.example` 可以提交。
2. `.env` 不能提交。
3. 生产密钥不能进入 Git。
4. 前端只允许暴露 `VITE_` 前缀变量。
5. 后端配置必须通过 `pydantic-settings` 读取。
6. 不允许在代码中硬编码数据库、模型、CAD Worker、MinIO 地址。

---

## 19. 安全规范

### 19.1 账号安全

1. 密码使用 Argon2id 或 bcrypt。
2. 禁止明文保存密码。
3. 登录失败必须限流。
4. 用户禁用后 token 立即失效。
5. refresh token 使用 HttpOnly Cookie。
6. 管理员操作必须写审计日志。

### 19.2 API 安全

1. 所有业务 API 必须鉴权。
2. 管理 API 必须检查角色和权限。
3. 文件、项目、任务访问必须检查归属关系。
4. 所有输入由 Pydantic 校验。
5. 所有错误响应不得暴露敏感堆栈信息。
6. 内网服务调用使用 API Key 或 mTLS。

### 19.3 文件安全

1. 限制上传大小。
2. 限制文件类型。
3. 校验文件头。
4. 计算 SHA-256。
5. 禁止路径穿越。
6. Worker 使用 sandbox。
7. 原始文件只读。
8. 派生文件与原始文件分桶存储。

### 19.4 CAD Worker 安全

1. Windows 节点只开放内网访问。
2. CAD Worker 使用独立低权限系统账号。
3. 每个任务独立 sandbox。
4. 不允许 Worker 访问非任务目录。
5. CAD Worker 日志不得输出密钥。
6. CAD Worker 必须验证后端签发的任务和下载 URL。

---

## 20. 日志、监控与审计

### 20.1 日志字段

所有后端和 Worker 日志必须尽可能包含：

```text
request_id
user_id
project_id
drawing_id
file_id
job_id
agent_run_id
worker_name
task_type
pipeline
status
duration_ms
error_code
error_message
```

### 20.2 最低监控能力

```text
Docker logs
Nginx access log
FastAPI structured log
Celery worker log
Flower
MySQL slow query log
MinIO access log
CAD Worker local log
```

### 20.3 生产增强监控

```text
Prometheus
Grafana
Loki
OpenTelemetry
Sentry，可选
```

### 20.4 审计事件

必须审计：

```text
用户登录/登出
用户创建/禁用/删除
角色权限变更
项目创建/删除
文件上传/删除/下载
任务创建/取消/重试
高精度 CAD 任务调用
结果审核通过/驳回
管理员重置密码
```

---

## 21. 测试规范

### 21.1 后端测试

技术：

```text
pytest
pytest-asyncio
httpx TestClient
factory_boy
testcontainers，可选
```

必须覆盖：

```text
登录与 token
RBAC 权限
项目权限
文件上传
文件越权访问
任务创建
Agent run 创建
Celery 任务投递
MinIO 文件写入
审计日志写入
```

### 21.2 前端测试

技术：

```text
Vitest
React Testing Library
Playwright
```

必须覆盖：

```text
登录流程
权限路由
文件上传
任务列表
任务详情
Agent steps 展示
复核流程
管理员用户管理
```

### 21.3 CAD Worker 测试

技术：

```text
xUnit / NUnit
固定 DWG 样本库
Golden JSON 对比
```

必须覆盖：

```text
打开 DWG
提取图层
提取文本
提取尺寸
高精度测量
CAD 崩溃恢复
许可证不可用错误
结果 JSON schema 校验
```

---

## 22. 推荐仓库结构

```text
dwg-agent-platform/
├── README.md
├── .env.example
├── compose.yaml
├── compose.prod.yaml
├── Makefile
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── database.md
│   ├── deployment.md
│   ├── agent.md
│   └── cad-worker-protocol.md
├── frontend/
├── backend/
├── agents/
│   ├── cad-agent/
│   ├── excel-agent/
│   └── report-agent/
├── cad-worker/
│   ├── ZwCadWorker.Api/
│   ├── ZwCadWorker.Core/
│   ├── ZwCadWorker.Plugin/
│   └── tests/
├── infra/
│   ├── nginx/
│   ├── mysql/
│   ├── redis/
│   └── minio/
└── scripts/
    ├── migrate.sh
    ├── seed_admin.sh
    ├── backup.sh
    └── restore.sh
```

说明：

1. `frontend/` 是 React 前端。
2. `backend/` 是平台主后端。
3. `agents/` 放不同 Agent 子模块。
4. `cad-worker/` 放 Windows C# CAD Worker。
5. `infra/` 放 Nginx、MySQL、Redis、MinIO 配置。
6. `docs/` 放架构、API、数据库、部署、CAD Worker 协议文档。
7. `scripts/` 放迁移、初始化、备份脚本。

---

## 23. 分阶段落地路线

### 阶段一：平台骨架闭环

目标：完成从用户到任务结果的最小生产骨架。

```text
Docker Compose 启动 nginx/backend/mysql/redis/minio
FastAPI 项目初始化
Alembic 迁移
Super Admin 初始化
登录/用户/RBAC
项目管理
文件上传到 MinIO
任务创建
Celery 假任务
任务状态展示
审计日志落库
```

验收：

```text
用户能登录；
管理员能创建账号；
用户能上传 DWG；
用户能创建任务；
任务能从 queued 到 succeeded；
结果文件能下载；
审计日志能查。
```

### 阶段二：Agent 子系统接入

```text
接入 LangGraph create_react_agent
接入 ChatOpenAI-compatible LLM
接入 MCP Client
实现 Redis session memory
实现 /api/v1/agent-runs
前端展示 AgentSteps
MCP 失败时返回 503 而不是服务崩溃
```

验收：

```text
用户能发起自然语言任务；
Agent 能调用工具；
步骤能展示；
Redis 能保存多轮历史；
工具不可用时错误可控。
```

### 阶段三：DXF 普通管线

```text
实现 DWG Converter 抽象
实现 DXF 解析 Worker
提取图层/文字/块/线段
生成 entities.json
前端展示结构化结果
低置信度进入人工复核
```

### 阶段四：Windows 中望 CAD Worker

```text
搭建 ASP.NET Core Worker Service
接入中望 CAD API
实现 DWG 打开、图层、文本、尺寸提取
实现结果 JSON 回传
实现 CAD 进程保活和崩溃恢复
实现许可证检查
```

### 阶段五：业务算法与报告

```text
LaR 左右进识别
构件清单比对
材料表提取
报告生成
批量任务
人工复核闭环
```

### 阶段六：生产增强

```text
RabbitMQ 替代 Redis Broker，可选
Prometheus/Grafana 监控
Loki 日志聚合
备份恢复策略
CI/CD
多 CAD Worker 节点扩展
```

---

## 24. 验收清单

### 24.1 架构验收

- [ ] 前后端严格分离。
- [ ] API 符合 RESTful 资源规范。
- [ ] 后端不直接执行长耗时 CAD 任务。
- [ ] MySQL 不存大文件。
- [ ] 文件存储有 MinIO/NAS 抽象。
- [ ] Agent 使用 LangGraph `create_react_agent`。
- [ ] Agent 工具通过 MCP / Tool Adapter 调用。
- [ ] Redis 短期记忆可用。
- [ ] Celery 任务可异步执行。
- [ ] Windows CAD Worker 与 Ubuntu 主服务解耦。
- [ ] Docker Compose 可启动主服务。

### 24.2 API 验收

- [ ] 所有接口在 `/api/v1` 下。
- [ ] 资源名使用复数名词。
- [ ] 创建资源返回 201 或 202。
- [ ] 删除成功返回 204。
- [ ] 错误响应使用 `error.code`。
- [ ] 不使用统一 `200 + code:0` 代替 HTTP 语义。
- [ ] Agent 执行使用 `/api/v1/agent-runs`。
- [ ] 文件下载使用短期 download-url。

### 24.3 安全验收

- [ ] 密码不明文保存。
- [ ] RBAC 后端强校验。
- [ ] 项目级权限可用。
- [ ] 文件路径经过安全校验。
- [ ] 上传文件校验后缀、大小、hash。
- [ ] 管理员操作写审计。
- [ ] CAD Worker 使用内网认证。
- [ ] `.env` 不入 Git。

### 24.4 工程验收

- [ ] `uv sync` 可安装后端依赖。
- [ ] `uv.lock` 已提交。
- [ ] `npm install` 可安装前端依赖。
- [ ] 前端不硬编码 API 地址。
- [ ] Alembic 迁移可执行。
- [ ] Docker 镜像构建成功。
- [ ] Worker 日志包含 `job_id`。
- [ ] README 和 docs 完整。

---

## 25. 最终结论

本规范将原有 Sorting-Agent 项目规范升级为完整的企业级 DWG-Agent 平台规范。最终系统不是单个 Agent 应用，而是：

```text
企业账号系统
+ 项目/图纸/文件管理
+ RESTful API 后端
+ MySQL 元数据
+ MinIO 文件存储
+ Redis 缓存与短期记忆
+ Celery 异步任务
+ LangGraph Agent 编排
+ MCP 工具调用
+ Python DXF 普通处理
+ Windows C# 中望 CAD 高精度处理
+ 人工复核
+ 审计日志
+ Docker Compose 部署
```

最重要的工程边界是：

```text
前端不解析 DWG；
FastAPI 不跑长任务；
MySQL 不存文件本体；
Agent 不负责安全边界；
Docker 不强行容器化中望 CAD；
CAD Worker 不管理业务权限；
所有正式接口遵守 RESTful API 资源规范。
```

推荐优先跑通：

```text
用户 → 项目 → 文件 → 任务 → Worker → 结果 → 复核 → 审计
```

在这条主链路稳定后，再逐步增强 Agent 智能编排、DXF 解析能力、中望 CAD 高精度能力和具体业务算法。
