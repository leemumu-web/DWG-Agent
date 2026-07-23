# Excel Final Robust Source Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task with TDD. This repository's user has explicitly required inline execution without subagents.

**Goal:** Make Excel Final reliably ingest B7, the 003 workbook, and historical same-type Tekla part lists without sample-specific row assumptions, while preserving all confirmed handbook, formula, part, report, and backend contracts.

**Architecture:** Add one `source_intake.py` interface that owns format detection and delegates to workbook, initial-table, delimited-text, and fixed-width-text adapters. Centralize header aliases in `input_contract.py` and fabricated-profile geometry in `fabricated_profile.py`; keep the canonical pipeline and backend protocol stable.

**Tech Stack:** Python 3.12, openpyxl, pandas, pytest, MySQL handbook repository, FastAPI backend.

---

## Task 1: Centralize the input schema

**Files:**
- Modify: `Stages/excel_final/input_contract.py`
- Test: `Stages/excel_final/tests/test_input_contract.py`

- [ ] **Step 1: Write failing alias and optional-batch tests**

```python
def test_header_accepts_aliases_without_batch(workbook_factory):
    ws = workbook_factory([
        ["构件号", "零件编号", "型材", "长度", "材质", "数量"],
    ]).active
    detected = detect_canonical_header(ws)
    assert dict(detected.columns) == {
        "构件编号": 1,
        "零件号": 2,
        "规格": 3,
        "零件长度": 4,
        "材质": 5,
        "数量": 6,
    }


def test_header_rejects_alias_collision(workbook_factory):
    ws = workbook_factory([
        ["构件编号", "零件号", "零件编号", "规格", "长度", "材质", "数量"],
    ]).active
    with pytest.raises(InputContractError, match="冲突"):
        detect_canonical_header(ws)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd Stages/excel_final
pytest tests/test_input_contract.py -q
```

Expected: alias/no-batch test fails because `批次` is required and aliases are not mapped.

- [ ] **Step 3: Implement the centralized schema**

```python
_HEADER_ALIASES = {
    "批次": {"批次"},
    "构件编号": {"构件编号", "构件号"},
    "零件号": {"零件号", "零件编号"},
    "规格": {"规格", "型材", "截面型材"},
    "材质": {"材质"},
    "数量": {"数量"},
    "单净重": {"单净重"},
    "总净重": {"总净重"},
    "单毛重": {"单毛重", "单重"},
    "总毛重": {"总毛重", "总重"},
    "单表面积": {"单表面积", "单面积", "单涂装面积"},
    "总表面积": {"总表面积", "总面积", "总涂装面积"},
}
_REQUIRED_HEADERS = ("构件编号", "零件号", "规格", "零件长度", "材质", "数量")
```

Build a reverse alias map once, record every source-column match, and raise
`InputContractError` if one canonical field is supplied by multiple source columns.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `pytest tests/test_input_contract.py -q`

Expected: all input-contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add Stages/excel_final/input_contract.py Stages/excel_final/tests/test_input_contract.py
git commit -m "feat(excel-final): generalize source header schema"
```

## Task 2: Add the single Source Intake interface

**Files:**
- Create: `Stages/excel_final/source_intake.py`
- Modify: `Stages/excel_final/reader.py`
- Modify: `Stages/excel_final/pipeline.py`
- Test: `Stages/excel_final/tests/test_source_intake.py`

- [ ] **Step 1: Write failing interface tests**

```python
def test_source_intake_detects_standard_workbook(b7_source):
    result = read_production_source(b7_source)
    assert result.source_format is SourceFormat.STANDARD_WORKBOOK
    assert result.parts
    assert result.component_rows


def test_source_intake_detects_initial_table(init_source):
    result = read_production_source(init_source)
    assert result.source_format is SourceFormat.INITIAL_WORKBOOK
    assert result.parts
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest tests/test_source_intake.py -q`

Expected: import fails because `source_intake` does not exist.

- [ ] **Step 3: Implement the interface and adapters**

```python
class SourceFormat(StrEnum):
    STANDARD_WORKBOOK = "standard_workbook"
    INITIAL_WORKBOOK = "initial_workbook"
    DELIMITED_TEKLA_TEXT = "delimited_tekla_text"
    FIXED_WIDTH_TEKLA_TEXT = "fixed_width_tekla_text"


