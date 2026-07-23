# Actionable Excel Final Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `处理报告`收缩为可执行的人工处置清单，无问题时显示“无”，并默认隐藏整理表和构件表的审计列。

**Architecture:** `quality.py`继续保存原始问题，但新增唯一的可操作报告投影，负责过滤信息、同源同类合并和生成操作建议；writer 与 `PipelineOutcome`共同使用该投影，避免统计口径分裂。后端 importer 识别“无”哨兵并继续兼容按表头读取的旧报告。

**Tech Stack:** Python、pytest、openpyxl、FastAPI/SQLAlchemy 后端。

---

## Task 1: 用失败测试锁定精炼报告投影

**Files:**
- Modify: `Stages/excel_final/tests/test_domain_quality.py`
- Modify: `Stages/excel_final/quality.py`

- [x] **Step 1: 添加过滤、合并和建议操作测试**

测试构造两个同来源、同类别但字段不同的严重问题，一个信息问题：

```python
rows = ledger.report_rows()
assert len(rows) == 1
assert rows[0] == {
    "级别": "严重",
    "类别": "关键字段缺失",
    "来源位置": "原表!8",
    "构件编号": "C1",
    "零件号": "P1",
    "涉及字段": "长度；材质",
    "说明": "长度缺失；材质缺失",
    "建议操作": "补齐涉及字段后重新处理",
}
```

并断言 `info_count == 0`、严重计数为合并后的 1。

- [x] **Step 2: 运行测试确认 RED**

```bash
cd Stages/excel_final
.venv/bin/pytest -q tests/test_domain_quality.py
```

Expected: 旧 `report_rows()`仍返回15列逐问题记录，因此失败。

- [x] **Step 3: 实现唯一可操作投影**

在 `quality.py` 中定义8列表头语义和类别到操作建议的映射。按以下键保持首次出现顺序分组：

```python
(
    issue.level,
    issue.category,
    issue.source_sheet,
    issue.source_row,
    issue.component_no,
    issue.part_no,
)
```

只处理 `WARNING/SEVERE/FATAL`，字段与说明去重后用中文分号连接。`QualityLedger.report_rows()`、警告/严重计数、类别统计和代表消息都使用合并后的结果；`quality_status`继续依据原始问题级别。

- [x] **Step 4: 运行测试确认 GREEN**

```bash
cd Stages/excel_final
.venv/bin/pytest -q tests/test_domain_quality.py
```

Expected: 全部通过。

## Task 2: 精简工作簿报告并隐藏审计列

**Files:**
- Modify: `Stages/excel_final/tests/test_writer_workbook.py`
- Modify: `Stages/excel_final/writer_parts.py`

- [x] **Step 1: 添加8列、空报告和隐藏列失败测试**

断言：

```python
assert REPORT_HEADERS == [
    "级别", "类别", "来源位置", "构件编号",
    "零件号", "涉及字段", "说明", "建议操作",
]
assert workbook["处理报告"]["A2"].value == "无"
for header in ("比重来源", "净材利用率", "重量核验"):
    index = ORGANIZED_HEADERS.index(header) + 1
    letter = get_column_letter(index)
    assert workbook["整理表"].column_dimensions[letter].hidden is True
for header in ("来源sheet", "行类型", "小计来源行"):
    index = COMPONENT_HEADERS.index(header) + 1
    letter = get_column_letter(index)
    assert workbook["构件表"].column_dimensions[letter].hidden is True
```

有问题时断言“建议操作”非空，同源同类问题只占一行。

- [x] **Step 2: 运行 writer 测试确认 RED**

```bash
cd Stages/excel_final
.venv/bin/pytest -q tests/test_writer_workbook.py
```

Expected: 旧报告仍有15列、空报告没有“无”、整理表列未隐藏。

- [x] **Step 3: 修改 writer**

`REPORT_HEADERS`改为8列，`_write_report_sheet`接收 `QualityLedger.report_rows()` 的字典。没有行时写：

```python
ws["A2"] = "无"
```

按报告行的“级别”设置警告/严重样式。写表后，把整理表的 `比重来源/净材利用率/重量核验` 和构件表的 `来源sheet/行类型/小计来源行` 对应列的 `hidden` 设为 `True`。

