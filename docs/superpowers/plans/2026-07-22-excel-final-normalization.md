# Excel Final Normalization Implementation Plan

> **For Codex:** Before implementation, read and follow the `test-driven-development` skill. Execute every task red → green → refactor, keep the repository buildable after each commit, and use the `requesting-code-review` skill before final handoff.

**Goal:** Replace the accidental 25-step Excel Final behavior with one tested, category-aware, physically auditable pipeline that produces the fixed six-sheet workbook, preserves component/part identity, validates source and theoretical weights, and exposes quality results through MySQL and the API.

**Architecture:** Keep the standalone Stage and backend subprocess boundary. Build one canonical parent-record pipeline shared by Tekla text and initial-table adapters: strict input → parent records → classification/handbook → parent evidence → controlled split → part projection → six-sheet writer/report. Preserve the existing `multi_split` public compatibility surface, but make the canonical pipeline call only confirmed BH/BOX/BT geometry. Extend the backend projection and Stage-runner protocol instead of moving algorithms into FastAPI.

**Tech Stack:** Python 3.11 Stage, pandas, openpyxl 3.1.5, PyMySQL, pytest, Ruff; Python 3.12 FastAPI backend, SQLAlchemy 2, Alembic, MySQL 8, pytest.

**Authoritative design:** `docs/superpowers/specs/2026-07-22-excel-final-normalization-design.md`

**User-data boundary:** Never add `Stages/excel_final/data/` to Git. The source ground truth, preprocessed workbook, generated workbook, and comparison reports remain user artifacts. Commit only source, tests, text fixtures, and documentation.

**Working-directory convention:** Start Tasks 1–12 in `Stages/excel_final`, start Tasks 13–15 in `backend`, and start Task 16 at the repository root. Each commit block explicitly returns to the repository root before using root-relative Git paths.

**Commit hygiene:** Before every commit, inspect `git status --short` and the staged diff. Stage only the paths listed by that task, preserve unrelated user changes, and never stage `Stages/excel_final/data/`.

---

## Task 1: Establish the Stage test harness and lossless preprocessor

**Files:**

- Modify: `Stages/excel_final/pyproject.toml`
- Modify mechanically: `Stages/excel_final/uv.lock`
- Create: `Stages/excel_final/tests/conftest.py`
- Create: `Stages/excel_final/tests/test_preprocess.py`
- Create: `Stages/excel_final/preprocess.py`

### Step 1: Add the failing preprocessor contract tests

First add `pytest` and `ruff` to the Stage dev dependency group, register the `handbook_mysql` and `live_data` markers, update `uv.lock`, and create the test harness. This is test infrastructure, not feature implementation.

Cover:

- multi-sheet source → exactly one target sheet named `原表`;
- only the first reviewed raw sheet is retained;
- cell values, data types, formulas, style IDs, merged ranges, dimensions, column widths, and row heights match;
- source path and SHA-256 are unchanged;
- source and output paths cannot be the same;
- ordinary production validation must not call this multi-sheet extraction path.

Representative assertion:

```python
result = preprocess_ground_truth(source, output)
assert result.sheet_name == "原表"
assert load_workbook(output).sheetnames == ["原表"]
assert sha256(source) == source_hash_before
assert_sheet_equivalent(source_ws, load_workbook(output)["原表"])
```

### Step 2: Run the tests and prove they fail

Run from `Stages/excel_final`:

```bash
uv sync --group dev
uv run pytest -q tests/test_preprocess.py
```

Expected: FAIL because `preprocess.py` and its contracts do not exist.

### Step 3: Implement the minimum controlled preprocessor

- Load with `data_only=False`.
- Retain the reviewed first sheet, remove the other sheets, and rename it `原表`.
- Save to a different path.
- Reopen source and output and verify the documented preservation scope.
- Return a typed summary containing source hash, output hash, dimensions, and sheet name.

### Step 4: Re-run and lint

```bash
uv run pytest -q tests/test_preprocess.py
uv run ruff check preprocess.py tests/test_preprocess.py
```

Expected: PASS.

### Step 5: Generate and verify the real preprocessed input

```bash
uv run python preprocess.py \
  'data/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3).xlsx' \
  'data/preprocessed/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3)_原表.xlsx'
sha256sum 'data/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3).xlsx'
```

Expected source hash: `af6d21411855bd25d9d1a9e43e45764f131fa66f6316343bcd3f073dc1e5f4d2`.

### Step 6: Commit source and tests only

```bash
cd ../..
git add Stages/excel_final/pyproject.toml Stages/excel_final/uv.lock \
  Stages/excel_final/preprocess.py Stages/excel_final/tests/conftest.py \
  Stages/excel_final/tests/test_preprocess.py
git commit -m "feat(excel-final): add verified raw-sheet preprocessor"
```

---

## Task 2: Introduce canonical domain records and the quality ledger

**Files:**

- Create: `Stages/excel_final/domain.py`
- Create: `Stages/excel_final/quality.py`
- Create: `Stages/excel_final/tests/test_domain_quality.py`