@dataclass(frozen=True, slots=True)
class SourceIntakeResult:
    source_path: Path
    source_format: SourceFormat
    sheet_name: str
    working_values: tuple[tuple[Any, ...], ...]
    parts: tuple[SourcePart, ...]
    component_rows: tuple[ComponentSourceRow, ...]
    issues: tuple[QualityIssue, ...]
    diagnostics: Mapping[str, object]


def read_production_source(path: str | Path) -> SourceIntakeResult:
    inspected = inspect_production_input(Path(path))
    adapter = _select_adapter(inspected)
    return adapter.read(inspected)
```

Workbook selection must score both canonical and initial-table signatures over the
first 100 rows. Equal complete winners are rejected with candidate diagnostics.
Move reusable canonicalization from `reader.py` behind the standard adapter.

- [ ] **Step 4: Route the pipeline through Source Intake**

```python
def run_auto_pipeline(input_file, output_file=None, *, internal_output_file=None):
    intake = read_production_source(input_file)
    return _run_intake(
        intake,
        output_file,
        internal_output_file=internal_output_file,
    )
```

Keep `run_pipeline` and `run_init_pipeline` as thin compatibility wrappers until
backend migration is complete.

- [ ] **Step 5: Run focused and existing reader tests**

Run:

```bash
pytest tests/test_source_intake.py tests/test_reader_canonical.py tests/test_pipeline_end_to_end.py -q
```

Expected: all tests pass and B7 row identities remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add Stages/excel_final/source_intake.py Stages/excel_final/reader.py \
  Stages/excel_final/pipeline.py Stages/excel_final/tests/test_source_intake.py
git commit -m "feat(excel-final): add unified production source intake"
```

## Task 3: Make initial and text adapters structure-aware

**Files:**
- Modify: `Stages/excel_final/reader_init.py`
- Modify: `Stages/excel_final/source_intake.py`
- Test: `Stages/excel_final/tests/test_source_intake.py`
- Test: `Stages/excel_final/tests/test_reader_canonical.py`

- [ ] **Step 1: Add failing dynamic-initial and fixed-width tests**

```python
def test_initial_table_metadata_and_header_can_move(tmp_path):
    path = build_initial_workbook(
        tmp_path,
        metadata_row=4,
        header_row=7,
        blank_rows=(1, 2, 3, 5, 6),
    )
    result = read_production_source(path)
    assert result.parts[0].source_row == 8


def test_fixed_width_blank_spec_does_not_shift_columns(tmp_path):
    path = write_fixed_width_tekla(
        tmp_path,
        rows=[{"零件编号": "M20", "型材": "", "材质": "C", "长度": "90", "数量": "2"}],
    )
    result = read_production_source(path)
    part = result.parts[0]
    assert part.part_no == "M20"
    assert part.original_spec == ""
    assert part.material == "C"
    assert part.length == Decimal("90")
    assert part.original_qty == Decimal("2")
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
pytest tests/test_source_intake.py -q
```

Expected: moving initial rows or blank fixed-width columns are parsed incorrectly.

- [ ] **Step 3: Implement dynamic initial-table discovery**

```python
def detect_initial_layout(worksheet) -> InitialLayout:
    candidates = score_initial_headers(worksheet, max_rows=100)
    header = unique_complete_candidate(candidates)
    metadata = find_component_metadata(worksheet, before_row=header.row_number)
    return InitialLayout(
        header_row=header.row_number,
        metadata_row=metadata.row_number,
        columns=header.columns,
    )
```

Use mapped columns rather than `range(1, 10)`. Preserve actual worksheet row
numbers in every `SourcePart`.

- [ ] **Step 4: Implement delimited and fixed-width parsing**

```python
def _text_layout(lines: Sequence[str]) -> TextLayout:
    header_index = locate_header_line(lines)
    header = lines[header_index]
    if "\t" in header:
        return DelimitedLayout(header_index, "\t")
    spans = tuple(header_spans_from_known_labels(header))
    validate_non_overlapping_spans(spans)
    return FixedWidthLayout(header_index, spans)
```

