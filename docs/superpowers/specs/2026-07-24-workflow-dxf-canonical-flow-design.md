# Workflow DXF Canonical Flow Design

**日期：** 2026-07-24  
**范围：** `/api/v1/workflows` 的 `linux_production` 输入接收、DWG→DXF、阶段产物和迁移  
**目标：** 操作员只提交一个 Excel 与一组 DWG；DWG 全部登记并转换后，图纸主链只允许 DXF，Excel、报告和清单继续使用各自业务格式。

## 1. 现状与问题

当前入口已经复用 `/files`、Jobs 和对象存储完成以下动作：

1. Excel 与每个 DWG 分别上传并建立 `files` 行；
2. workflow input batch 只引用既有 `file_id`；
3. 每个 DWG 绑定一个可重试、受 attempt 隔离的 `convert_dwg_to_dxf` Job；
4. 成功结果建立派生 DXF 的 `files` 与 `analysis_results` 行；
5. 冻结前重新校验源对象、Excel 规则、DXF 结构和同名配对；
6. 分类阶段只读取冻结条目的派生 DXF。

这条链路已有正确基础，但不能完整保证“入口后只流通 DXF”：

- 冻结时创建的 `DrawingVersion.file_id` 仍指向源 DWG，`Drawing.current_version_id` 因而把 DWG 暴露成当前工作图；
- artifact 名称 `source_file`、`derived_dxf`、`drawing_files`、`processed_drawing`、`cam_result`、`delivery_file` 没有稳定表达图纸格式；
- 留白和外部阶段只要求任意一个 artifact，未要求关键 DXF 与报告同时存在；
- artifact 绑定没有集中保证 Result/File 一致、Result 所属 Job 与 workflow 同项目；
- 自动阶段按 Job 成功推进，但没有统一检查该阶段的必需输出是否齐全；
- workflow 转换同步有一套简化 DXF sentinel 判断，与 Files 模块的权威 DXF 结构校验重复；
-部分当前文档仍保留“DXF/DWG 人工配对”“十阶段”“DXF→Excel 属于主流程”等过期描述。

## 2. 业务边界

“后续只流通 DXF”限定为图纸类数据：

- 原始 DWG 只存在于输入登记、源对象存储、转换 Job 参数和审计追溯中；
- 冻结后的当前工作图、分类输出、拆板输出、CAM 输入、CAM 输出、接纳输出与交付图纸都必须是 DXF；
- 输入 Excel、阶段 Excel、JSON/CSV 报告、人工复核记录和归档清单不是图纸，不强制改成 DXF；
- DXF→Excel 独立工具继续存在，但不重新进入 `linux_production` 主流程；
- 自动拆板、CAM、Windows Node Agent/SinoCAM 和接纳算法仍保持诚实的 placeholder/external 状态，本次只完善其输入输出合同，不伪造执行能力。

## 3. 权威数据流

### 3.1 输入登记

浏览器继续先调用 `/api/v1/files` 上传，再把返回的 `file_id` 登记到 workflow input batch。输入合同保持：

- 恰好一个 `.xls` 或 `.xlsx`；
- 至少一个 `.dwg`；
- 不允许人工 DXF；
- 所有对象先完成 Files 登记，workflow 不保存第二份字节；
- Excel 登记保留版本化检查快照，DWG 登记保留规范化 stem。

输入对象关系为：

```text
source_excel file ───────────────────────────────┐
                                                ├─ frozen input manifest
source_dwg file ─ convert_dwg_to_dxf Job ─ canonical_dxf file
```

### 3.2 DWG→DXF 与冻结

每个 `source_dwg` 只能接纳其绑定当前 attempt 的成功转换结果。同步必须核对：

- Job ID 与 attempt；
- Job 参数中的源 `file_id`；
- Result 类型与 Result 中的源 `file_id`；
- Result 的 `result_file_id`；
- 派生文件可用、扩展名为 `.dxf`；
- Files 模块权威 DXF 结构校验通过；
- 派生 DXF 的规范化 stem 与源 DWG 一致。

冻结仍在一个数据库事务中完成全部 Drawing、artifact、manifest 与阶段推进。每个输入图纸创建一个 Drawing，但其第一个且当前版本改为：

- `DrawingVersion.file_id = derived_dxf_file_id`；
- `DrawingVersion.source = "workflow_input_dxf"`。

源 DWG 不建立当前 DrawingVersion。它通过 `WorkflowInputItem.file_id`、`source_dwg` artifact、转换 Job 参数与不可变 manifest 保留完整追溯。

### 3.3 后续图纸链

图纸 artifact 使用以下单向合同：

