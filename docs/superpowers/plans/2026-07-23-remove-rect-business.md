# Remove Excel Final RECT Business Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完整删除 Excel Final 的 RECT 计算、标记和质量提示，同时保留 `part` 表“文件”列并使所有数据行固定为空。

**Architecture:** 将 part 准入从 `RectDecision` 解耦：候选构造函数只接收明确的身份有效性，并继续独立应用严重重量异常和拆板有效性限制。`PartCandidate`/`PartRow` 不再承载文件业务值，writer 只为兼容表结构输出一个空单元格。

**Tech Stack:** Python 3.12/3.13、pytest、openpyxl、FastAPI 后端适配器、MySQL 五金手册。

---

### Task 1: 用失败测试锁定“文件列固定为空”

**Files:**
- Modify: `Stages/excel_final/tests/test_part_builder.py`
- Modify: `Stages/excel_final/tests/test_writer_workbook.py`
- Modify: `Stages/excel_final/tests/test_pipeline_end_to_end.py`

- [ ] **Step 1: 修改候选构造测试，要求 API 不再接收 RECT 决策**

在 `test_part_builder.py` 中让父件和拆板候选使用以下调用：

```python
candidate = candidate_from_parent(
    parent,
    cut_length=parent.source.length,
    identity_consistent=True,
)
assert not hasattr(candidate, "file_value")
```

拆板候选同样传 `identity_consistent=True`，并验证身份不一致时 `excluded is True`。

- [ ] **Step 2: 修改 writer 测试，要求 PartRow 无 file_value 且 K 列为空**

```python
PartRow(
    import_component_no="C1",
    import_part_no="P1",
    spec=Decimal("10"),
    width=Decimal("200"),
    cut_length=Decimal("1000"),
    material="Q355B",
    summary=Decimal("2"),
    team="",
    graphic="",
    part_type="板材",
)
assert workbook["part"]["K2"].value is None
```

- [ ] **Step 3: 修改端到端测试，要求全部文件单元格为空且无 RECT 报告**

```python
file_values = [
    row[10]
    for row in workbook["part"].iter_rows(min_row=2, values_only=True)
]
assert file_values
assert all(value is None for value in file_values)
assert not any(
    "RECT" in str(row[1] or "")
    for row in workbook["处理报告"].iter_rows(min_row=2, values_only=True)
)
```

- [ ] **Step 4: 运行测试并确认 RED**

Run:

```bash
Stages/excel_final/.venv/bin/pytest -q \
  Stages/excel_final/tests/test_part_builder.py \
  Stages/excel_final/tests/test_writer_workbook.py \
  Stages/excel_final/tests/test_pipeline_end_to_end.py
```

Expected: 因候选函数仍要求 `RectDecision`、`PartRow` 仍要求 `file_value` 或输出仍含 `RECT` 而失败。

### Task 2: 删除 RECT 领域模型并保留独立 part 准入

**Files:**
- Modify: `Stages/excel_final/part_builder.py`
- Modify: `Stages/excel_final/canonical_pipeline.py`
- Modify: `Stages/excel_final/writer_parts.py`
- Delete: `Stages/excel_final/tests/test_rect.py`

- [ ] **Step 1: 删除 RECT 模型和推断函数**

从 `part_builder.py` 删除：

```python
RectDecision
_rect_issue
_decision
infer_plate_rect
infer_split_rect
```

同时删除只为这些函数服务的面积和重量舍入导入。

- [ ] **Step 2: 从 part 数据模型删除 file_value**

`PartCandidate` 和 `PartRow` 不再声明 `file_value`。候选构造函数改为：

```python
def candidate_from_parent(
    parent: ParentPartEvidence,
    *,
    cut_length: Decimal,
    identity_consistent: bool,
    team: str = "",
) -> PartCandidate:
    ...
    excluded=(
        not identity_consistent
        or parent.weight_validation_status == "severe_warning"
    )
```

`candidate_from_split` 使用同样的身份和严重重量准入规则。

- [ ] **Step 3: 从规范管线移除 RECT 调用**

`canonical_pipeline.py` 不再导入或调用 `infer_plate_rect` / `infer_split_rect`，不再追加 RECT issues。候选调用直接传：

```python
candidate_from_parent(
    evidence,
    cut_length=source.length,
    identity_consistent=identity_consistent,
)
```

拆板候选使用同一 `identity_consistent`。

- [ ] **Step 4: writer 固定写空文件列**

`_write_part_sheet` 的最后一个值改为：

```python
item.part_type, None,
```

- [ ] **Step 5: 删除 RECT 专属测试并运行 GREEN**

删除 `Stages/excel_final/tests/test_rect.py`，然后运行 Task 1 的聚焦命令。

Expected: 全部通过。

- [ ] **Step 6: 提交领域删除**

```bash
git add Stages/excel_final
git commit -m "refactor(excel-final): remove RECT business"
```

### Task 3: 更新真实 GT、对比工具和文档合同

