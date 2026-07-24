# Linux 生产工作流框架

## 1. 定位与事实源

工作流记录项目内从输入冻结到交付归档的业务进程。它不另建队列或文件系统：

- `WorkflowRun` / `WorkflowStageRun` 保存业务阶段、责任边界和进度；
- `Job` / `JobStep` 是异步执行和 attempt 的事实源；
- `StoredFile` 与 storage adapter 管文件登记和对象字节；
- `AnalysisResult` 管处理结果；
- `WorkflowArtifact` 只保存已有 file/result 引用和阶段元数据。

公开 `/workflows` 路由已经接通生产输入账本、DWG→DXF、Steel DXF 分类、冻结 Excel 第一阶段、Job attempt 同步、结果产物挂接和 active Job 取消。DXF→Excel 只保留为独立转换工具，不是生产工作流阶段。拆板算法、CAM 工作包算法、Windows Node Agent 与 SinoCAM 尚无服务器实现；它们拥有稳定阶段与输入输出合同，但前端不提交虚假任务，直接说明未实现边界。

### 1.1 代码归属与输入规则校正

工作流正式实现位于 `backend/app/modules/workflows/`：`models/` 拥有五张表，`schemas/`
拥有 HTTP/展示契约，`templates.py` 是阶段事实源，`lifecycle.py` 与 `job_sync.py` 分别负责
业务状态和 Job attempt 投影，`intake/` 按登记、转换、冻结和展示拆分，`routes/` 组合 16 个
operation。其他业务模块只能导入 `workflows.interface`；旧的 workflow API/model/schema/service
横向文件已经退出。

`/home/Creeken/Paper/CAD_research/结构图/` 中的早期流程文字包含人工上传 DXF。随后确认的
当前产品规则优先：人工只上传多个 DWG 和一个 Excel，DXF 必须由服务器 DWG→DXF Job
生成、登记、校验和配对。结构图节点仍用于追溯，但不能据旧文字恢复人工 DXF 输入。

## 2. 模板与阶段能力

`GET /api/v1/workflows/templates` 是模板元数据的权威入口。前端根据返回的 `execution_mode`、`implementation_status`、`execution_kind`、`required_inputs` 和 `artifact_types` 渲染操作，不在浏览器中猜测后端能力。

兼容模板 `excel_delivery` 和 `file_delivery` 保持原有人工阶段顺序。新增完整服务器框架 `linux_production`：

| 顺序 | stage code | 执行方式 | 当前实现与产物 |
|---:|---|---|---|
| 1 | `source_intake` | guarded | 人工上传多个 DWG 和唯一 Excel；服务器生成并校验同名 DXF，创建 Drawing 后冻结输入清单 |
| 2 | `dxf_classification` | automated | 真实创建 `classify_steel_dxf` Job；按 1.1.0 契约预处理和分类分流；产物为逐图 DXF、JSON 报告和 CSV 清单 |
| 3 | `drawing_processing` | placeholder | 自动/人工拆板与独立校验接口留白；分类已在上一阶段完成 |
| 4 | `excel_stage1` | automated | 从冻结输入中解析唯一 `source_excel`，重核对象摘要和登记时表格检查，真实创建 `process_excel_final` Job；产物 `stage1_excel` |
| 5 | `design_barrier` | manual | 人工确认图纸和基础 Excel 已满足最终合并条件 |
| 6 | `cam_packaging` | placeholder | 生产规则分组、清单冻结和 CAM 工作包接口留白 |
| 7 | `windows_cam` | external | Node Agent、租约、fencing token、SinoCAM Runner 外部接口留白 |
| 8 | `result_acceptance` | placeholder | 结果摘要、文件稳定性和正式接纳接口留白 |
| 9 | `delivery_archive` | manual | 确认已登记产物并结束流程 |

`implemented` 表示服务器代码存在，仍受 feature flag、Stage、有效输入、数据库、worker 和存储约束；不表示默认可用。`placeholder` / `external` 表示接口与产物契约存在但核心执行器不存在。

## 3. 数据模型

### 3.1 `workflow_runs`

保存 `project_id`、`created_by`、名称、模板、状态、当前阶段、整体进度、配置、错误和生命周期时间。状态为 `draft`、`waiting_input`、`running`、`waiting_review`、`succeeded`、`failed` 或 `cancelled`。

### 3.2 `workflow_stage_runs`

按模板创建固定顺序阶段，`(workflow_run_id, stage_code)` 唯一。自动阶段记录 `job_id` 与 `job_attempt`；同步只接受 Job 当前 attempt 与绑定 attempt 相等的状态，旧 worker 不能推进新世代。

### 3.3 `workflow_artifacts`

