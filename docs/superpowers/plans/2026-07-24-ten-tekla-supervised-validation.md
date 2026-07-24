# Ten Tekla Supervised Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing Excel Final production flow on ten reviewed Tekla inputs, compare only mutually populated program/人工-GT fields, fix evidence-backed general defects, and leave one concise report per project.

**Architecture:** Keep `run_auto_pipeline` as the only production entry. Add a reusable supervised comparison tool that matches `整理表` rows by stable source identity and `part` rows by the complete business key, reports shared-nonempty field differences without deciding which side is correct, and writes machine-readable evidence. Execute projects 01–10 strictly in order; each discrepancy is traced through source input, canonical records, handbook result, formulas, and reviewed GT before any production change.

**Tech Stack:** Python 3.11+, openpyxl, Decimal, pytest, existing Excel Final canonical pipeline, read-only MySQL hardware handbook.

---

## Task 1: Lock the supervised comparison contract

**Files:**
- Create: `Stages/excel_final/tools/compare_supervised_sample.py`
- Create: `Stages/excel_final/tests/test_compare_supervised_sample.py`
- Modify: `Stages/excel_final/README.md`

- [ ] **Step 1: Write failing tests for shared-nonempty comparison**

Cover these exact rules:

```python
def test_shared_nonempty_comparison_ignores_gt_and_output_only_values() -> None:
    result = compare_shared_fields(
        {"长度(mm)": 1000, "下料长度(mm)": None, "材质": "Q355B"},
        {"长度(mm)": 1000, "下料长度(mm)": 1010, "材质": None},
        fields=("长度(mm)", "下料长度(mm)", "材质"),
    )
    assert result.compared == {"长度(mm)": (1000, 1000)}
    assert result.differences == {}
```

```python
def test_numeric_comparison_uses_display_precision_without_hiding_real_difference() -> None:
    assert values_equal(1.2344, 1.234, field="理单重(kg)")
    assert not values_equal(1.236, 1.234, field="理单重(kg)")
```

```python
def test_part_complete_key_keeps_different_dimensions_separate() -> None:
    assert part_key({"导入零件号": "P1", "规格": 10, "宽度": 100,
                     "下料长度": 1000, "材质": "Q355B"}) != part_key(
        {"导入零件号": "P1", "规格": 10, "宽度": 120,
         "下料长度": 1000, "材质": "Q355B"}
    )
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
cd Stages/excel_final
uv run pytest -q tests/test_compare_supervised_sample.py
```

Expected: collection/import failure because the comparison module does not yet exist.

- [ ] **Step 3: Implement the comparison tool**

The tool must:

- accept `--canonical`, `--ground-truth`, `--csv`, and `--json`;
- read cached values with `data_only=True`;
- compare only the `整理表` and `part` sheets;
- match organized rows first by `导入构件编号 + 导入零件号`, then disambiguate repeated identities without using a single target field as the sole key;
- match `part` by `导入构件编号 + 导入零件号 + 规格 + 宽度 + 下料长度 + 材质`;
- compare only cells nonempty on both sides;
- normalize `BOX盖` to `BOX翼`, integer-like numerics, and Excel display precision;
- separately report unmatched program rows, unmatched GT rows, ambiguous groups, compared cells, equal cells, and different cells;
- never label either side correct automatically.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd Stages/excel_final
uv run pytest -q tests/test_compare_supervised_sample.py tests/test_compare_ground_truth.py
```

Expected: all pass.

- [ ] **Step 5: Document the comparison boundary**

Add a README section stating that supervised comparisons are diagnostic only, GT empty cells are not requirements, program-only formulas are not failures, and all differences require source/physical review.

- [ ] **Step 6: Commit the comparison contract**

```bash
git add Stages/excel_final/tools/compare_supervised_sample.py \
        Stages/excel_final/tests/test_compare_supervised_sample.py \
        Stages/excel_final/README.md \
        docs/superpowers/plans/2026-07-24-ten-tekla-supervised-validation.md
