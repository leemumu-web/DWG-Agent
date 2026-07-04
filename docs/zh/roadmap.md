# DWG-Agent 平台路线图

> 产品负责人与集成工程师视角的六阶段交付计划。
> 当前阶段：**阶段1已完成，阶段2为下一阶段。**
> 规范依据：`DWG-Agent企业平台技术规范.md` 第11-15节、第23节。

---

## 1. 阶段概览

| 阶段 | 名称 | 状态 | 核心交付物 | 依赖项 | 预估工时 |
|-------|------|--------|------------------|--------------|-------------|
| **1** | 平台骨架 | **已完成** | 认证、RBAC、项目、文件上传、作业生命周期、审计、64个API端点、432个测试、React前端（10个页面）、MinIO/Celery部署基础 | 无 | 已完成 |
| **2** | Agent子系统 | **下一阶段** | LangGraph `create_react_agent`、DeepSeek LLM、MCP客户端、Redis会话记忆、Agent Celery任务体、`/api/v1/agent-runs` 上线、AgentSteps界面 | 阶段1 | 2-3周 |
| **3** | DXF管线 | 计划中 | DWG转换器抽象层、ezdxf解析Worker、entities.json提取、结构化结果展示、低置信度复核 | 阶段2（用于Agent工具集成） | 2-3周 |
| **4** | Windows CAD工作节点 | 计划中 | ASP.NET Core Worker Service、ZWCAD API集成、拉取式任务分发、cad_result.json导出、CAD崩溃恢复 | 阶段1（内部API）、阶段2（用于分发工具） | 3-4周 |
| **5** | 业务算法 | 计划中 | LaR左右进标注、构件表比对、材料表提取、Excel/PDF/ZIP报告、批量任务、复核闭环 | 阶段3、阶段4 | 4-6周 |
| **6** | 生产环境加固 | 计划中 | RabbitMQ（可选）、Prometheus/Grafana、Loki、备份/恢复、CI/CD、多CAD工作节点扩展、速率限制、令牌黑名单中间件 | 阶段5 | 持续进行 |

---

## 2. 阶段1：平台骨架 -- 完成报告

### 2.1 基础设施

| 组件 | 状态 | 详情 |
|-----------|--------|---------|
| Docker Compose | 配置就绪，尚未经过生产环境测试 | 9个服务（nginx、backend-api、worker-agent、worker-dxf、worker-report、mysql、redis、minio、flower）；worker-report为默认服务，Agent/DXF和监控使用profiles按需启动；`.env.docker.example` 模板 |
| MySQL 8.x | 运行时数据库 | `DATABASE_URL=mysql+pymysql://...`；连接池：`pool_size=10, max_overflow=20, pool_recycle=3600`；WAL编译指令；`init.sql` 种子脚本 |
| Redis (Valkey) | 已部署并验证 | Systemd管理；`redis_client`（延迟初始化，不可用时无崩溃）、`redis_memory`、`cache_service` 均已测试；FakeRedis（通过conftest autouse进行419个非真实Redis测试）+ 真实Redis集成（13个测试）双层验证 |
| MinIO | Docker存储后端就绪 | 三层抽象：`base.py` / `local_storage.py` / `minio_storage.py`；本地开发使用本地存储，Docker使用MinIO |
| Celery | 阶段1伪任务就绪 | 真实Celery应用，Redis作为broker/result backend；`worker-report` 运行 `run_stub_job` 执行 queued→running→succeeded 流程 |
| Nginx | 生产环境 + 本地开发双配置 | `infra/nginx/nginx.conf`（Docker）、`infra/nginx/nginx.local.conf`（本地开发）；反向代理 `/api/v1/*` 到后端；SPA静态文件服务 |
| Alembic | 3个迁移版本 | `40452ddd24e7_initial.py`（17张表）+ `b8f9e7d6c5a4_add_missing_timestamp_columns.py`（TimestampMixin修复）+ `c3d2e1f0a9b8_fix_audit_logs_resource_id_type.py`（resource_id类型修复）；`scripts/db.sh migration-test` 验证端到端流程 |

### 2.2 后端 -- 11个路由模块共64个API端点

| 模块 | 端点数 | 关键特性 |
|--------|-----------|--------------|
| **Auth**（认证）(5) | POST sessions、DELETE sessions/current、POST tokens/refresh、GET me、PATCH password | 使用JWT access token + HttpOnly refresh cookie进行登录/登出；登出时令牌黑名单；修改密码需验证旧密码 |
| **Users**（用户）(11) | 完整CRUD + 角色管理 + 密码重置 + 禁用/启用 | 仅管理员可用；软删除；`super_admin` 保护（不可删除/禁用）；通过 `PATCH /users/me` 自行更新；用户名模式 `^[a-zA-Z0-9_.@-]+$`；密码最少12字符且需满足复杂度要求 |
| **Roles**（角色）(4) | GET roles、POST roles、GET permissions、PUT permissions | 7个全局角色 + 4个项目角色；5张RBAC表；super_admin绕过所有检查 |
| **Projects**（项目）(9) | CRUD + 成员管理（4个项目角色） | 级联激活状态检查（`require_active_project`）；已删除项目 → 对所有成员返回404；创建者自动分配 `project_owner` |
| **Files**（文件）(6) | 上传、列表、详情、删除、下载URL、下载 | DWG校验：文件头（AC1012-AC1032）、最小1024字节、扩展名白名单、SHA-256/MD5哈希；HMAC签名下载URL（TTL=300秒）；所有权 + 项目成员访问控制 |
| **Drawings**（图纸）(8) | CRUD + 版本管理 + 预览 | 自动递增 `version_no`；项目范围隔离；阶段1中预览端点返回占位内容 |
| **Jobs**（作业）(9) | 创建、取消、重试、步骤、日志、事件、结果 | 状态机：pending→queued→running→succeeded/failed/cancelled；取消/重试的状态守卫；阶段1存根worker使用Celery worker-report自动推进状态 |
| **Results**（结果）(4) | 详情、下载URL、提交复核、复核历史 | `approved`/`rejected` 决策；置信度评分 |
| **Reviews**（复核）(1) | 待处理列表 | 按项目成员身份过滤 |
| **Audit**（审计）(2) | 列表（最近200条）、详情 | 仅super_admin + auditor可用；记录登录、用户管理、角色变更、文件操作、作业操作、复核操作 |
| **Agent**（智能体）(4) | POST agent-runs、GET agent-runs/{id}、GET steps、GET tools | `AGENT_ENABLED=false` 时全部返回503；资源模型已建立，启用时无需前端改动 |