保存 `artifact_type`、阶段、`file_id`、`result_id`、版本和 `metadata_json`。API 要求 file/result 至少一个非空，并验证文件或结果可读权限。Linux 生产模板按阶段强制校验声明的 artifact type 白名单，类型不匹配返回 `WORKFLOW_ARTIFACT_TYPE_INVALID`，不能用任意文件绕过留白交接；未声明白名单的旧模板保持兼容。相同 workflow、stage、type、file、result 的重复绑定返回原 artifact，不复制记录。

数据库仍没有 artifact 非空 CHECK、版本唯一约束或跨项目 CHECK；这些不变量由 service/API 维护，不能绕过应用直写数据库。

### 3.4 `workflow_input_batches` / `workflow_input_items`

每个 Linux workflow 至多一个输入批次。批次记录状态、冻结版本、规范清单 SHA-256 和冻结时间；条目只引用现有 `StoredFile`，记录 DWG/Excel 角色、规范化 stem、转换 `job_id + attempt`、派生 DXF 和最终 `drawing_id`。创建使用唯一约束、savepoint 和 winner reload 保证并发幂等；登记/移除/转换通过批次行锁串行化，避免并发登记两个 Excel。对象字节、大小和 SHA-256 仍由 `/files` 与 storage adapter 管理，不复制存储。

### 3.5 `dxf_classification_runs` / `dxf_classification_items`

每个 Job attempt 建立独立分类 run，保存 workflow/project/job、冻结输入清单 SHA-256、分类器/CLI/报告 schema 版本、汇总、JSON 报告和 CSV 清单文件引用。逐图 item 保存 Drawing、来源派生 DXF、分流输出 DXF、处置、零件类型、诊断、证据和遵循 1.1.0 的输出目录名。`(job_id, job_attempt)` 与 `(run_id, source_file_id)` 唯一，旧 attempt 不覆盖新结果。

## 4. 状态与执行

### 4.1 创建、启动和人工交接

创建时第一阶段 `ready`，其他阶段 `pending`，流程为 `draft`。启动后第一阶段为 `waiting_input`。

completion API 只接受当前可操作阶段：

- automated 阶段返回 409 `WORKFLOW_STAGE_REQUIRES_EXECUTION`，必须调用 executions；
- `source_intake` 必须通过专用 input-batch freeze；通用 completion 返回 409 `WORKFLOW_INPUT_BATCH_NOT_FROZEN`；
- placeholder/external 无交接 artifact 返回 409 `WORKFLOW_HANDOFF_ARTIFACT_REQUIRED`；
- 合法确认把阶段置为成功并将下一阶段置为 `waiting_input`。

人工确认代表有权限的操作员接受已经绑定的交接产物，不代表平台执行了留白算法。

### 4.2 生产输入、服务器转换与冻结

人工输入严格为至少一个 DWG 和恰好一个 Excel（XLS/XLSX），不上传 DXF。浏览器先复用带 `Idempotency-Key` 的 `/files` 上传，再把 `file_id` 登记到 workflow input batch。服务端逐个重新读取对象并校验登记大小、SHA-256、DWG 文件头或 Excel 可读工作表；第二个 Excel、人工 DXF、同名 DWG 和伪扩展文件均返回稳定问题码。

转换请求为每个 DWG 建立稳定的 `convert_dwg_to_dxf` Job：活动/成功 Job 幂等复用，失败或取消 Job 通过现有 retry 递增 attempt。API 先提交数据库再投递 worker；明确的 broker 投递失败会以 status + attempt 条件把仍 queued 的 Job 标为 `JOB_ENQUEUE_FAILED`，下一次请求可递增 attempt 重投，已被 worker 领取的状态不会被覆盖。状态查询只接纳条目绑定 attempt 的成功 Result，并验证派生对象、DXF 结构和规范化同名配对。

冻结在事务和行锁内再次校验全部源对象及配对；随后按 DWG 创建 `Drawing` 与 DWG `DrawingVersion`，挂接 `source_file`、`derived_dxf` 和批次级 `source_excel` artifact，计算 canonical JSON 清单 SHA-256，并原子完成 `source_intake`。冻结后批次只读，不能增删文件或重投转换；通用 `/files` 单文件、批量和批次删除也会锁定并拒绝冻结清单引用，防止 DWG、Excel 或派生 DXF 被旁路软删除。

### 4.3 自动执行

`POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/executions` 先验证项目角色、当前阶段和 execution kind。

`dxf_classification`：

