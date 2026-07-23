# Excel Final Part Component Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 BH/BOX/BT 子板在 `part` 中保留构件号，其他可下料零件清空构件号并按完整属性跨构件汇总。

**Architecture:** 范围判定、冲突检测、分组和排序全部收敛到 `part_builder.py`；上游继续提供构件级候选，writer 只写固定 11 列。后端继续从 `整理表`导入构件级数据，仅在下载工作簿中验证新的 122 行 `part` 投影。

**Tech Stack:** Python 3.12、dataclasses、Decimal、pytest、openpyxl、FastAPI/SQLAlchemy 后端测试、真实 MySQL 五金手册。

---

## Task 1: 按类型实现构件范围与全局范围分组

**Files:**
- Modify: `Stages/excel_final/tests/test_part_builder.py`
- Modify: `Stages/excel_final/part_builder.py`

- [x] **Step 1: 添加构件范围和全局范围失败测试**

在 `test_part_builder.py` 中把旧的“不跨构件”测试拆成以下合同：

```python
def test_component_scoped_types_keep_component_and_never_cross_components() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, import_component_no="C1", part_type="BOX腹"),
        _candidate(builder, import_component_no="C2", part_type="BOX腹"),
    ])

    assert len(result.rows) == 2
    assert {row.import_component_no for row in result.rows} == {"C1", "C2"}
    assert {row.summary for row in result.rows} == {Decimal("6")}


def test_global_types_clear_component_and_merge_across_components() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, source_row=8, import_component_no="C1"),
        _candidate(builder, source_row=9, import_component_no="C2"),
    ])

    assert result.issues == ()
    assert len(result.rows) == 1
    assert result.rows[0].import_component_no == ""
    assert result.rows[0].summary == Decimal("12")


def test_global_same_part_number_with_different_attributes_stays_separate() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, import_component_no="C1", width=Decimal("100")),
        _candidate(builder, import_component_no="C2", width=Decimal("101")),
    ])

    assert result.issues == ()
    assert len(result.rows) == 2
    assert {row.width for row in result.rows} == {Decimal("100"), Decimal("101")}
    assert {row.import_component_no for row in result.rows} == {""}


def test_global_grouping_keeps_team_boundary() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, import_component_no="C1", team="A"),
        _candidate(builder, import_component_no="C2", team="B"),
    ])

    assert len(result.rows) == 2
    assert {row.team for row in result.rows} == {"A", "B"}
```

保留并调整投影测试：只有 `BH腹/BH翼/BOX腹/BOX翼/BT腹/BT翼` 要求非空构件号，板材和扁钢要求空构件号。

把现有主身份冲突测试的候选类型显式改为 `BOX腹`：

```python
def test_same_component_and_part_id_with_conflicting_geometry_is_severe_and_excluded() -> None:
    builder = _builder()
    candidates = [
        _candidate(
            builder,
            source_row=8,
            width=Decimal("100"),
            part_type="BOX腹",
        ),
        _candidate(
            builder,
            source_row=9,
            width=Decimal("101"),
            part_type="BOX腹",
        ),
        _candidate(
            builder,
            source_row=10,
            import_part_no="safe",
            part_type="BOX腹",
        ),
    ]

    result = builder.build_part_rows(candidates)

    assert [row.import_part_no for row in result.rows] == ["safe"]
    assert len(result.issues) == 1
    assert result.issues[0].category == "导入零件身份冲突"
```

- [x] **Step 2: 运行单元测试确认 RED**

```bash
cd Stages/excel_final
.venv/bin/pytest -q tests/test_part_builder.py
```

Expected: 全局类型仍按构件分组、构件号非空，新增测试失败。

- [x] **Step 3: 实现显式范围类型和分组策略**

在 `part_builder.py` 中增加：

```python
COMPONENT_SCOPED_TYPES = frozenset({
    "BH腹", "BH翼", "BOX腹", "BOX翼", "BT腹", "BT翼",
})
GLOBAL_SCOPED_TYPES = frozenset({"扁钢", "板材"})


def _is_component_scoped(part_type: str) -> bool:
    return part_type in COMPONENT_SCOPED_TYPES
```

