# PL / XBOX 拆板前端契约（后端交接）

> 本文档是**前端**为新增的 `pl_xbox_split` 拆板阶段定义的行为与接口契约，供后端按此实现匹配接口。
> 前端只完成了 UI 与 API 客户端；`pl_xbox_split` 阶段在 workflow 模板与后端端点落地之前不会渲染/工作。
> 对应前端代码：`frontend/src/features/workflows/PlXboxDrawingProcessingPanel.tsx` 及其导出组件、类型（`workflow.ts` 中 `PlXbox*`）、API（`workflows.api.ts` 中 `*PlXbox*`）。

## 1. 现有 BH/BOX 拆板实现摘要（作为参照）

端到端链路（`linux_production` 工作流）：

```
source_intake → dxf_classification → drawing_processing → excel_stage1 → ...
                      │                    │
        classified_dxf（分族目录）       BH/BOX 整批拆板
```

- 阶段 `drawing_processing`：前端 `DrawingProcessingPanel` 读取 `GET /api/v1/workflows/{id}/drawing-processing`（返回 `DxfSplitRun`），执行走通用 `POST /api/v1/workflows/{id}/stages/drawing_processing/executions`（`execution_kind=drawing_processing`）。
- 后端执行：`backend/app/modules/dxf_splitting/execution.py::run_dxf_splitting` → 从分类账取 `list_split_candidate_inputs`（只放行 `part_type in {"BH","BOX"}`）→ 写分类清单 `STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0` → 调 Stage CLI `steel-dxf-split` → 要求产出 `BH拆板信息表.xlsx` → 产物 `processed_dxf / weld_allowance_dxf / split_report / weld_allowance_report / validation_report / bh_split_ledger / split_manifest`。
- 分批导出：`POST /api/v1/workflows/{id}/batch-exports`（类别 `classified_dxf / processed_dxf / source_excel / stage1_excel / split_result_normal / split_result_allowance`），下载走 `download_url`；物理清理需二次确认。
- 选择性导出：`GET /api/v1/workflows/{id}/drawing-processing/runs/{runId}/selective-export-preview`、`POST .../selective-exports`，类别 `failed_bh / failed_box / pl / other`（PL 已被单独归为 `pl` 桶）。

## 2. 后端需扩展的白名单接入点（实现 PL/XBOX 时对齐）

现状把拆板硬编码为 BH/BOX，PL/XBOX 只进"分类保留"。要实现 PL/XBOX 拆板，后端须在以下位置扩展（均来自现状代码调研）：

1. `backend/app/modules/dxf_classification/persistence.py::list_split_candidate_inputs`
   - 当前：`item.part_type not in {"BH", "BOX"}` 跳过。
   - 扩展：允许 `{"PL", "XBOX"}` 进入 PL/XBOX 拆板候选（或新增并列 loader）。
2. `backend/app/modules/dxf_splitting/adapter.py`
   - `SUPPORTED_PART_TYPES = frozenset({"BH", "BOX"})` 与 `source_contract_for(part_type)`：为 PL/XBOX 增加源契约（如 `project_tekla_pl_dxf_v1` / `project_tekla_xbox_dxf_v1`）。
   - `invoke_splitter` 的 CLI 授权参数需为新契约增加 `--authorize-...` 标志。
3. `backend/app/modules/dxf_splitting/execution.py::_write_classification_manifest`
   - 当前：`family not in {"BH","BOX"}` 抛 `DxfSplitError`。
   - 扩展：PL/XBOX 阶段允许 `family in {"PL","XBOX"}`。
4. `Stages/steel_dxf_split_v1.5.2/.../cli.py` 与 `pipeline.split_classified_dxf`
   - 当前：清单 family 白名单 `{"BH","BOX"}`、分发只认 BH/BOX。
   - 扩展：PL/XBOX 拆板核心（新的 Stage 或扩展）。
