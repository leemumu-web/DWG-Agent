# Linux 生产工作流框架

## 1. 定位与事实源

工作流记录项目内从输入冻结到交付归档的业务进程。它不另建队列或文件系统：

- `WorkflowRun` / `WorkflowStageRun` 保存业务阶段、责任边界和进度；
- `Job` / `JobStep` 是异步执行和 attempt 的事实源；
- `StoredFile` 与 storage adapter 管文件登记和对象字节；
- `AnalysisResult` 管处理结果；
- `WorkflowArtifact` 只保存已有 file/result 引用和阶段元数据。

公开 `/workflows` 路由已经接通生产输入账本、DWG→DXF、Steel DXF 分类、Steel DXF Split 1.5.2 整批拆板与独立校验、冻结 Excel 第一阶段、四类文件流式分批导出与确认后物理释放、Job attempt 同步、结果产物挂接和 active Job 取消。DXF→Excel 只保留为独立转换工具，不是生产工作流阶段。拆板纵向切片默认关闭，仍需真实 MinIO/MySQL 与代表性业务样本验收；CAM 工作包算法、Windows Node Agent 与 SinoCAM 尚无服务器实现。

### 1.1 代码归属与输入规则校正

工作流正式实现位于 `backend/app/modules/workflows/`：`models/` 拥有六张表，`schemas/`
拥有 HTTP/展示契约，`templates.py` 是阶段事实源，`lifecycle.py` 与 `job_sync.py` 分别负责
业务状态和 Job attempt 投影，`intake/` 按登记、转换、冻结和展示拆分，`routes/` 组合 70 个
operation。其他业务模块只能导入 `workflows.interface`；旧的 workflow API/model/schema/service
横向文件已经退出。

`/home/Creeken/Paper/CAD_research/结构图/` 中的早期流程文字包含人工上传 DXF。随后确认的
当前产品规则优先：人工分别上传 DWG 文件夹和一个 `.xls`/`.xlsx` 单文件，DXF 必须由服务器 DWG→DXF Job
生成、登记、校验和配对。结构图节点仍用于追溯，但不能据旧文字恢复人工 DXF 输入。

## 2. 模板与阶段能力

`GET /api/v1/workflows/templates` 是模板元数据的权威入口。前端根据返回的 `execution_mode`、`implementation_status`、`execution_kind`、`required_inputs`、`artifact_types` 和 `required_outputs` 渲染操作，不在浏览器中猜测后端能力。新建 `linux_production` 使用 `definition_revision 4`；已存在的流程保留创建时的 revision 和阶段行。

图纸主链是：

`source_dwg → canonical_dxf → classified_dxf → processed_dxf → cam_input_dxf → cam_output_dxf → accepted_dxf → delivery_dxf`

操作员提交一个 Excel 和多个 DWG；所有源对象先登记，每个 DWG 转换并核验为
`canonical_dxf`，冻结 manifest 和指向 DXF 的 `DrawingVersion` 后，所有后续图纸工件都必须
是 DXF。Excel、报告和 manifest 保持各自格式。

兼容模板 `excel_delivery` 和 `file_delivery` 保持原有人工阶段顺序。新增完整服务器框架 `linux_production`：

| 顺序 | stage code | 执行方式 | 当前实现与产物 |
|---:|---|---|---|
| 1 | `source_intake` | guarded | 登记 `source_dwg` 与 `source_excel`；服务器生成 `canonical_dxf`，创建指向 DXF 的 DrawingVersion 后冻结输入清单 |
| 2 | `dxf_classification` | automated | 消费 `canonical_dxf`；产出 `classified_dxf`、JSON 报告和清单 |
| 3 | `drawing_processing` | automated | 整批消费 `classified_dxf`；BH/BOX 通过 Steel DXF Split 1.5.2 生成正常拆板与余量增长 DXF，并由独立步骤重开校验；其他类型或未通过图纸保留明确原因且不进入正式交接；产出 7 类正式 artifact |
| 4 | `excel_stage1` | automated | 从冻结输入中解析唯一 `source_excel`，同时接收当前拆板 run 的 `processed_dxf` 与 `BH拆板信息表.xlsx` 交接，重核对象摘要和登记时表格检查，真实创建 `process_excel_final` Job；产物 `stage1_excel` |
| 5 | `excel_stage2` | placeholder | 消费 `stage1_excel` 与 `processed_dxf`，预留 `stage2_excel`；当前等待上线 |
| 6 | `design_barrier` | manual | 人工确认图纸和 `stage2_excel` 已满足后续生产条件 |
| 7 | `cam_packaging` | placeholder | 合同要求 `cam_input_dxf` 与 CAM 清单；等待上线 |
| 8 | `windows_cam` | external | 合同要求 `cam_output_dxf`；Node Agent、租约、fencing token、SinoCAM Runner 等待上线 |
| 9 | `result_acceptance` | placeholder | 合同要求 `accepted_dxf` 与接纳报告；等待上线 |
| 10 | `delivery_archive` | manual | 合同要求 `delivery_dxf`、基于 `stage2_excel` 的交付 Excel 和归档清单；前端等待上线 |

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