### Step 1: Write failing model and quality tests

Test immutable `SourcePart`, `ParentPartEvidence`, `SplitPart`, `QualityIssue`, and `PipelineOutcome` records. Verify:

- source sheet/row/sequence, component identity, original quantity/spec, and all source weights survive transformation;
- warning/severe counts equal issue-detail counts, not affected-row counts;
- `ok`, `warning`, and `severe_warning` are derived deterministically;
- severe issues mark `affects_part=True`; ordinary warnings do not;
- issue serialization contains every `处理报告` column.

### Step 2: Run and prove failure

```bash
uv run pytest -q tests/test_domain_quality.py
```

Expected: FAIL because the canonical records and ledger do not exist.

### Step 3: Implement typed immutable records and ledger

Use frozen dataclasses and enums. Do not put pandas/openpyxl objects into domain records. Preserve unrounded values as `Decimal`; format only in the writer.

### Step 4: Run, lint, and commit

```bash
uv run pytest -q tests/test_domain_quality.py
uv run ruff check domain.py quality.py tests/test_domain_quality.py
cd ../..
git add Stages/excel_final/domain.py Stages/excel_final/quality.py \
  Stages/excel_final/tests/test_domain_quality.py
git commit -m "feat(excel-final): add canonical records and quality ledger"
```

---

## Task 3: Enforce strict input, header, and row-structure contracts

**Files:**

- Modify: `Stages/excel_final/reader.py`
- Modify: `Stages/excel_final/reader_init.py`
- Create: `Stages/excel_final/input_contract.py`
- Create: `Stages/excel_final/tests/test_input_contract.py`
- Create: `Stages/excel_final/tests/test_reader_canonical.py`

### Step 1: Write failing tests

Cover:

- `.xlsx/.xlsm` with more than one sheet is fatal;
- no silent row-6 fallback;
- ambiguous headers report the first 15 candidate scores and missing fields;
- duplicate `长度(mm)` columns resolve to part length vs component length/width/height by grouped header and data semantics;
- all half-width/full-width spaces are removed from the working copy only;
- `清洗表` candidates contain only part rows;
- `构件表` source data has one component per row and retains source start/subtotal rows;
- inconsistent duplicate component IDs create severe issues;
- initial-table `单重/总重` map to source gross fields while net fields stay empty;
- Tekla text `.xls` remains a single-text-source adapter.

### Step 2: Run and prove failure

```bash
uv run pytest -q tests/test_input_contract.py tests/test_reader_canonical.py
```

Expected: FAIL on first-sheet fallback, row-6 fallback, and duplicate length semantics.

### Step 3: Implement strict readers

- Add one production workbook validator used before format detection.
- Replace positional fallback with scored, unique header detection.
- Normalize duplicate headers before selecting columns.
- Return canonical `SourcePart` and component-summary records.
- Keep raw workbook access separate from the normalized working values.

### Step 4: Run, lint, and commit

```bash
uv run pytest -q tests/test_input_contract.py tests/test_reader_canonical.py
uv run ruff check input_contract.py reader.py reader_init.py tests/test_input_contract.py tests/test_reader_canonical.py
cd ../..
git add Stages/excel_final/input_contract.py Stages/excel_final/reader.py \
  Stages/excel_final/reader_init.py Stages/excel_final/tests/test_input_contract.py \
  Stages/excel_final/tests/test_reader_canonical.py
git commit -m "refactor(excel-final): enforce canonical input records"
```

---

## Task 4: Normalize classification without geometry guesses

**Files:**

- Modify: `Stages/excel_final/spec_parser.py`
- Create: `Stages/excel_final/tests/test_spec_parser_normalized.py`

### Step 1: Write the classification table as failing parameterized tests

Include:

- `PL6*30` and intermediate `规格=6, 宽度=30` → flat steel `6*30`, width empty;
- other explicit PL retains written order; never sort smaller-first;
- `FB/FLAT/扁钢/扁铁` → flat steel;
- bare `t*w` returns a two-phase flat-steel candidate, not immediate plate;
- BH/BOX/BT are split candidates;
- ordinary I is a handbook I-beam and never a split candidate;
- HA is unsupported/unclassified;
- D+diameter with HPB…/Q355B → round bar;
- D+diameter with HRB… → rebar;
- D with blank/other material → material-insufficient miss;
- bolts, nuts/NUT, sleeves, and TT → explicit skip with blank theoretical fields;
- other known profiles map to exactly one category.

### Step 2: Run and prove current misclassifications

```bash
uv run pytest -q tests/test_spec_parser_normalized.py
```

Expected failures: PL order, HA, I, D15–D29, and fastener handling.

### Step 3: Implement a typed classification result

Return normalized type/spec/width, handbook category, lookup policy, and split policy. Remove `D15-D29 = stud` and the special D8 constant path. Classification must not query MySQL itself.

### Step 4: Run, lint, and commit