Fixed-width rows must be sliced by character spans. Never split a business row
with `re.split(r"\s+")`.

- [ ] **Step 5: Run adapter regression tests**

Run:

```bash
pytest tests/test_source_intake.py tests/test_reader_canonical.py tests/test_preprocess.py -q
```

Expected: all tests pass, including bolt rows with blank specs.

- [ ] **Step 6: Commit**

```bash
git add Stages/excel_final/reader_init.py Stages/excel_final/source_intake.py \
  Stages/excel_final/tests/test_source_intake.py \
  Stages/excel_final/tests/test_reader_canonical.py
git commit -m "feat(excel-final): adapt dynamic and fixed-width sources"
```

## Task 4: Centralize fabricated-profile geometry

**Files:**
- Create: `Stages/excel_final/fabricated_profile.py`
- Modify: `Stages/excel_final/spec_parser.py`
- Modify: `Stages/excel_final/splitter.py`
- Modify: `Stages/excel_final/weights.py`
- Modify: `Stages/excel_final/writer_parts.py`
- Test: `Stages/excel_final/tests/test_fabricated_profile.py`
- Test: `Stages/excel_final/tests/test_splitter.py`
- Test: `Stages/excel_final/tests/test_weights.py`
- Test: `Stages/excel_final/tests/test_writer_workbook.py`

- [ ] **Step 1: Write failing three/four-parameter tests**

```python
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("BOX1000*1000*60", ("BOX", D("1000"), D("1000"), D("60"), D("60"))),
        ("BOX1000*1000*40*60", ("BOX", D("1000"), D("1000"), D("40"), D("60"))),
        ("BH500*300*12*20", ("BH", D("500"), D("300"), D("12"), D("20"))),
    ],
)
def test_parse_fabricated_profile(spec, expected):
    profile = parse_fabricated_profile(spec)
    assert (profile.kind, profile.height, profile.width, profile.web, profile.flange) == expected
```

Add consistency assertions that splitter rows, theoretical unit weight, and
writer formulas use the same parsed dimensions.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
pytest tests/test_fabricated_profile.py tests/test_splitter.py tests/test_weights.py -q
```

Expected: BOX three-parameter parsing fails in the canonical path.

- [ ] **Step 3: Implement the geometry value object**

```python
@dataclass(frozen=True, slots=True)
class FabricatedProfile:
    kind: SplitPolicy
    height: Decimal
    width: Decimal
    web: Decimal
    flange: Decimal

    @property
    def web_plate_width(self) -> Decimal:
        return self.height - self.flange * 2


def parse_fabricated_profile(spec: object) -> FabricatedProfile | None:
    match = _FABRICATED_RE.fullmatch(_compact(spec))
    if not match:
        return None
    dimensions = tuple(Decimal(item) for item in match.group("dims").split("*"))
    kind = SplitPolicy(match.group("kind").upper())
    if kind is SplitPolicy.BOX and len(dimensions) == 3:
        height, width, thickness = dimensions
        web, flange = thickness, thickness
    elif len(dimensions) == 4:
        height, width, web, flange = dimensions
    else:
        raise FabricatedProfileError(f"{kind.value} 规格参数数量非法")
    if min(height, width, web, flange) <= 0:
        raise FabricatedProfileError("组合截面尺寸必须为正数")
    return FabricatedProfile(kind, height, width, web, flange)
```

Replace all independent BH/BOX/BT regular expressions in production modules
with this function. Invalid/non-positive geometry produces the existing severe
split issue rather than a guessed result.

- [ ] **Step 4: Run fabricated-profile and formula tests**

Run:

```bash
pytest tests/test_fabricated_profile.py tests/test_splitter.py tests/test_weights.py \
  tests/test_writer_workbook.py -q
```

Expected: all tests pass and formula caches remain valid.

- [ ] **Step 5: Commit**

```bash
git add Stages/excel_final/fabricated_profile.py Stages/excel_final/spec_parser.py \
  Stages/excel_final/splitter.py Stages/excel_final/weights.py \
  Stages/excel_final/writer_parts.py Stages/excel_final/tests
