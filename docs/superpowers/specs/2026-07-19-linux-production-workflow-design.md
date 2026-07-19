# Linux 生产工作流前后端设计

## 目标与边界

在现有 `workflow_runs`、`workflow_stage_runs`、`workflow_artifacts` 三表和 `/files`、`/jobs`、`/excel-final` 能力之上，补齐 `/workflows` 的 Linux 服务器编排框架，使一个生产流程可以完成文件登记、真实 Linux 作业调度、状态同步、产物归档和人工/外部阶段交接。

本次不实现尚无可靠后端的拆板算法、CAM 工作包算法、Windows Node Agent 或 SinoCAM Adapter。它们必须在模板、OpenAPI 和 UI 中拥有稳定的阶段代码、输入输出契约、能力状态和可调用占位端点；调用时返回明确的“尚未实现/外部执行”业务错误，不能伪造成功。已有的 DXF→Excel 与 Excel Final 管线直接接入，不复制实现。

## 方案比较与选择

### 方案 A：重建专用生产编排引擎

为总流程图逐节点新建模型、队列和 API。表达力最强，但会与现有 Job、File、Result 和 Workflow 重复，迁移面大，短期无法保持可运行。

### 方案 B：在现有 Workflow 上增加薄编排层（采用）

保留现有工作流模型，以模板元数据描述阶段能力；用现有 Job 作为执行事实源、StoredFile/AnalysisResult 作为产物事实源。新增文件绑定和阶段执行 API，把已实现管线接入，其他阶段显式留白。该方案改动可分段验证，并与当前架构“route 负责 HTTP、service 负责不变量、Celery task 调 service”一致。

### 方案 C：只完善前端引导和文档

改动最小，但服务器仍不能编排真实作业，也无法形成生产链路证据，不满足目标。

## 生产模板

新增 `linux_production` 模板，并保持已有 `excel_delivery`、`file_delivery` 兼容。模板阶段固定如下：

| 顺序 | 阶段代码 | 运行方式 | 当前能力 |
|---:|---|---|---|
| 1 | `source_intake` | 文件绑定 + 人工冻结 | 已实现，复用 `/files` |
| 2 | `drawing_processing` | 算法/人工拆板交接 | 占位接口 |
| 3 | `excel_stage1` | Celery `extract_dxf_to_excel` | 已实现，按 `batch_name` 执行 |
| 4 | `design_barrier` | 人工完整性确认 | 已实现，人工确认 |
| 5 | `excel_final` | Celery `process_excel_final` | 已实现，按 Excel `file_id` 执行 |
| 6 | `cam_packaging` | CAM 工作包生成 | 占位接口 |
| 7 | `windows_cam` | Windows Node Agent / SinoCAM | 外部能力占位接口 |
| 8 | `result_acceptance` | CAM 结果校验与接纳 | 占位接口 |
| 9 | `delivery_archive` | 人工确认 + 文件归档 | 已实现，复用产物与签名下载 |

阶段定义由后端单一注册表维护，包含名称、说明、执行模式、实现状态、输入字段和产物类型。`GET /workflows/templates` 公开这些元数据，前端不再硬编码对能力的猜测。

## API 契约

新增以下端点：

- `GET /api/v1/workflows/templates`：返回可创建模板及每个阶段的能力元数据。
- `POST /api/v1/workflows/{workflow_id}/artifacts`：把现有 `file_id` 或 `result_id` 绑定到指定阶段。服务端验证项目成员权限、文件/结果可读性、阶段存在性，并保证同一阶段、类型和文件不会重复登记。
- `POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/executions`：统一阶段执行入口。`excel_stage1` 接收 `batch_name`，`excel_final` 接收 `file_id`；占位阶段返回稳定错误码和其接口契约。
- `POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/completion`：保留人工阶段确认，但拒绝用它绕过 automated 或 placeholder 阶段。

阶段执行响应返回工作流详情、绑定 Job 和是否复用已有请求。请求使用工作流、阶段和输入组成的服务端幂等键，防止重复点击创建重复 Job。

所有端点补充 FastAPI `summary`、`description`、响应 schema 和业务错误描述，使生成的 `docs/api.md` 与 OpenAPI 同步。

## 文件与产物管理

文件字节仍只由 `/files` 和 storage adapter 管理；Workflow 不接受第二套 multipart 上传。前端在生产流程中列出用户可读的现有文件和批次，引导用户先上传到文件模块，再通过 `file_id` 绑定。

工作流产物只保存 `file_id`/`result_id` 引用和版本元数据。Job 成功同步时，服务自动查找当前 Job 的 AnalysisResult，把结果文件挂接到相应阶段；重复同步不得生成重复 artifact。下载继续使用 `/files/{id}/download-url` 的短时签名能力。

源文件冻结以阶段绑定记录表达：`source_intake` 至少绑定一个输入文件后才能人工完成。`excel_final` 输入文件必须为可读 `.xls`/`.xlsx`；`excel_stage1` 的 batch 必须至少包含一个调用者可读 DXF。

## 状态流与错误处理

工作流启动后首阶段进入 `waiting_input`。人工阶段确认后推进下一阶段；自动阶段通过 executions 入口创建并绑定 Job，工作流进入 `running`。详情查询同步 Job 的 attempt、进度、终态和错误，并在成功时自动挂接结果产物，再把下一阶段置为 `waiting_input`。

feature flag 关闭时返回已有的 503 错误语义；输入不合法返回 4xx；占位能力返回 501 和稳定错误码 `WORKFLOW_STAGE_NOT_IMPLEMENTED`，外部 Windows 阶段返回同码并声明所需接口。任何失败都不伪造产物，不覆盖先前版本。

取消工作流时，如当前阶段绑定 active Job，先按现有 Job 状态机提交取消，再取消开放阶段；终态 Job 和历史产物保留审计引用。

## 前端设计

沿用现有管理端的工业控制台风格，强化“流程轨道”而非另做视觉体系：

- 新建流程支持 Linux 生产模板，并展示九阶段能力摘要。
- 详情抽屉读取模板元数据，为 manual、implemented job、placeholder/external 分别显示不同操作。
- 文件面板从 `/files` 读取可访问文件，可按批次/扩展名筛选并绑定；已有产物可预览其文件名、阶段、版本和下载。
- `excel_stage1` 提交 batch name；`excel_final` 从已绑定或上一步生成的 Excel 文件中选择 `file_id`。
- 占位阶段显示所需输入输出和“接口已预留”，触发时展示后端稳定错误，不允许一键伪完成。
- 运行态轮询详情，显示 Job、attempt、进度和错误；成功后自动刷新产物。

## 测试与验收

后端按 TDD 覆盖模板、权限、文件绑定去重、输入约束、真实 Job 创建/绑定、feature flag、占位错误、Job 同步产物和人工阶段边界。前端至少通过 TypeScript build、源码契约测试和 Playwright 工作流页面场景。

最终服务器验证分两层：默认关闭真实管线时，完整九阶段框架可创建、绑定、查询并对占位能力返回真实边界；启用可用管线和有效样本时，验证 `/files` → workflow artifact → DXF→Excel Job → Excel result → Excel Final Job → final artifact 的 Linux 闭环。未提供外部 Windows/SinoCAM 实现时，不把其占位阶段声明为已生产验证。