### 2.3 前端 -- React 19 + TypeScript + Vite

- **10个页面：** 登录、仪表盘、项目、图纸、文件、作业、复核、管理（用户/角色/审计）、个人资料
- **12个API客户端文件** 位于 `src/api/`（11个模块 + client.ts）
- **8个共享组件：** FileUpload、TaskInput、AgentSteps、ResultPanel、DrawingPreview、JobTimeline、PermissionGuard、ReviewPanel（其中6个为阶段2+的存根组件，仅FileUpload和PermissionGuard有完整实现）
- **路由级认证守卫** 具备基于角色的访问控制
- **SessionStorage** 令牌存储（非localStorage）
- **npm ci + npm run build** 构建通过

### 2.4 测试覆盖 -- 432个测试

```text
ruff check app tests    →  All checks passed (0 errors)
pytest -q               →  432 passed, 0 failed
```

已覆盖的测试领域：
- 认证流程（登录、登出、刷新、密码修改）
- RBAC强制执行（基于角色的访问控制、super_admin绕过、跨项目隔离）
- 文件上传校验（DWG文件头、大小、扩展名、哈希）
- 作业生命周期（创建、状态转换、取消/重试守卫）
- 审计日志直写
- Redis客户端、记忆和缓存服务（FakeRedis + 真实Redis双重验证）
- 安全边界（时序攻击防御、路径遍历、HTML注入、SQL完整性）
- API回归测试（跨项目读取、下载、复核授权、文件列表泄漏）
- 配置测试（MySQL组件字段、Redis URL组装、Celery URL组装）

### 2.5 已知限制（阶段1）

| 限制项 | 解决阶段 |
|------------|-----------------|
| Docker Compose未经生产环境测试 | 阶段2（渐进加固） |
| Agent/DXF/CAD工作节点的任务体为存根 | 阶段2-4 |
| Agent对所有请求返回503 | 阶段2 |
| 无DWG→DXF转换 | 阶段3 |
| 无ZWCAD集成 | 阶段4 |
| 无SSE事件流（端点已定义，返回占位内容） | 阶段2 |
| 无分块上传 | 阶段6 |
| 前端详情页较为基础 | 持续改进 |
| 无管理员令牌内省/撤销端点 | 阶段6 |

---

## 3. 阶段2：Agent子系统 -- 实现指南

### 3.1 目标架构

```
用户 → POST /api/v1/agent-runs → FastAPI → Celery agent队列
                                              ↓
                                    LangGraph create_react_agent
                                              ↓
                                      ┌───────┼───────┐
                                      ↓       ↓       ↓
                                   MCP客户端  LLM    Redis记忆
                                (CAD工具) (DeepSeek) (会话历史)
```

### 3.2 已有组件（无需为此编写新代码）

| 组件 | 文件 | 状态 |
|-----------|------|--------|
| Agent API端点（4个） | `backend/app/api/v1/agent_runs_api.py` | 已定义，返回503；资源模型无需改动 |
| Agent工具端点 | `backend/app/api/v1/agent_runs_api.py` | 已定义，返回503 |
| Agent工厂存根 | `backend/app/agents/agent_factory.py` | 占位 -- 替换为真实LangGraph agent |
| 系统提示词 | `backend/app/agents/prompts.py` | 占位 -- 定义CAD专用提示词 |
| 工具注册表存根 | `backend/app/agents/tool_registry.py` | 占位 -- 注册MCP→LangChain工具适配器 |
| MCP客户端存根 | `backend/app/mcp_client/cad_mcp_client.py` | 占位 -- 实现connect/list_tools/call_tool |
| MCP工具适配器存根 | `backend/app/mcp_client/mcp_tool_adapter.py` | 占位 -- 将MCP工具封装为LangChain工具 |
| Redis记忆服务 | `backend/app/services/redis_memory.py` | **已完整实现并测试** -- 在 `agent:memory:{session_id}` 键上调用 `save_session_history()`/`get_session_history()`，TTL=7200秒，最多20条消息 |
| Redis客户端 | `backend/app/core/redis_client.py` | **已完整实现** -- 延迟初始化，不可用时无崩溃，同步redis-py |
| 缓存服务 | `backend/app/services/cache_service.py` | **已完整实现** -- 通用 `cache:{namespace}:{key}` 模式 |
| Celery应用 | `backend/app/workers/celery_app.py` | 已实现 -- Redis broker/result backend，队列路由，测试中使用eager模式 |
| Agent任务存根 | `backend/app/workers/tasks_agent.py` | 占位 -- 需要真实的Agent执行任务 |
| Celery broker URL | `backend/app/core/config.py` | `celery_broker_url` 属性由Redis组件字段自动组装 |
| LLM配置 | `backend/app/core/config.py` | `model_name`、`model_api_key`、`model_base_url` 字段就绪 |
| MCP配置 | `backend/app/core/config.py` | `mcp_cad_command`、`mcp_cad_args` 字段就绪 |
| 功能开关 | `backend/app/core/config.py` | `agent_enabled: bool = False` -- 设为 `true` 激活 |
| Docker Compose worker profile | `compose.yaml` | `worker-agent` 服务定义在 `profiles: [workers]` 下 |