git commit -m "refactor(excel-final): centralize fabricated profile geometry"
```

## Task 5: Aggregate the actionable report without losing row evidence

**Files:**
- Modify: `Stages/excel_final/quality.py`
- Modify: `Stages/excel_final/writer_parts.py`
- Test: `Stages/excel_final/tests/test_domain_quality.py`
- Test: `Stages/excel_final/tests/test_writer_workbook.py`

- [ ] **Step 1: Write failing aggregation tests**

```python
def test_report_groups_repeated_lookup_misses_by_action_signature():
    ledger = QualityLedger([
        lookup_issue(row=10, spec="D8", material="Q235B"),
        lookup_issue(row=11, spec="D8", material="Q235B"),
        lookup_issue(row=12, spec="D12", material="Q235B"),
    ])
    rows = ledger.actionable_report_rows()
    assert len(rows) == 2
    assert rows[0]["影响行数"] == 2
    assert "10、11" in rows[0]["来源位置"]
```

Also assert that different severity, field, material, or recommendation does not
merge; no-issue output remains `A2=无`.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
pytest tests/test_domain_quality.py tests/test_writer_workbook.py -q
```

Expected: current per-source grouping returns three report rows.

- [ ] **Step 3: Implement actionable signature grouping**

```python
@dataclass(frozen=True, slots=True)
class ActionableIssueGroup:
    level: IssueLevel
    category: str
    spec: str | None
    material: str | None
    field: str | None
    action: str
    issues: tuple[QualityIssue, ...]
```

The report remains the existing eight columns. Put `影响 N 行；代表来源 10、11 等`
in `说明`/`来源位置`; do not add a new public workbook or protocol column.
`warning_count`, `severe_warning_count`, and category totals equal grouped report
rows so Stage output and backend workbook re-import stay identical.

- [ ] **Step 4: Run quality and backend import contract tests**

Run:

```bash
pytest Stages/excel_final/tests/test_domain_quality.py \
  Stages/excel_final/tests/test_writer_workbook.py \
  backend/tests/excel_processing/test_excel_final_quality.py \
  backend/tests/excel_processing/test_excel_final_import.py -q
```

Expected: all tests pass and counts match report rows.

- [ ] **Step 5: Commit**

```bash
git add Stages/excel_final/quality.py Stages/excel_final/writer_parts.py \
  Stages/excel_final/tests/test_domain_quality.py \
  Stages/excel_final/tests/test_writer_workbook.py
git commit -m "feat(excel-final): condense actionable quality reports"
```

## Task 6: Remove duplicate entry-point detection

**Files:**
- Modify: `Stages/excel_final/main.py`
- Modify: `backend/app/modules/excel_processing/staging.py`
- Modify: `backend/app/modules/excel_processing/stage_runner.py`
- Modify: `backend/app/modules/excel_processing/execution.py`
- Test: `Stages/excel_final/tests/test_cli.py`
- Test: `backend/tests/excel_processing/test_excel_final_adapter.py`
- Test: `backend/tests/excel_processing/test_excel_final_idempotency.py`

- [ ] **Step 1: Write failing auto-entry tests**

```python
def test_stage_runner_processes_without_backend_format_guess(monkeypatch, source):
    args = process_args(source, format=None)
    result = invoke_stage(args)
    assert result["operation"] == "process"


def test_backend_stages_source_without_opening_workbook(source, monkeypatch):
    monkeypatch.setattr(openpyxl, "load_workbook", forbidden)
    staged, _ = stage_excel_source(db, source.id, work_dir)
    assert staged.is_file()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
pytest Stages/excel_final/tests/test_cli.py \
  backend/tests/excel_processing/test_excel_final_adapter.py -q
```

Expected: CLI/runner still require the duplicated format value.

- [ ] **Step 3: Route both entry points through `run_auto_pipeline`**

```python
from pipeline import run_auto_pipeline

result = run_auto_pipeline(
    args.input.resolve(),
    args.output.resolve(),
    internal_output_file=args.internal_output.resolve(),
)
```

Delete `_INIT_TABLE_SIGNATURE`, `detect_source_format`, Stage `detect_format`,
and their unused openpyxl imports after all callers are migrated. Staging owns
only safe file resolution/download.

