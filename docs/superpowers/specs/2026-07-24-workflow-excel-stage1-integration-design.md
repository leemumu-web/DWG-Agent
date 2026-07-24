# 生产流程与 Excel 第一阶段接入设计

日期：2026-07-24  
状态：已确认，待实施  
范围：`/workflows`、`/workflows/:workflowId`、`/files/excel-final`、workflow 编排、Excel 输入契约与错误返回

## 1. 目标

本设计解决四个实际问题：

1. 现有 `/workflows` 同时承担列表、创建、输入上传、十阶段详情、文件选择和阶段执行，页面过重，操作员难以判断当前阶段和下一步。
2. 当前名为 `excel_final` 的整理表/part 流程实际是 Excel 处理第一阶段，业务名称和 workflow 阶段含义错误。
3. Workflow 通过全局文件下拉框选择 Excel，容易跨项目选错，且没有利用已经冻结的输入清单。
4. 非规范表格目前多被压缩成“流水线处理失败”，没有说明具体 sheet、行、字段、期望规范和人工操作。

完成后，排版员应能：

- 从批次总览进入一个独立生产控制台。
- 清楚看到九个生产阶段、当前阶段、输入、任务、产物和人工操作。
- 在上传时立即知道表格为什么不合规范。
- 在后台二次校验失败时看到同一种结构化错误。
- 对合法结果查看质量摘要和最终工作簿“处理报告”。

## 2. 已确认业务决定

### 2.1 Excel 阶段语义

当前生成“整理表”和“part”的处理流程正式定义为 `excel_stage1`，中文名称为“Excel 第一阶段处理”。

它不是最终合并阶段。现有 `excel_final` 代码、任务名和公共 API 前缀可暂作兼容技术标识，但不得继续在操作页面、workflow 阶段和操作说明中称为“最终合并”。

### 2.2 DXF→Excel 边界

DXF→Excel 从 `linux_production` 主流程移除。

以下能力保留：

- `/files/dxf2excel` 独立工具页面。
- 现有 DXF→Excel API、Job 和 Worker。
- 已生成文件和历史任务。

主流程不再读取 DXF 批次名，也不再把 DXF→Excel 产物作为 Excel 第一阶段输入。

### 2.3 Excel 第一阶段输入

Workflow 的 Excel 第一阶段只使用冻结输入清单中唯一的 `source_excel`。

- 浏览器不提交任意 `file_id`。
- 后端按 workflow、input batch、manifest 和 artifact 解析输入。
- 冻结后不得替换文件。
- 冻结前发现 Excel 非法时，操作员删除 invalid 输入项并上传修正版。

### 2.4 错误反馈

上传后立即预检，正式 Worker 再次校验。

两次校验使用同一 Stage 输入契约和同一错误代码，不允许网页与后台分别维护两套表头规则。

## 3. 生产流程定义

新的 `linux_production` 为九阶段顺序流程：

1. `source_intake`：文件接收、DWG→DXF 派生和输入冻结。
2. `dxf_classification`：DXF 分类与分流。
3. `drawing_processing`：图纸分类、拆板和人工回流。
4. `excel_stage1`：处理冻结的原始 Tekla Excel，生成整理表和 part。
5. `design_barrier`：人工确认图纸与 Excel 第一阶段结果满足后续条件。
6. `cam_packaging`：CAM 工作包生成。
7. `windows_cam`：Windows CAM 排版。
8. `result_acceptance`：CAM 结果接纳。
9. `delivery_archive`：交付与归档。

删除主模板中的：

- 执行类型为 `dxf_to_excel` 的旧 `excel_stage1` 能力。
- 重复的 `excel_final` 阶段。

新的 `excel_stage1` 能力：

```text
stage_code: excel_stage1
execution_mode: automated
execution_kind: excel_stage1
required_inputs: frozen_source_excel
artifact_types: stage1_excel
task_type: process_excel_final
```

现有数据库中只有一个 `waiting_input` workflow，旧 `excel_stage1` 仍为 pending，且没有 `stage1_excel` artifact。迁移应：

- 将 pending `excel_stage1` 改为新名称和能力语义。
- 删除同一 workflow 中尚未开始的旧 `excel_final` stage。
- 重新排列后续 sequence，保证 `_next_stage` 连续。
- 若发现旧 Excel 阶段已有 Job、Result 或 Artifact，迁移必须失败并提示人工处理，不得静默重解释历史产物。

新建 workflow 在 `config_json` 记录 `definition_revision=2`，用于审计，不为本次实现保留两套运行模板。

