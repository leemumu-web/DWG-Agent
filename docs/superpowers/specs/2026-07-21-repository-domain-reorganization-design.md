# Repository Domain Reorganization Design

## 1. 目标与依据

本次工作是在不减少基本业务能力、测试和公开契约的前提下，将仓库从“按技术层散放”重构为“平台基础设施 + 领域 module + 外部 adapter + 可追溯文档”的结构，使接手者可以从一个业务概念定位到后端、前端、数据库、异步任务、Stage、基础设施和测试。

规范依据仅使用：

1. `/home/Creeken/Paper/CAD_research/结构图/架构设计.txt`；
2. `/home/Creeken/Paper/CAD_research/结构图/总流程图.mmd`；
3. `/home/Creeken/Paper/CAD_research/结构图/总节点图.mmd`；
4. 当前 `main` 的代码、迁移、OpenAPI、Compose 和自动测试。

目标图中的输入描述与已确认业务要求冲突时，以已确认的“多个 DWG + 一个 Excel，服务器生成 DXF”入口为准。此次重构不得重新允许人工上传 DXF。

## 2. 当前基线与已知缺陷

### 2.1 必须保持的基线

| 项目 | 当前事实 | 重构验收 |
|---|---:|---:|
| FastAPI path | 114 | 不减少 |
| FastAPI operation | 135 | 不减少，method/path/operationId 集合一致 |
| HTTP 合同摘要 | `ef35e9cbb2e613a0f0b37f6fdf87a5001375c88d518a0346ce8db2fea5e63019` | 每批迁移一致 |
| SQLAlchemy 模型表 | 36 | 表名、列、约束不因搬迁变化 |
| Alembic revision | 17 个线性 revision，head `e2f4b8c6a130` | 历史文件与 revision ID 不改写 |
| Celery 任务名 | 11 个 `app.workers.*` 名称 | 消息协议名称保持不变 |
| 后端测试收集 | 1010 | 不减少；允许新增 |
| 后端基线 | 1001 passed、6 skipped、3 failed | 先修复 3 个纯文档删除失败，再以全绿为后续门禁 |
| 前端路由 | 登录、仪表盘、项目、生产流程、四条文件管线、图纸、Job、复核、用户、角色、基础设施、审计、个人资料 | URL 与权限不变 |
| 前端构建 | TypeScript + Vite 通过 | 每批前端迁移通过 |
| Compose | `docker compose config --quiet` 通过 | 服务名、队列名、volume 语义不变 |

### 2.2 已确认的结构问题

- 后端同一功能横跨 `api/v1`、`models`、`schemas`、`services`、`workers`，理解一个文件流程需要跨 5 个横向目录。
- `files_api.py` 1480 行、`excel_final_api.py` 896 行、`data_admin_api.py` 709 行、`jobs_api.py` 696 行，多个独立用例共享一个浅 module。
- `job_service.py`、`storage_service.py`、`workflow_input_service.py` 同时暴露事务、校验、投递和执行细节，interface 接近 implementation。
- 前端页面已按 feature 分类，但 18 个请求文件和 11 个类型文件仍横向集中；修改一个功能需要在 `features`、`api`、`types`、`components` 间跳转。
- `ConversionPage.tsx` 958 行、`InfrastructurePage.tsx` 536 行、`ExcelPreview.tsx` 519 行，状态查询、命令、展示和弹窗混在同一文件。
- 根脚本是稳定操作入口，但 `db.sh`、`lib.sh` 承担过多 implementation；直接移动会破坏 Makefile、Compose、Dockerfile和运维习惯。
- 最新提交删除 `docs/` 后，README、Stage 文档、测试和 `check_docs.py` 仍引用旧路径，导致文档检查崩溃和后端 3 个失败。
- 根目录残留两份大型规范、实现报告和重复 logo，降低入口清晰度。
- `agents/`、`cad-worker/` 与多个 1 行空 Python 文件会让接手者误认为能力已实现。
- 基础设施已有 Nginx/MySQL/MinIO，但目录没有明确呈现目标 RabbitMQ/Outbox/Beat 与当前 MySQL broker 的差异。

