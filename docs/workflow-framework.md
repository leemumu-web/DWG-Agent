# 通用工作流框架

## 1. 目的与边界

通用工作流用于记录一个项目内“输入、处理、确认、交付”的业务进程。它把流程实例、阶段状态和产物引用持久化到 MySQL，使操作员能查看当前阶段、进度、失败原因和历史时间戳。

它与 Celery Job 是两层概念：

- `WorkflowRun` 表达业务编排和人工责任；
- `Job`/`JobStep` 表达一次具体异步执行及 attempt；
- `WorkflowArtifact` 只引用已经由平台登记的 `files` 或 `analysis_results`，不保存对象字节；
- Celery queue 仍由 pipeline API 创建 Job 后投递，工作流本身不是 broker。

当前工作流是**人工编排骨架**。它不会自动创建 Excel Final Job、不会从文件上传自动推进阶段、不会把 Job 结果自动登记为流程产物，也没有交付包生成。CAD 构件提取、分类、拆板、左右进、交互式 CAD、Windows CAD Worker 和 Agent 执行均不在本模块范围。

## 2. 数据模型

迁移 `e4a1c7f2b930` 在 `a74c2e9f1d30` 之后新增三张表；随后数据控制台迁移 `6d2f8a9c1b40` 成为当前 Alembic head，不改变这三张工作流表的职责。

### 2.1 `workflow_runs`

| 字段 | 作用 |
|---|---|
| `project_id` | 权限和数据归属项目，不能创建无项目流程 |
| `created_by` | 创建用户，用于审计和追踪；访问仍以项目成员关系为准 |
| `name` | 1-128 字符，schema 会去除首尾空白并拒绝空名称 |
| `workflow_type` | 只接受 `excel_delivery` 或 `file_delivery` |
| `status` | `draft`、`waiting_input`、`running`、`waiting_review`、`succeeded`、`failed`、`cancelled` |
| `current_stage` | 当前未终结阶段的 `stage_code`；草稿创建时为空 |
| `progress` | 所有阶段 `progress` 的整数平均值；全部成功时强制为 100 |
| `config_json` | 调用方配置快照；当前没有执行器消费这些字段 |
| `error_code/error_message` | 从失败阶段汇总的错误；公开错误仍必须遵守脱敏规则 |
| 时间字段 | `started_at`、`finished_at` 和通用创建/更新时间 |

索引覆盖项目、创建者、状态及其组合。数据库没有 status CHECK constraint，合法转换由 service 负责，因此绕过应用直接写库会破坏状态机。

### 2.2 `workflow_stage_runs`

每个流程按模板创建固定顺序阶段。`(workflow_run_id, stage_code)` 唯一；`sequence` 用于排序。阶段可以保存 `job_id` 与 `job_attempt`，同步时只有 Job 当前 attempt 与记录 attempt 相等才会复制状态，避免旧执行代次推进流程。

阶段还保存 `input_json`、`output_json`、进度、错误和起止时间。`workflow_run_id` 删除时级联删除阶段；可选 `job_id` 没有级联删除策略。当前 API 不提供编辑阶段定义、跳过阶段或直接绑定 Job 的端点。

### 2.3 `workflow_artifacts`

产物包含 `artifact_type`、可选 `stage_run_id`、`file_id`、`result_id`、`version` 和 `metadata_json`。service 要求 `file_id` 与 `result_id` 至少一个非空，并要求阶段代码属于本流程。

当前数据库没有以下强约束：

- 没有 CHECK 保证 file/result 至少一个非空；
- 没有唯一约束自动递增同类型 version；默认始终是 1；
- 没有外键证明 artifact 的 file/result 与流程项目相同；
- 没有公开 API 调用 `attach_artifact()`。

因此“版本化产物结构存在”不等于“版本管理闭环已完成”。

## 3. 模板

### 3.1 `excel_delivery`

| 顺序 | stage code | 显示名称 | 当前执行方式 |
|---|---|---|---|
| 1 | `source_upload` | 上传源 Excel | 人工确认；不校验或挂接上传文件 |
| 2 | `excel_process` | Excel 零件清单处理 | 人工确认；不会自动创建 Excel Final Job |
| 3 | `quality_review` | 结果确认 | 人工确认；未与 review record 自动关联 |
| 4 | `delivery` | 交付归档 | 人工确认；不生成交付包或清单 |

### 3.2 `file_delivery`

| 顺序 | stage code | 显示名称 | 当前执行方式 |
|---|---|---|---|
| 1 | `source_upload` | 上传源文件 | 人工确认 |
| 2 | `quality_review` | 文件确认 | 人工确认 |
| 3 | `delivery` | 交付归档 | 人工确认 |

创建时第一阶段状态为 `ready`，其他阶段为 `pending`，流程为 `draft` 且 `current_stage` 为空。

## 4. 状态转换

### 4.1 流程启动

只有 `draft` 可以启动。启动后流程转为 `waiting_input`，第一阶段从 `ready` 转为 `waiting_input`，同时设置流程和阶段 `started_at`。重复启动返回 409 `WORKFLOW_NOT_DRAFT`。

### 4.2 人工完成阶段

只有 `ready`、`waiting_input` 或 `waiting_review` 阶段可以由 completion API 完成。完成后阶段为 `succeeded`、进度 100，并把下一个 `pending` 阶段置为 `waiting_input`。对 `queued`、`running`、已成功或已取消阶段重复确认会返回 409。

当前 API 没有 payload，无法在确认时提交文件、结果、评论或验收清单。用户点击“确认当前阶段”只代表状态推进，不能解释成数据验证已经发生。