1. 要求 `DXF_CLASSIFICATION_PIPELINE_ENABLED=true`，且生产输入已经冻结；
2. 只读取冻结条目登记的服务器派生 DXF，重新核对对象大小和 SHA-256；
3. 临时输入目录命名为 `<项目代码>-workflow-<id>_dxf`，通过 `python -m steel_dxf_classifier.cli --json` 调用 1.1.0 正式进程契约；
4. 分类器只在临时副本上增加 `*_拆板前.dxf`，原始 MinIO DXF 不改名；
5. 输出严格使用 `<项目名>_<零件类型>_dxf`、`<项目名>_待确认_dxf`、`<项目名>_无法读取_dxf`，逐图核对报告、数量和字节摘要；
6. 每个分流 DXF、JSON 报告和 CSV 清单分别写入 MinIO、登记 `files`，并关联 classification item/artifact/result；
7. CLI 退出码 2 表示“完成但需确认”，仍保存完整结果并进入拆板留白阶段；退出码 1/64 或契约不一致使当前 attempt 失败并允许重试。

`excel_stage1`：

1. 要求 `EXCEL_FINAL_PIPELINE_ENABLED=true`；
2. 输入批次必须冻结，清单摘要存在，并且恰好有一个状态为 frozen 的 `source_excel`；
3. `source_intake` 阶段必须有且仅有一个与该条目同文件的 `source_excel` artifact；
4. 重新核对文件权限、对象 SHA-256、登记时的表格检查版本与规范检查结果；浏览器不能提交或替换 `file_id`；
5. 请求体严格只有 `{"execution_kind":"excel_stage1"}`，服务端生成 `file_id`、`workflow_id` 和冻结清单摘要参数；
6. 以工作流/阶段幂等键创建或复用 `process_excel_final` Job，同事务绑定 attempt，commit 后 dispatch。

相同工作流阶段重放返回同一 Job 且不重复投递；改用不同参数会触发现有 Job 幂等参数冲突。

### 4.4 同步与产物

`GET /api/v1/workflows/{workflow_id}` 同步已绑定 Job 的状态、进度、错误和时间。Job 成功时查询其成功 AnalysisResult，根据阶段能力自动挂接 file/result；重复 GET 幂等。随后推进下一阶段。失败 Job 把流程收敛为 failed，并保留错误与已完成产物。

### 4.5 取消

取消流程时，如果当前阶段绑定 `pending`、`queued`、`running`、`validating` 或 `waiting_cad_worker` Job，先调用现有 guarded Job cancellation，再取消未终态阶段。已完成 Job 和历史 artifact 保留。

### 4.6 失败恢复

自动阶段 Job 失败或被单独取消后，流程停留在原阶段并进入可恢复的 `failed` 状态。重新调用同一 executions 端点会复用原 Job、递增 `attempt`、刷新阶段绑定并重新投递，响应返回 `retried=true`。旧 attempt 的 worker/result 仍由现有 fencing 规则拒绝；显式取消整个流程后不会自动重开。

## 5. API

路由前缀为 `/api/v1/workflows`。

核心写入口为 `POST /api/v1/workflows/{workflow_id}/artifacts` 与 `POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/executions`；前者复用文件/结果，后者统一承载真实 Linux 执行和留白能力探测。

| Method | Path | 行为 |
|---|---|---|
| GET | `/api/v1/workflows/templates` | 模板、阶段顺序和能力契约 |
| GET | `/api/v1/workflows` | 权限过滤后的分页列表，可按项目/状态过滤 |
| POST | `/api/v1/workflows` | 创建流程与阶段 |
| GET | `/api/v1/workflows/{workflow_id}` | 详情、Job 同步和自动产物挂接 |
| GET | `/api/v1/workflows/{workflow_id}/dxf-classification` | 最新分类 attempt、Job、汇总、逐图来源/输出和报告登记 |
| POST | `/api/v1/workflows/{workflow_id}/artifacts` | 绑定已有 File/Result，重复请求幂等 |
| POST | `/api/v1/workflows/{workflow_id}/start` | 启动草稿 |
| GET, POST | `/api/v1/workflows/{workflow_id}/input-batch` | 读取/幂等建立生产输入批次 |
| POST | `/api/v1/workflows/{workflow_id}/input-batch/files` | 登记 `/files` 中的 DWG 或唯一 Excel |
| DELETE | `/api/v1/workflows/{workflow_id}/input-batch/files/{item_id}` | 冻结前移除条目并取消其活动 Job |
| POST | `/api/v1/workflows/{workflow_id}/input-batch/conversion-requests` | 幂等创建或重试 DWG→DXF Job |
| POST | `/api/v1/workflows/{workflow_id}/input-batch/freeze` | 重校验、建 Drawing、冻结清单并推进阶段 |
| POST | `/api/v1/workflows/{workflow_id}/stages/{stage_code}/executions` | 执行真实 Linux 阶段或返回留白契约 |
| POST | `/api/v1/workflows/{workflow_id}/stages/{stage_code}/completion` | 确认 manual 或已有 artifact 的交接阶段 |
| POST | `/api/v1/workflows/{workflow_id}/cancellation-requests` | 取消 active Job 与流程 |