## 3. 方案比较

### 方案 A：只增加 README 和索引

优点是风险小；缺点是 implementation 仍按横向技术层散放，删除索引后复杂度原样回到调用者。它无法通过 deletion test，不提供 locality，因此拒绝。

### 方案 B：一次性 feature-first 全仓搬迁

优点是最终结构快速显现；缺点是同时改变 Python import、字符串 monkeypatch、Celery 加载、Compose 命令、前端 import 和 Playwright 路径。失败时无法确定是目录、注册、业务还是测试补丁问题，因此拒绝。

### 方案 C：带合同护栏的渐进式领域重构（采用）

先建立机器可检查的合同快照和 module catalog，再按一个垂直领域一批迁移。每批保持 HTTP、ORM、Celery task name、前端 URL 和测试收集基线，通过后单独提交。稳定的操作命令保留 facade，implementation 下沉到分类目录。该方案在风险、locality 和可回滚性之间最合理。

## 4. 分层原则

### 4.1 根目录只表达产品边界

根目录保留：

```text
backend/          Linux FastAPI 控制平面与 Celery implementation
frontend/         React 管理端
Stages/           独立算法包；保持现有稳定路径
infra/            Linux/Compose 基础设施配置与验证
windows/          Node Agent、CAM Runner、SinoCAM Adapter 契约或实现
agents/           可选 Agent 扩展；必须标明当前交付状态
scripts/          稳定操作命令 facade 与分类 implementation
docs/             架构、参考、指南、验证和计划
compose.yaml      标准生产形态入口
compose.dev.yaml  开发覆盖
Makefile          人类可发现的短命令
README.md         单一中文入口
README_EN.md      英文概览
CONTEXT.md        领域语言
```

`DWG-Agent企业平台技术规范.md` 移入 `docs/architecture/platform-specification.md`；`目标架构实现进度报告.md` 移入并更新为 `docs/architecture/implementation-status.md`；根 `image.png` 改为引用已有 `frontend/public/logo.png` 后删除重复文件。

### 4.2 后端依赖方向

```text
main.py / bootstrap
        ↓
modules/* interfaces
        ↓
platform/* seams
        ↓
external adapters (MySQL, Local/MinIO, Celery, Stage)
```

硬规则：

1. `platform/` 不得导入 `modules/`；现有 `core.permissions` 必须移入身份/项目访问 module。
2. 一个领域只通过另一领域的 `interface.py` 或 `contracts.py` 使用其能力，不导入对方私有 route 或 worker implementation。
3. HTTP route 只完成依赖注入、请求/响应转换和调用 interface；事务不变量属于领域 implementation。
4. Celery task 只领取消息、调用领域执行 interface 并映射重试；业务状态机不写在 task 文件。
5. Stage adapter 只负责输入输出和错误映射；Stage 不导入平台 ORM、HTTP 或权限。
6. `model_registry.py` 是 Alembic/metadata 唯一模型注册入口，不允许依赖偶然 import 副作用。
7. 未实现 module 只允许 `README.md`、schema/contract 和明确的 `NotImplemented` interface，不允许空的“成功” implementation。

## 5. 后端目标目录

