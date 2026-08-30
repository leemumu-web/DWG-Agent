# PL / XBOX 拆板阶段接口与 XBOX 交接

> 更新日期：2026-08-30
> 当前状态：PL 已实现并接入生产工作流；XBOX 仅保留分类、类型和接口位，本版本不执行 XBOX 拆板。

## 1. 当前生产链路

```text
source_intake
  → dxf_classification
  → pl_xbox_split       # 当前只拆 PL
  → drawing_processing  # 原 BH/BOX 拆板保持不变
  → excel_stage1
  → ...
```

- 阶段 code 固定为 `pl_xbox_split`，位于分类之后、原 BH/BOX 拆板之前。
- 通用执行端点：`POST /api/v1/workflows/{id}/stages/pl_xbox_split/executions`，请求中的 `execution_kind` 为 `pl_xbox_split`。
- 分类候选由 `list_pl_split_candidate_inputs` 单独读取，只接收已明确分类且可进入下一阶段的 `PL`；原 `list_split_candidate_inputs` 仍只接收 `BH/BOX`。
- XBOX 不会被送入 PL、BOX 或 BH 拆板器；没有 PL 候选时，本阶段以 `no_pl_candidates` 跳过。

## 2. PL 后端实现

### 2.1 独立 Stage

- 目录：`Stages/steel_dxf_split_pl/`
- 包：`steel_dxf_split_pl==0.2.0`
- CLI：`steel-dxf-split-pl`
- 源契约：`project_tekla_pl_dxf_v1`
- 依赖：`ezdxf==1.4.4`、`Shapely>=2.1,<3`

该 Stage 自己拥有 PL 源图解析、几何展开、制造轮廓生成、零件号标注、批次报告和命令行入口，不导入原 BH/BOX 拆板实现。

### 2.2 后端适配与独立校验

- 进程适配：`backend/app/modules/dxf_splitting/pl_adapter.py`
- 作业编排：`backend/app/modules/dxf_splitting/pl_execution.py`
- 保存后独立校验：`backend/app/modules/dxf_splitting/pl_validation.py`
- PL 选择性导出：`backend/app/modules/dxf_splitting/pl_selective_exports.py`

PL Stage 产出的 DXF 会由后端重新打开并独立验证单位、实体层、闭合材料轮廓、唯一 `p=<零件号>` 标签、宽度、长度和 0.1 mm 向上取整规则。只有独立校验通过的图才登记为正式结果；其余图进入安全拒绝账本。

### 2.3 PL 产物合同

- 每张通过图只登记一个 `normal_dxf_file_id`。
- `weld_allowance_dxf_file_id` 对 PL 永远为 `null`。
- 不生成 PL 余量版，不修改原 BH/BOX 的成对产物规则。
- 运行账本按 `pl_xbox_split` 当前 `job_id + job_attempt` 精确读取，旧 attempt 不可通过当前接口导出。
- 产物类型：`processed_dxf / split_report / validation_report / split_ledger / split_manifest`。

## 3. 已实现接口

### 3.1 读取运行

`GET /api/v1/workflows/{id}/pl-xbox-split`

返回 `PlXboxSplitRun | null`。字段与前端 `frontend/src/features/workflows/workflow.ts` 中的 `PlXboxSplitRun`、`PlXboxSplitItem` 一致：

- 运行：`id / workflow_run_id / status / splitter_version / cli_schema / validation_schema`
- 计数：`input_count / processed_count / failed_count / auto_accepted_count / manual_review_count`
- 分类投影：`classification_input_count / classification_only_count / classification_only_type_counts`
- 合同与账本：`source_contracts / split_ledger_file / split_manifest_file`
- 世代：`job`，其中 attempt 必须与工作流当前阶段一致
- 逐图：`items[]`；当前正式项目的 `family` 只会是 `PL`

### 3.2 PL 选择性导出

- `GET /api/v1/workflows/{id}/pl-xbox-split/runs/{runId}/selective-export-preview`
- `POST /api/v1/workflows/{id}/pl-xbox-split/runs/{runId}/selective-exports`
- 下载地址使用 POST 返回的 `download_url`

类别：

| key | 当前行为 |
|---|---|
| `failed_pl` | 导出本 attempt 未形成正式结果的 PL 分类源 DXF |
| `failed_xbox` | 稳定接口预留，当前固定为 0 |
| `other` | 防御性兜底；正常 PL 流程应为 0 |

下载凭据与 `workflow_id / run_id / export_uid / categories / actor_user_id` 绑定，ZIP 直接流式传输，不在服务器暂存压缩包。

### 3.3 通用批次导出

PL 面板复用 `POST /api/v1/workflows/{id}/batch-exports`：

- PL 正式结果只请求 `split_result_normal`。
- 原 BH/BOX 面板仍同时请求 `split_result_normal + split_result_allowance`。
- 当两个类别同时请求时，后端继续强制校验 BH/BOX 成对完整；附带的 PL 原长文件不要求余量配对。
- 只请求 `split_result_normal` 时，仅导出当前 PL attempt 的正式原长结果，不混入 BH/BOX 单边文件。

## 4. 前端当前行为

- 页面仍显示统一阶段名“PL / XBOX 拆板与独立校验”，便于后续 XBOX 在同一位置接入。
- 执行按钮、进度、成功提示和成品下载明确显示当前只执行 PL。
- 页面明确说明 XBOX 仅保留分类与接口位。
- PL 成品下载只请求原长类别，不请求余量类别。
- `failed_xbox` 会显示为“未通过的 XBOX（预留）”，当前不可选择。

## 5. XBOX 后续接入边界

下一个实现者应保持现有 PL、BH、BOX 几何代码不变，沿以下接缝增加 XBOX：

1. 新建独立 XBOX Stage，例如 `Stages/steel_dxf_split_xbox/`，不要把 XBOX 塞进现有 BOX 拆板核心。
2. 在分类接口新增 XBOX 专用候选读取器；不要扩大 PL 读取器或原 BH/BOX 读取器的白名单。
3. 新增 XBOX 源契约和独立进程适配器，例如 `project_tekla_xbox_dxf_v1`。
4. 明确 XBOX 是单产物还是成对产物，再为它实现保存后独立校验；不要照搬 PL 的“仅原长”假设。
5. 在 `pl_xbox_split` 阶段编排中合并 PL 与 XBOX 两个独立执行结果，但仍按当前 `job_id + attempt` 形成一个前端投影。
6. XBOX 逐图账本使用 `family="XBOX"` 和自己的 `source_contract_id`；失败原图进入 `failed_xbox`。
7. 将前端 `IMPLEMENTED_FAMILIES` 从 `['PL']` 扩为 `['PL', 'XBOX']`，并更新当前“XBOX 预留”的提示。
8. 补充 XBOX 的独立 Stage 测试、保存后验证测试、工作流世代测试、选择性导出测试和完整回归。

以下契约应保持稳定：`stage_code=pl_xbox_split`、现有三个 `/pl-xbox-split` 路由族、`PlXboxSplitRun`/`PlXboxSplitItem` 字段、PL 源契约、PL 单产物规则、原 BH/BOX `drawing_processing` 行为。