项目 owner/engineer 可写；项目成员可读；全局管理员沿用统一项目访问规则。文件绑定复用 file/result 的读取授权，拿到 workflow ID 不会扩大资源权限。

主要业务错误：

| HTTP | code | 含义 |
|---:|---|---|
| 409 | `WORKFLOW_STAGE_NOT_CURRENT` | 请求的不是当前阶段 |
| 409 | `WORKFLOW_STAGE_REQUIRES_EXECUTION` | 自动阶段不能人工确认 |
| 409 | `WORKFLOW_HANDOFF_ARTIFACT_REQUIRED` | 留白/外部阶段尚无交接产物 |
| 409 | `WORKFLOW_INPUT_BATCH_NOT_FROZEN` | 试图用通用 completion 绕过输入冻结 |
| 409 | `INPUT_EXCEL_ALREADY_EXISTS` / `INPUT_DWG_NAME_CONFLICT` | 唯一 Excel 或规范化 DWG 名冲突 |
| 422 | `INPUT_DXF_NOT_ALLOWED` | 人工登记了应由服务器生成的 DXF |
| 409/415 | `INPUT_OBJECT_CHECKSUM_MISMATCH` / `FILE_NOT_DWG` / `INPUT_EXCEL_UNREADABLE` | 对象摘要或真实格式未通过复核 |
| 409 | `FILE_REFERENCED_BY_FROZEN_INPUT` | 通用文件删除试图破坏冻结输入清单 |
| 503 | `JOB_ENQUEUE_FAILED` | Job 已保存但 broker 投递失败；Job 已收敛为可重试失败状态 |
| 422 | `EXCEL_INPUT_*` | 冻结 Excel 的工作表、标题、必需列或行值不符合第一阶段输入合同；响应包含人工操作建议和来源位置 |
| 501 | `WORKFLOW_STAGE_NOT_IMPLEMENTED` | 阶段接口存在，但核心实现留白；details 返回输入/产物契约 |
| 503 | `DXF_CLASSIFICATION_PIPELINE_DISABLED` | DXF 分类分流 flag 关闭 |
| 409 | `CLASSIFICATION_SOURCE_MISSING` / `CLASSIFICATION_SOURCE_REQUIRED` | 冻结清单缺少可读派生 DXF |
| 503 | `EXCEL_FINAL_PIPELINE_DISABLED` | Excel Final flag 关闭 |

## 6. 前端

React `生产流程` 页面读取模板，提供：

- 页面级“新建生产批次”入口：选择项目和批次名称后连续创建并启动 Linux workflow，再进入可直接收藏和恢复的 `/workflows/{id}` 独立详情页；
- 已创建但启动失败的 draft 在详情页保留启动恢复入口；
- Linux 九阶段生产轨道和实现状态标签；
- 项目内流程创建、分页、状态筛选、启动和取消；
- source intake 专用四步面板：多选 DWG、单个 Excel、服务器转换、确认冻结；
- 逐文件上传/登记、Job attempt/进度、DXF 配对、结构化问题和修复建议；
- 上传成功但登记失败时只重试登记，避免重复保存对象；
- 冻结后详情工作区自动进入 DXF 分类控制台，无需重新选择文件；
- 分类开始/重试、Job 进度、类型汇总、逐图处置/诊断、分流 DXF 与 JSON/CSV 下载；
- Excel 第一阶段只提交 `execution_kind`，服务端自动使用冻结 `source_excel`，页面不存在第二个文件选择器；
- placeholder/external 只展示明确留白和输入/产物合同，不发送必然失败的探测请求；
- Job/attempt/进度/错误展示；
- 阶段产物和复用 `/files` 签名下载。

界面按钮不是权限边界；FastAPI 仍执行项目、文件、状态和 feature flag 校验。

## 7. 当前验证和未完成边界

当前工作流为九阶段 revision 2。后端回归锁定冻结 Excel 校验、严格执行体、迁移拒绝遗留执行证据和产物挂接；前端 Playwright 锁定独立详情页、冻结 Excel 自动执行、结构化表格错误，以及新建→上传→冻结→分类分流闭环。最终门禁和确切计数见[当前验证证据](../verification/current.md)。

仍未完成：

- 自动拆板、人工拆板结果业务校验；
- 深化设计 barrier 的机器完整性校验；
- CAM 规则分组和工作包生成；
- Windows Node Agent、租约、fencing token、SinoCAM Runner/Adapter；
- CAM 结果正式接纳算法与确定性交付清单；
- 多请求并发推进的行锁/version 控制；
- 获准真实生产图纸下的分类准确率业务验收。

因此本次交付已完成服务器输入冻结和 DXF 分类分流，并保留后续拆板/CAM 边界；不是 SinoCAM 生产闭环已经完成的声明。