5. `backend/app/modules/dxf_splitting/persistence.py::record_split_item`
   - `type_resolution` / `source_contract_id` 逻辑：为 PL/XBOX 设置 `classifier_confirmed`（分类 PL/XBOX 与拆板 family 一致）与对应源契约。

## 3. 新接口契约（前端已按此实现，后端必须对齐）

### 3.1 阶段与执行

- 阶段 code：`pl_xbox_split`（位于 `dxf_classification` 之后、`drawing_processing` 之前）。
- 执行：复用通用 `POST /api/v1/workflows/{id}/stages/pl_xbox_split/executions`，`execution_kind="pl_xbox_split"`。
- 返回 `{ workflow, job, reused, retried }`（与现有阶段一致）。

### 3.2 读取运行

- `GET /api/v1/workflows/{id}/pl-xbox-split` → `PlXboxSplitRun | null`
- 响应字段（`frontend/src/features/workflows/workflow.ts` 中 `PlXboxSplitRun`）：

```
id, workflow_run_id
status: 'running' | 'completed' | 'completed_with_review' | 'failed'
splitter_version, cli_schema?, validation_schema?
input_manifest_sha256
input_count, processed_count, failed_count, reviewed_count, elapsed_seconds
throughput_per_minute?, estimated_remaining_seconds?
auto_accepted_count, manual_review_count
classifier_confirmed_count, splitter_detected_count, unresolved_count
classification_input_count, classification_only_count, classification_only_type_counts
source_contracts
split_ledger_file?（对应 BH 的 bh_split_ledger_file）
split_manifest_file?
job
items: PlXboxSplitItem[]
error_code?, error_message?, started_at?, finished_at?, created_at, updated_at
```

- `PlXboxSplitItem`：

```
id, drawing_id?, classification_item_id, source_file_id, source_name
classification_disposition, classification_part_type?
type_resolution: 'classifier_confirmed' | 'splitter_detected' | 'unresolved'
part_type, profile_normalized?, family?: 'PL' | 'XBOX' | null
source_contract_id?, automation_route: 'auto_accepted' | 'manual_review'
disposition, normal_dxf_file_id?, weld_allowance_dxf_file_id?
diagnostics, validation
```

### 3.3 选择性导出（PL/XBOX 专用）

- `GET /api/v1/workflows/{id}/pl-xbox-split/runs/{runId}/selective-export-preview` → `PlXboxSelectiveExportPreview`（类别 `failed_pl / failed_xbox / other`，含 `file_count / size_bytes / available`）。
- `POST /api/v1/workflows/{id}/pl-xbox-split/runs/{runId}/selective-exports`，body `{ categories: PlXboxSelectiveExportCategory[] }` → `PlXboxSelectiveExport`（含 `download_url / token_expires_at / file_count / source_size_bytes / filename`）。
- 下载：前端直接请求返回的 `download_url`（带认证 Blob 下载）。

### 3.4 分批导出与生产文件

- **后端无需新端点**：前端 PL/XBOX 面板复用通用 batch-export（`POST /api/v1/workflows/{id}/batch-exports`），类别 `split_result_normal / split_result_allowance / classified_dxf`，与 BH/BOX 面板一致。
- 后端需保证 `pl_xbox_split` 的产物进入同一批导出账本（按 stage 的 job/attempt 关联）。

## 4. 前端行为说明

- `PlXboxDrawingProcessingPanel` 在 `WorkflowDetailPage` 中按 `stage_code === 'pl_xbox_split'` 渲染；后端模板未定义该阶段前不渲染（"待接"）。
- 面板按工程族分别展示：PL（板件）与 XBOX（箱型）的正式配对数量分开统计（`familyAcceptedCounts`），逐图账本按 `family` 打 `PL`/`XBOX` 标签，不混并。
- 世代过滤：`WorkflowDetailPage` 对 `pl_xbox_split` 与 `drawing_processing` 一样按 `job_id + job_attempt` 过滤产物。
- 就绪/执行/汇总/下载/账本状态与 BH/BOX 面板一致（镜像 `DrawingProcessingPanel`）。