构件身份冲突只检查 `COMPONENT_SCOPED_TYPES`：

```python
for candidate in source_candidates:
    if _is_component_scoped(candidate.part_type):
        by_identity.setdefault(
            (candidate.import_component_no, candidate.import_part_no),
            [],
        ).append(candidate)
```

分组时选择输出构件号：

```python
output_component_no = (
    candidate.import_component_no
    if _is_component_scoped(candidate.part_type)
    else ""
)
key = (
    output_component_no,
    candidate.import_part_no,
    candidate.spec,
    candidate.width,
    candidate.cut_length,
    candidate.material,
    candidate.part_type,
    candidate.team,
)
```

构造 `PartRow` 时写入 `output_component_no`。贡献仍为：

```python
contribution = candidate.child_quantity * candidate.component_quantity
```

排序改为两个区段：

```python
def row_scope(row: PartRow) -> int:
    return 0 if _is_component_scoped(row.part_type) else 1

rows = sorted(
    grouped.values(),
    key=lambda row: (
        row_scope(row),
        component_order.get(row.import_component_no, len(component_order)),
        TYPE_PRIORITY[row.part_type],
        row.import_part_no,
        _sort_value(row.spec),
        _sort_value(row.width),
        _sort_value(row.cut_length),
        row.material,
    ),
)
```

- [x] **Step 4: 运行单元测试确认 GREEN**

```bash
cd Stages/excel_final
.venv/bin/pytest -q tests/test_part_builder.py
```

Expected: 全部通过。

- [x] **Step 5: 提交算法与单元测试**

```bash
git add Stages/excel_final/part_builder.py Stages/excel_final/tests/test_part_builder.py
git commit -m "refactor(excel-final): scope part aggregation by type"
```

## Task 2: 固化真实 GT 的 122 行合同

**Files:**
- Modify: `Stages/excel_final/tests/fixtures/ground_truth_baseline.json`
- Modify: `Stages/excel_final/tests/test_ground_truth_regression.py`
- Modify: `Stages/excel_final/tools/compare_ground_truth.py`
- Modify: `Stages/excel_final/tests/test_pipeline_end_to_end.py`

- [x] **Step 1: 添加真实样本失败断言**

在 baseline 中加入：

```json
{
  "part_rows": 122,
  "part_component_scoped": 84,
  "part_global_scoped": 38,
  "part_global_summary": 1216
}
```

在 `test_ground_truth_regression.py` 中对 `part` 增加：

```python
component_types = {"BH腹", "BH翼", "BOX腹", "BOX翼", "BT腹", "BT翼"}
component_scoped = [row for row in part if row["类型"] in component_types]
global_scoped = [row for row in part if row["类型"] not in component_types]

assert len(part) == baseline["part_rows"]
assert len(component_scoped) == baseline["part_component_scoped"]
assert len(global_scoped) == baseline["part_global_scoped"]
assert all(row["导入构件编号"] for row in component_scoped)
assert all(row["导入构件编号"] is None for row in global_scoped)
assert sum(row["汇总"] for row in global_scoped) == baseline["part_global_summary"]
```

在端到端测试中新增两个构件、相同板材候选，断言只输出一条构件号为空、汇总为两构件贡献之和的 `part` 行，同时 BOX 子板继续保留构件号。

- [x] **Step 2: 更新 GT 对比工具合同**

在 `compare_ground_truth.py` 中定义：

```python
COMPONENT_SCOPED_TYPES = frozenset({
    "BH腹", "BH翼", "BOX腹", "BOX翼", "BT腹", "BT翼",
})


def _normalized_part_type(value: object) -> str:
    return "板材" if value is None else str(value).replace("BOX盖", "BOX翼")


def _part_signature(row: dict[object, object]) -> tuple[object, ...]:
    return (
        row.get("导入零件号"),
        row.get("规格"),
        row.get("宽度"),
        row.get("下料长度"),
        row.get("材质"),
        _normalized_part_type(row.get("类型")),
    )
```

将旧的 478 行和“构件号全部完整”判断替换为：