```bash
uv run pytest -q tests/test_spec_parser_normalized.py
uv run ruff check spec_parser.py tests/test_spec_parser_normalized.py
cd ../..
git add Stages/excel_final/spec_parser.py Stages/excel_final/tests/test_spec_parser_normalized.py
git commit -m "fix(excel-final): make classification material aware"
```

---

## Task 5: Replace cross-table handbook guessing with a category-aware repository

**Files:**

- Modify: `Stages/excel_final/config.py`
- Modify: `Stages/excel_final/handbook.py`
- Create: `Stages/excel_final/tests/test_handbook_repository.py`
- Create: `Stages/excel_final/tests/test_handbook_mysql.py`

### Step 1: Write fake-repository failing tests

Verify:

- lookup requires `(category, normalized_spec)`; D calls also require material class;
- each category executes only its owned table query;
- cache keys include category/spec and D material class;
- a successful SELECT returning no row yields `not_found`, never a formula estimate;
- connection, missing-table, missing-column, and SQL errors are fatal infrastructure errors;
- board returns constant 7.85 without querying;
- bolts/NUT/sleeves/TT return explicit skipped/blank;
- bare `t*w` may query flat steel once, then the caller can fall back to board;
- no miscellaneous NUT/TT constants remain;
- Stage defaults contain no host/user/password/database secret.

### Step 2: Run and prove failure

```bash
uv run pytest -q tests/test_handbook_repository.py
```

Expected failures: generic material lookup priority, computational fallback, and hard-coded credentials.

### Step 3: Implement the category repository

- Define an allow-listed category→SQL mapping.
- Parameterize values only; table/column identifiers come from the allow-list.
- Connect from injected/environment configuration.
- Validate required schema once at startup.
- Return value, source table/category, and `hit/not_found/skipped` status.
- Keep queries read-only.

### Step 4: Add real MySQL integration tests

Mark them `handbook_mysql`, but run them as mandatory acceptance in this workspace. Assert:

```text
flat_steel 6*30 -> 1.413
round_bar 24 -> about 3.55
round_bar 30 -> about 5.55
rebar 24/30 -> not found
```

Also prove the same `24`/`30` spec cannot cross from the requested category into another table.

The integration fixture must obtain connection data through the same platform settings contract used by `stage_adapter`: `HANDBOOK_MYSQL_*` when present, otherwise the existing `MYSQL_HOST/PORT/USER/PASSWORD` with database default `hardware_handbook`. Run this live test with the backend environment so `app.platform.config.settings` performs that parsing. The test must not import credentials from Stage constants, print configuration values, or place secrets in pytest IDs/errors; when run only in the standalone Stage environment it must skip with an explicit “platform settings unavailable” reason.

### Step 5: Run fake and real tests

```bash
uv run pytest -q tests/test_handbook_repository.py
cd ../../backend
uv run pytest -q -m handbook_mysql ../Stages/excel_final/tests/test_handbook_mysql.py
cd ../Stages/excel_final
```

Expected: all PASS against the existing MySQL handbook.

### Step 6: Lint and commit

```bash
uv run ruff check config.py handbook.py tests/test_handbook_repository.py tests/test_handbook_mysql.py
cd ../..
git add Stages/excel_final/config.py Stages/excel_final/handbook.py \
  Stages/excel_final/tests/test_handbook_repository.py Stages/excel_final/tests/test_handbook_mysql.py
git commit -m "refactor(excel-final): query handbook by confirmed category"
```

---

## Task 6: Build parent-level theoretical weight and physical validation

**Files:**

- Create: `Stages/excel_final/weights.py`
- Create: `Stages/excel_final/tests/test_weights.py`
- Create: `Stages/excel_final/tests/test_weight_validation.py`

### Step 1: Write failing formula and boundary tests

Cover:

- plate unit gross theory: `t*w*L*7.85/1_000_000`;
- handbook profiles: `kg_per_m*L/1000`;
- BH/BOX/BT combined parent theory includes every child plate contribution;
- rectangular six-face area formula;
- internal `Decimal` values remain unrounded; writer-facing values round to 3 weight decimals and 2 area decimals;
- source chains allow 0.1 kg absolute error;
- theory/gross thresholds at exactly 0.01 kg, 0.5%, and 2%;
- utilization `net/theory`, with no low cutoff;
- net > theory thresholds at 0.5% and 2%;
- missing source weights warn but do not backfill or isolate a dimensionally valid part;
- severe physical violations isolate part and identify the exact abnormal fields.

### Step 2: Run and prove failure

```bash
uv run pytest -q tests/test_weights.py tests/test_weight_validation.py
```

Expected: FAIL because only plate/D8 calculations and coarse one-kilogram log checks exist.

### Step 3: Implement parent evidence and validation

Compute all source and theoretical relationships before splitting. Store both concise `重量核验` text and structured issue details. Do not mutate source weights.

### Step 4: Run, lint, and commit

