# 图纸拆板完整生产闭环设计

## 目标

将 `linux_production` 的第三步“图纸拆板与独立校验”从已接入但默认关闭的运行时切片，完善为可在真实生产工作流中执行、跟踪、复核、下载和向下游交接的完整功能。

本设计复用当前 Steel DXF Split 1.5.2、Files、Jobs、Celery、MinIO、工作流阶段、分类结果和现有前端工作台。DWG 仅存在于第一步输入与转档；从分类开始至拆板、Excel、CAM 和归档，图纸数据只流转 DXF。

## 基线保护

PR #7 基于当前生产流程主线增量接入，未覆盖以下既有能力；实现和验收必须继续锁定这些合同：

- 每次新建生产批次实质上新建一个项目及其唯一完整工作流。
- Excel 与 DWG 分步上传；Excel 只能是一个 `.xls` 或 `.xlsx` 文件。
- DWG 只能以文件夹上传；文件夹存在其他文件时须由用户确认，服务器只接收 DWG。
- 生产下载统一为 ZIP，不提供生产文件的单文件下载入口。
- 路线阶段可点击查看，但只有服务器权威当前阶段允许写操作。
- 分类结果保留按类型文件夹浏览、分页明细、分类 ZIP 和全部分类 DXF ZIP。
- 第三步必须复用当前分类 attempt 的正式、可进入下一步的 DXF，不能读取历史或临时结果。

`2ca73f1` 是 PR 基线 `785829f` 的祖先，因此上述功能在提交历史上没有被 PR 替换。后续改动只扩展拆板闭环。

## 生产数据流

1. 输入冻结阶段将 DWG 入库并生成配对 DXF。
2. DXF 分类阶段生成当前正式分类 attempt，并标记哪些分类 DXF 可进入拆板。
3. 拆板阶段启动前重新读取工作流，验证：
   - 当前阶段是 `drawing_processing`；
   - 分类 Job、attempt、项目和输入清单与工作流登记完全一致；
   - 分类运行已结束且没有未解决的分类复核项；
   - 输入文件全部为已登记、可读取的 DXF。
4. 后端创建不可变拆板 run，Job 投递到 `dxf_split` 队列。
5. Worker 按图执行 Steel DXF Split 1.5.2。CLI 每完成一张图就原子更新进度 sidecar，平台适配器读取后持续写入批次已处理数量和 Job 进度；速度与预计剩余时间由真实处理数量和耗时计算。
6. 每张图进入以下结果之一：
   - `auto_accepted`：自动拆板和独立校验通过；
   - `review_required`：已生成候选结果，但必须人工决定；
   - `failed`：没有形成可采用结果，必须转人工处理。
7. 所有待处理项完成逐图复核后，后端生成最终交接清单。
8. 只有自动通过或人工明确采用的最终 DXF 进入 Excel/CAM；转人工处理的图不会静默进入自动下游。

## 后端领域模型

### 拆板批次

复用 `DxfSplitRun`，补充项目级进度投影：

- `processed_count`
- `failed_count`
- `reviewed_count`
- `elapsed_seconds`
- `throughput_per_minute`
- `estimated_remaining_seconds`

速度和 ETA 从真实已处理数量及 Worker 时间计算，不写模拟值。批次终态包括：

- `completed`
- `completed_with_review`
- `failed`

### 拆板明细与人工决定

复用 `DxfSplitItem` 保存每张图的自动执行结果。新增独立的人工决定记录，避免覆盖机器原始判断：

- `split_item_id`
- `decision`：`accept_candidate` 或 `manual_processing`
- `final_normal_dxf_file_id`：采用候选时绑定正式正常拆板 DXF；转人工处理时为空
- `final_weld_allowance_dxf_file_id`：采用候选时绑定正式余量增长 DXF；转人工处理时为空
- `comment`：必填
- `decided_by`
- `decided_at`
- `version`

每个 item 只允许存在一个当前有效决定。更新使用行锁和版本校验，重复提交保持幂等；历史批次和非当前 attempt 禁止修改。

独立校验失败但仍形成结构可读的成对 DXF 时，候选文件单独入库，不登记为正式工作流产物。人工可通过候选复核 ZIP 核对原图、候选 DXF、报告和失败诊断，并以显式决定覆盖自动校验结论。只有候选文件成对存在时才能选择 `accept_candidate`；没有候选结果的失败项只能选择 `manual_processing`。

### 阶段完成条件

- 无人工项时，拆板 Job 成功后自动完成阶段。
- 有人工项时，工作流进入 `waiting_review`。
- 所有人工项均已有决定后，才能提交批次复核结论。
- `accept_candidate` 项进入最终交接清单。
- 只要存在 `manual_processing` 项，阶段继续保持 `waiting_review` 并明确显示需要线下补图；它不会自动进入 Excel/CAM，也不能伪装为全部完成。
- 全部待处理项均为 `accept_candidate` 时，复核完成接口才完成阶段并生成最终交接清单。

本轮不实现任意 DXF 单文件回传。未来若要补录人工制作 DXF，应作为受审核的文件夹上传扩展，不破坏“生产上传以文件夹为单位”的规则。

## HTTP 接口

保留：

- `GET /api/v1/workflows/{workflow_id}/drawing-processing`
- `GET /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive`
- 通用阶段执行和阶段 ZIP 接口