## 4. 页面架构

### 4.1 `/workflows`

职责仅限：

- 批次统计。
- 搜索、状态和阶段筛选。
- 分页列表。
- 新建并启动生产批次。
- 进入 `/workflows/:workflowId`。

列表页不得加载：

- 全局文件列表。
- 文件批次列表。
- Excel 文件选择项。
- 当前阶段产物选择项。
- workflow 详情中的全部阶段子查询。

### 4.2 `/workflows/:workflowId`

独立控制台由三部分组成：

1. `WorkflowStageRail`
   - 九阶段顺序、状态、进度和实现能力。
   - 明确区分 implemented、placeholder 和 external。
2. `WorkflowStageWorkspace`
   - 根据当前 `stage_code` 渲染对应面板。
   - 只展示当前工作所需操作。
3. `WorkflowEvidencePanel`
   - 输入 manifest、File、Job attempt、Result、Artifact、质量摘要。
   - 失败时显示受控结构化诊断。

阶段组件：

- `ProductionInputPanel`
- `DxfClassificationPanel`
- `DrawingProcessingPanel`
- `ExcelStage1Panel`
- 通用人工确认面板
- 通用 placeholder/external 面板

现有巨型详情 Drawer 和通用文件手选控制块删除。

### 4.3 `/files/excel-final`

公共地址保留，页面名称改为“Excel 第一阶段处理”。

页面采用 URL 标签并按需加载：

- `?view=process`：上传、即时预检、活动任务、具体错误、预览和下载。
- `?view=batches`：历史批次和质量摘要。
- `?view=parts`：跨批次零件查询。
- `?view=handbook`：按类别、规格和材质查询唯一五金手册。

默认只请求 process 所需资源。页首提示：

> 正式项目请从“生产流程”进入；本页面用于独立单文件处理、历史查询和专业工具。

五金手册查询不得继续只传 `spec`。前端必须提交：

- `category`
- `spec`
- D 系列所需的 `material`

这与现有后端严格查询契约保持一致。

## 5. 输入预检

### 5.1 Stage inspect

隔离 Stage 增加 `inspect` 操作。它执行：

- 输入扩展名和容器读取。
- 工作表数量检查。
- 标准表、初始表、分隔文本或定宽文本识别。
- 唯一表头检测。
- 必需列检查。
- 构件/零件关系读取。
- 逐行基础数值检查。
- 输出 source format、sheet、header row、part count、component count 和输入契约版本。

它不执行：

- 五金手册查询。
- 拆板重量计算。
- 工作簿生成。
- 业务数据库导入。

`run_auto_pipeline` 继续在正式任务中调用相同 Source Intake，形成二次校验。

扩展名由 Stage 结合文件容器判断：

- `.xlsx` / `.xlsm` 按 OOXML 工作簿检查，且只能有一个生产工作表。
- `.xls` 先识别 Tekla 文本导出与旧二进制 XLS，不能由 workflow 预先假定为其中一种。
- 能被当前 Source Intake 读取的 Tekla 文本继续处理。
- 不能读取的旧二进制 XLS 返回具体的不支持错误，并提示另存为单 sheet XLSX 或 Tekla 文本后重新上传。

Workflow 现有基于 openpyxl/xlrd 的浅层 Excel 检查由共享 inspect 替代，避免同一个文件在输入冻结和正式任务中得到不同结论。

### 5.2 独立页面上传

`POST /api/v1/excel-final/upload-and-process`：

1. 使用现有 durable transfer 保存文件并校验对象。
2. 调用 Stage inspect。
3. 预检失败：返回 415/422，不创建 Job。
4. 预检成功：创建或复用 Job，返回 202。

`POST /api/v1/excel-final/process?file_id=` 对已登记文件执行同样预检。

### 5.3 Workflow 输入

Workflow Excel 文件注册：

1. 读取 StoredFile 并核对大小、SHA 和扩展名。
2. 创建或复用 `WorkflowInputItem`。
3. 调用共享 Excel inspect interface。
4. 成功时保存 validation 摘要，状态为 `uploaded`。
5. 失败时保存 validation 错误，状态为 `invalid`，提交诊断后返回 422。
6. invalid 项阻止 freeze，允许冻结前删除和替换。

冻结时按 `validated_sha256` 和当前 contract version 再校验。冻结后创建 `source_excel` artifact 和不可变 manifest。

失败注册的事务顺序必须固定为：保存 invalid item 与审计记录、提交事务、再构造 422 error envelope。不得通过抛出异常回滚刚保存的诊断。重复登记同一 `file_id` 返回同一个 item 和同一校验结果。