每个 Job attempt 建立独立分类 run，保存 workflow/project/job、冻结输入清单 SHA-256、分类器/CLI/报告 schema 版本、汇总、JSON 报告和 CSV 清单文件引用。逐图 item 保存 Drawing、来源派生 DXF、分流输出 DXF、处置、零件类型、原始/规范规格、类型来源、稳定 `group_key`、`next_stage_eligible`、诊断、证据和遵循 1.2.0 的输出目录名。`(job_id, job_attempt)` 与 `(run_id, source_file_id)` 唯一，旧 attempt 不覆盖新结果。

### 3.6 `dxf_split_runs` / `dxf_split_items`

每个拆板 Job attempt 建立不可变 run，保存 workflow/project/job、输入清单 SHA-256、Split/CLI/独立校验 schema、输入/自动通过/未形成正式结果计数、来源合同映射和批次级 ledger/manifest/validation 文件引用。逐图 item 保存分类 item、分类原始文件、Drawing、零件类型、来源合同、自动路线、处置、正常拆板/余量增长 DXF、两类算法报告、诊断和独立校验结果。`(job_id, job_attempt)` 与 `(run_id, source_file_id)` 唯一；拆板整批只执行一次，旧 attempt 仅作历史兼容，不进入当前 UI、Excel 交接或正式归档。

### 3.7 `workflow_batch_exports`

每次创建导出时冻结用户选择、当前 attempt 的文件登记属性和固定 ZIP 路径，并保存随机下载
能力的 SHA-256 摘要，不保存明文 token 或 ZIP 字节。状态为
`prepared → downloading → downloaded`，中断变为 `download_failed` 并允许重试；只有服务端
出库流水已成功且用户二次确认后才能进入 `purged`。物理清理成功时清空 manifest 和 token
摘要，记录对象/预览缓存数量与释放字节；`files` 行保留为带 `purged_at` 的外键墓碑。

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

人工输入分为两个命令：`POST /input-excel` 只接收一个 `.xls` 或 `.xlsx`，`POST /input-dwg-folder` 只接收同一根目录下的一个或多个 DWG。浏览器选择的 DWG 文件夹若混有其他文件，先列出并要求确认，确认后这些文件不发送到服务器；绕过前端向 DWG 入口发送非 DWG 时整批拒绝。两类输入分别复用 Files 存储与 Workflow 登记能力；任一对象的 DWG 文件头、Excel 可读工作表、大小或 SHA-256 校验失败，该次请求回滚。

转换请求为每个 DWG 建立稳定的 `convert_dwg_to_dxf` Job：活动/成功 Job 幂等复用，失败或取消 Job 通过现有 retry 递增 attempt。API 先提交数据库再投递 worker；明确的 broker 投递失败会以 status + attempt 条件把仍 queued 的 Job 标为 `JOB_ENQUEUE_FAILED`，下一次请求可递增 attempt 重投，已被 worker 领取的状态不会被覆盖。状态查询只接纳条目绑定 attempt 的成功 Result，并验证派生对象、DXF 结构和规范化同名配对。