```text
backend/app/
├── main.py                         # 稳定 ASGI 入口，只组装 application
├── bootstrap/
│   ├── application.py              # lifespan、中间件、错误处理、health
│   ├── router.py                   # 按 module 注册 router，固定顺序
│   ├── model_registry.py           # 显式导入全部 ORM model
│   └── task_registry.py            # 显式导入全部 Celery task
├── platform/
│   ├── config/                     # Settings、常量、配置校验
│   ├── database/                   # Base、Session、分页、seed
│   ├── http/                       # 响应 envelope、异常、通用 dependency
│   ├── messaging/                  # Celery app、队列 topology、worker lifecycle
│   ├── observability/              # logging、request_id、健康探针基础
│   ├── security/                   # JWT、密码、token primitive
│   └── storage/                    # AbstractStorageBackend、Local/MinIO adapters
├── modules/
│   ├── identity/                   # session、用户、角色、权限、token blacklist
│   ├── projects/                   # 项目、成员、图纸、图纸版本
│   ├── files/                      # 文件登记、流转、批次、下载、预览入口
│   ├── jobs/                       # Job/Step/Result/Review、attempt、事件、投递
│   ├── workflows/                  # Workflow、输入批次、冻结、Artifact、阶段编排
│   ├── cad_processing/             # DWG↔DXF、DXF→Excel、预览、批量转换
│   ├── dxf_classification/         # Steel Classifier 运行、逐图结果、产物
│   ├── excel_processing/           # Excel Final 上传、执行、关系化查询、工具
│   ├── operations/                 # data admin、归档、对账、控制平面、审计
│   └── automation/                 # Agent/Windows 契约；当前明确未实现
└── integrations/                   # 仅保留真正跨产品 adapter 的共享入口
```

### 5.1 每个业务 module 的内部模板

小 module 使用平坦结构，避免每层只有一个文件：

```text
identity/
├── README.md
├── router.py
├── models.py
├── schemas.py
├── access.py
├── authentication.py
└── users.py
```

大 module 在真实职责超过三个时再分子目录：

```text
files/
├── README.md
├── interface.py
├── models.py
├── schemas.py
├── access.py
├── validation.py
├── storage_transactions.py
└── routes/
    ├── router.py
    ├── uploads.py
    ├── catalog.py
    ├── batches.py
    ├── previews.py
    └── downloads.py
```

每个 `README.md` 固定回答：module 做什么、public interface、拥有的数据、依赖、错误模式、同步/异步数据流、测试位置、当前实现与目标差距。

## 6. 后端逐文件归属

### 6.1 平台 module

| 当前文件 | 目标位置 | 说明 |
|---|---|---|
| `core/config.py`、`constants.py`、`validators.py` | `platform/config/` | Settings、全局常量和通用输入白名单 |
| `core/exceptions.py`、`schemas/common.py`、`api/deps.py` 的通用部分 | `platform/http/` | HTTP envelope、错误和无领域 dependency |
| `core/security.py` | `platform/security/tokens.py` | 纯 JWT/密码 primitive；角色判断不在此处 |
| `core/logger.py` | `platform/observability/logging.py` | 日志初始化 |
| `db/*` | `platform/database/` | Base、Session、分页、seed；公开 import 由 `__init__.py` 限定 |
| `storage/base.py`、`local_storage.py`、`minio_storage.py`、`utils/path_utils.py` | `platform/storage/` | 两个真实 adapter 形成有效 seam |
| `workers/celery_app.py` | `platform/messaging/celery_app.py` | 当前 MySQL broker 生命周期如实保留；目标 RabbitMQ 不伪装完成 |
| `main.py` | `bootstrap/application.py` + 根 `main.py` | 根文件保留稳定 `app.main:app` interface |

### 6.2 身份、项目和文件

| 当前范围 | 目标 module |
|---|---|
| `auth_api`、`users_api`、`roles_api`、用户/角色/token models、auth/user schemas、auth/user/audit helpers | `modules/identity/`；审计写入通过 operations interface |
| `projects_api`、`drawings_api`、project/drawing models/schemas/services、`core.permissions` 的项目部分 | `modules/projects/` |
| `files_api`、file/file_transfer/storage_scan models、file schemas、file/file_transfer/storage services | `modules/files/` |
| `dxf_preview_service` | `modules/cad_processing/preview.py`，由 files preview route 调用其 interface |
| `storage_reconciliation_service` | `modules/operations/storage_reconciliation/`，不塞入基础 storage adapter |