### 4.3 Job 绑定与同步

内部 `bind_stage_job()` 会记录 `job.id` 和 `job.attempt`，把阶段状态/进度复制为 Job 当前值，并把流程置为运行后重新计算。内部 `sync_workflow_from_jobs()` 在读取详情时逐阶段加载 Job：

1. 没有 `job_id` 的阶段跳过；
2. Job 不存在或 attempt 已变化时跳过；
3. attempt 匹配时复制状态、进度、错误和时间；
4. `excel_process` 成功时把下一个 pending 阶段置为 `waiting_review`；
5. 重新计算整体状态。

公开 route 没有调用 `bind_stage_job()`，现有 pipeline route 也没有接收 workflow/stage 标识，所以正常用户路径不会产生该绑定。详情 GET 会执行同步并 commit，这意味着 GET 在已有内部绑定时可能更新 workflow/stage 行；这是当前有意行为，但不是纯只读查询。

### 4.4 终态与取消

- 任一阶段失败时流程转 `failed`，复制该阶段错误并设置完成时间；
- 全部阶段为 `succeeded` 或 `skipped` 时流程转 `succeeded`；当前没有公开 skip 操作；
- 取消非终态流程时，流程和全部未终态阶段转 `cancelled`；
- 取消工作流不会撤销已绑定 Celery Job，也不会终止子进程；
- 已成功、失败或取消的流程不能再次取消。

## 5. API

路由前缀为 `/api/v1/workflows`。

| Method | Path | 行为 | 权限 |
|---|---|---|---|
| GET | `/api/v1/workflows` | 按更新时间分页，可按项目和状态过滤 | 管理员看全部；其他用户仅见成员项目 |
| POST | `/api/v1/workflows` | 创建流程和模板阶段 | `project_owner` 或 `project_engineer` |
| GET | `/api/v1/workflows/{workflow_id}` | 返回阶段/产物详情，并同步已绑定 Job | 项目成员 |
| POST | `/api/v1/workflows/{workflow_id}/start` | 启动草稿 | owner/engineer |
| POST | `/api/v1/workflows/{workflow_id}/stages/{stage_code}/completion` | 人工完成可操作阶段 | owner/engineer |
| POST | `/api/v1/workflows/{workflow_id}/cancellation-requests` | 取消流程记录 | owner/engineer |

列表先在 SQL 中应用项目成员过滤，再分页和计数。`status` 只接受七种流程状态。创建、启动、阶段确认和取消写入 audit log；详情同步 Job 时没有单独写审计事件。

当前没有公开接口用于：更新名称/config、删除流程、重新打开失败阶段、绑定 Job、挂接产物、调整产物版本、批准/退回复核、下载交付包。

## 6. 前端

React `生产流程` 页面提供：

- 分页列表和状态筛选；
- 当前页流程、运行中、待操作、已完成统计；
- 基于可见项目列表的新建表单；
- 详情抽屉、阶段时间线、进度和已登记产物标签；
- 启动、确认当前阶段和取消操作；
- 运行流程每 4 秒刷新列表，详情运行时每 2.5 秒刷新。

前端没有项目 ID 筛选控件，也没有上传、选择 Job、绑定结果或交付下载控件。UI 根据状态隐藏按钮只是交互辅助，FastAPI 权限和状态校验才是安全边界。

## 7. 事务、权限与审计

route 负责项目访问检查，service 负责状态转换，成功后 route 写审计并统一 commit。创建流程时主表和全部阶段在同一事务内写入。读取详情使用 `selectinload` 加载阶段/产物，避免序列化时隐式逐行查询。

项目 owner/engineer 可写；reviewer/viewer 只能读。全局管理员通过统一项目访问规则获得全局访问。流程产物未来接线时仍必须复用文件/result 的项目权限，不能因为拿到 workflow ID 就跳过资源校验。

## 8. 已验证内容

本轮聚焦测试在 backend 锁定环境中运行了工作流 API、边界、框架、基础设施、Compose、Celery recovery 和 Excel Final import 测试，共 123 条通过；其中三份工作流测试文件收集 69 条。工作流测试覆盖模板、schema 校验、状态转换、attempt 同步、产物 service 约束、分页、权限、审计和 HTTP 错误。

这些测试主要使用 SQLite/TestClient，不证明：

- 已填充生产规模数据库的升级耗时、锁影响或 downgrade；空 MySQL 迁移本轮已经通过 `e4a1c7f2b930`；
- 浏览器生产流程页面完成真实项目操作；
- 工作流自动接通 Celery/MinIO；该能力本来就尚未实现；
- 并发人工确认或多实例竞争下没有 lost update；当前模型没有版本列或行锁。

最新全量证据和待重跑门禁见[全栈工作流验证](workflow-verification.md)。

## 9. 完成自动闭环所需工作

1. 为 pipeline Job 创建请求增加受权限保护的 workflow/stage 关联，并在同一事务或可补偿流程中绑定 `(job_id, attempt)`。
2. 把源文件、结果文件和 `AnalysisResult` 按项目权限验证后挂接为产物，并定义 version 唯一性。
3. 将 review approve/reject 与 `quality_review` 状态机接通，定义退回和重试语义。
4. 工作流取消时协调 Job cancellation，明确无法中断的 child process 如何收敛。
5. 引入并发控制，防止两个确认请求同时推进同一阶段。
6. 生成带文件 SHA-256、算法版本、Job attempt 和审计引用的交付清单。
7. 增加 MySQL、Celery、MinIO 和 Playwright 真实闭环测试，再更新“已实现”声明。