git commit -m "test(excel-final): add supervised sample comparator"
```

## Task 2: Establish the ten-project artifact layout

**Files:**
- Create outside the repository: `/home/Creeken/Paper/CAD_research/Data/十份排版/程序验证/排版01` through `排版10`

- [ ] **Step 1: Create explicit project directories**

Each project directory contains:

```text
排版NN-程序结果.xlsx
排版NN-共同字段对照.csv
排版NN-共同字段对照.json
排版NN-验证报告.md
```

- [ ] **Step 2: Record immutable inputs**

For each project report, record the SHA256 of:

- `ground_truth整理/排版NN/输入/排版NN-Tekla输入.xls`
- `ground_truth整理/排版NN/规范监督结果/排版NN-规范监督结果.xlsx`

Do not copy or rewrite the source/GT files.

## Task 3: Process and close project 01

**Inputs:**
- `/home/Creeken/Paper/CAD_research/Data/十份排版/ground_truth整理/排版01/输入/排版01-Tekla输入.xls`
- `/home/Creeken/Paper/CAD_research/Data/十份排版/ground_truth整理/排版01/规范监督结果/排版01-规范监督结果.xlsx`

- [ ] Run `uv run python main.py` with the explicit project output path.
- [ ] Run the supervised comparator.
- [ ] Review every difference group against the source input, the C-region reviewed sheets, and the program report.
- [ ] Check C/D/G region isolation, BOX identity, `PL6*30` flat-steel lookup, formulas, and part quantities.
- [ ] If the program is wrong, add a minimal failing test, fix the general rule, rerun project 01 and focused regressions.
- [ ] Write `排版01-验证报告.md` with source scope, output status, shared-field counts, confirmed differences, program changes, and remaining human actions.

## Task 4: Process and close project 02

Repeat the Task 3 execution sequence for `排版02`, specifically checking D-region isolation, duplicated aliases, BOX rows, flat-steel routing, and complete-key part grouping. Any fix must rerun project 01 before closing project 02.

## Task 5: Process and close project 03

Repeat the Task 3 execution sequence for `排版03`, specifically checking the small G-region population, connector rows with missing geometry, BOX identity, and the six rows omitted from the old combined `Sheet1`. Any fix must rerun projects 01–02 before closing project 03.

## Task 6: Process and close project 04

Repeat the Task 3 execution sequence for `排版04`, specifically checking fixed-width intake at larger scale, absence of component relationships, blank original length, `6*30` width placeholders, and whether program `part` admission correctly follows source identities rather than manual downstream-only knowledge. Any fix must rerun projects 01–03.

## Task 7: Process and close project 05

Repeat the Task 3 execution sequence for `排版05`, specifically checking BOX split naming, parent-weight single-display behavior, original versus reviewed length, connector exclusions, and ordinary-part aggregation. Any fix must rerun projects 01–04.

## Task 8: Process and close project 06

Repeat the Task 3 execution sequence for `排版06`, specifically checking the 14 pipe/profile rows absent from human `part`, handbook classification, reviewed length, and whether common nonempty GT fields agree without forcing downstream manual selection rules into production. Any fix must rerun projects 01–05.

## Task 9: Process and close project 07

Repeat the Task 3 execution sequence for `排版07`, specifically checking the largest no-part supervision gap, 60 rows without uniquely matched original length, component summaries, and whether program-only valid `part` rows are recorded as unmatched rather than failures. Any fix must rerun projects 01–06.

## Task 10: Process and close project 08

Repeat the Task 3 execution sequence for `排版08`, specifically checking BH/BOX component scoping, parent-weight deduplication, ordinary-part aggregation, and direct manual length correspondence. Any fix must rerun projects 01–07.

## Task 11: Process and close project 09

Repeat the Task 3 execution sequence for `排版09`, specifically checking 1,615 organized GT rows, BH split recovery despite overwritten manual type text, 249 original/cut-length differences, the `14B-156` manhole reinforcing plate, D-series material routing, and 753 part keys. Any fix must rerun projects 01–08.

## Task 12: Process and close project 10

Repeat the Task 3 execution sequence for `排版10`, specifically checking 56 BH rows with reviewed cut length, empty left/right inset fields, formula cache visibility, D-series material routing, and 380 part keys. Any fix must rerun projects 01–09.

## Task 13: Final regression and repository handoff

**Files:**
- Modify when warranted: production modules under `Stages/excel_final/`
- Modify: `Stages/excel_final/README.md`
- Modify: `Stages/excel_final/PROCESS.md`
- Create: `/home/Creeken/Paper/CAD_research/Data/十份排版/程序验证/十项目验证汇总.md`

- [ ] **Step 1: Run focused and full stage regressions**

```bash
cd Stages/excel_final
uv run pytest -q -m "not handbook_mysql and not live_data" tests multi_split/tests
```

Expected: all tests pass.

- [ ] **Step 2: Rerun all affected real projects**

Rerun only through the same production entry. A project report is final only after its latest output was generated after the last relevant production-code change.

- [ ] **Step 3: Verify output contracts**

For every output confirm six sheets, final 31/11 columns, formula caches, blank `part.备注/文件`, finite numeric values, and report readability.

- [ ] **Step 4: Write the ten-project summary**

Summarize project-by-project processing status, shared-field equality/differences, GT issues, program issues, implemented fixes, and unresolved human-review items. Do not count GT-only or program-only cells as field failures.

- [ ] **Step 5: Review and commit production changes**

```bash
git diff --check
git status --short
git add Stages/excel_final docs
git commit -m "fix(excel-final): harden Tekla supervised sample handling"
git push origin main
```

Only commit if production/tests/docs changed after the comparator commit; do not add real input/output workbooks.