`files_api.py` 按现有 route 顺序拆为 uploads、catalog、batches、previews、downloads；静态 `/batches`、`/download-zip` 路由必须在 `/{file_id}` 前注册，避免路由遮蔽。

### 6.3 Job 与生产流程

| 当前范围 | 目标 module |
|---|---|
| `jobs_api`、`results_api`、`reviews_api`、job/result models/schemas、job access/events/review | `modules/jobs/` |
| `job_service` 的 create/claim/progress/complete/fail | `modules/jobs/lifecycle.py` |
| `job_service` 的 enqueue/dispatch | `modules/jobs/dispatch.py` |
| `job_service` 的 cancel/retry | `modules/jobs/commands.py` |
| `workflows_api`、`workflow_inputs_api`、workflow/input models/schemas/services | `modules/workflows/` |
| 输入注册、转换同步、冻结、展示 | `modules/workflows/intake/{registration,conversion,freeze,presentation}.py` |

Job module 对外提供一个深 `interface.py`；转换、Excel 和 Workflow 不应知道 Job 表更新细节。

### 6.4 CAD、分类与 Excel

| 当前范围 | 目标 module |
|---|---|
| `dxf_service`、`dxf2dwg_service`、`dxf2excel_service`、`cad_batch_service`、`dxf_stats` | `modules/cad_processing/` |
| 三条转换中重复的 source staging、JobStep、失败映射 | `modules/cad_processing/execution.py` |
| `tasks_dxf*` | 对应 `modules/cad_processing/tasks.py`，显式保留原 task `name=` |
| dxf classification model/schema/service/task | `modules/dxf_classification/` |
| excel_final model/schema/service/API/integration/task | `modules/excel_processing/` |

转换 module 不合并 Stage 内部算法。`Stages/*` 保持独立包路径和版本，因为它们具有独立 CLI、lock、README 和测试，是有价值的产品 seam。

### 6.5 运营与未实现契约

| 当前范围 | 目标 module |
|---|---|
| daily archive model/schema/service/routes/task | `modules/operations/daily_archive/` |
| data admin overview/files/objects/transfers | `modules/operations/data_catalog/` |
| storage scan/remediation | `modules/operations/storage_reconciliation/` |
| control plane model/schema/service/routes | `modules/operations/control_plane/` |
| audit model/schema/routes/write interface | `modules/operations/audit/` |
| `agents/`、`mcp_client/`、`integrations/zwcad`、agent models/routes | `modules/automation/`，按 delivered/placeholder 分开 |
| `tasks_agent.py`、`tasks_cad.py`、`tasks_dispatch.py` 的空文件 | 删除空实现；契约写入 README/registry 状态，不用 1 行 Python 假 module |

## 7. 前端目标目录

```text
frontend/src/
├── app/                            # providers、router、layout、bootstrap
├── shared/
│   ├── api/                        # axios client、error envelope
│   ├── auth/                       # auth store、permission guards、init hook
│   ├── components/                 # 真正跨领域的 PageHeader、StatusChip 等
│   ├── hooks/                      # 跨领域 hook
│   └── styles/                     # tokens、layout、通用 surface
└── features/
    ├── identity/                   # login、profile、users、roles、audit access
    ├── projects/                   # projects + drawings
    ├── files/                      # registry、batch、download、preview
    ├── jobs/                       # list、timeline、SSE
    ├── workflows/                  # production entry、input freeze、classification
    ├── cad-processing/             # conversion shell、DWG↔DXF、DXF→Excel
    ├── excel-processing/           # Excel Final console、preview、tools
    ├── operations/                 # infrastructure、storage、archive、control plane
    ├── reviews/                    # human review
    └── dashboard/                  # cross-domain read model only
```

每个 feature 共置 `api.ts`、`types.ts`、page、components、hooks 和 style。只有被两个以上 feature 使用且语义一致的代码进入 `shared/`。