- 规范结果总数为 baseline 的 122；
- 84 条构件范围记录构件号非空；
- 38 条全局记录构件号为空；
- 84 条构件范围记录与 GT 在构件号、完整签名和汇总上完全一致；
- GT 117 条普通板材去掉班组后折叠为同一组 38 个签名；
- 使用 GT `整理` 的 `总数`对账后，38 个全局汇总全部一致且合计 1216；
- GT `part`四条漏乘构件数继续标记为不成熟 GT 已知差异，而不是规范结果失败。

- [x] **Step 3: 运行离线聚焦测试**

```bash
cd Stages/excel_final
.venv/bin/pytest -q \
  tests/test_part_builder.py \
  tests/test_pipeline_end_to_end.py \
  -m "not handbook_mysql and not live_data"
```

Expected: 全部通过。

- [x] **Step 4: 提交真实合同和对比逻辑**

```bash
git add \
  Stages/excel_final/tests/fixtures/ground_truth_baseline.json \
  Stages/excel_final/tests/test_ground_truth_regression.py \
  Stages/excel_final/tests/test_pipeline_end_to_end.py \
  Stages/excel_final/tools/compare_ground_truth.py
git commit -m "test(excel-final): lock scoped part projection"
```

## Task 3: 保持后端构件级目录并验证下载结果

**Files:**
- Modify: `backend/tests/excel_processing/test_excel_final_live_flow.py`

- [x] **Step 1: 更新后端真实链路断言**

保留：

```python
assert plate_catalog.json()["pagination"]["total"] == 394
```

将下载工作簿断言改为：

```python
part_rows = [
    dict(zip(
        [cell.value for cell in workbook["part"][1]],
        values,
        strict=True,
    ))
    for values in workbook["part"].iter_rows(min_row=2, values_only=True)
]
component_types = {"BH腹", "BH翼", "BOX腹", "BOX翼", "BT腹", "BT翼"}
component_scoped = [row for row in part_rows if row["类型"] in component_types]
global_scoped = [row for row in part_rows if row["类型"] not in component_types]

assert len(part_rows) == 122
assert len(component_scoped) == 84
assert len(global_scoped) == 38
assert all(row["导入构件编号"] for row in component_scoped)
assert all(row["导入构件编号"] is None for row in global_scoped)
assert sum(row["汇总"] for row in global_scoped) == 1216
assert all(row["文件"] is None for row in part_rows)
```

不修改 `backend/app/modules/excel_processing/importers.py`，因为数据库仍从 `整理表`导入。

- [x] **Step 2: 运行后端聚焦非实时测试**

```bash
cd backend
.venv/bin/ruff check \
  tests/excel_processing/test_excel_final_live_flow.py \
  app/modules/excel_processing
.venv/bin/pytest -q \
  tests/excel_processing/test_excel_final_import.py \
  tests/excel_processing/test_excel_final_quality.py
```

Expected: Ruff 和测试全部通过，旧格式缺失构件身份仍被跳过。

- [x] **Step 3: 提交后端下载合同**

```bash
git add backend/tests/excel_processing/test_excel_final_live_flow.py
git commit -m "test(excel-final): verify scoped part download"
```

## Task 4: 更新生产文档

**Files:**
- Modify: `Stages/excel_final/PROCESS.md`
- Modify: `Stages/excel_final/README.md`
- Modify: `backend/app/modules/excel_processing/README.md`

- [x] **Step 1: 更新 part 业务规则**

在 Stage 文档中明确：

```text
BH/BOX/BT 子板保留导入构件编号并在构件内汇总。
板材、扁钢清空导入构件编号，按零件号、规格、宽度、
下料长度、材质、类型和班组跨构件汇总。
同名不同完整属性分别输出。
```

将真实样本基线更新为：

```text
part 122 行：84 条 BOX 子板和 38 条全局板材；
全局板材汇总数量 1216；
GT 的 201 行包含没有来源的人工班组维度。
```

后端 README 明确：

```text
数据库仍从整理表导入构件级记录；
part 是下载工作簿中的下料汇总投影，不覆盖数据库构件身份。
```

- [x] **Step 2: 运行文档和差异检查**