```text
source_dwg
  └─ canonical_dxf
       └─ classified_dxf
            └─ processed_dxf
                 └─ cam_input_dxf
                      └─ cam_output_dxf
                           └─ accepted_dxf
                                └─ delivery_dxf
```

`source_dwg` 是唯一允许引用 DWG 的 artifact。其余图纸 artifact 必须引用可用 `.dxf` 文件。

## 4. 九阶段合同

| 阶段 | 必需输入 | 允许输出 | 完成必需输出 |
|---|---|---|---|
| `source_intake` | `dwg_files`, `excel_file` | `source_dwg`, `source_excel`, `canonical_dxf` | 三类均存在；DWG/DXF 数量与逐图配对由冻结用例保证 |
| `dxf_classification` | `canonical_dxf` | `classified_dxf`, `classification_report`, `classification_manifest` | 三类均存在 |
| `drawing_processing` | `classified_dxf` | `processed_dxf`, `validation_report` | 两类均存在 |
| `excel_stage1` | `source_excel` | `stage1_excel` | `stage1_excel` |
| `design_barrier` | `processed_dxf`, `stage1_excel` | `review_record` | `review_record` |
| `cam_packaging` | `processed_dxf`, `stage1_excel`, `review_record` | `cam_input_dxf`, `cam_package_manifest` | 两类均存在 |
| `windows_cam` | `cam_input_dxf`, `cam_package_manifest` | `cam_output_dxf`, `runner_diagnostics` | `cam_output_dxf`；诊断可选 |
| `result_acceptance` | `cam_output_dxf` | `accepted_dxf`, `acceptance_report` | 两类均存在 |
| `delivery_archive` | `accepted_dxf`, `stage1_excel`, `acceptance_report` | `delivery_dxf`, `delivery_excel`, `archive_manifest` | 三类均存在 |

模板公开增加 `required_outputs`，与 `artifact_types` 分离：

- `artifact_types` 表示该阶段允许绑定的输出类型；
- `required_outputs` 表示阶段进入成功状态前必须存在的类型；
- `required_inputs` 使用上游 artifact 名称，入口阶段的 `dwg_files`、`excel_file` 是唯一例外；
- 前端只展示服务器返回的合同，不自行推断。

## 5. 产物绑定和阶段推进不变量

### 5.1 artifact 绑定

`attach_artifact` 继续只保存既有 File/Result 引用，但增加以下领域校验：

1. artifact 类型必须属于目标阶段允许列表；
2.格式约束 artifact 必须提供 `file_id`；
3. DXF artifact 的 StoredFile 必须可用且 `file_ext == ".dxf"`；
4. Excel artifact 的 StoredFile 必须是支持的 Excel 扩展名；
5.同时提供 `result_id` 与 `file_id` 时，Result 的 `result_file_id` 必须等于该文件；
6. Result 所属 Job 的 `project_id` 必须等于 workflow 的 `project_id`；
7. 同一 workflow、阶段、类型、File、Result 的重复绑定继续幂等返回。

Files 上传已经对人工 DXF 执行结构检查。服务器生成 DXF 在对应 Stage 持久化前检查。人工或外部阶段完成前还要重新读取必需 DXF 对象，核对登记大小、SHA-256 和 DXF 结构，防止只有扩展名正确。

### 5.2 输入和输出门禁

- 自动阶段准备执行前，检查声明的上游 artifact 是否存在；
- manual、placeholder、external 阶段确认前，检查声明的上游 artifact；
- 任一阶段标记成功前，检查 `required_outputs`；
- 自动 Job 已成功但输出不完整时，阶段标记为失败，错误码为 `WORKFLOW_STAGE_OUTPUT_INCOMPLETE`，不得推进；
- 上游缺失时返回 `WORKFLOW_STAGE_INPUT_INCOMPLETE`；
- 文件格式不符时返回 `WORKFLOW_ARTIFACT_FORMAT_INVALID`；
- Result/File 不一致时返回 `WORKFLOW_ARTIFACT_RESULT_FILE_MISMATCH`；
- 跨项目 Result 返回 `WORKFLOW_ARTIFACT_PROJECT_MISMATCH`。

`source_intake` 仍只能由专用 freeze 用例完成，不能用通用 completion 绕过逐图数量、配对和清单检查。

## 6. 数据迁移

新增 definition revision 3 和一个 Alembic 数据迁移。升级动作：

1. 将所有 `linux_production` 的 `config_json.definition_revision` 更新为 `3`；
2. 将 `source_intake` 的 `source_file` 改为 `source_dwg`；
3. 将 `source_intake` 的 `derived_dxf` 改为 `canonical_dxf`；
4. 将 `drawing_processing` 的 `processed_drawing` 改为 `processed_dxf`；
5. 将 `windows_cam` 的 `cam_result` 改为 `cam_output_dxf`；
6. 将 `delivery_archive` 的 `delivery_file` 仅在其实际文件为 `.dxf` 时改为 `delivery_dxf`；
7. 对每个 `WorkflowInputItem.drawing_id`，把来源为 `workflow_input_dwg` 的初始 DrawingVersion 改指其 `derived_dxf_file_id`，并把 source 改为 `workflow_input_dxf`。