### 7.1 大文件拆分

- `ConversionPage.tsx` → `ConversionPage.tsx` + `useConversionWorkspace.ts` + `ConversionToolbar.tsx` + `BatchGrid.tsx` + `ConversionTable.tsx` + `status.ts`。
- `InfrastructurePage.tsx` → shell + overview/files/objects/transfers/consistency/runtime panels；DailyArchive 保持独立 panel。
- `Dxf2ExcelPage.tsx` → workspace hook + batch card/table + Excel Final handoff command。
- `WorkflowsPage.tsx` → list shell + submission drawer + detail drawer + stage actions；现有 ProductionInput/DxfClassification panel 继续独立。
- `ExcelPreview.tsx` → loader hook + fast table + enhanced table + download/refresh toolbar。
- `styles.css` → app layout、shared surfaces、各 feature stylesheet；选择器随组件移动但视觉行为不变。

## 8. 基础设施、Windows、脚本与测试

### 8.1 `infra/`

```text
infra/
├── gateway/nginx/
├── database/mysql/
├── storage/minio/
├── messaging/rabbitmq/README.md
├── operations/{backup,monitoring}/README.md
├── verification/verify.sh
└── README.md
```

RabbitMQ 目录记录目标拓扑、配置 interface 和“当前未实现”状态；当前 Compose 仍使用 MySQL broker 时不得加入虚假健康检查或宣称生产就绪。Nginx/MySQL/MinIO 移动后同步 Compose、脚本、Dockerfile、测试和文档。

### 8.2 `windows/`

用 `windows/` 替代模糊的 `cad-worker/`：

```text
windows/
├── README.md
├── node-agent/README.md
├── cam-runner/README.md
├── sinocam-adapter/README.md
└── protocols/README.md
```

这些目录只描述结构图中的 interface、身份、租约、Named Pipe 和结果清单，不生成空可执行文件。

### 8.3 `scripts/`

保留 `scripts/start-all.sh`、`start-dev.sh`、`stop-all.sh`、`status.sh`、`doctor.sh`、`db.sh`、`docker.sh`、`verify.sh` 作为稳定 operator interface。implementation 下沉：

```text
scripts/
├── lib/common.sh
├── lib/local_stack.sh
├── lib/database.sh
├── lib/compose.sh
├── lib/cad_worker.sh
├── architecture/check_module_catalog.py
├── docs/{check.py,generate_api.py}
├── storage/{reap.py,verify_transactions.py}
├── cad/benchmark_conversion.py
└── windows/forward_to_win11.sh
```

根 shell facade 只解析参数并调用分类 implementation；用户命令和 Makefile 入口保持不变。

### 8.4 测试镜像

后端测试按领域移动到 `tests/{architecture,contracts,identity,projects,files,jobs,workflows,cad_processing,dxf_classification,excel_processing,operations,security,infrastructure,regression}`。`conftest.py` 保留根部，新增 `tests/support/paths.py`，消除 `parents[2]` 对目录深度的假设。

字符串 monkeypatch 必须与新 import 位置同步；新增测试遍历 monkeypatch target，确保每个 target 可 import 且 attribute 存在，防止补丁悄然失效。

前端 E2E 按 `auth/files/jobs/workflows/operations/excel` 分类，Playwright 递归发现；package scripts 不再列出脆弱的平铺路径，而使用 tag 或分类目录。

## 9. 文档与追溯

```text
docs/
├── README.md
├── architecture/
│   ├── overview.md
│   ├── module-catalog.md
│   ├── traceability.md
│   ├── workflow.md
│   ├── platform-specification.md
│   └── implementation-status.md
├── reference/
│   ├── api.md
│   ├── database.md
│   └── configuration.md
├── guides/
│   ├── development.md
│   ├── deployment.md
│   ├── operations.md
│   └── security.md
├── verification/current.md
└── superpowers/{specs,plans}/
```