```bash
uv run pytest -q tests/test_weights.py tests/test_weight_validation.py
uv run ruff check weights.py tests/test_weights.py tests/test_weight_validation.py
cd ../..
git add Stages/excel_final/weights.py Stages/excel_final/tests/test_weights.py \
  Stages/excel_final/tests/test_weight_validation.py
git commit -m "feat(excel-final): validate parent weights physically"
```

---

## Task 7: Normalize BH/BOX/BT splitting while preserving legacy API tests

**Files:**

- Modify: `Stages/excel_final/multi_split/profile.py`
- Create: `Stages/excel_final/splitter.py`
- Create: `Stages/excel_final/tests/test_splitter.py`
- Modify: `Stages/excel_final/multi_split/tests/test_vba_parity.py` only if a test must distinguish legacy API from canonical API; do not weaken existing assertions.

### Step 1: Write failing canonical split tests

Verify exact geometry and quantities:

```text
BH  -> web tw*(H-2tf), N; flange tf*B, 2N
BOX -> web tw*(H-2tf), 2N; flange tf*B, 2N
BT  -> web tw*(H-tf), N; flange tf*B, N
```

Also verify:

- labels `BH腹/BH翼`, `BOX腹/BOX翼`, `BT腹/BT翼`;
- original profile remains immutable in `截面型材`;
- import part ID is `原零件号-类型`;
- both children keep parent source sequence and original quantity;
- current child quantity and total count have distinct meanings;
- web/main row shows parent weights, areas, density, combined theory, and validation;
- flange row displays those fields blank while its contribution remains in parent evidence;
- non-positive/inset-invalid geometry is severe and excluded from part;
- I and HA cannot reach canonical splitting.

### Step 2: Run canonical tests and the existing 259 regressions

```bash
uv run pytest -q tests/test_splitter.py
uv run pytest -q multi_split/tests
```

Expected: canonical tests FAIL first; the existing multi_split suite remains green before implementation.

### Step 3: Extract shared pure geometry and implement the canonical adapter

Keep legacy `split_profile_df` behavior available for its existing compatibility callers. Add a pure geometry helper and a canonical `split_parent()` adapter that accepts only already-confirmed BH/BOX/BT classifications. Do not duplicate formulas in two modules.

### Step 4: Run both suites, lint, and commit

```bash
uv run pytest -q tests/test_splitter.py multi_split/tests
uv run ruff check splitter.py multi_split/profile.py tests/test_splitter.py
cd ../..
git add Stages/excel_final/splitter.py Stages/excel_final/multi_split/profile.py \
  Stages/excel_final/tests/test_splitter.py
git commit -m "refactor(excel-final): split only confirmed fabricated profiles"
```

---

## Task 8: Implement strict RECT inference and per-component part projection

**Files:**

- Create: `Stages/excel_final/part_builder.py`
- Create: `Stages/excel_final/tests/test_rect.py`
- Create: `Stages/excel_final/tests/test_part_builder.py`

### Step 1: Write failing RECT tests

For ordinary PL, test every required condition independently: unit/total net=gross, unit/total theory=gross after specified rounding, unit/total six-face area, cut length agreement, and identity consistency. Test flat steel never gets RECT.

For split parents, test both proven and unproven outlines. Unproven outline is informational and remains in part; severe geometry/identity failure is excluded.

### Step 2: Write failing part grouping and ordering tests

Assert:

- only board, flat steel, and BH/BOX/BT children enter;
- every row has import component and import part IDs;
- exact grouping key is component, part, spec, width, cut length, material, type, team;
- no cross-component aggregation;
- `汇总 = child_qty * component_qty`;
- zero remains zero;
- type order is BH腹, BH翼, BOX腹, BOX翼, BT腹, BT翼, 扁钢, 板材;
- then import part ID and dimensions/material;
- same component+import part ID with conflicting geometry/material is severe, unmerged, and excluded.

### Step 3: Run and prove failure

```bash
uv run pytest -q tests/test_rect.py tests/test_part_builder.py
```

### Step 4: Implement, run, lint, and commit

```bash
uv run pytest -q tests/test_rect.py tests/test_part_builder.py
uv run ruff check part_builder.py tests/test_rect.py tests/test_part_builder.py
cd ../..
git add Stages/excel_final/part_builder.py Stages/excel_final/tests/test_rect.py \
  Stages/excel_final/tests/test_part_builder.py
git commit -m "feat(excel-final): build strict per-component part projection"
```

---

## Task 9: Write the fixed six-sheet workbook with formula caches and reports

**Files:**

- Modify: `Stages/excel_final/writer_parts.py`
- Create: `Stages/excel_final/ooxml_formula.py`
- Create: `Stages/excel_final/tests/test_writer_workbook.py`
- Create: `Stages/excel_final/tests/test_formula_cache.py`

### Step 1: Write failing writer contract tests

Assert exact sheet order and exact column order for `整理表` and `part`. Check:

- `原表` preservation;
- `清洗表` and `构件表` contents;
- source sequence is not renumbered and split siblings share it;
- `比重` and `比重来源` semantics;
- all weight cells use three-decimal formatting;
- `下料长度` is a formula under `data_only=False` and the numeric cache is readable under `data_only=True`;
- formula cache equals the Python cut-length value;
- lookup miss writes red-font `查无` and appears in `处理报告`;
- severe physical cells and `重量核验` use red font plus light-red fill;
- report counts, colors, and detail rows agree;
- `班组` and `图形` remain blank;
- no title/summary row shifts the fixed part header contract.

### Step 2: Run and prove failure

```bash
uv run pytest -q tests/test_writer_workbook.py tests/test_formula_cache.py
```

Expected: FAIL on five-sheet legacy output, old columns, missing report, and absent formula cache.

### Step 3: Implement the writer

- Start from a copied single-sheet workbook so `原表` remains intact.
- Create/replace only the other five canonical sheets.
- Write numeric derived values, except `下料长度` formulas.
- Save normally, then patch only the target worksheet XML cells to contain both `<f>` and `<v>`.
- Reopen twice and fail the run if formula/cache verification disagrees.

### Step 4: Run, lint, and commit

```bash
uv run pytest -q tests/test_writer_workbook.py tests/test_formula_cache.py
uv run ruff check writer_parts.py ooxml_formula.py tests/test_writer_workbook.py tests/test_formula_cache.py
cd ../..
git add Stages/excel_final/writer_parts.py Stages/excel_final/ooxml_formula.py \
  Stages/excel_final/tests/test_writer_workbook.py Stages/excel_final/tests/test_formula_cache.py
git commit -m "feat(excel-final): emit audited six-sheet workbook"
```

---

## Task 10: Switch both Stage entry paths to the canonical pipeline

**Files:**

- Modify: `Stages/excel_final/pipeline.py`
- Modify: `Stages/excel_final/main.py`
- Modify: `Stages/excel_final/transformer.py`
- Modify: `Stages/excel_final/transform_init.py`
- Create: `Stages/excel_final/tests/test_pipeline_end_to_end.py`
- Create: `Stages/excel_final/tests/test_cli.py`

### Step 1: Write failing synthetic end-to-end tests

Create one strict single-sheet Tekla workbook and one initial-table workbook containing PL, 6x30, bare t*w hit/miss, BOX, BH, BT, I, D with each material class, NUT, TT, a handbook miss, missing source weight, and a severe identity conflict.

Assert both adapters feed the same classification/handbook/weight/split/writer engine and produce equivalent canonical semantics.

### Step 2: Write CLI behavior tests

Assert:

- multi-sheet `.xlsx` fails before format detection;
- DB infrastructure failure is fatal and safe;
- successful output returns `ok/warning/severe_warning` plus counts;
- handbook misses are printed as actionable final warnings;
- no DB secret, DSN, host path, or child traceback is printed.

### Step 3: Run and prove failure

```bash
uv run pytest -q tests/test_pipeline_end_to_end.py tests/test_cli.py
```

### Step 4: Replace orchestration, preserving adapters

- `run_pipeline` and `run_init_pipeline` become thin input adapters around one canonical function.
- Return `PipelineOutcome`, including output path and quality summary. Until Task 15 versions the backend protocol, implement `os.PathLike[str]`/`__fspath__` on the outcome so the existing runner's `Path(result)` compatibility check continues to work.
- Remove calls to coarse `finalize` verification and post-split proration from the active path.
- Keep compatibility imports temporarily until Task 12 proves they are unreferenced.

### Step 5: Run all Stage tests and commit

```bash
uv run pytest -q -m "not handbook_mysql and not live_data" tests multi_split/tests
uv run ruff check . --exclude .venv --exclude data
cd ../..
git add Stages/excel_final/pipeline.py Stages/excel_final/main.py \
  Stages/excel_final/transformer.py Stages/excel_final/transform_init.py \
  Stages/excel_final/tests/test_pipeline_end_to_end.py Stages/excel_final/tests/test_cli.py
git commit -m "refactor(excel-final): route both inputs through canonical pipeline"
```

---

## Task 11: Lock the real ground-truth regression and produce the comparison report

**Files:**

- Create: `Stages/excel_final/tests/fixtures/ground_truth_baseline.json`
- Create: `Stages/excel_final/tests/test_ground_truth_regression.py`
- Create: `Stages/excel_final/tools/compare_ground_truth.py`

### Step 1: Add the text baseline

Record only non-sensitive facts:

```json
{
  "sha256": "af6d21411855bd25d9d1a9e43e45764f131fa66f6316343bcd3f073dc1e5f4d2",
  "parent_parts": 485,
  "pl": 394,
  "box": 42,
  "tt": 41,
  "d": 4,
  "nut": 4,
  "organized_rows": 527,
  "strict_pl_rect": 196
}
```

### Step 2: Write the failing real-sample test