扩展：

- `GET /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/review-items`
  - 分页返回待处理项和已有决定。
- `PUT /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/review-items/{item_id}/decision`
  - 登记或幂等更新当前决定。
- `POST /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/review-completion`
  - 校验所有待处理项均有决定且不存在 `manual_processing` 阻断项，再同步工作流阶段。
- `GET /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/results-archive`
  - 下载当前 run 的全部正式拆板结果 ZIP。
- `GET /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/review-candidates-archive`
  - 下载当前 run 的人工复核材料 ZIP；只包含待处理原图、候选 DXF、候选报告和诊断 manifest，不把候选提前登记为正式产物。

所有写接口必须同时校验项目成员权限、写角色、服务器当前阶段、当前 Job attempt 和 run 归属。所有下载均生成 ZIP，并记录审计事件。

## ZIP 合同

拆板阶段提供四种压缩包：

- 全部拆板结果 ZIP：包含正式 normal DXF、余量 DXF、拆板报告、验证报告、台账和 manifest。
- 待复核原图 ZIP：只包含当前 run 中待处理项进入拆板前的分类 DXF。
- 通用阶段结果 ZIP：沿用现有阶段产物归档，内容与工作流 artifact 登记一致。

人工候选复核 ZIP 是复核材料而非生产结果。ZIP 中按 item 分目录包含原图、存在的候选 DXF、候选报告和诊断 manifest；没有候选的 item 只包含原图和诊断。

归档路径必须稳定、去重并包含 manifest。任何上述文件通过 Files 单文件下载或任意单文件 ZIP 绕过时，继续返回 `WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED`。

## 前端交互

现有十阶段工作台、阶段导航和分类页面保持不变。第三步使用工业生产控制台式布局：

### 未开始

- 展示当前输入图纸总数与分类清单摘要。
- “开始整批拆板”只在权威当前阶段可用。
- 点击前再次请求工作流详情进行阶段校验。

### 运行中

- 项目总进度条。
- 已完成/总数。
- 自动完成、待人工、失败数量。
- 实时速度，单位为张/分钟。
- 预计剩余时间。
- 当前 Job、attempt 和 Worker 状态。
- 两秒轮询真实后端状态；终态后停止轮询。

### 已完成

- 默认只显示汇总，不罗列所有成功文件。
- 提供“下载全部拆板结果 ZIP”和“下载本阶段结果 ZIP”。
- 可展开查看分页明细。

### 待人工处理

- 保留“下载待处理原图 ZIP”。
- 提供“下载候选复核材料 ZIP”，用于核对成对候选 DXF 与失败诊断。
- 显示紧凑异常清单，可按失败类型筛选。
- 点击一项打开复核抽屉，展示原图名称、类型、诊断、候选结果是否存在。
- 选择“采用自动拆板结果”或“转人工处理”，填写说明后提交。
- 页面显示已复核/待复核计数；未全部处理时完成按钮禁用。

历史与未来阶段只能查看。任何提交期间发现服务器阶段或 attempt 已变化，立即停止请求并提示刷新。

## 错误处理

- 功能未启用：返回 `DXF_SPLIT_PIPELINE_DISABLED`，页面显示部署状态，不生成假任务。
- 分类结果不完整或过期：返回明确的分类 run/attempt 错误。
- Worker 失败：保留已完成 item 和当前 attempt 诊断，允许按既有重试预算重试。
- 复核并发冲突：返回版本冲突，前端刷新对应 item。
- 归档没有成员：返回明确的 409，不生成空 ZIP。
- 历史批次写入：返回 404 或 409，不允许修改。

## 启用策略

完成迁移、自动测试、真实 FastAPI/Celery/MySQL/MinIO 批次和浏览器验收后，将 `DXF_SPLIT_PIPELINE_ENABLED` 在本地生产配置中启用。示例配置保持安全默认值，避免新部署未经验收自动开启。

## 验收标准

- 既有上传、阶段导航、分类分流和 ZIP-only 合同全部回归通过。
- 当前正式分类 DXF 能创建一个真实拆板 Job 并由 `worker-dxf-split` 执行。
- 项目级进度、速度和 ETA 来自真实数据，页面没有模拟值。
- 自动通过批次可正常完成并向 Excel 交接。
- 待人工批次可逐图登记决定，未处理完整时不能完成。
- 历史 run、非当前阶段和错误 attempt 的写请求均被拒绝。
- 全部结果、待复核原图和阶段产物均可作为 ZIP 下载。
- 生产文件不能通过单文件接口下载。
- 后端聚焦测试、工作流回归、架构合同、前端构建和浏览器 E2E 全部通过。
- 数据库迁移、服务状态和线上 `:8080` 工作流验收通过后才启用生产开关。

## 非目标

- 不修改 Steel DXF Split 1.5.2 的 BH/BOX 几何算法。
- 允许扩展 CLI 的平台进度 sidecar 合同，但不改变单图几何算法、判定或产物内容。
- 不让非 BH/BOX 类型绕过人工复核。
- 不新增生产单文件上传或下载。
- 不在本轮实现人工制作 DXF 的回传。
- 不修改 Excel 第二阶段、CAM 或归档阶段的占位能力。