`docs/architecture/module-catalog.json` 是机器可检查的事实源。每个条目至少包含：code、status、architecture_nodes、backend_paths、frontend_paths、tables、http_prefixes、celery_tasks、queues、stages、tests、docs。检查器验证路径存在、所有 36 个 ORM 表有唯一主要 owner、所有 135 个 operation 和 11 个 task 有 owner、未实现能力标记为 placeholder/external。

## 10. 兼容与迁移策略

1. 先快照 HTTP method/path/operationId、ORM tables、Celery task names、前端 URL、Compose services 和测试收集数。
2. 先建立目标 package 和 architecture tests，再移动一个 module。
3. 先移动 model/schema/interface，更新 model registry，运行 collect、Alembic check 和该领域测试。
4. 再移动 route/task implementation，保持函数名、router prefix、task `name=` 和注册顺序。
5. 更新生产 import、测试 import 和字符串 patch target；不得用永久批量 re-export 掩盖未完成迁移。
6. 对确有外部 interface 的入口使用薄 facade：`app.main:app` 与根 operator scripts。内部旧层目录在全部调用者迁移后删除。
7. 每批只包含一个清晰领域或一种平台 seam，验证通过后提交；失败可通过单 commit revert 恢复。
8. 迁移期间不修改数据库 schema、HTTP payload、权限语义、业务状态机、算法参数或 UI 行为；发现既有缺陷时单独记录并在结构稳定后修复。

## 11. 验证矩阵

### 每批后端移动

- `python -m compileall app`
- `ruff check app tests`
- `pytest --collect-only -q`，数量不得低于 1010
- 领域聚焦测试
- OpenAPI 合同摘要一致
- ORM 36 表集合一致
- Celery 11 个显式任务名一致
- `alembic check`

### 每批前端移动

- TypeScript production build
- frontend contract tests
- 对应 Playwright 分类
- 前端 URL/权限快照一致
- 浏览器 console error 为 0

### 基础设施和脚本移动

- Shell `bash -n`
- `docker compose config --quiet`
- infrastructure/compose/script contract tests
- `scripts/status.sh` 只读检查
- Nginx 配置测试

### 最终门禁

- 后端全量至少 1010 collected，全部非条件测试通过
- 5 个 Stage 的现有测试全部运行，测试文件不减少
- 前端 production build 与全量 Playwright
- Alembic head、模型表、活动 MySQL 表和 storage backend 状态核对
- 文档生成与链接检查
- module catalog 覆盖率 100%
- `git diff --check`、工作树审计、远程推送后 SHA 核对

## 12. 明确不在本次重构中伪实现的能力

RabbitMQ、transactional outbox、单实例 Beat、租约/fencing token 完整模型、自动拆板、人工拆板回流、Windows Node Agent、CAM Runner、SinoCAM Adapter、CAM 工作包和持久 SSE replay 仍按实际状态保留 interface 或 placeholder。此次工作只为它们建立合理位置和可追溯 owner，不用空函数、假队列或前端假成功宣称交付。

## 13. 完成定义

只有同时满足以下条件才算完成：

1. 所有生产代码、测试、脚本、基础设施和文档均进入目标分类，没有未解释的横向旧目录或空 Python 占位。
2. module catalog 能从结构图每个已实现节点追溯到代码、表、HTTP、队列、Stage 和测试；未实现节点状态真实。
3. HTTP、数据库、Celery、前端 URL、权限和 Stage 行为无减少。
4. 测试文件和收集数不减少，最终门禁全绿；条件跳过有明确外部依赖理由。
5. 文档链接、生成脚本、Makefile、Compose、Dockerfile和操作命令全部指向真实路径。
6. 每个 module 的 interface、依赖、错误模式和验证入口可由 README 独立理解。
7. Git 历史按可回滚批次提交并推送到远程 `main`。