Mark the test `live_data`. It must locate the user data file, skip with an explicit reason only when absent, and otherwise assert all baseline facts, source hash preservation, six sheets, D24/D30 round-bar sources, NUT/TT blank theory, and no duplicated source weight after BOX splitting. Run it under the backend environment so the canonical pipeline receives the real platform handbook settings.

### Step 3: Implement the comparison tool

Generate a rule-by-rule CSV/Markdown report under `Stages/excel_final/data/reports/` comparing source, immature ground-truth result sheets, and canonical output. Explicitly label intended differences: BOX翼 naming, no BOX→BH, restored component IDs, changed part row count, corrected weights, and stricter RECT.

### Step 4: Run against the real file and real MySQL

```bash
cd ../../backend
uv run pytest -q -m handbook_mysql ../Stages/excel_final/tests/test_handbook_mysql.py
uv run pytest -q -m live_data ../Stages/excel_final/tests/test_ground_truth_regression.py
uv run python - <<'PY'
from pathlib import Path
from app.modules.excel_processing.stage_adapter import run_excel_final_pipeline

root = Path("../Stages/excel_final").resolve()
run_excel_final_pipeline(
    root / "data/preprocessed/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3)_原表.xlsx",
    root / "data/results/20260320-首都体育学院B7#地下部分-excel-final-规范结果.xlsx",
    source_format="tsv",
)
PY
cd ../Stages/excel_final
uv run python tools/compare_ground_truth.py \
  --source 'data/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3).xlsx' \
  --preprocessed 'data/preprocessed/20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb(3)_原表.xlsx' \
  --output 'data/results/20260320-首都体育学院B7#地下部分-excel-final-规范结果.xlsx'
```

Expected: 485 parent rows, 527 organized rows, 196 strict PL RECT, and actual MySQL hits for 6x30/D24/D30.

### Step 5: Inspect the actual workbook

Open with openpyxl in formula and data-only modes. Check representative PL, BOX, D, NUT, TT, missing-handbook, warning, and severe rows. If LibreOffice is available, open/recalculate/save a copy under `data/reports/` and confirm formulas and layout remain valid.

### Step 6: Commit text/code only

```bash
cd ../..
git add Stages/excel_final/tests/fixtures/ground_truth_baseline.json \
  Stages/excel_final/tests/test_ground_truth_regression.py \
  Stages/excel_final/tools/compare_ground_truth.py
git commit -m "test(excel-final): lock real sample invariants"
```

---

## Task 12: Retire unreachable legacy behavior and synchronize Stage documentation

**Files:**

- Modify: `Stages/excel_final/README.md`
- Modify: `Stages/excel_final/PROCESS.md`
- Modify: `Stages/excel_final/multi_split/CLAUDE.md`
- Delete only after `rg` proves unreferenced: `Stages/excel_final/calculator.py`
- Delete only after `rg` proves unreferenced: `Stages/excel_final/finalize.py`
- Delete only after `rg` proves unreferenced: `Stages/excel_final/post_split.py`
- Delete only after `rg` proves unreferenced: `Stages/excel_final/prorate.py`

### Step 1: Write a failing documentation consistency test

Create or extend a Stage contract test that rejects obsolete claims: default row 6, PL dimension sorting in the canonical path, HA/BH equivalence, ordinary I splitting, computational handbook fallbacks, NUT/TT static weights, five-sheet output, and old part headers.

Run before changing the documents:

```bash
uv run pytest -q tests/test_pipeline_end_to_end.py -k documentation_contract
```

Expected: FAIL because `README.md`, `PROCESS.md`, and `multi_split/CLAUDE.md` still describe legacy behavior.

### Step 2: Prove legacy modules are unreachable

```bash
rg -n "calculator|finalize|post_split|prorate" Stages/excel_final \
  --glob '!.venv/**' --glob '!data/**' --glob '!__pycache__/**'
```

Delete a module only when active code and tests no longer import it. Preserve any reusable function by moving it under the new owning module with tests first.

### Step 3: Update documentation

Document the production input contract, category-aware MySQL lookup, physical weight meanings, strict split rules, six-sheet schema, report/status levels, formula cache, real-sample baseline, and backend boundary. Mark legacy `split_profile_df` as compatibility-only and canonical splitting as classification-gated.

### Step 4: Run all Stage tests and commit

```bash
uv run pytest -q -m "not handbook_mysql and not live_data" tests multi_split/tests
uv run ruff check . --exclude .venv --exclude data
cd ../..
git add Stages/excel_final/README.md Stages/excel_final/PROCESS.md \
  Stages/excel_final/multi_split/CLAUDE.md \
  Stages/excel_final/tests/test_pipeline_end_to_end.py
git add -u Stages/excel_final/calculator.py Stages/excel_final/finalize.py \
  Stages/excel_final/post_split.py Stages/excel_final/prorate.py
git commit -m "docs(excel-final): align implementation and process contract"
```

---

## Task 13: Add backend identity and quality persistence

**Files:**