**Files:**
- Modify: `Stages/excel_final/tests/test_ground_truth_regression.py`
- Modify: `Stages/excel_final/tools/compare_ground_truth.py`
- Modify: `Stages/excel_final/README.md`
- Modify: `Stages/excel_final/PROCESS.md`
- Modify: `backend/app/modules/excel_processing/README.md`
- Modify: `backend/tests/excel_processing/test_excel_final_adapter.py`
- Modify: `backend/tests/excel_processing/test_excel_final_quality.py`

- [ ] **Step 1: 先修改真实 GT 测试并确认 RED**

将真实样本期望改为：

```python
assert result.report_summary["info_count"] == 0
assert all(row["文件"] is None for row in part)
assert not any(
    "RECT" in str(row["类别"])
    for row in _rows_by_headers(values["处理报告"])
)
```

Run:

```bash
cd backend
.venv/bin/pytest -q -m live_data ../Stages/excel_final/tests/test_ground_truth_regression.py
```

Expected: 旧实现仍产生 RECT 信息，因此失败。

- [ ] **Step 2: 删除对比工具的 RECT 规则**

从 `compare_ground_truth.py` 删除“普通板RECT严格证明”和“BOX子板RECT”两项，新增：

```python
_add_result(
    results,
    "part文件列固定留空",
    "GT混用RECT标记",
    f"空={blank_files}/{len(part)}",
    blank_files == len(part),
    "文件列不再承载RECT业务",
)
```

质量报告结论改为不再期望 198 条 RECT 信息。

- [ ] **Step 3: 清除后端测试 fixture 中的 RECT 示例**

将仅用于通用质量摘要测试的 `RECT未证明` 替换为中性的 `数据备注`，保持 warning/severe 聚合测试含义不变。

- [ ] **Step 4: 更新 README 和 PROCESS**

文档明确：

- `part_builder.py` 只负责候选准入、身份冲突与汇总。
- “文件”列是兼容保留列，当前固定为空。
- 不存在 RECT 推断、标记和质量提示。
- 当前真实样本处理报告为 0 条。

- [ ] **Step 5: 运行 GT 与文档相关测试**

```bash
cd backend
.venv/bin/pytest -q -m live_data ../Stages/excel_final/tests/test_ground_truth_regression.py
.venv/bin/pytest -q \
  tests/excel_processing/test_excel_final_adapter.py \
  tests/excel_processing/test_excel_final_quality.py
.venv/bin/python ../scripts/docs/check.py
```

Expected: 全部通过。

### Task 4: 重生成产物并执行完整回归

**Files:**
- Regenerate: `Stages/excel_final/data/results/20260320-首都体育学院B7#地下部分-excel-final-规范结果.xlsx`
- Regenerate: `Stages/excel_final/data/reports/20260320-首都体育学院B7#地下部分-excel-final-规范结果-ground-truth-comparison.csv`
- Regenerate: `Stages/excel_final/data/reports/20260320-首都体育学院B7#地下部分-excel-final-规范结果-ground-truth-comparison.md`

- [ ] **Step 1: 用真实 MySQL 重生成规范结果**

从 `backend` 环境调用 `run_excel_final_pipeline(..., source_format="canonical")`，输入预处理单表，覆盖 `data/results` 中的规范结果。

Expected:

```text
quality_status=ok
warning_count=0
severe_warning_count=0
info_count=0
```

- [ ] **Step 2: 重生成 GT 对比报告**

```bash
backend/.venv/bin/python Stages/excel_final/tools/compare_ground_truth.py \
  --source 'Stages/excel_final/data/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3).xlsx' \
  --preprocessed 'Stages/excel_final/data/preprocessed/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3)_原表.xlsx' \
  --output 'Stages/excel_final/data/results/20260320-首都体育学院B7#地下部分-excel-final-规范结果.xlsx' \
  --report-dir 'Stages/excel_final/data/reports'
```

Expected: 规则 0 FAIL，“文件列固定留空”PASS。

- [ ] **Step 3: 执行 Stage、真实手册、后端完整回归**

```bash
Stages/excel_final/.venv/bin/pytest -q -m "not handbook_mysql and not live_data" \
  Stages/excel_final/tests Stages/excel_final/multi_split/tests
cd backend
.venv/bin/pytest -q -m handbook_mysql ../Stages/excel_final/tests/test_handbook_mysql.py
DWG_RUN_LIVE_EXCEL_FINAL=1 .venv/bin/pytest -q -s \
  tests/excel_processing/test_excel_final_live_flow.py
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/alembic check
```

Expected: 所有启用测试通过，迁移无漂移。

- [ ] **Step 4: 验证源码无 RECT 业务残留**

```bash
rg -n "RectDecision|infer_plate_rect|infer_split_rect|RECT未证明|RECT证据冲突" \
  Stages/excel_final backend/app/modules/excel_processing
```

Expected: 无生产代码命中。

- [ ] **Step 5: 提交最终更新**

```bash
git add -u
git commit -m "test(excel-final): lock blank file column"
```