### 3.3 需要构建的内容

#### 3.3.1 Celery Agent任务集成

**文件：** `backend/app/workers/tasks_agent.py`、`backend/app/workers/tasks_dxf.py`、`backend/app/workers/tasks_cad.py`

Celery应用本身已存在。阶段2应在现有Redis后端应用之上添加真实的Agent任务体，并在FastAPI服务中保持平台安全检查：

```python
@celery_app.task(name="app.workers.tasks_agent.run_agent")
def run_agent_task(agent_run_id: int) -> dict:
    ...
```

#### 3.3.2 LangGraph Agent实现

**文件：** `backend/app/agents/agent_factory.py`

实现Agent创建函数：

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

依据规范第11.3节的要求：
- 使用 `create_react_agent`（非自定义图）
- `temperature=0` 确保确定性行为
- API密钥从环境变量获取，绝不硬编码
- 硬性平台规则（认证、路径、管线路由）不得委托给LLM

#### 3.3.3 MCP客户端实现

**文件：** `backend/app/mcp_client/cad_mcp_client.py`

必须实现（依据规范第11.4节）：
- `connect()` -- MCP stdio连接
- `disconnect()` -- 干净关闭
- `list_tools() -> list[dict]` -- 工具清单
- `call_tool(tool_name, arguments) -> str` -- 同步工具调用

关键MCP行为：
- 连接失败不得导致服务崩溃
- MCP不可用 → `POST /api/v1/agent-runs` 返回503
- Stdio stdout必须仅包含合法JSON

#### 3.3.4 MCP到LangChain工具适配器

**文件：** `backend/app/mcp_client/mcp_tool_adapter.py`

将每个MCP工具封装为LangChain `BaseTool`：
- 工具名称和描述来自MCP `list_tools()` 输出
- 参数schema由MCP工具参数定义派生
- `_run()` 委托给 `mcp_client.call_tool()`

#### 3.3.5 Agent Worker任务

**文件：** `backend/app/workers/tasks_agent.py`

将存根替换为真实的Celery任务：

```python
@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def execute_agent_run(self, agent_run_id: int):
    """
    1. 从数据库加载agent_run
    2. 获取/创建MCP客户端连接
    3. 通过agent_factory创建agent
    4. 从Redis加载会话历史（redis_memory.get_session_history）
    5. 调用agent.ainvoke()或agent.invoke()
    6. 提取最终答案和工具调用步骤
    7. 将步骤保存到agent_run_steps表
    8. 更新agent_run状态+答案
    9. 将更新后的历史保存到Redis（redis_memory.save_session_history）
    10. 失败时：重试或标记失败并记录错误
    """
```

#### 3.3.6 Agent服务

**文件：** `backend/app/services/agent_service.py`

实现以下功能的服务：
- 校验agent-run请求
- 创建 `agent_run` 数据库记录（status=queued）
- 分发Celery任务
- 返回202 Accepted及agent_run ID
- 提供agent-run详情和步骤的查询方法

#### 3.3.7 Agent步骤前端组件

**文件：** `frontend/src/components/AgentSteps.tsx`

构建用于展示以下内容的界面：
- 工具调用（工具名称、参数、状态）
- LLM推理步骤
- 最终答案展示
- 错误状态（MCP不可用、工具失败、超时）

### 3.4 配置清单

准备启用阶段2时，在 `.env` 中设置以下内容：

```bash
# 启用Agent子系统
AGENT_ENABLED=true

# LLM (DeepSeek -- 兼容OpenAI接口)
MODEL_NAME=deepseek-chat
MODEL_API_KEY=sk-your-key-here
MODEL_BASE_URL=https://api.deepseek.com

# MCP CAD工具服务器
MCP_CAD_COMMAND=uvx
MCP_CAD_ARGS=cad-mcp-server,stdio

# Redis（已运行，记忆TTL配置）
REDIS_MEMORY_TTL=7200
REDIS_MAX_MESSAGES=20

# Celery（已通过redis组件字段配置）
# CELERY_BROKER_URL和CELERY_RESULT_BACKEND自动计算
```

### 3.5 接口契约：阶段1到阶段2

以下API契约已在阶段1代码库中定义。资源模型无需任何更改；仅行为从503变为真实执行。

#### POST /api/v1/agent-runs

**请求：**
```json
{
  "session_id": "sess_abc123",
  "task": "从此DWG文件中提取所有图层和文本",
  "file_id": 1001,
  "context": {
    "project_id": 1,
    "drawing_id": 123
  }
}
```

