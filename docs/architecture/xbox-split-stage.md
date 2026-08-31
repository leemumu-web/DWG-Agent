# XBOX 独立拆板 Stage（steel_dxf_split_xbox）

## 定位与链路

XBOX（封闭箱形焊接构件，`BOX5`/`HK` 两种方言）由**完全自包含**的独立 Stage 拆板，与 PL 在同一工作流阶段 `pl_xbox_split` 合并处理，位于 `dxf_classification` 之后、`drawing_processing`（BH/BOX 整批拆板）之前：

```text
source_intake → dxf_classification → pl_xbox_split → drawing_processing → excel_stage1 → ...
                                        │
                        PL 子执行（steel_dxf_split_pl，原长单产物）
                        XBOX 子执行（steel_dxf_split_xbox，原长+余量成对产物）
```

一次执行派发一个 Celery 任务（`app.workers.tasks_pl_dxf_split.split_pl_dxf`，队列 `dxf_split`）；该任务在同一 Job attempt 内串行调用两个 Stage 子进程，把两边通过独立校验的结果合并写入**一条** `DxfSplitRun`（items 带 `family="PL"`/`family="XBOX"`）。PL 与 XBOX 各自空批时静默跳过对应子执行；两族候选均为空时阶段被 `no_pl_xbox_candidates` 跳过。

## 自包含与隔离

- `Stages/steel_dxf_split_xbox` 不 import BH/BOX 的 `steel_dxf_split` 包；`box/`（封闭箱形几何核心）、四个顶层辅助模块与 `manufacturing_decision/` 是字节固定的 vendored 副本。
- `steel_dxf_split` 原包在本分支**零改动**；`split_classified_dxf` 的清单分发继续拒绝 XBOX，BH/BOX `drawing_processing` 行为不变。
- 三个分类候选读取器互斥：`list_split_candidate_inputs`（BH/BOX）、`list_pl_split_candidate_inputs`（PL）、`list_xbox_split_candidate_inputs`（XBOX）。

## 契约

| 项 | 值 |
|---|---|
| 源契约 | `project_tekla_xbox_dxf_v1`（`tekla_structures / single_part_drawing / welded_xbox`） |
| Stage 版本 | `steel-dxf-split-xbox` 0.1.0 |
| 报告 schema | `steel-dxf-split-xbox-report/1` |
| 后端 schema | `DWG-AGENT-PL-XBOX-SPLIT-{VALIDATION,MANIFEST,LEDGER}-1.0` |
| run 版本串 | `pl-0.2.0;xbox-0.1.0` |
| 产物命名 | `{member}_正常拆板.dxf` / `{member}_余量增长.dxf` + 两份报告 JSON |

## 成对产物规则

XBOX 正式结果**永远是成对的**：正常拆板 DXF + 焊接余量增长 DXF。余量档位（长度上界含）：

| 板长 mm | 纵向加量 |
|---|---|
| ≤2000 | +0 |
| ≤5000 | +5 |
| ≤10000 | +10 |
| ≤15000 | +15 |
| >15000 | +20 |

只对已证明的纵向可移动端加量；板宽不得改变。登记时 `normal_dxf_file_id` 与 `weld_allowance_dxf_file_id` 双真（与 PL 的 `weld=None` 恒定单产物规则对照）。批量导出时 XBOX 对并入 `split_result_normal`/`split_result_allowance` 两类；只请求原长类而存在 XBOX 对时返回 409 `DXF_SPLIT_EXPORT_XBOX_PAIR_REQUIRED`。

## 独立校验（保存后，后端不 import Stage 包）

逐图重开两份 DXF（ezdxf + audit、毫米单位、实体契约），并断言：

1. 正常版恰好 2 条闭合 4 顶点 LWPOLYLINE + 恰好 2 条 `p=<member>腹/翼` TEXT；
2. 板宽与规范化规格推导值（`H−2×tf`、`B−2×tw`）交叉核对（0.1mm 容差）；
3. 余量版宽度与正常版严格相等、长度增量精确命中档位表。

任一失败进入 `manual_review` 并计入 `failed_xbox` 选择导出类别。

## 发布认证

`release_evidence/xbox_release_attestation.json` 绑定三指纹：样本清单 SHA-256（`XBOX配对清单.json`，20 组配对，不入 Git）、验收门指纹（`tools/acceptance_gate.json`：校准 10 + 独立验收 10）、实现指纹（自有层 + vendored 层逐文件 SHA-256）。任何实现文件字节变化触发 drift 拒绝；重签必须重跑完整 20 组验收：

```bash
python -m steel_dxf_split_xbox.tools.acceptance_check \
    --corpus-root "<XBOX图纸根目录>" --work-root "<scratch>"
```

受保护镜像：构建时 `write_xbox_protected_runtime_manifest()` 在删除源码前冻结实现 payload；此后指纹从 `xbox_protected_runtime_manifest.json` 读取（与 BOX 机制同型）。