冻结在事务和行锁内再次校验全部源对象及配对；随后按 DWG 创建 `Drawing` 与指向
`canonical_dxf` 的 `DrawingVersion`，挂接 `source_dwg`、`canonical_dxf` 和批次级
`source_excel` artifact，计算 canonical JSON 清单 SHA-256，并原子完成 `source_intake`。
冻结后批次只读，不能增删文件或重投转换；通用 `/files` 删除也拒绝冻结清单引用。

### 4.3 自动执行

`POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/executions` 先验证项目角色、当前阶段和 execution kind。

`dxf_classification`：

1. 要求 `DXF_CLASSIFICATION_PIPELINE_ENABLED=true`，且生产输入已经冻结；
2. 只读取冻结条目登记的服务器派生 DXF，重新核对对象大小和 SHA-256；
3. 临时输入目录命名为 `<项目代码>-workflow-<id>_dxf`，通过 `python -m steel_dxf_classifier.cli --json` 调用 1.2.0 正式进程契约；
4. 分类器只在临时副本上增加 `*_拆板前.dxf`，原始 MinIO DXF 不改名；
5. 输出严格使用 `<项目名>_<零件类型>_dxf`、`<项目名>_待确认_dxf`、`<项目名>_无法读取_dxf`，逐图核对报告、数量和字节摘要；
6. 每个分流 DXF、JSON 报告和 CSV 清单分别写入 MinIO、登记 `files`，并关联 classification item/artifact/result；
7. CLI 退出码 2 表示“完成但需确认”，仍保存完整结果；只有 `next_stage_eligible=true` 的分类输出可进入拆板。退出码 1/64 或契约不一致使当前 attempt 失败并允许重试。

分类查询按数据库 `group_key` 聚合为类型、待确认和无法读取文件夹，逐类详情分页读取；下一阶段公共接口只返回 `next_stage_eligible=true` 且输出对象存在的 DXF。分类页面不展示 JSON/CSV 审计文件。任一类别或全部分类下载都复用 Files 的权限、传输账本和流式 ZIP，且 ZIP 成员严格限于已登记 `.dxf`。

`drawing_processing`：

1. 要求 `DXF_SPLIT_PIPELINE_ENABLED=true`；服务端只读取最新分类 run 中 `next_stage_eligible=true` 的文件登记，不接受浏览器路径、临时 URL 或人工上传 DXF；
2. 创建整批冻结清单并以一个 Job attempt 处理全部图纸，不因第一张人工复核而中止；BH 使用 `project_tekla_bh_dxf_v1`，BOX 使用 `project_tekla_box_dxf_v1`，其他已分类类型直接标为 `manual_review`；
3. BH/BOX 在隔离目录调用 `python -m steel_dxf_split.cli <input_directory> --output-dir <output_directory>`，并显式传入上述两个来源合同授权参数；产物文件名固定为 `<构件>_正常拆板.dxf` 和 `<构件>_余量增长.dxf`，平台内部字段分别为 `normal_dxf`、`weld_allowance_dxf`；
4. 算法子进程结束后，由 Job 的第二个步骤独立重开两个 DXF，核对成对文件、报告路径、自动处置和目录边界；任一业务校验不通过只把该图纸标为未形成正式结果，其余图纸继续；
5. 正常拆板、余量增长、两类算法报告、独立校验报告、批次 manifest 与 `BH拆板信息表.xlsx` 均登记为正式文件；DXF 写入现有 `dxf-derived` bucket，报告/ledger 写入现有 `dwg-reports` bucket，key 固定在 `workflows/{workflow_id}/drawing-processing/attempt-{attempt}/...`，不新增 bucket；
6. 全部自动通过时 Job/run 为 `succeeded`/`completed`；部分图纸未形成结果时 Job 仍为 `succeeded`、run 为 `completed_with_review`。只要至少有一组完整正式产物，第三阶段即成功并解锁第四阶段，Excel 交接只包含正式配对图纸；若一组正式产物也没有，则以缺少阶段产物明确失败；
7. executions 端点先锁定工作流行；阶段一旦绑定拆板 Job，后续请求始终复用该 Job，不因项目操作者变化而新建逻辑任务。整批最多执行 1 个 attempt，不因明确的单图问题重跑。投递或执行失败时 Job/run 收敛为 `failed`；运行中的 Job 被取消时只关闭对应 run；
8. 本批原图 ZIP 由分类阶段流式提供，保留全部分类图纸且不混入拆板成品。正式拆板 ZIP 只包含已通过成对验收的 DXF，固定使用 `原长/` 与 `余量增长后短文件/` 两个一级目录。