## 6. 错误协议

### 6.1 Stage 子进程协议

Stage runner 增加严格错误输出：

```text
DWG_EXCEL_FINAL_ERROR=<bounded JSON>
```

预期输入错误通过 stdout 协议返回，父进程不得再从 traceback 或英文消息片段猜测错误类型。

父进程只接受一个合法 result 或一个合法 error。字段、长度、数量和枚举均受限。stderr 只用于内部诊断，不进入 Job、HTTP 或日志正文。

### 6.2 HTTP 与 Job 结构

错误结构：

```json
{
  "code": "EXCEL_INPUT_REQUIRED_COLUMNS_MISSING",
  "message": "表格缺少 Excel 第一阶段所需字段。",
  "retryable": false,
  "details": {
    "phase": "input_contract",
    "file_id": 381,
    "issues": [
      {
        "location": {
          "sheet": "原表",
          "row": 8,
          "column": null,
          "field": "零件号"
        },
        "actual": "未检测到",
        "expected": "构件编号、零件号、规格、长度、材质、数量",
        "action": "补回零件号列，保持一行一个零件后重新上传。"
      }
    ]
  }
}
```

HTTP 错误放入现有 `error` envelope；异步错误放入 Job `progress_data.failure`。workflow 同步时将 failure 快照放入阶段 `output_json.failure`。

保留 `error_code` 和 `error_message` 兼容字段。

### 6.3 输入错误代码

- `EXCEL_INPUT_EMPTY`
- `EXCEL_INPUT_UNSUPPORTED_EXTENSION`
- `EXCEL_INPUT_UNREADABLE`
- `EXCEL_INPUT_NO_WORKSHEET`
- `EXCEL_INPUT_MULTIPLE_WORKSHEETS`
- `EXCEL_INPUT_HEADER_NOT_FOUND`
- `EXCEL_INPUT_HEADER_AMBIGUOUS`
- `EXCEL_INPUT_DUPLICATE_COLUMNS`
- `EXCEL_INPUT_REQUIRED_COLUMNS_MISSING`
- `EXCEL_INPUT_COMPONENT_ONLY`
- `EXCEL_INPUT_SCHEMA_AMBIGUOUS`
- `EXCEL_INPUT_TEXT_ENCODING_UNSUPPORTED`
- `EXCEL_INPUT_BINARY_XLS_UNSUPPORTED`
- `EXCEL_INPUT_ROW_VALUE_INVALID`
- `EXCEL_INPUT_PART_WITHOUT_COMPONENT`
- `EXCEL_INPUT_OBJECT_CHANGED`

错误 issue 最多返回 20 条；sheet 名最多返回 10 个；单个显示值和操作建议限制长度。

### 6.4 运行错误代码

- `EXCEL_STAGE1_UNAVAILABLE`
- `EXCEL_STAGE1_HANDBOOK_UNAVAILABLE`
- `EXCEL_STAGE1_TIMEOUT`
- `EXCEL_STAGE1_OUTPUT_MISSING`
- `EXCEL_STAGE1_IMPORT_FAILED`
- `EXCEL_STAGE1_STORAGE_FAILED`
- `EXCEL_STAGE1_INTERNAL_ERROR`

超时、手册库暂不可用、存储瞬时失败可以重试。确定性输入错误、SHA 不一致和输出协议错误不可直接重试。

### 6.5 质量问题

合法输入中的业务质量问题不转成技术失败。

成功状态必须返回：

- `quality_status`
- `warning_count`
- `severe_warning_count`
- `category_counts`
- `representative_messages`

最终工作簿“处理报告”继续保存精炼人工操作项；无问题时 A2 为“无”。

## 7. 持久化与不可变性

`WorkflowInputItem` 增加：

- `validation_json`
- `validation_contract_version`
- `validated_sha256`

invalid Excel 项保存诊断，便于页面刷新后继续显示。冻结前可删除，冻结后不可变。

不新建文件表、任务表或 artifact 表。

Job 结构化错误使用现有 `progress_data`；WorkflowStage 使用现有 `output_json`，避免为 Excel 单独新建错误表。

## 8. API 调整

### 8.1 保留

- `/api/v1/excel-final/*`
- `/api/v1/workflows/*`
- `/api/v1/files/*`
- `/api/v1/jobs/*`
- `/files/excel-final`
- `/files/dxf2excel`

### 8.2 修改