- [ ] **Step 4: Run Stage/backend entry tests**

Run:

```bash
pytest Stages/excel_final/tests/test_cli.py \
  backend/tests/excel_processing/test_excel_final_adapter.py \
  backend/tests/excel_processing/test_excel_final_idempotency.py -q
```

Expected: all tests pass with one Stage-owned detection path.

- [ ] **Step 5: Commit**

```bash
git add Stages/excel_final/main.py backend/app/modules/excel_processing \
  Stages/excel_final/tests/test_cli.py backend/tests/excel_processing
git commit -m "refactor(excel-final): make stage own source detection"
```

## Task 7: Real-corpus and end-to-end acceptance

**Files:**
- Create: `Stages/excel_final/tests/test_source_corpus.py`
- Modify: `Stages/excel_final/README.md`
- Modify: `Stages/excel_final/PROCESS.md`
- Modify: `backend/app/modules/excel_processing/README.md`

- [ ] **Step 1: Add corpus acceptance tests**

```python
@pytest.mark.real_corpus
def test_all_historical_part_lists_enter_source_intake(historical_part_lists):
    results = [read_production_source(path) for path in historical_part_lists]
    assert len(results) == 11
    assert all(result.parts for result in results)


@pytest.mark.real_corpus
def test_component_only_lists_are_explicitly_rejected(component_only_lists):
    for path in component_only_lists:
        with pytest.raises(InputContractError, match="没有零件明细"):
            read_production_source(path)
```

- [ ] **Step 2: Run B7 and 003 with real MySQL**

Run:

```bash
cd Stages/excel_final
pytest tests/test_ground_truth_regression.py tests/test_source_corpus.py -q
python main.py "data/003郑州宝冶-构件零件清单(毛净重)去构造板.xlsx" \
  -o "data/results/003郑州宝冶-excel-final-规范结果.xlsx"
```

Expected:

- B7: 22 comparison rules pass.
- 003: 6892 parent parts and 688 components are retained.
- D8/Q235B and D12/Q235B remain explicit handbook misses but collapse to two
  actionable report groups.
- Final deleted columns stay absent; visible formulas and caches remain present.

- [ ] **Step 3: Run complete Stage and backend suites**

Run:

```bash
pytest Stages/excel_final/tests -q
pytest backend/tests/excel_processing -q
DWG_RUN_LIVE_EXCEL_FINAL=1 pytest \
  backend/tests/excel_processing/test_excel_final_live_flow.py -q
ruff check Stages/excel_final backend/app/modules/excel_processing \
  backend/tests/excel_processing
```

Expected: all enabled tests and lint checks pass; skipped tests are only
environment-gated tests documented by their markers.

- [ ] **Step 4: Inspect every output sheet and performance**

Read B7 and 003 outputs twice with openpyxl (`data_only=False` and `True`).
Assert:

```python
assert workbook.sheetnames == ["原表", "清洗表", "构件表", "整理表", "part", "处理报告"]
assert deleted_organized_headers.isdisjoint(organized_headers)
assert "类型" not in part_headers
assert formula_count["整理表"] > 0
assert formula_count["part"] > 0
assert all_formula_caches_are_finite
```

Record 003 elapsed time and reject any new quadratic cache/header scan.

- [ ] **Step 5: Remove dead code and update documentation**

Use:

```bash
rg -n "detect_source_format|detect_format|_INIT_TABLE_SIGNATURE|read_init_pipeline" \
  Stages/excel_final backend
```

Delete only compatibility paths with zero production/test callers. Document the
four adapters, explicit component-only rejection, BOX shorthand, report grouping,
formula/cache contract, and real acceptance commands.

- [ ] **Step 6: Final commit and push**

```bash
git diff --check
git status --short
git add Stages/excel_final backend/app/modules/excel_processing \
  backend/tests/excel_processing docs CONTEXT.md
git commit -m "feat(excel-final): generalize production source processing"
git fetch origin
git rebase origin/main
git push origin main
git status --short
```

Expected: tracked work is committed, generated data remains untracked, and local
`HEAD` equals `origin/main`.