- [x] **Step 4: 运行 writer 与端到端测试确认 GREEN**

```bash
cd Stages/excel_final
.venv/bin/pytest -q tests/test_writer_workbook.py tests/test_pipeline_end_to_end.py
```

Expected: 全部通过。

## Task 3: 后端识别空报告哨兵并保持统计一致

**Files:**
- Modify: `backend/tests/excel_processing/test_excel_final_import.py`
- Modify: `backend/tests/excel_processing/test_excel_final_quality.py`
- Modify: `backend/app/modules/excel_processing/importers.py`

- [x] **Step 1: 添加空报告 importer 失败测试**

构造8列表头且 `A2=无` 的工作簿，断言：

```python
stats = import_quality_report(path)
assert stats == {
    "quality_status": "ok",
    "warning_count": 0,
    "severe_warning_count": 0,
    "report_summary": {
        "info_count": 0,
        "warning_count": 0,
        "severe_warning_count": 0,
        "category_counts": {},
        "representative_messages": [],
    },
}
```

- [x] **Step 2: 运行后端测试确认 RED**

```bash
cd backend
.venv/bin/pytest -q \
  tests/excel_processing/test_excel_final_import.py \
  tests/excel_processing/test_excel_final_quality.py
```

Expected: importer 将“无”识别为未知级别并失败。

- [x] **Step 3: 实现哨兵解析与更新 fixture**

当一行只有“级别”列为 `无`、类别和说明为空时跳过；若“无”与其他内容混用则拒绝。测试 fixture 改用8列报告，同时保留一个旧15列表头兼容用例。

- [x] **Step 4: 运行后端聚焦测试确认 GREEN**

运行 Step 2 命令，Expected: 全部通过。

## Task 4: 更新文档、真实产物与完整回归

**Files:**
- Modify: `Stages/excel_final/README.md`
- Modify: `Stages/excel_final/PROCESS.md`
- Modify: `backend/app/modules/excel_processing/README.md`
- Modify: `Stages/excel_final/tests/test_ground_truth_regression.py`
- Modify: `backend/tests/excel_processing/test_excel_final_live_flow.py`
- Regenerate: `Stages/excel_final/data/results/20260320-首都体育学院B7#地下部分-excel-final-规范结果.xlsx`
- Regenerate: `Stages/excel_final/data/reports/*.csv`
- Regenerate: `Stages/excel_final/data/reports/*.md`

- [x] **Step 1: 更新真实回归合同**

真实 GT 与后端下载测试断言：

```python
assert workbook["处理报告"]["A2"].value == "无"
assert workbook["处理报告"].max_row == 2
for header in ("比重来源", "净材利用率", "重量核验"):
    assert hidden(header)
for header in ("来源sheet", "行类型", "小计来源行"):
    assert component_hidden(header)
```

- [x] **Step 2: 更新当前生产文档**

说明报告仅含可操作问题、8列合并规则、空报告“无”哨兵，以及整理表三个审计列默认隐藏。

- [x] **Step 3: 重生成真实结果和对比报告**

使用后端 `.venv` 和 `source_format="canonical"` 调用真实 MySQL Stage；确认质量状态为 `ok`，报告 `A2=无`，478行 part 文件列为空，重量总数不变。

- [x] **Step 4: 执行完整回归**

```bash
Stages/excel_final/.venv/bin/pytest -q -m "not handbook_mysql and not live_data" \
  Stages/excel_final/tests Stages/excel_final/multi_split/tests
cd backend
.venv/bin/pytest -q -m handbook_mysql ../Stages/excel_final/tests/test_handbook_mysql.py
.venv/bin/pytest -q -m live_data ../Stages/excel_final/tests/test_ground_truth_regression.py
DWG_RUN_LIVE_EXCEL_FINAL=1 .venv/bin/pytest -q -s \
  tests/excel_processing/test_excel_final_live_flow.py
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/alembic check
.venv/bin/python ../scripts/docs/check.py
```

Expected: 所有启用测试通过，无迁移漂移。

- [x] **Step 5: 提交**

```bash
git add -u
git commit -m "refactor(excel-final): emit actionable reports"
```
