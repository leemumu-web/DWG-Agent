# 生产输入批次上传、转换与冻结设计

**日期：** 2026-07-19  
**范围：** `linux_production` 的 `source_intake` 阶段  
**业务输入：** 多个 DWG + 恰好一个 Excel  
**服务器派生：** 每个 DWG 通过现有 ODA 管线生成一个 DXF

## 1. 已确认边界

本阶段只完成文件接收、真实格式校验、服务器 DWG→DXF、配对诊断、输入清单冻结和内部 `drawing_id` 建立。后续人工拆板、DXF→Excel、Excel Final、CAM 和结果接纳不在本次实现范围内。

人工端只允许提交：

- 一个或多个真实 DWG；
- 恰好一个可读的 `.xls` 或 `.xlsx`。

人工不能向生产输入批次登记 DXF。所有 DXF 必须由服务器现有 `convert_dwg_to_dxf` Job 生成，并由 `AnalysisResult.result_file_id` 证明来源。

## 2. 方案比较与选择

### 方案 A：只使用 `workflow.config_json`

改动小，但无法可靠表达文件级上传、转换失败、补传、唯一 Excel、冻结版本和并发更新。JSON 也不能建立必要唯一约束。

### 方案 B：前端组合 `/files`、`/jobs/batches` 和 workflow artifact

复用率高，但浏览器成为状态机；刷新或部分请求失败后容易出现“文件已上传但批次不知道”“Job 已创建但未关联”等人工难以判断的状态。

### 方案 C：持久输入批次账本，复用现有执行与存储（采用）

新增工作流范围的输入批次及条目模型，只保存业务状态与已有 File/Job/Result/Drawing 引用。文件字节、对象写入、SHA-256、下载、ODA 转换和 attempt fencing 继续由现有实现负责。该方案增加一组模型和 API，但能在服务端建立防误操作不变量并支持断点恢复。

## 3. 数据模型

### 3.1 `workflow_input_batches`

每个 `linux_production` 工作流只能有一个输入批次：

- `id`：公开 `batch_id`；
- `workflow_run_id`：唯一外键；
- `project_id`、`created_by`：权限和审计域；
- `status`：`uploading`、`needs_attention`、`converting`、`ready_to_freeze`、`frozen`；
- `version`：本轮固定为 1；
- `manifest_sha256`、`frozen_at`：冻结事实；
- `error_code`、`error_message`：批次级最近异常。

冻结后不允许增加、删除或替换文件，不提供“解冻”捷径。需要修改时由未来的显式新版本功能处理，避免操作员误改已经投产的输入。

### 3.2 `workflow_input_items`

每行表示一个人工源文件：

- `role`：`source_dwg` 或 `source_excel`；
- `file_id`：引用已有 `files`；
- `original_name`、`normalized_stem`：保留原名并用于配对；
- `status`：`uploaded`、`converting`、`converted`、`conversion_failed`、`paired`、`frozen`；
- DWG 专用 `conversion_job_id`、`derived_dxf_file_id`、`drawing_id`；
- `error_code`、`error_message`：文件级可操作反馈。

约束：同一批次不能重复登记同一 File；只能有一个 `source_excel`；冻结时规范化 DWG stem 必须唯一。派生 DXF 不伪装成人工输入条目，而通过 DWG 条目的 `derived_dxf_file_id` 保存血缘。

## 4. 名称规范化与配对

规范化只影响比较，不改写原始文件名：

1. 去除目录和扩展名；
2. Unicode NFKC；
3. 去除首尾空白；
4. 连续空白折叠为单个空格；
5. 使用 `casefold()` 比较。

服务器转换产物沿用现有行为，把 `A.dwg` 生成 `A.dxf`。配对检查要求：

- 每个源 DWG 有且只有一个成功 Job 的当前 attempt；
- Job 有成功 AnalysisResult 和未删除的 `.dxf` File；
- 派生 DXF 的规范化 stem 等于源 DWG；
- 不存在两个源 DWG 归一化为同一 stem。

异常必须返回条目 ID、原始名称、错误码和中文修复建议，不能只返回“批次不完整”。

## 5. 真实格式与完整性校验

### DWG

上传仍调用现有 `/files`：扩展名/MIME 白名单、最小大小、`AC1012`–`AC1032` 文件头、大小、SHA-256、对象写入和 Transfer saga 均复用。登记输入批次时再次读取对象并核对登记大小/SHA，防止旧对象缺失或损坏。

### Excel

只接受 `.xls`/`.xlsx`。登记时从现有 storage 流式读取并核对大小/SHA：

- `.xlsx` 使用 `openpyxl` 只读打开；
- `.xls` 使用现有 `xlrd` 打开；
- 必须至少有一个可见工作表；
- 空文件、伪扩展名、损坏文件和加密/不可读文件拒绝进入批次。

本阶段只证明 Excel 容器可读，不提前执行后续零件字段业务校验。

## 6. API