- Modify: `backend/app/modules/excel_processing/models.py`
- Create: `backend/migrations/versions/f3a7c9d2e6b1_add_excel_final_quality_fields.py`
- Modify: `backend/tests/excel_processing/test_excel_final_models.py`
- Modify: `backend/tests/infrastructure/test_migrations.py`

### Step 1: Write failing model tests

Require part fields:

```text
import_component_no, import_part_no, source_batch, team, original_qty,
density_source, material_utilization, weight_validation
```

Require batch fields:

```text
quality_status, warning_count, severe_warning_count, report_summary
```

Use `JSON` for `report_summary`; default quality status to `ok` and counts to zero.

### Step 2: Write the failing migration contract

Assert revision `f3a7c9d2e6b1` extends current head `e2f4b8c6a130`, adds only the owned columns/indexes, and has a reversible downgrade.

### Step 3: Run and prove failure

From `backend`:

```bash
uv run pytest -q tests/excel_processing/test_excel_final_models.py tests/infrastructure/test_migrations.py
```

### Step 4: Implement model and migration

Do not edit historical migrations. Use lengths consistent with current widened identifiers.

### Step 5: Run migration checks and commit

```bash
uv run pytest -q tests/excel_processing/test_excel_final_models.py tests/infrastructure/test_migrations.py
uv run alembic heads
uv run alembic check
uv run ruff check app/modules/excel_processing/models.py migrations/versions/f3a7c9d2e6b1_add_excel_final_quality_fields.py tests/excel_processing/test_excel_final_models.py
cd ..
git add backend/app/modules/excel_processing/models.py \
  backend/migrations/versions/f3a7c9d2e6b1_add_excel_final_quality_fields.py \
  backend/tests/excel_processing/test_excel_final_models.py backend/tests/infrastructure/test_migrations.py
git commit -m "feat(backend): persist excel final identity and quality"
```

---

## Task 14: Import canonical fields, report quality, and correct batch totals

**Files:**

- Modify: `backend/app/modules/excel_processing/importers.py`
- Modify: `backend/app/modules/excel_processing/persistence.py`
- Modify: `backend/app/modules/excel_processing/schemas.py`
- Modify: `backend/app/modules/excel_processing/presentation.py`
- Modify: `backend/tests/excel_processing/test_excel_final_import.py`
- Create: `backend/tests/excel_processing/test_excel_final_quality.py`

### Step 1: Write failing importer tests

Verify all new `整理表` fields persist, `下料长度` is readable through `data_only=True`, and legacy 27-column workbooks still import with new fields null/default.

Verify `处理报告` parsing:

- counts equal detail rows by severity;
- summary JSON is bounded and contains category counts plus representative messages;
- workbook summary counts must agree with parsed details;
- mismatches fail import rather than silently reporting false quality.

### Step 2: Write failing total-weight tests

Use split web/flange fixtures to prove:

- batch total net sums `表净重` only;
- batch total gross sums `表毛重` only;
- blank flange fields do not duplicate parent values;
- neither total directly sums `总净重/总毛重`.

### Step 3: Run and prove failure

```bash
uv run pytest -q tests/excel_processing/test_excel_final_import.py \
  tests/excel_processing/test_excel_final_quality.py
```

### Step 4: Implement streaming import and presentation

Preserve sequential row iteration; do not introduce random worksheet cell access. Add new fields to batch/part list, detail, search, and process-status projections.

### Step 5: Run, lint, and commit

```bash
uv run pytest -q tests/excel_processing/test_excel_final_import.py \
  tests/excel_processing/test_excel_final_quality.py
uv run ruff check app/modules/excel_processing tests/excel_processing
cd ..
git add backend/app/modules/excel_processing/importers.py \
  backend/app/modules/excel_processing/persistence.py \
  backend/app/modules/excel_processing/schemas.py \
  backend/app/modules/excel_processing/presentation.py \
  backend/tests/excel_processing/test_excel_final_import.py \
  backend/tests/excel_processing/test_excel_final_quality.py
git commit -m "fix(backend): import excel final quality and table totals"
```

---

## Task 15: Version the Stage protocol and expose safe quality/API warnings

**Files:**

- Modify: `backend/app/modules/excel_processing/stage_runner.py`
- Modify: `backend/app/modules/excel_processing/stage_adapter.py`
- Modify: `backend/app/modules/excel_processing/staging.py`
- Modify: `backend/app/modules/excel_processing/execution.py`
- Modify: `backend/app/modules/excel_processing/routes/tools.py`
- Modify: `backend/app/modules/excel_processing/README.md`
- Modify: `backend/tests/excel_processing/test_excel_final_adapter.py`
- Modify: `backend/tests/excel_processing/test_excel_final_idempotency.py`
- Modify: `backend/tests/excel_processing/test_excel_final_retry.py`
- Modify: `backend/tests/workflows/test_workflow_production.py` if it asserts the old completion payload.

### Step 1: Write failing Stage protocol tests

