# DWG-Agent 企业 CAD 处理平台

[简体中文](README.md) | [English](README_EN.md)

**交付级别：v0.1 技术预览版。文档审计基线：2026-07-18 的当前工作树。** 该级别面向技术人员试用与继续开发，不代表生产就绪。运行事实以当前代码、迁移、配置和本轮验证为准；[技术预览指南](docs/developer-preview.md)给出首次安装和验收路径，[审计报告](docs/audit-report-2026-07-18.md)记录证据与剩余风险，[企业平台技术规范](DWG-Agent企业平台技术规范.md)给出规范性边界。仓库只维护中文项目文档。

> [!IMPORTANT]
> 本 README 只描述仓库当前实现，不把占位目录、关闭的功能开关或尚未配置的基础设施写成已交付能力。详细项目文档仅维护[中文版本](docs/README.md)，英文 README 用于提供项目概览。

## 🧭 分层阅读

根据你的目标选择入口：

| 你想了解什么 | 建议阅读 |
|---|---|
| 项目能做什么、不能做什么 | [平台状态](#-平台状态) → [范围边界](#-范围边界) |
| 系统如何部署和通信 | [系统架构](#️-系统架构) → [本地启动](#-本地启动) |
| 哪些处理管线可以启用 | [处理能力](#-处理能力) → [启用条件](docs/processing-pipelines.md) |
| 如何开发和验证 | [开发与验证](#-开发与验证) → [开发文档](docs/development.md) |
| 如何部署和运维 | [Compose 部署](#compose-部署) → [部署文档](docs/deployment.md) → [运维文档](docs/operations.md) |

状态标记：**✅ 已实现** · **⚠️ 有条件可用** · **⏸️ 默认关闭/占位** · **❌ 不在当前交付范围**

## 🚦 平台状态

### 核心能力

| 领域 | 状态 | 当前实现 | 关键边界 |
|---|---|---|---|
| Web 与 API | ✅ | React 管理端、Nginx 网关、99 个 OpenAPI path 和 118 个 operation | 生产配置关闭 `/docs`、`/redoc`、`/openapi.json`；Nginx 不是授权边界 |
| 数据 | ✅ | MySQL 8.x 是唯一运行时业务事实源；Alembic 管理 28 张模型表，Celery 按需创建 8 张 broker/result 表 | 空迁移库为 29 张表；Celery runtime 全部初始化后最多 37 张；SQLite 只用于 pytest |
| 异步任务 | ✅ | Celery 使用 MySQL SQLAlchemy transport 和 MySQL result backend | 适合当前有界 worker 拓扑，不等同于高吞吐消息队列 |
| 存储 | ✅ | Local/MinIO 清单、流转账本、异步一致性扫描、DXF 预览生命周期和四类安全处置 | MySQL 保存登记，存储层保存字节；跨系统使用 saga/补偿，不宣称单一 ACID |
| 数据控制台 | ✅ | 总览、文件登记、存储对象、入出库流水、一致性五页签 | 管理员可扫描/处置，审计员只读/预检；永久清理不可恢复且必须确认 |
| Excel Final 控制台 | ✅ | 权限过滤精确总览、任务监视、跨批次检索、比重查询、批次/零件/构件分页、结果预览和 URL 状态恢复 | 上传/建任务使用数据库级幂等键；健康栏显示实际数据库/存储后端；管线关闭时历史数据仍可浏览 |

### 编排与扩展能力

| 领域 | 状态 | 当前实现 | 关键边界 |
|---|---|---|---|
| Linux 生产工作流 | ⚠️ | 九阶段 `linux_production`、文件绑定、DXF→Excel/Excel Final Job、attempt 同步、自动产物、取消和生产流程控制台 | 图纸拆板、CAM 工作包、Windows/SinoCAM、结果接纳为显式留白接口；两条管线默认关闭 |
| 转换管线 | ⚠️ | report、DWG → DXF、DXF → DWG、DXF → Excel、Excel Final 服务路径；DXF 鉴权 SVG 预览 | 四条业务管线默认关闭，分别受 ODA、Stage 完整性和手册库约束；在线预览有独立大小/复杂度上限 |
| Agent | ⏸️ | API、模型和权限边界保留 | 不继续实现；`tasks_agent.py` 保持占位，`AGENT_ENABLED=false` |
| Windows CAD worker | ⏸️ | 图纸元数据与格式转换边界保留 | 构件提取、分类、拆板、左右进、交互式 CAD 和 CAD Worker 不在当前交付范围 |
| Redis/Valkey | ❌ | 当前运行时不使用 | 业务状态、SSE、token 吊销、Agent memory、broker/result 均直接使用 MySQL |

## 🏗️ 系统架构

### 实际拓扑

```text
Browser
  -> Nginx
     -> React SPA
     -> FastAPI
        -> MySQL (业务数据 + Celery broker/result 表)
        -> Local FS 或 MinIO
Celery workers（无入站监听端口）
  -> MySQL:3306 领取消息并条件更新 Job/JobStep
  -> Local FS 或 MinIO 读取源文件、写入结果
  -> 独立 Stage / ODA 子进程
```

### 端口与网络

| 模式 | 用户入口 | FastAPI | MySQL / MinIO |
|---|---|---|---|
| 本地开发 | Vite `127.0.0.1:5173` 或 Nginx `127.0.0.1:8080` | `127.0.0.1:8010` | MySQL `127.0.0.1:3306`；MinIO 可选 |
| Compose | Nginx 宿主 HTTP `:80` → 容器 `:8080` | 仅内部 `backend-api:8010` | 仅 `internal` 网络，不发布宿主端口 |

> [!WARNING]
> 当前 Compose 仅发布 HTTP，默认把宿主 `${HTTP_PORT:-80}` 映射到 Nginx 容器 `8080`，**不发布 443，也不提供 TLS**。公网部署前必须在受控入口补齐证书、HTTPS 跳转、HSTS、续期和真实浏览器/握手验证；不能把网络隔离或安全响应头等同于传输加密。

## 🧩 处理能力

### 管线矩阵

| 管线 / 队列 | 状态 | 默认开关 | 运行前提 |
|---|---|---|---|
| framework smoke / `report` | ✅ 可运行的框架任务 | 核心 worker 默认启动 | MySQL broker/result 与存储可用；不代表报告 Agent 已实现 |
| DWG → DXF / `dxf` | ⚠️ 服务、task、测试和 ODA 适配存在 | `DXF_PIPELINE_ENABLED=false` | ODA File Converter、无头 X 环境、源 DWG 校验通过 |
| DXF → DWG / `dxf2dwg` | ⚠️ 服务、task、测试和 ODA 适配存在 | `DXF2DWG_PIPELINE_ENABLED=false` | 同上，并要求有效 DXF |
| DXF → Excel / `dxf2excel` | ⚠️ Stage 源码、平台 service/task 和测试已纳入父仓库 | `DXF2EXCEL_PIPELINE_ENABLED=false` | 有效 DXF、Stage 锁定依赖；当前内置单测只覆盖解码，真实批次仍需外部 corpus 验收 |
| Excel Final / `excel_final` | ⚠️ backend 适配、隔离子进程、关系化导入和 Stage 测试存在 | `EXCEL_FINAL_PIPELINE_ENABLED=false` | 有效 Tekla/初始表 schema、`hardware_handbook` 只读库、足够超时 |
| Agent / `agent` | ⏸️ API 和持久化边界存在，task 为空占位 | `AGENT_ENABLED=false` | 尚未满足交付条件 |
| CAD / `cad` | ⏸️ task 和 `cad-worker/` 均为空占位 | `CAD_WORKER_ENABLED=false` | 尚未满足交付条件；Compose 没有 `worker-cad` |

### 任务一致性

任务以 `(job_id, attempt)` 作为执行世代。重试递增 `attempt`；worker 的领取、进度和终态更新都必须匹配当前状态与 attempt，从而阻止旧消息或旧 worker 覆盖新一轮任务。SSE 轮询 MySQL 并发送当前 attempt 的权威快照，不提供按 event ID 的历史回放。

### 工作流边界

工作流以 `workflow_runs → workflow_stage_runs → workflow_artifacts` 统筹业务阶段和产物引用。`linux_production` 覆盖输入冻结、图纸交接、Excel 两阶段、CAM/Windows 交接、结果接纳和归档；`excel_stage1` 与 `excel_final` 已直接复用现有 Job/Celery 管线，详情同步成功结果并自动挂接 File/AnalysisResult。

这仍不是 SinoCAM 完整生产闭环：图纸拆板、CAM 工作包、Windows Node Agent/SinoCAM 与结果接纳返回 `WORKFLOW_STAGE_NOT_IMPLEMENTED`，同时暴露输入输出契约；操作员绑定外部交接产物后才可确认推进。详见[Linux 生产工作流框架](docs/workflow-framework.md)。

## 🎯 范围边界

### 当前继续完善

- 项目、文件与格式转换；
- Excel Final 与通用流程；
- 任务、复核、权限与审计；
- 部署和运维框架。

### 不在当前交付范围

- CAD 图纸构件提取、自动分类、自动/交互拆板和左右进业务算法；
- 中望 CAD 二次开发及 Windows CAD Worker；
- Agent、模型调用、MCP 工具编排和 Agent memory 产品化。

仓库中的相关 route、model、config 或占位目录只作为历史/兼容边界保留，不表示将继续实现。

## ⚠️ 已知限制

1. Compose 当前仅提供 HTTP 且不发布 `443`；TLS 入口、证书生命周期和 HTTPS 验证尚未实现。
2. 备份、保留策略、监控告警、集中日志和灾难恢复演练尚未自动化；文档中的相关步骤是操作基线，不是已部署服务。
3. MySQL SQL transport 缺少 RabbitMQ 一类 broker 的吞吐、路由和远程控制能力。扩容 broker 时仍应保留 MySQL 作为业务事实源。
4. ODA 转换依赖专有二进制及其许可/运行环境；单元测试通过不等于所有真实 DWG/DXF 版本均兼容。
5. 仓库尚未声明 LICENSE；在项目负责人确认授权、第三方许可和样本数据分发范围前，只能作为内部技术预览使用，不得推定为开源或可对外再分发。

## 🚀 本地启动

### 前置条件

- Python 3.12 与 `uv`；
- Node.js 与 npm；
- MySQL 8.x；
- 与启用管线匹配的 Stage 依赖。

### 启动开发环境

```bash
cp .env.example .env
cp .env.example backend/.env
# 替换密码和 JWT secret；两份文件的 MYSQL_* 必须一致。

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

`start-dev.sh` 启动五个已实现队列 worker（不含 agent/cad）、FastAPI `8010` 和 Vite。`start-all.sh` 还会构建前端并启动本地 Nginx `8080`。功能开关关闭时 worker 可以存活，但对应 API 会拒绝创建任务。

需要复用容器内 MySQL/MinIO 并热更新 API 时，可运行：

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile workers up --build
```

开发覆盖只把 `127.0.0.1:8010` 发布到宿主，不发布 MySQL/MinIO。

### 常用管理命令

```bash
bash scripts/status.sh
bash scripts/stop-all.sh
bash scripts/db.sh status
```

### Compose 部署

在接受当前仅 HTTP、内部技术预览和外部 Stage 依赖边界后：

```bash
cp .env.docker.example .env.docker
# 替换全部 CHANGE_ME_*，不要提交 .env.docker。
npm --prefix frontend ci
npm --prefix frontend run build

docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

核心集合为 `nginx/backend-api/mysql/minio/worker-report`；`workers` profile 增加转换 worker 和占位的 `worker-agent`。`worker-agent` healthy 只表示 Celery 进程已连接 broker，不表示 Agent task 已实现。

## 🧪 开发与验证

### 文档、静态检查与后端测试

```bash
make docs-check
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ..
```

### Stage、数据库与基础设施契约

```bash
cd Stages/dwg2dxf && uv run pytest -q && cd ../..
cd Stages/dxf2dwg && uv run pytest -q && cd ../..
cd Stages/dxf2excel && uv run pytest -q && cd ../..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet
```

### 前端构建与浏览器测试

```bash
cd frontend
npm run build
npx playwright test
```

测试层级不能互相替代：SQLite pytest 验证业务逻辑，`migration-test` 验证空 MySQL schema，`infra/verify.sh` 验证静态与活动基础设施契约，Playwright 验证浏览器交互。

完整发布验收还必须使用真实 MySQL、Celery、MinIO 和有效样本，完成上传、处理、重试、SSE、签名下载、存储中断与恢复闭环。详见[工作流验证](docs/workflow-verification.md)。

## 🗂️ 仓库结构

```text
backend/        FastAPI、SQLAlchemy、Alembic、Celery、存储适配与 pytest
frontend/       React 管理端、API client 与 Playwright
Stages/         独立 CAD/Excel 处理阶段；Python Stage 源码已跟踪，外部二进制/corpus 另行管理
agents/         未交付的 Agent 目录占位
cad-worker/     未交付的 Windows CAD worker 协议占位
infra/          Nginx、MySQL 初始化、Compose 验证
scripts/        本地启停、数据库与文档工具
docs/           唯一维护的中文详细文档
third_parts/    外部/上游项目；不代表平台直接交付的能力
```

## 📚 文档导航

| 分类 | 文档 |
|---|---|
| 总览 | [技术预览指南](docs/developer-preview.md) · [审计报告](docs/audit-report-2026-07-18.md) · [文档索引](docs/README.md) · [贡献指南](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) |
| 规范 | [企业平台技术规范](DWG-Agent企业平台技术规范.md) |
| 设计 | [架构](docs/architecture.md) · [数据库](docs/database.md) · [通用工作流框架](docs/workflow-framework.md) |
| 开发 | [开发指南](docs/development.md) · [API](docs/api.md) · [配置参考](docs/configuration.md) |
| 管线 | [处理管线](docs/processing-pipelines.md) · [工作流验证](docs/workflow-verification.md) |
| 交付 | [部署](docs/deployment.md) · [运维](docs/operations.md) · [安全](docs/security.md) · [路线图](docs/roadmap.md) |

路由变更后运行 `make docs-generate` 生成 `docs/api.md`；提交前运行 `make docs-check`。