所有写接口要求项目 `project_owner` 或 `project_engineer`，读接口要求项目成员。

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/workflows/{workflow_id}/input-batch` | 幂等创建并返回 `batch_id` |
| GET | `/workflows/{workflow_id}/input-batch` | 同步 Job/Result 后返回批次、条目、问题和冻结条件 |
| POST | `/workflows/{workflow_id}/input-batch/files` | 登记一个已由 `/files` 上传的 DWG/Excel |
| DELETE | `/workflows/{workflow_id}/input-batch/files/{item_id}` | 冻结前移除错误条目；必要时取消活动转换 Job |
| POST | `/workflows/{workflow_id}/input-batch/conversion-requests` | 为所有尚未成功的 DWG 批量创建/重试现有转换 Job |
| POST | `/workflows/{workflow_id}/input-batch/freeze` | 锁定校验、创建 Drawing/Version、计算清单哈希并完成 `source_intake` |

文件上传不复制新实现：前端先以稳定 `Idempotency-Key` 调用现有 `POST /files?batch_name=workflow-input-{batch_id}`，成功后把 `file_id` 登记到输入批次。若第二步失败，界面明确显示“已存入文件中心、尚未加入生产批次”，并允许只重试登记，避免重复上传对象。

### 6.1 转换请求

服务器复用现有 `create_conversion_jobs` 和 `dispatch_committed_conversion_batch`。只选择本批次的 `source_dwg`，创建 `convert_dwg_to_dxf` Job，并把 Job ID/attempt 绑定到对应条目。重复请求：

- queued/running/succeeded Job 只复用；
- failed/cancelled Job 使用既有 retry 状态机递增 attempt；
- 不为同一 DWG 产生第二条并行 Job。

### 6.2 冻结事务

冻结在同一数据库事务中重新检查全部条件，而不信任前端缓存：

1. 至少一个 DWG；
2. 恰好一个可读 Excel；
3. 没有重复 file_id 或规范化 stem；
4. 每个 DWG 的当前 attempt 成功且派生 DXF 可读、同名；
5. 批次尚未冻结。

随后按 DWG 创建一个 `Drawing`，`drawing_no` 使用原始 stem，建立 DWG `DrawingVersion(source=input_dwg)`；DXF 通过工作流 artifact/条目血缘关联，不把派生文件冒充用户版本。清单按稳定字段排序后进行 canonical JSON SHA-256，写入 `manifest_sha256`。最后把全部条目标为 frozen、挂接 source artifacts，并完成 workflow `source_intake`。

冻结接口使用行锁和幂等终态：相同批次重复调用返回原 manifest；并发第二个请求不能创建重复 Drawing。

## 7. 前端防误操作设计

工作流抽屉中的 `source_intake` 改为四步工业批次向导：

1. **建立批次**：展示批次 ID、所属项目和不可混入 DXF 的规则；
2. **上传源文件**：两个明确入口“上传 DWG（可多选）”“上传 Excel（仅一个）”，接受范围由 input 限制但不作为安全边界；
3. **服务器转换与检查**：文件表逐项显示上传、校验、Job attempt、转换进度、DXF 配对和具体错误；
4. **确认并冻结**：显示 DWG 数、Excel 1/1、DXF 配对数、重复/缺失计数和即将创建的 Drawing 数。

防误操作规则：

- 选择 DXF 或其他类型时在浏览器立即拒绝，后端仍重复校验；
- 已有 Excel 时禁用第二次 Excel 上传，替换必须先明确移除；
- 上传、登记或转换期间禁用冻结；
- 冻结按钮只有所有条件通过时可用；
- 点击冻结弹出清单摘要，要求再次确认“冻结后不可修改”；
- 失败行保留原文件名、阶段、错误码和“重试登记/重试转换/移除”单一建议动作；
- 页面刷新后完全从服务端批次恢复，不依赖浏览器内存。

视觉继续使用现有 Ant Design 工业控制台，不引入独立设计系统。用清晰的阶段轨道、紧凑状态表和高对比异常区取代装饰性卡片堆叠；所有状态变化使用 `aria-live`，按钮有明确禁用原因。

## 8. 失败与恢复

- 部分上传失败：批次保持 `uploading`/`needs_attention`，成功文件不回滚，可补传；
- 文件已上传但登记失败：不重复写对象，只重试 file_id 登记；
- 某个转换失败：其他成功结果保留，只重试失败 Job attempt；
- 对象缺失或摘要不符：冻结被拒绝并指明文件；
- 浏览器断开：Job 在服务器继续，GET/SSE 恢复状态；
- freeze 事务失败：不产生半冻结批次或重复 Drawing；
- 已冻结写操作：统一返回 `INPUT_BATCH_FROZEN`。

## 9. 测试与发布门禁

后端测试覆盖模型约束、权限、DWG/Excel 真实格式、对象 size/SHA、唯一 Excel、禁止人工 DXF、同名冲突、部分上传、批量转换、attempt 重试、Result 血缘、配对、冻结幂等/并发及 Drawing 建立。

前端合同和 Playwright 覆盖多选 DWG、单 Excel、错误类型拒绝、逐文件反馈、部分失败恢复、转换进度、冻结禁用原因、确认弹窗和刷新恢复。

发布门禁沿用全量 backend、四个 Stage、frontend build、Playwright、docs-check、Alembic migration/check、空 schema migration、infra 和 Compose config。真实发布探针必须使用多个有效 DWG 和一个有效 Excel，确认 ODA 生成同数量 DXF、下载摘要匹配、manifest 稳定且 Drawing 数等于 DWG 数。

## 10. 明确不实现

- 人工上传 DXF；
- 从 Excel 推断应有 DWG 数量或零件业务字段；
- 自动拆板或人工拆板回流；
- 输入解冻/修订版本；
- Windows/SinoCAM 执行。