Require process result JSON containing protocol version, output path, quality status, warning counts, and safe summary. Reject missing keys, wrong types, impossible counts, unknown status, and extra secret-bearing traceback fields.

### Step 2: Write failing input/API tests

- backend staging rejects multi-sheet `.xlsx/.xlsm` before first-sheet format detection;
- `/weights/lookup` requires `category` and `spec`;
- a D spec additionally requires `material`;
- D material/category conflict returns a validation error;
- runner/adapter pass category and material without putting DB credentials on the command line;
- successful warning/severe jobs stay `succeeded` but expose quality in progress, done event, analysis JSON, process status, and batch endpoints;
- handbook misses appear in the final message/summary.

### Step 3: Run and prove failure

```bash
uv run pytest -q tests/excel_processing/test_excel_final_adapter.py \
  tests/excel_processing/test_excel_final_idempotency.py \
  tests/excel_processing/test_excel_final_retry.py \
  tests/workflows/test_workflow_production.py
```

### Step 4: Implement the versioned protocol

- Stage runner prints one prefixed JSON result for `process` and `lookup`.
- Adapter parses into a typed result instead of returning only `Path`.
- Keep secrets in environment variables.
- Execution persists and broadcasts quality without changing Job success semantics.
- Remove the legacy post-output bolt-shift repair once canonical reader tests prove it is unnecessary; otherwise constrain it to old-output compatibility only.

### Step 5: Run, lint, and commit

```bash
uv run pytest -q tests/excel_processing tests/workflows/test_workflow_production.py
uv run ruff check app/modules/excel_processing tests/excel_processing tests/workflows/test_workflow_production.py
cd ..
git add backend/app/modules/excel_processing/stage_runner.py \
  backend/app/modules/excel_processing/stage_adapter.py \
  backend/app/modules/excel_processing/staging.py \
  backend/app/modules/excel_processing/execution.py \
  backend/app/modules/excel_processing/routes/tools.py \
  backend/app/modules/excel_processing/README.md \
  backend/tests/excel_processing/test_excel_final_adapter.py \
  backend/tests/excel_processing/test_excel_final_idempotency.py \
  backend/tests/excel_processing/test_excel_final_retry.py \
  backend/tests/workflows/test_workflow_production.py
git commit -m "feat(backend): surface excel final quality results"
```

---

## Task 16: Full regression, live MySQL verification, and final completeness audit

**Files:**

- Modify only if evidence requires: any files above
- Create user artifact only: `Stages/excel_final/data/reports/final-verification.md`

### Step 1: Run the complete Stage suite

```bash
cd Stages/excel_final
uv run pytest -q -m "not handbook_mysql and not live_data" tests multi_split/tests
uv run ruff check . --exclude .venv --exclude data
cd ../../backend
uv run pytest -q -m handbook_mysql ../Stages/excel_final/tests/test_handbook_mysql.py
uv run pytest -q -m live_data ../Stages/excel_final/tests/test_ground_truth_regression.py
cd ..
```

Expected: all new tests and all existing 259 multi_split tests pass; real MySQL checks pass.

### Step 2: Regenerate and inspect the real output

Re-run preprocessing and the canonical pipeline. Verify:

- source SHA-256 unchanged;
- fixed six sheets and exact headers;
- 485 parent parts and 527 organized rows;
- 42 BOX parents → 84 child rows;
- 196 ordinary PL RECT;
- D24/D30 source is round steel due material;
- NUT/TT theory remains blank;
- all handbook misses are red and summarized;
- formula and cached values agree;
- source and table net/gross conservation holds at parent scope;
- `part` retains component relationships and uses the approved type order.

Write the evidence to the untracked `data/reports/final-verification.md`.

### Step 3: Run backend targeted and global checks

```bash
cd backend
uv run pytest -q tests/excel_processing tests/infrastructure/test_migrations.py \
  tests/workflows/test_workflow_production.py
uv run ruff check app tests ../tests/run_full_verify.py
uv run alembic check
uv run pytest -q
```

If the repository provides the MySQL migration smoke environment, also run the documented migration test; do not substitute SQLite model tests for it.

### Step 4: Run a final design-to-evidence matrix

For every decision in the authoritative design, record:

- implementation owner;
- unit/integration/real-sample test name;
- actual command result;
- artifact cell/API evidence where applicable.

No item may remain “implemented but untested” or “tested only by ground truth coincidence.”

### Step 5: Use the code-review skill and resolve findings

Invoke `requesting-code-review`, review the complete diff, and fix all correctness, data-loss, security, migration, and test-quality findings. Re-run the affected suites after every correction.

### Step 6: Check repository hygiene and commit final corrections

```bash
cd ..
git diff --check
git status --short
git ls-files Stages/excel_final/data
```

Expected: no ground-truth/generated binary is tracked; only deliberate source/doc/test changes remain.

Commit final review fixes, then report the output workbook path, comparison report path, live MySQL evidence, test totals, migration head, quality counts, and any remaining limitation.