```bash
cd backend
.venv/bin/python ../scripts/docs/check.py
cd ..
git diff --check
```

Expected: 文档检查与差异检查通过。

- [x] **Step 3: 提交文档**

```bash
git add \
  Stages/excel_final/PROCESS.md \
  Stages/excel_final/README.md \
  backend/app/modules/excel_processing/README.md
git commit -m "docs(excel-final): document scoped part aggregation"
```

## Task 5: 真实数据、MySQL、后端和全量验收

**Files:**
- Regenerate only: `Stages/excel_final/data/results/*.xlsx`
- Regenerate only: `Stages/excel_final/data/reports/*.csv`
- Regenerate only: `Stages/excel_final/data/reports/*.md`
- Modify: `docs/superpowers/plans/2026-07-23-part-component-scope.md`

- [x] **Step 1: 运行 Stage 离线全量测试**

```bash
cd Stages/excel_final
.venv/bin/pytest -q -m "not handbook_mysql and not live_data" \
  tests multi_split/tests
```

Expected: 全部启用测试通过。

- [x] **Step 2: 运行真实 MySQL 手册测试**

```bash
backend/.venv/bin/pytest -q -m handbook_mysql Stages/excel_final/tests
```

Expected: 全部通过，板材常量、扁钢、D 系列材质路由及跳过类别行为不变。

- [x] **Step 3: 运行真实 ground truth 测试**

```bash
DWG_RUN_LIVE_EXCEL_FINAL=1 backend/.venv/bin/pytest -q -s \
  Stages/excel_final/tests/test_ground_truth_regression.py
```

Expected: 真实结果为 122 条 part，84 条构件范围、38 条全局范围、全局汇总 1216，质量状态 `ok`。

- [x] **Step 4: 重生成规范结果和对比报告**

使用后端 `.venv`、真实 MySQL 和 `source_format="canonical"` 重生成：

```text
Stages/excel_final/data/results/20260320-首都体育学院B7#地下部分-excel-final-规范结果.xlsx
Stages/excel_final/data/reports/20260320-首都体育学院B7#地下部分-excel-final-规范结果-ground-truth-comparison.csv
Stages/excel_final/data/reports/20260320-首都体育学院B7#地下部分-excel-final-规范结果-ground-truth-comparison.md
```

直接回读并断言：

```python
assert len(part_rows) == 122
assert len(component_scoped) == 84
assert len(global_scoped) == 38
assert sum(row["汇总"] for row in global_scoped) == 1216
assert all(row["导入构件编号"] for row in component_scoped)
assert all(row["导入构件编号"] is None for row in global_scoped)
assert all(row["文件"] is None for row in part_rows)
assert workbook["处理报告"]["A2"].value == "无"
```

- [x] **Step 5: 运行后端真实上传处理入库下载测试**

```bash
cd backend
DWG_RUN_LIVE_EXCEL_FINAL=1 .venv/bin/pytest -q -s \
  tests/excel_processing/test_excel_final_live_flow.py
```

Expected: 上传、worker、构件级入库、目录、下载全部通过；plate API 为 394，下载 part 为 122。

- [x] **Step 6: 运行后端全量门禁**

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/alembic check
.venv/bin/python ../scripts/docs/check.py
```

Expected: Ruff、全量测试、迁移一致性和文档检查全部通过。

- [x] **Step 7: 清理缓存并检查工作区**

仅清理 Stage 和 Excel Processing 模块中的测试缓存，不删除 `data/`：

```bash
find Stages/excel_final backend/app/modules/excel_processing \
  -path '*/.venv' -prune -o -type f -name '*.pyc' -delete
git diff --check
git status --short
```

Expected: 没有未提交的跟踪文件；`Stages/excel_final/data/`继续保持未跟踪。

- [x] **Step 8: 完成交付审查**

核对：

```text
构件范围类型集合没有使用字符串前缀猜测；
全局分组键包含完整属性和班组；
数据库导入仍读取整理表；
GT 84 条主零件完全一致；
GT 117 条普通零件去班组后等于 38 条；
四条 GT 汇总错误未被复制；
没有恢复 RECT；
没有提交 data/。
```