**响应（202 Accepted）：**
```json
{
  "data": {
    "id": 9001,
    "session_id": "sess_abc123",
    "status": "queued",
    "answer": null,
    "history_count": 4,
    "created_at": "2026-07-03T10:00:00+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

#### GET /api/v1/agent-runs/{agent_run_id}

**响应（200 OK，完成之后）：**
```json
{
  "data": {
    "id": 9001,
    "session_id": "sess_abc123",
    "status": "succeeded",
    "answer": "提取完成。发现18个图层和326个文本对象。结果已保存。",
    "steps": [
      {
        "type": "tool_call",
        "title": "解析DXF实体",
        "tool_name": "parse_dxf_entities",
        "arguments": {"file_id": 1001},
        "status": "success"
      }
    ],
    "output_file_id": 2001,
    "history_count": 6,
    "created_at": "2026-07-03T10:00:00+08:00",
    "started_at": "2026-07-03T10:00:01+08:00",
    "finished_at": "2026-07-03T10:00:15+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:20+08:00"
  }
}
```

#### GET /api/v1/agent-tools

**响应（200 OK）：**
```json
{
  "data": [
    {
      "name": "list_project_files",
      "description": "列出指定项目中的所有文件。",
      "parameters": {
        "project_id": {"type": "integer", "description": "项目ID"}
      }
    },
    {
      "name": "convert_dwg_to_dxf",
      "description": "将DWG文件转换为DXF格式。",
      "parameters": {
        "file_id": {"type": "integer", "description": "源DWG文件ID"}
      }
    },
    {
      "name": "parse_dxf_entities",
      "description": "从DXF文件中解析实体（图层、文本、线段）并返回结构化JSON。",
      "parameters": {
        "file_id": {"type": "integer", "description": "DXF文件ID"}
      }
    },
    {
      "name": "dispatch_to_zwcad_worker",
      "description": "将高精度任务分派到ZWCAD Windows工作节点。",
      "parameters": {
        "file_id": {"type": "integer", "description": "源DWG文件ID"},
        "task_type": {"type": "string", "description": "CAD任务类型"}
      }
    },
    {
      "name": "generate_report",
      "description": "根据分析结果生成Excel或PDF报告。",
      "parameters": {
        "result_id": {"type": "integer", "description": "分析结果ID"},
        "format": {"type": "string", "enum": ["xlsx", "pdf"], "description": "报告格式"}
      }
    }
  ],
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

### 3.6 迁移步骤：阶段1 → 阶段2

**面向集成工程师的逐步清单：**

1. **启用功能开关**
   - 在 `.env` 中设置 `AGENT_ENABLED=true`
   - 验证：`GET /api/v1/agent-runs/1` 不再返回503（若无运行记录则可能返回404）

2. **配置LLM凭证**
   - 设置 `MODEL_API_KEY` 为有效的DeepSeek API密钥
   - 验证连通性：直接curl DeepSeek API

3. **启动Celery workers**
   ```bash
   # 方案A：使用workers profile的Docker Compose
   docker compose --profile workers up -d
   
   # 方案B：本地开发
   celery -A app.workers.celery_app worker -Q agent -n agent@%h --concurrency=2
   ```

4. **启动MCP服务器**
   - 确保CAD MCP服务器正在运行且可访问
   - 验证：MCP客户端 `list_tools()` 返回预期的工具列表

5. **验证Redis记忆功能**
   - `redis_memory.py` 已经过测试；使用真实Redis验证：
   ```bash
   redis-cli KEYS "agent:memory:*"
   ```

6. **运行集成冒烟测试**
   ```bash
   # 创建agent运行
   curl -X POST http://localhost:8000/api/v1/agent-runs \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"session_id":"test","task":"列出可用工具"}'
   
   # 轮询状态（将9001替换为返回的ID）
   curl http://localhost:8000/api/v1/agent-runs/9001 \
     -H "Authorization: Bearer $TOKEN"
   ```

7. **前端验证**
   - 导航到图纸详情页
   - 在Agent输入组件中输入自然语言任务
   - 验证AgentSteps组件渲染工具调用和最终答案

8. **回滚方案**
   - 设置 `AGENT_ENABLED=false` -- 4个agent端点重新返回503
   - 停止Celery workers -- 作业保留在DB中，无数据丢失
   - 阶段2期间创建的agent运行记录仍可查询（历史数据）

---

## 4. 阶段3：DXF管线 -- 技术规范

### 4.1 范围

阶段3实现用于低/中精度任务的开源DWG处理管线。

**范围内：**
- DWG转换器抽象层（可插拔后端）
- DWG → DXF转换（通过ODA File Converter、LibreDWG或商业SDK）
- 基于ezdxf的实体提取：图层、文本、块、线段、多段线、圆、弧
- 结构化 `entities.json` 输出
- 低置信度自动标记 → `need_review` 状态
- 前端结构化结果展示

**范围外：**
- 高精度测量（→ 阶段4，ZWCAD）
- 复杂动态块（→ 阶段4）
- 3D实体（→ 阶段4）
- 代理对象（→ 阶段4）

### 4.2 管线流程

```
DWG文件（MinIO / 本地存储）
  ↓ 下载到worker沙箱
DWG转换器（抽象层）
  ↓ 转换
converted.dxf（临时文件）
  ↓ ezdxf读取
entities.json（结构化输出）
  ↓ 规则处理
result.json（最终分析结果）
  ↓ 上传到MinIO
analysis_results表（MySQL索引）
  ↓ 置信度检查
≥ 0.85 → succeeded
< 0.85 → CAD工作节点（回退到高精度管线，依据规范§16.3）
```

### 4.3 已有组件

| 组件 | 文件 | 状态 |
|-----------|------|--------|
| DXF任务存根 | `backend/app/workers/tasks_dxf.py` | 占位 -- 需要真实的DXF处理任务 |
| 功能开关 | `backend/app/core/config.py` | `dxf_pipeline_enabled: bool = False` |
| Docker Compose worker | `compose.yaml` | `worker-dxf` 服务定义在 `profiles: [workers]` 下 |
| 存储抽象 | `backend/app/storage/` | Base + local + MinIO适配器就绪 |

### 4.4 需要构建的内容

1. **DWG转换器抽象层**（`backend/app/integrations/converter/`）
   - `base.py` -- 抽象接口：`convert(dwg_path) -> dxf_path`
   - `oda_converter.py` -- ODA File Converter实现
   - `libredwg_converter.py` -- LibreDWG/dwg2dxf实现
   - 配置驱动的后端选择

2. **DXF解析服务**（`backend/app/services/dxf_service.py`）
   - `extract_layers(dxf_path) -> list[str]`
   - `extract_texts(dxf_path) -> list[dict]`
   - `extract_blocks(dxf_path) -> list[dict]`
   - `extract_geometry(dxf_path) -> list[dict]`（线段、多段线、圆、弧）
   - `extract_all(dxf_path) -> entities.json`

3. **DXF worker任务**（`backend/app/workers/tasks_dxf.py`）
   - 从存储下载DWG到沙箱
   - 调用DWG转换器
   - 调用ezdxf解析器
   - 计算置信度分数
   - 上传派生文件（converted.dxf、entities.json、preview.png）
   - 写入 `analysis_results` 行
   - 转换作业状态

4. **结构化结果前端**（`frontend/src/components/ResultPanel.tsx`）
   - 图层树视图
   - 实体表格（类型/图层/坐标）
   - 文本内容搜索
   - 置信度指示器

### 4.5 接口契约：entities.json输出Schema

```json
{
  "source": "dxf",
  "converter": "oda_file_converter",
  "converter_version": "25.6.0",
  "parser": "ezdxf",
  "parser_version": "1.4.0",
  "confidence": 0.92,
  "layers": ["0", "DIM", "TEXT", "STEEL", "CONCRETE"],
  "entities": [
    {
      "type": "TEXT",
      "layer": "TEXT",
      "text": "BH650*300*14*24",
      "position": [120.5, 88.0],
      "rotation": 0.0,
      "height": 3.5,
      "style": "STANDARD"
    },
    {
      "type": "LINE",
      "layer": "STEEL",
      "start": [0.0, 0.0],
      "end": [1000.0, 0.0]
    },
    {
      "type": "CIRCLE",
      "layer": "DIM",
      "center": [500.0, 300.0],
      "radius": 25.0
    },
    {
      "type": "INSERT",
      "layer": "STEEL",
      "block_name": "BEAM_SECTION",
      "position": [200.0, 150.0],
      "scale": [1.0, 1.0, 1.0],
      "rotation": 0.0
    }
  ],
  "stats": {
    "total_entities": 1247,
    "text_count": 326,
    "line_count": 580,
    "circle_count": 45,
    "arc_count": 89,
    "insert_count": 207
  }
}
```

### 4.6 接口契约：DXF作业创建

**请求（POST /api/v1/jobs）：**
```json
{
  "drawing_id": 123,
  "project_id": 1,
  "task_type": "extract_all_dxf",
  "precision_level": "normal",
  "params": {
    "include_hidden_layers": false,
    "export_preview": true,
    "force_dxf_pipeline": true
  }
}
```

**响应（202 Accepted）：**
```json
{
  "data": {
    "id": 456,
    "status": "queued",
    "pipeline": "dxf_open_source",
    "task_type": "extract_all_dxf",
    "created_at": "2026-07-03T10:00:00+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

---

## 5. 阶段4：Windows CAD工作节点 -- 技术规范

### 5.1 范围

阶段4实现在独立Windows节点上使用ZWCAD的高精度CAD处理管线。

**范围内：**
- ASP.NET Core Worker Service（C#）
- ZWCAD .NET API集成
- 拉取式任务分发：`GET /api/v1/internal/cad-worker/jobs/next`
- DWG下载到本地沙箱
- 在ZWCAD中打开 → 加载C#插件 → 执行任务
- 导出 `cad_result.json` → 上传到MinIO
- PATCH作业状态回FastAPI
- CAD崩溃恢复和许可证不可用错误码

**范围外：**
- 在Linux Docker内运行ZWCAD
- 管理业务用户/权限/项目（由FastAPI负责）

### 5.2 拉取式任务模型

```
CAD工作节点（Windows）              FastAPI后端（Linux）
      │                                  │
      ├── POST /heartbeats ────────────→ │  （注册，定期发送）
      │                                  │
      ├── GET /jobs/next ──────────────→ │  （轮询任务）
      │←─ 200 {job_id, file_url, ...} ──┤
      │                                  │
      │  [下载DWG到沙箱]                 │
      │  [打开ZWCAD，执行任务]           │
      │  [导出cad_result.json]           │
      │  [上传结果到MinIO]               │
      │                                  │
      ├── PATCH /jobs/{job_id} ────────→ │  （更新状态+结果）
      │←─ 200 OK ───────────────────────┤
      │                                  │
      ├── GET /jobs/next ──────────────→ │  （轮询下一个任务）
```

### 5.3 已有组件

| 组件 | 文件 | 状态 |
|-----------|------|--------|
| ZWCAD客户端存根 | `backend/app/integrations/zwcad/client.py` | 占位 -- 需要真实的HTTP客户端 |
| ZWCAD schemas存根 | `backend/app/integrations/zwcad/schemas.py` | 占位 -- 需要Pydantic模型 |
| CAD worker任务存根 | `backend/app/workers/tasks_cad.py` | 占位 -- 需要分发任务 |
| 配置字段 | `backend/app/core/config.py` | `cad_worker_api_base`、`cad_worker_api_key` 就绪 |
| 功能开关 | `backend/app/core/config.py` | `cad_worker_enabled: bool = False` |
| Docker Compose worker | `compose.yaml` | 仅注释引用 — `worker-cad-dispatch` 保留给阶段4实现 |

### 5.4 需要构建的内容

#### 后端（Linux侧）

1. **内部CAD Worker API**（`backend/app/api/v1/internal/`）
   - `GET /api/v1/internal/cad-worker/jobs/next` -- 返回下一个待处理的CAD作业
   - `PATCH /api/v1/internal/cad-worker/jobs/{job_id}` -- 更新作业状态/结果
   - `POST /api/v1/internal/cad-worker/heartbeats` -- worker健康上报
   - 全部使用 `X-API-Key` 头验证保护

2. **ZWCAD集成客户端**（`backend/app/integrations/zwcad/client.py`）
   - `dispatch_job(job_id, task_type, params)` -- 创建CAD分发记录
   - `handle_callback(job_id, status, result_json)` -- 处理worker回调
   - `get_worker_status()` -- 查询worker健康状态

3. **CAD分发worker任务**（`backend/app/workers/tasks_cad.py`）
   - 轮询CAD worker可用性
   - 通过内部API将作业分发到Windows worker
   - 监控超时和重试逻辑

#### Windows Worker（C#侧）

```
cad-worker/
├── ZwCadWorker.Api/              ASP.NET Core Worker Service
│   ├── Program.cs                服务入口点
│   ├── Controllers/
│   │   └── InternalController.cs  （如果使用API模式）
│   └── appsettings.json
├── ZwCadWorker.Core/             核心模型和协议
│   ├── Models/
│   │   ├── CadJob.cs
│   │   ├── CadResult.cs
│   │   └── Heartbeat.cs
│   └── Services/
│       ├── JobPoller.cs          轮询 GET /jobs/next
│       ├── JobExecutor.cs        编排CAD执行
│       └── ResultUploader.cs     上传到MinIO
├── ZwCadWorker.Plugin/           ZWCAD .NET插件
│   ├── CadCommands.cs            CAD API命令
│   ├── LayerExtractor.cs
│   ├── TextExtractor.cs
│   ├── DimensionExtractor.cs
│   └── GeometryExtractor.cs
├── ZwCadWorker.Infrastructure/
│   ├── BackendClient.cs          到FastAPI的HTTP客户端
│   ├── MinioClient.cs            上传结果
│   ├── SandboxManager.cs         每个作业独立沙箱
│   └── LicenseChecker.cs         ZWCAD许可证状态
└── tests/
```

### 5.5 接口契约：CAD Worker内部API

所有内部端点需要 `X-API-Key` 头匹配 `CAD_WORKER_API_KEY`。

#### GET /api/v1/internal/cad-worker/jobs/next

**响应（200 OK -- 有可用作业）：**
```json
{
  "data": {
    "job_id": 789,
    "task_type": "extract_dimensions",
    "drawing_id": 123,
    "params": {
      "precision": "high",
      "units": "mm"
    },
    "file_download_url": "http://minio:9000/dwg-original/project/1/drawing/123/v2/source.dwg?signature=...",
    "file_sha256": "abc123def456..."
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

**响应（200 OK -- 无可用作业）：**
```json
{
  "data": null,
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

#### PATCH /api/v1/internal/cad-worker/jobs/{job_id}

**请求：**
```json
{
  "status": "succeeded",
  "progress": 100,
  "result": {
    "cad_result_file_key": "dwg-derived/project/1/drawing/123/job/789/cad_result.json",
    "preview_file_key": "dwg-derived/project/1/drawing/123/job/789/cad_preview.png",
    "summary": {
      "layers_extracted": 18,
      "dimensions_extracted": 245,
      "texts_extracted": 326,
      "blocks_identified": 42
    }
  }
}
```

**响应（200 OK）：**
```json
{
  "data": {
    "job_id": 789,
    "status": "succeeded",
    "updated_at": "2026-07-03T10:05:00+08:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

#### POST /api/v1/internal/cad-worker/heartbeats

**请求：**
```json
{
  "worker_id": "cad-worker-01",
  "status": "idle",
  "zwcad_version": "2025",
  "license_available": true,
  "active_jobs": 0,
  "cpu_percent": 12.5,
  "memory_mb": 2048
}
```

**响应（200 OK）：**
```json
{
  "data": {
    "acknowledged": true,
    "server_time": "2026-07-03T10:00:00+08:00"
  }
}
```

### 5.6 接口契约：cad_result.json输出Schema

```json
{
  "source": "zwcad",
  "cad_version": "ZWCAD 2025",
  "plugin_version": "1.0.0",
  "drawing_units": "mm",
  "layers": [
    {"name": "0", "color": 7, "line_type": "Continuous", "entity_count": 0},
    {"name": "DIM", "color": 3, "line_type": "Continuous", "entity_count": 245},
    {"name": "TEXT", "color": 2, "line_type": "Continuous", "entity_count": 326},
    {"name": "STEEL", "color": 1, "line_type": "Continuous", "entity_count": 580}
  ],
  "texts": [
    {
      "layer": "TEXT",
      "content": "BH650*300*14*24",
      "position": {"x": 120.5, "y": 88.0, "z": 0.0},
      "rotation": 0.0,
      "height": 3.5,
      "style": "STANDARD",
      "alignment": "Left"
    }
  ],
  "dimensions": [
    {
      "layer": "DIM",
      "type": "AlignedDimension",
      "value": 6000.0,
      "unit": "mm",
      "tolerance": null,
      "definition_points": [
        {"x": 0.0, "y": 0.0},
        {"x": 6000.0, "y": 0.0}
      ]
    }
  ],
  "blocks": [
    {
      "name": "BEAM_SECTION",
      "layer": "STEEL",
      "insertions": [
        {"position": {"x": 200.0, "y": 150.0}, "scale": {"x": 1.0, "y": 1.0, "z": 1.0}}
      ],
      "attributes": {
        "SECTION_ID": "B-001",
        "STEEL_GRADE": "Q345B"
      }
    }
  ],
  "geometry_summary": {
    "lines": 580,
    "polylines": 124,
    "circles": 45,
    "arcs": 89,
    "splines": 12
  },
  "execution": {
    "started_at": "2026-07-03T10:00:05+08:00",
    "finished_at": "2026-07-03T10:04:58+08:00",
    "duration_seconds": 293
  }
}
```

---

## 6. 阶段5：业务算法

### 6.1 范围

阶段5在阶段3和阶段4的原始提取管线之上叠加具体的业务算法。

| 算法 | 输入 | 输出 | 管线 |
|-----------|-------|--------|----------|
| **LaR左右进标注识别** | entities.json或cad_result.json | 带方向标注的实体 | DXF或CAD |
| **构件表比对** | 两个版本的图纸 | 差异报告（新增/删除/变更） | DXF或CAD |
| **材料表提取** | 含BOM/明细表的图纸 | 结构化材料清单 | DXF或CAD |
| **报告生成** | 分析结果 | Excel、PDF或ZIP | 报告worker |
| **批量任务编排** | 多个图纸 | 批量结果汇总 | 所有worker |
| **人工复核闭环** | need_review结果 | 带反馈的批准/驳回 | 复核API |

### 6.2 依赖项

- 阶段3（DXF管线）用于低精度提取
- 阶段4（CAD工作节点）用于高精度提取
- 阶段2（Agent）用于自然语言任务分解（可选，但推荐）
- 报告worker队列（在 `compose.yaml` 中定义，需要真实实现）

---

## 7. 阶段6：生产环境加固

### 7.1 范围（按优先级排列）

| 优先级 | 项目 | 描述 |
|----------|------|-------------|
| P0 | **备份/恢复策略** | MySQL转储、MinIO桶同步、灾难恢复操作手册 |
| P0 | **CI/CD管线** | 后端和前端的自动化测试→构建→部署管线 |
| P0 | **速率限制** | 对认证和上传端点进行按用户和按IP的速率限制 |
| P1 | **监控** | Prometheus指标（API延迟、worker队列深度、错误率）、Grafana仪表盘 |
| P1 | **日志聚合** | Loki用于跨所有容器的集中日志查询 |
| P1 | **管理员令牌管理** | 管理员令牌内省和手动撤销端点（基础jti黑名单和密码变更失效已实现） |
| P2 | **RabbitMQ broker** | 替换Redis作为Celery broker以提高生产环境可靠性（可选） |
| P2 | **多CAD工作节点扩展** | 具备健康感知分发的负载均衡CAD工作节点集群 |
| P3 | **分块上传** | 支持断点续传的大文件上传及进度跟踪 |

---

## 8. 接口契约附录

### 8.1 管线选择逻辑（阶段2+）

系统必须确定性地将任务路由到正确的管线。此逻辑位于作业服务中，不在LLM Agent中。

```
if 用户指定 precision_level == "high":
    → CAD工作节点（阶段4）
elif task_type in ("precise_measurement", "dimension_extraction"):
    → CAD工作节点（阶段4）
elif DXF转换失败:
    → CAD工作节点（阶段4）作为回退
elif DXF解析置信度 < 0.85:
    → CAD工作节点（阶段4）作为回退
elif 图纸包含 complex_blocks、proxy_objects、3d_solids:
    → CAD工作节点（阶段4）
else:
    → DXF管线（阶段3）
```

### 8.2 作业状态状态机（全阶段）

```
                    ┌─────────┐
                    │ pending │
                    └────┬────┘
                         ↓
                    ┌─────────┐
              ┌─────│ queued  │
              │     └────┬────┘
              │          ↓
              │     ┌─────────┐
              │     │ running │──────────┐
              │     └────┬────┘          │
              │          ↓               ↓
              │     ┌──────────────────────┐
              │     │ waiting_cad_worker   │ （仅阶段4）
              │     └─────────┬────────────┘             
              │               ↓
              │          ┌──────────┐
              │          │validating│
              │          └────┬─────┘
              │               ↓
              │     ┌─────────┴─────────┐
              │     ↓                   ↓
              │ ┌──────────┐    ┌────────────┐
              │ │need_review│    │ succeeded  │
              │ └─────┬────┘    └────────────┘
              │       ↓ （复核后）
              │  ┌──────────┐
              │  │ succeeded│
              │  └──────────┘
              │
              ↓
         ┌─────────┐
    ┌───→│ failed  │←──── 重试（仅从failed/cancelled状态）
    │    └─────────┘
    │
    │    ┌───────────┐
    └───→│ cancelled │←──── 取消（仅从queued/running状态）
         └───────────┘
```

### 8.3 Agent-MCP工具边界（阶段2 → 阶段3/4）

Agent（阶段2）将工具视为扁平列表。工具实现路由到正确的后端：

| 工具名称 | 后端 | 阶段 |
|-----------|---------|-------|
| `list_project_files` | FastAPI服务 | 阶段2 |
| `get_file_metadata` | FastAPI服务 | 阶段2 |
| `create_processing_job` | FastAPI服务 | 阶段2 |
| `get_job_status` | FastAPI服务 | 阶段2 |
| `convert_dwg_to_dxf` | Celery DXF worker | 阶段3 |
| `parse_dxf_entities` | Celery DXF worker | 阶段3 |
| `extract_layers` | Celery DXF worker | 阶段3 |
| `extract_texts` | Celery DXF worker | 阶段3 |
| `extract_blocks` | Celery DXF worker | 阶段3 |
| `dispatch_to_zwcad_worker` | Celery CAD分发 → Windows工作节点 | 阶段4 |
| `validate_analysis_result` | FastAPI服务 | 阶段3+ |
| `generate_report` | Celery报告worker | 阶段5 |
| `create_review_record` | FastAPI服务 | 阶段2+ |

### 8.4 Worker通信错误码规范

所有worker（DXF、CAD、报告）必须使用以下标准错误码报告错误：

| 错误码 | 含义 | 可重试 |
|------|---------|-----------|
| `DWG_CONVERSION_FAILED` | DWG→DXF转换错误 | 否 |
| `DXF_PARSE_ERROR` | ezdxf解析失败 | 否 |
| `CAD_OPEN_FAILED` | ZWCAD无法打开文件 | 是 |
| `CAD_CRASH` | ZWCAD进程崩溃 | 是 |
| `CAD_LICENSE_UNAVAILABLE` | ZWCAD许可证不可用 | 是（等待后重试） |
| `CAD_TIMEOUT` | 任务超出最大执行时间 | 是 |
| `SANDBOX_ERROR` | 无法创建/清理沙箱 | 否 |
| `UPLOAD_FAILED` | 无法上传结果到MinIO | 是 |
| `SCHEMA_VALIDATION_FAILED` | 结果JSON与schema不匹配 | 否 |
| `UNKNOWN_ERROR` | 未分类的失败 | 否 |

---

## 9. 风险登记表

| 风险 | 概率 | 影响 | 缓解措施 |
|------|-------------|--------|------------|
| MCP CAD服务器不可用/不稳定 | 中 | 阶段2受阻 | MCP客户端必须优雅处理断开连接；返回503而非500 |
| DeepSeek API速率限制或中断 | 中 | Agent不可用 | 队列化agent运行；worker中超时+重试 |
| DWG转换器未生成有效的DXF | 中 | 阶段3不完整 | 回退到CAD工作节点；标记文件需人工处理 |
| ZWCAD许可证服务器不可达 | 低 | 阶段4受阻 | 队列化作业；明确的错误码；告警 |
| CAD工作节点硬件故障 | 低 | 阶段4停机 | 多节点部署（阶段6）；作业队列持久化在MySQL中 |
| 后端与CAD Worker之间的schema漂移 | 中 | 集成失败 | 版本化API；双方进行JSON schema验证 |
| Redis记忆丢失（未配置持久化） | 低 | 会话历史丢失 | Docker配置中启用AOF；TTL提供自动清理 |

---

## 10. 成功指标（按阶段）

### 阶段1（基线）
- [x] 64个API端点可运行
- [x] 432个测试通过
- [x] RBAC具备7个全局角色 + 4个项目角色
- [x] DWG上传带文件头校验
- [x] 作业生命周期从queued到succeeded
- [x] 审计日志直写

### 阶段2（目标）
- [ ] Agent在30秒内响应自然语言任务
- [ ] MCP工具调用成功率 > 95%
- [ ] Redis会话记忆在20+条消息中保持上下文
- [ ] Agent不可用 → 503（非500，非崩溃）
- [ ] AgentSteps前端组件正确渲染工具调用和答案

### 阶段3（目标）
- [ ] 标准DWG文件的DWG→DXF转换成功率 > 90%
- [ ] 小于50MB文件的entities.json提取在60秒内完成
- [ ] 低置信度检测正确标记 > 80%的问题文件

### 阶段4（目标）
- [ ] CAD工作节点持续处理速率：每5分钟1个作业
- [ ] CAD崩溃恢复在60秒内完成
- [ ] 许可证不可用 → 在检测后10秒内返回明确错误码

### 阶段5（目标）
- [ ] LaR标注识别准确率 > 95%
- [ ] 构件表比对识别 > 98%的变更
- [ ] 报告生成在30秒内完成

### 阶段6（目标）
- [ ] 99.5% API正常运行时间
- [ ] P95 API延迟 < 500ms
- [ ] 备份RPO < 1小时
- [ ] 多worker扩展至3+个CAD工作节点