`excel_stage1`：

1. 要求 `EXCEL_FINAL_PIPELINE_ENABLED=true`；
2. 输入批次必须冻结，清单摘要存在，并且恰好有一个状态为 frozen 的 `source_excel`；
3. `source_intake` 阶段必须有且仅有一个与该条目同文件的 `source_excel` artifact；
4. 重新核对文件权限、对象 SHA-256、登记时的表格检查版本与规范检查结果；浏览器不能提交或替换 `file_id`；
5. 请求体严格只有 `{"execution_kind":"excel_stage1"}`，服务端生成 `file_id`、`workflow_id`、冻结清单摘要与当前拆板 run 的稳定交接参数；交接含正常拆板文件 ID、`BH拆板信息表.xlsx` 文件 ID 和 run/attempt 摘要，不传本地路径或临时 URL；
6. 以工作流/阶段幂等键创建或复用 `process_excel_final` Job，同事务绑定 attempt，commit 后 dispatch。

相同工作流阶段重放返回同一 Job 且不重复投递；改用不同参数会触发现有 Job 幂等参数冲突。

### 4.4 同步与产物

`GET /api/v1/workflows/{workflow_id}` 同步已绑定 Job 的状态、进度、错误和时间。Job 成功时查询其成功 AnalysisResult，根据阶段能力自动挂接 file/result；重复 GET 幂等。普通成功随后解锁下一阶段，但前端工作区继续显示刚完成阶段，直到操作员主动点击下一阶段；拆板 `completed_with_review` 表示整批已处理且部分图纸未形成正式配对结果，只要至少存在一组完整正式产物，第三阶段仍成功并解锁 Excel 第一阶段。失败 Job 把流程收敛为 failed，并保留错误与已完成产物。工作流归档以已验证的项目成员资格和 artifact/run 账本为下载边界，因此同项目工程师可接续前一位操作者生成的服务器产物；非项目成员仍在进入归档收集前被拒绝。

### 4.5 分批导出与物理释放

入口只放在 Stage A3 “03 · 图纸拆板与独立校验”卡片标题栏右侧。四个 UI 标签映射为：

- `原 DXF` → 当前分类 attempt 的 `classified_dxf` → `原DXF/`；
- `正常拆板 DXF` → 当前拆板 attempt 的 `processed_dxf` → `正常拆板DXF/`；
- `原 Excel` → 冻结输入 `source_excel` → `原Excel/`；
- `产出 Excel` → 当前成功 Excel 第一阶段 Job 的 `stage1_excel` → `产出Excel/`。

叶子名称严格使用 `files.original_name`；不改名、不翻译、不加前后缀，路径不安全或同一目录
不区分大小写重名时失败关闭。ZIP 使用 stdlib streaming data descriptor 直接读取存储分块并
向 HTTP 响应产出，不在服务器磁盘落临时文件。浏览器通过原生 `<a>` 下载；路径级 HttpOnly
能力避免把 bearer/token 放进 URL。

下载流结束后，独立数据库事务同时把 `file_transfers(operation=workflow_batch_export)` 和
export 状态标记成功；中断则标记失败且保留对象。purge 要求创建者或管理员、项目写角色、
`downloaded` 状态和成功出库流水，并在 queued/running stage 上失败关闭。确认后删除所选
Local/MinIO 对象及其 DXF SVG 预览缓存，移除对应 workflow artifact 文件引用，将登记标记为
`deleted + purged_at`，但不破坏 Drawing、Job、输入和拆板账本外键。

### 4.6 取消

取消流程时，如果当前阶段绑定 `pending`、`queued`、`running`、`validating` 或 `waiting_cad_worker` Job，先调用现有 guarded Job cancellation，再取消未终态阶段。已完成 Job 和历史 artifact 保留。

### 4.7 失败恢复