迁移不猜测以下矛盾历史：

- 冻结 DWG 条目没有派生 DXF；
- 初始 DrawingVersion 与 input item 对不上；
- 被命名为 DXF 的旧 artifact 实际引用非 DXF；
- 同一旧 generic artifact 无法确定新的唯一语义。

出现这些情况时升级明确失败并报告 workflow/artifact/item ID，由操作者先修复数据。降级执行可逆名称和 DrawingVersion 恢复，并把 definition revision 恢复为 2；revision 3 已产生且无法映射回旧 generic 合同的业务 artifact 会阻止降级。

当前只读实库核对结果为：2 个 `linux_production`、0 个 frozen DWG item、0 个 `workflow_input_dwg` DrawingVersion、0 个 workflow artifact，因此本次升级无需猜测现有生产数据。

## 7. 代码清理

本次清理严格限定于 workflow 主链：

- 删除模板、测试夹具和当前文档中的旧 generic drawing artifact 名称；
- workflow conversion 改用 Files 模块的 `validate_dxf_structure`，移除重复 sentinel 逻辑；
- 把阶段输入、输出和格式规则集中在 workflow 合同模块，避免 lifecycle、artifact 和前端各自硬编码；
- 不删除独立 `dxf_to_excel`、`dxf_to_dwg` 或余料库能力，它们仍是其他入口的有效功能；
- 不改动 Excel Final 的业务解析、重量、part 列或当前未提交工作。

## 8. 前端与文档

前端 workflow 类型增加 `required_outputs`，详情页对留白阶段同时展示：

- 上游必需输入；
- 允许输出；
- 完成必需输出；
- “图纸类产物必须为 DXF，DWG 只在输入阶段留档”的固定说明。

同步更新：

- workflow 后端/前端分区 README；
- `docs/architecture/workflow.md`；
- API 生成源及生成后的 `docs/reference/api.md`；
-数据库和当前实现状态中属于“当前事实”的段落；
-中英文总览中关于九阶段、Excel 第一阶段和 DXF-only 图纸主链的描述。

历史计划和历史验证快照不重写，但当前事实段必须明确旧输入文字已经失效。

## 9. 测试与验收

### 9.1 自动测试

按测试先行补充以下失败场景：

- 冻结后的 DrawingVersion 指向 DXF，源 DWG 只存在于输入账本与 `source_dwg`；
-模板中不存在 generic drawing artifact；
-转换 Result 的源 file 不匹配时拒绝配对；
- DXF artifact 绑定 Excel 文件时拒绝；
- Result/File 不一致、Result 跨项目时拒绝；
- 缺少上游输入时不能执行或确认；
-缺少必需输出时不能完成；
-成功 Job 缺少必需 artifact 时 workflow 失败而不推进；
- migration 升级、降级、矛盾历史阻断；
- API 与前端显示 revision 3 合同。

随后运行 workflow、files、jobs、CAD conversion、classification、architecture、migration、前端 workflow E2E、文档检查和 production build，再运行统一 full gate。

### 9.2 真实链路

在非破坏测试项目中使用获准真实文件：

1. 上传一个有效 Excel 和至少两个真实 DWG；
2.核对每个源文件的 `files`、input item、Job/attempt；
3.等待真实 ODA 转换完成；
4.逐个下载或读取派生对象，检查 DXF 结构、文件数和同名配对；
5.冻结输入；
6.核对 manifest、DrawingVersion、artifact 和删除保护；
7.启动分类并核对分类输入全部来自 canonical DXF；
8.确认数据库中入口后的图纸 artifact 没有 `.dwg`；
9.记录 Job、File、Drawing、workflow ID 与输出摘要；
10.清理测试项目时只使用受支持的可恢复/显式删除接口，不直接删除共享业务对象。

真实 ODA 与 Excel Stage 的成功才证明该链可用；fixture、worker healthy 或单独单元测试不能替代这项证据。

## 10. 不在本次范围

- 实现自动拆板算法；
- 实现 CAM 分组算法；
- 实现 Windows Node Agent、租约或 SinoCAM；
- 改造独立转换工具和余料库的人工 DWG/DXF 输入规则；
- 把 Excel、报告或清单错误地转换成 DXF；
- 复制 Files 字节、Job 状态或 AnalysisResult 数据到 workflow 新表。