- Excel upload/process 在创建 Job 前执行 inspect。
- process status 增加 `failure`。
- workflow input registration 返回持久化 validation。
- workflow detail 返回阶段 failure 快照。
- `excel_stage1` execution 从冻结 input batch 解析 Excel。

### 8.3 删除

`WorkflowStageExecutionCreate` 删除：

- `batch_name`
- `file_id`

DTO 设置 `extra="forbid"`，旧页面误传参数返回明确 422。

主流程页面删除：

- DXF batch selector。
- Excel global file selector。
- workflow detail 中的全局文件和批次查询。

## 9. 前端错误呈现

shared API 层新增结构化失败解析器：

- 解析 HTTP envelope、Job failure 和 Blob 错误。
- 普通 toast 只显示一句摘要。
- `ExcelInputFailurePanel` 完整展示 code、请求 ID、位置、实际、期望和人工动作。
- 不能把结构化 details 压成一个不可读字符串。

操作按钮按错误语义呈现：

- 非规范输入：删除/替换输入、下载原输入、查看输入规范。
- 瞬时服务错误：重试。
- 质量警告：查看报告、预览结果、下载结果、进入人工屏障。
- 不可变对象异常：新建输入批次或联系管理员，不显示无意义重试。

## 10. 性能与稳定性

- `/workflows` 只查询列表、项目和模板。
- workflow 详情以 workflow ID 为 query key。
- Excel 四标签按需加载。
- 只轮询 active Job。
- 预检使用同一 Stage inspect，不进行五金手册查询和写表。
- 大表按流式/只读方式读取，不使用逐单元格随机访问。
- 幂等键继续按用户和 operation 作用域化。
- 相同 workflow/stage 复用当前 Job，失败 retry 增加 attempt。
- artifact 继续绑定既有 File/Result ID，不重新上传结果字节。

## 11. 安全边界

公共错误不得包含：

- 服务器绝对路径。
- 临时目录。
- traceback。
- SQL、DSN、数据库主机或口令。
- 对象存储密钥。

错误 actual 值必须截断和转义。用户只能读取有项目/文件权限的输入、任务和结果。

## 12. 测试与验收

### 12.1 Stage

- 每种 InputContractError 对应稳定 code/details/action。
- inspect 和 process 使用同一输入契约。
- 错误协议字段和长度限制。
- 多 sheet、缺列、歧义表头、重复别名、构件-only、非法数值和文本编码。
- 错误不泄露路径或 traceback。

### 12.2 Backend

- 非规范 upload-and-process 返回 415/422 且不创建 Job。
- workflow invalid Excel 持久化诊断并阻止 freeze。
- valid Excel 冻结后自动解析为 `excel_stage1` 输入。
- execution 请求拒绝 `file_id/batch_name`。
- worker 二次校验返回相同 code。
- retryable 与 non-retryable 行为。
- workflow template 无 dxf_to_excel 和重复 excel_final。
- Job 成功后登记 Batch、Result、File、stage1_excel artifact。

### 12.3 Frontend

- `/workflows` 进入 `/workflows/:id`。
- 九阶段轨道与当前阶段工作区。
- invalid 输入展示 sheet/行/字段和人工操作。
- 确定性错误不显示重试。
- 质量警告显示报告与下载。
- Excel 四标签按需请求。
- 窄屏和键盘操作。

### 12.4 真实链路

必须完成：

1. B7 单 sheet 原表通过真实上传、预检、worker、MySQL 手册、内部导入、结果登记和下载。
2. 多 sheet GT 合集通过真实 HTTP 返回 `EXCEL_INPUT_MULTIPLE_WORKSHEETS`，列出 5 个 sheet。
3. Workflow 上传非法 Excel 时不能冻结。
4. Workflow 上传合法输入并冻结后，Excel 第一阶段无需文件下拉框即可完成。
5. workflow detail 展示 Job、Result、Artifact 和质量摘要。
6. 运行 Stage 全套、backend 全套、frontend typecheck/build/lint、分域 Playwright。
7. 在 `http://localhost:8080` 完成真实浏览器 E2E。

Mock 页面和孤立单元测试不能代替上述真实链路。

## 13. 文档与清理

同步更新：

- workflow README 和阶段能力说明。
- Excel processing README，将业务名称改为 Excel 第一阶段。
- 前端 workflows 与 excel-processing README。
- API 路由说明、错误码表和操作员输入规范。
- 数据库迁移说明和回滚条件。

确认无调用后删除旧 Drawer、通用文件选择状态、旧 workflow dxf_to_excel 合同测试和重复样式。不得删除独立 DXF→Excel 功能或 Excel 批次/零件/手册能力。