一般自动阶段 Job 失败或被单独取消后，流程停留在原阶段并进入可恢复的 `failed` 状态。重新调用同一 executions 端点会复用原 Job、递增 `attempt`、刷新阶段绑定并重新投递，响应返回 `retried=true`。拆板整批只允许一次权威 attempt：明确的单图业务问题保留具体原因并继续同批其他图纸，不自动重做整批；技术失败保留原 Job 和错误，由操作员排障。旧 attempt 的 worker/result 仍由现有 fencing 规则拒绝；显式取消整个流程后不会自动重开。

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
| GET | `/api/v1/workflows/{workflow_id}/drawing-processing` | 仅返回工作流当前拆板 Job/attempt 对应的 run、汇总和逐图处置；尚未建立 run 时返回 `data: null` |
| GET | `/api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive` | 历史兼容：即时下载当前 run 未形成正式结果图纸的分类原始 DXF ZIP |
| POST | `/api/v1/workflows/{workflow_id}/artifacts` | 绑定已有 File/Result，重复请求幂等 |
| POST | `/api/v1/workflows/{workflow_id}/start` | 启动草稿 |
| GET, POST | `/api/v1/workflows/{workflow_id}/input-batch` | 读取/幂等建立生产输入批次 |
| POST | `/api/v1/workflows/{workflow_id}/input-excel` | 审核并登记一个 `.xls` 或 `.xlsx` |
| POST | `/api/v1/workflows/{workflow_id}/input-dwg-folder` | 审核并登记一个只含 DWG 的文件夹 |
| DELETE | `/api/v1/workflows/{workflow_id}/input-folder` | 冻结前整批清空输入文件夹并取消活动 Job |
| GET | `/api/v1/workflows/{workflow_id}/download-archive` | 按阶段和产物类型下载完整生产 ZIP |
| GET | `/api/v1/workflows/{workflow_id}/stages/excel_stage1/preflight` | 用正式执行的同一规则预检冻结输入、唯一源表、对象摘要、Excel 合同和正式拆板交接，不创建 Job |
| GET | `/api/v1/workflows/{workflow_id}/stages/excel_stage1/download-result` | 校验 workflow/stage/Job/Result/File/对象完整来源链后，直接下载唯一 `.xlsx` |
| GET | `/api/v1/workflows/{workflow_id}/batch-exports/preview` | 统计当前四类可导出文件，不读取对象字节 |
| POST | `/api/v1/workflows/{workflow_id}/batch-exports` | 冻结选择并签发路径级短期下载能力 |
| GET | `/api/v1/workflows/{workflow_id}/batch-exports/{export_uid}` | 读取创建者本次导出的下载/清理状态 |
| GET | `/api/v1/workflows/{workflow_id}/batch-exports/{export_uid}/download` | 通过 HttpOnly 能力直接流式发送 ZIP；此步不删除 |
| POST | `/api/v1/workflows/{workflow_id}/batch-exports/{export_uid}/purge` | 下载完成后二次确认物理删除对象并保留引用墓碑 |
| POST | `/api/v1/workflows/{workflow_id}/input-batch/conversion-requests` | 幂等创建或重试 DWG→DXF Job |
| POST | `/api/v1/workflows/{workflow_id}/input-batch/freeze` | 重校验、建 Drawing、冻结清单并推进阶段 |
| POST | `/api/v1/workflows/{workflow_id}/stages/{stage_code}/executions` | 执行真实 Linux 阶段或返回留白契约 |
| POST | `/api/v1/workflows/{workflow_id}/stages/{stage_code}/completion` | 确认 manual 或已有 artifact 的交接阶段 |
| POST | `/api/v1/workflows/{workflow_id}/cancellation-requests` | 取消 active Job 与流程 |

项目 owner/engineer 可写；项目成员可读；全局管理员沿用统一项目访问规则。文件绑定复用 file/result 的读取授权，拿到 workflow ID 不会扩大资源权限。

主要业务错误：

| HTTP/位置 | code | 含义 |
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
| 503 | `DXF_SPLIT_PIPELINE_DISABLED` | DXF 拆板 flag 关闭 |
| 409 | `DXF_SPLIT_ATTEMPTS_EXHAUSTED` | DXF 拆板权威 Job 已执行过唯一一次完整批次尝试 |
| 409 | `DXF_SPLIT_WORKFLOW_EXECUTION_REQUIRED` | 公共 Job 创建/重试端点不能绕过工作流阶段绑定和 attempt 预算 |
| 409 | `DXF_SPLIT_JOB_BINDING_INVALID` | 阶段已绑定的权威 Job 与当前项目、冻结输入或 attempt 不一致 |
| 409 | `DXF_CLASSIFICATION_REVIEW_UNRESOLVED` / `DXF_CLASSIFICATION_PROJECT_MISMATCH` / `DXF_SPLIT_INPUT_REQUIRED` / `DXF_SPLIT_SOURCE_MISSING` | 当前分类 run、项目 Job 或登记对象不满足拆板输入合同 |
| Job error | `DXF_SPLIT_FAILED` | CLI、对象存储或持久化发生技术失败；保留同一 Job 和具体错误，不自动重跑整批 |
| run error | `DXF_SPLIT_ATTEMPT_INTERRUPTED` | Job 被取消或历史 attempt 被取代；只关闭对应旧 run，不改写当前 Job |
| run status | `completed_with_review` | 单图算法或独立校验未通过；Job 仍成功，该图保留原因与原图但不进入正式 ZIP/Excel，同批正式配对结果继续流转 |
| 404/409 | `DXF_SPLIT_RUN_NOT_CURRENT` / `DXF_SPLIT_RUN_STALE` / `DXF_SPLIT_REVIEW_ARCHIVE_*` | 历史兼容接口请求的 run 不是当前 attempt，Excel 交接指向旧 attempt，或没有可下载的未通过原图 |
| 503 | `EXCEL_FINAL_PIPELINE_DISABLED` | Excel Final flag 关闭 |
| 409 | `EXCEL_STAGE_RESULT_NOT_READY` / `EXCEL_STAGE_RESULT_CARDINALITY_INVALID` / `EXCEL_STAGE_RESULT_BINDING_INVALID` | Excel 阶段尚未成功、结果不是唯一文件，或 workflow/Job/attempt/Result 来源链不一致 |
| 409/404 | `EXCEL_STAGE_RESULT_FILE_UNAVAILABLE` / `EXCEL_STAGE_RESULT_FORMAT_INVALID` / `EXCEL_STAGE_RESULT_OBJECT_MISSING` / `EXCEL_STAGE_RESULT_OBJECT_CHANGED` | 唯一结果登记不可用、不是 `.xlsx`、对象缺失或对象大小已变化 |
| 409 | `EXCEL_STAGE_SINGLE_FILE_DOWNLOAD_REQUIRED` | 试图通过通用阶段 ZIP 入口下载 Excel；应改用唯一结果下载端点 |
| 409 | `WORKFLOW_EXPORT_CATEGORY_EMPTY` / `WORKFLOW_EXPORT_FILENAME_INVALID` / `WORKFLOW_EXPORT_FILENAME_CONFLICT` | 选中类别为空、登记名不适合安全 ZIP 路径，或保留原文件名会产生路径冲突 |
| 409 | `DXF_SPLIT_EXPORT_PAIR_REQUIRED` / `DXF_SPLIT_EXPORT_INCOMPLETE` | 正式拆板 ZIP 没有同时选择两类配对文件，或配对文件引用/可用数量与自动通过数不一致 |
| 403/410 | `WORKFLOW_EXPORT_TOKEN_INVALID` / `WORKFLOW_EXPORT_TOKEN_EXPIRED` | 原生下载的路径级能力无效或过期；源文件不删除 |
| 409 | `WORKFLOW_EXPORT_MANIFEST_STALE` / `WORKFLOW_EXPORT_NOT_DOWNLOADED` / `WORKFLOW_EXPORT_PURGE_ACTIVE_STAGE` | 冻结登记变化、服务端尚未完整发送 ZIP，或仍有 queued/running stage |
| 503 | `WORKFLOW_EXPORT_PURGE_FAILED` | 对象清理未完整完成；查看 `workflow_export_purge` 流水和一致性扫描后重试 |

## 6. 前端

React `生产流程` 页面读取模板，提供：

- 页面级“新建生产项目”入口：填写项目编号、名称和说明后，由 `POST /api/v1/workflows/production-projects` 在同一事务中创建 Project、所有者关系及其唯一 Linux production workflow 并启动，再进入可直接收藏和恢复的 `/workflows/{id}` 独立详情页；
- 生产项目列表响应聚合 Project 编号/名称并提供全局状态统计；状态筛选只影响行分页，不改变统计口径；
- 通用 Workflow 创建服务锁定 Project，并以 `PRODUCTION_WORKFLOW_ALREADY_EXISTS` 拒绝同一项目的第二条 `linux_production` 流程；兼容 workflow 类型保持原行为；
- 已创建但启动失败的 draft 在详情页保留启动恢复入口；
- Linux 十阶段生产轨道、实现状态标签和等待上线视觉边界；
- 项目内流程创建、分页、状态筛选、启动和取消；
- source intake 专用面板：分步提交 Excel 与 DWG 文件夹、确认忽略其他文件、服务器转换、确认冻结；
- 普通工作流产物按阶段组织为 ZIP；Excel 第一阶段只提供唯一 `.xlsx` 单文件下载，不套 ZIP；
- 逐文件上传/登记、Job attempt/进度、DXF 配对、结构化问题和修复建议；
- 上传成功但登记失败时只重试登记，避免重复保存对象；
- 冻结后服务端解锁 DXF 分类，但详情工作区保留在刚完成的输入阶段；操作员主动点击已解锁阶段后进入分类控制台；
- 分类开始/重试、Job 进度、类型汇总、逐图处置/诊断以及分类/全量 DXF-only 下载；JSON/CSV 只进入审计产物；
- 拆板整批启动、当前 Job/attempt 进度、输入/正式配对/未形成正式结果汇总和逐图原因展示；
- Stage A3 拆板卡片标题栏右侧的“分批导出”：四类勾选、原生浏览器流式下载、下载完成状态、明确“暂不删除”和第二次不可恢复清理确认；该入口不出现在全局“生产产物与证据”区；
- 拆板卡片提供正式配对结果和本批全部分类原图两个异步原生下载入口；不展示候选图、算法报告或逐图人工复核工作台；
- Excel 第一阶段先显示五层预检；预检通过后只提交 `execution_kind`，正式执行再次运行同一门禁。服务端自动使用冻结 `source_excel`，页面不存在第二个文件选择器；成功结果只下载一个 `.xlsx`；
- Excel 第二阶段及 CAM/归档节点只展示等待上线和输入/产物合同，不发送必然失败的探测请求；
- Job/attempt/进度/错误展示；
- 生产产物按 artifact type 精炼汇总，并复用 `/files` 生成全量 ZIP。

界面按钮不是权限边界；FastAPI 仍执行项目、文件、状态和 feature flag 校验。

## 7. 当前验证和未完成边界

新建工作流为十阶段 revision 4，历史流程保留原阶段快照。后端回归锁定冻结 Excel 校验、Excel 同规则预检与单 `.xlsx` 下载、严格执行体、拆板整批单次执行、当前 attempt 产物过滤、正式配对结果数量守恒、流式 ZIP、下载中断保留、确认后对象与预览缓存物理删除、迁移和 Excel 交接，以及 `excel_stage2` 未实现门禁和产物挂接；前端合同和真实浏览器验收锁定阶段完成后只解锁且不自动切换、第三阶段异步原生下载、40 张原图完整保留，以及正式包仅含 `原长/`、`余量增长后短文件/` 各 40 张 DXF。最终门禁和确切计数见[当前验证证据](../verification/current.md)。

仍未完成：

- 无法证明唯一余量伸长端的复杂腹板仍不猜测加工，保留明确原因和整批原图供线下处理；
- 未形成正式结果的图纸若经线下处理，当前没有重新上传并接回自动链路的产品流程；
- 深化设计 barrier 的机器完整性校验；
- CAM 规则分组和工作包生成；
- Windows Node Agent、租约、fencing token、SinoCAM Runner/Adapter；
- CAM 结果正式接纳算法与确定性交付清单；
- 多请求并发推进的行锁/version 控制；
- 获准真实生产图纸下的分类准确率业务验收。

因此本次交付已把服务器输入冻结、DXF 分类分流和默认关闭的 BH/BOX 拆板纵向切片接通，并保留人工复核后的产品流程与后续 CAM 边界；不是 SinoCAM 生产闭环或拆板业务验收已经完成的声明。
