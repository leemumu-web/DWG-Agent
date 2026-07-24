# Workflow Excel Stage One Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. This repository task must be executed inline; do not dispatch subagents unless the user explicitly changes that instruction. Steps use checkbox syntax.

**Goal:** Make the existing Excel processing pipeline the single Excel first-stage operation in Linux production workflows, reject invalid source tables with specific actionable errors, and provide clear standalone and workflow user interfaces without putting DXF→Excel in the main workflow.

**Architecture:** The standalone `Stages/excel_final` package owns one versioned input-inspection contract and emits bounded JSON result/error records over its child-process boundary. The backend reuses that exact contract for immediate upload validation, freeze-time revalidation, worker execution, and structured failure persistence. Linux workflows resolve the frozen `source_excel` artifact internally and dispatch the existing Excel job without asking the browser for a file ID. The frontend consumes one failure shape in both the Excel work center and a dedicated workflow detail console.

**Tech Stack:** Python 3.12, pytest, FastAPI, SQLAlchemy 2, Alembic, Celery, MySQL, React 19, TypeScript 6, Ant Design 6, TanStack Query 5, Playwright.

---

## Invariants to Preserve

- The standalone `/files/dxf2excel` tool remains available, but no `linux_production` stage dispatches `dxf_to_excel`.
- `linux_production` has nine stages and one Excel stage named `excel_stage1`; there is no later duplicate `excel_final` stage.
- `excel_stage1` reads the unique frozen `source_excel` from the workflow input batch. Its execute request contains only `execution_kind`.
- Upload validation, freeze validation, and worker validation use the same Stage-owned inspection logic and contract version.
- Input failures are operator-fixable and return bounded, sanitized details. Runtime failures remain separate from input failures.
- Quality warnings produced after successful processing do not turn a job into a failed job; they remain in the workbook report and persisted quality summary.
- Invalid workflow Excel registrations remain visible as failed input items, while the HTTP request returns a structured 422 after the database transaction commits.
- Existing `error_code` and `error_message` fields remain populated for compatibility; the complete structured object is stored alongside them.
- No absolute paths, tracebacks, database credentials, storage keys, or unbounded sheet/row dumps enter HTTP responses.
- Standalone Excel output behavior and the existing authoritative MySQL hardware-handbook query path remain unchanged except for clearer lookup inputs and errors.

## Canonical Failure Shape

All boundaries use this logical payload:

```json
{
  "code": "EXCEL_INPUT_REQUIRED_COLUMNS_MISSING",
  "message": "表格缺少 Excel 第一阶段所需列。",
  "action": "请在唯一工作表中补充：零件号、材质。",
  "contract_version": 1,
  "issues": [
    {
      "sheet": "Sheet1",
      "row": 7,
      "column": null,
      "field": "零件号",
      "value": null,
      "reason": "required_column_missing"
    }
  ],
  "sheets": ["Sheet1"],
  "meta": {
    "missing_fields": ["零件号", "材质"]
  }
}
```

Limits:

- At most 20 `issues`; include `issue_count` and `issues_truncated` in `meta` when more exist.
- At most 10 worksheet names; include `sheet_count` and `sheets_truncated` in `meta` when more exist.
- `value` is a short display value only, capped before serialization.
- HTTP errors use the normal application envelope and put this payload in `details.failure`.
- Async Jobs use `progress_data.failure`.
- Workflow stage runs use `output_json.failure` and mirror `code/message` into legacy columns.

Input codes:

```text
EXCEL_INPUT_EMPTY
EXCEL_INPUT_UNSUPPORTED_EXTENSION
EXCEL_INPUT_UNREADABLE
EXCEL_INPUT_NO_WORKSHEET
EXCEL_INPUT_MULTIPLE_WORKSHEETS
EXCEL_INPUT_HEADER_NOT_FOUND
EXCEL_INPUT_HEADER_AMBIGUOUS
EXCEL_INPUT_DUPLICATE_COLUMNS
EXCEL_INPUT_REQUIRED_COLUMNS_MISSING
EXCEL_INPUT_COMPONENT_ONLY
EXCEL_INPUT_SCHEMA_AMBIGUOUS
EXCEL_INPUT_TEXT_ENCODING_UNSUPPORTED
EXCEL_INPUT_BINARY_XLS_UNSUPPORTED
EXCEL_INPUT_ROW_VALUE_INVALID
EXCEL_INPUT_PART_WITHOUT_COMPONENT
EXCEL_INPUT_OBJECT_CHANGED
```

Runtime codes:

```text
EXCEL_STAGE1_UNAVAILABLE
EXCEL_STAGE1_HANDBOOK_UNAVAILABLE
EXCEL_STAGE1_TIMEOUT
EXCEL_STAGE1_OUTPUT_MISSING
EXCEL_STAGE1_IMPORT_FAILED
EXCEL_STAGE1_STORAGE_FAILED
EXCEL_STAGE1_INTERNAL_ERROR
```

## Task 1: Add a Stage-Owned Structured Input Failure Contract

**Files:**

- Create: `Stages/excel_final/input_errors.py`
- Modify: `Stages/excel_final/input_contract.py`
- Modify: `Stages/excel_final/source_intake.py`
- Modify: `Stages/excel_final/reader_init.py`
- Modify: `Stages/excel_final/reader.py`
- Test: `Stages/excel_final/tests/test_input_contract.py`
- Test: `Stages/excel_final/tests/test_source_intake.py`

- [ ] **Step 1: Write failing tests for stable codes and bounded diagnostics**

Add focused cases that construct:

- an empty input;
- an unsupported suffix;
- a workbook with two sheets;
- no recognizable header;
- two equally valid header rows;
- duplicate header aliases;
- a component-only table without `零件号`;
- missing required fields;
- a binary OLE `.xls`;
- invalid row values already rejected by the production reader.

The assertions must check the code, Chinese message/action, contract version, issue locations, and bounds rather than matching ad-hoc exception strings.

```python
def test_missing_columns_are_structured(workbook_factory):
    source = workbook_factory(headers=["构件编号", "规格", "数量"])

    with pytest.raises(InputContractError) as caught:
        read_production_source(source)

    failure = caught.value.failure
    assert failure.code == "EXCEL_INPUT_REQUIRED_COLUMNS_MISSING"
    assert failure.contract_version == 1
    assert failure.meta["missing_fields"] == ["零件号", "零件长度", "材质"]
    assert all(issue.sheet == "Sheet1" for issue in failure.issues)
    assert len(failure.issues) <= 20
```

Run:

```bash
cd Stages/excel_final
uv run pytest -q tests/test_input_contract.py tests/test_source_intake.py
```

Expected: FAIL because `InputContractError` currently only contains free-form text.

- [ ] **Step 2: Implement immutable failure records and safe serialization**

Use a small domain record, not FastAPI or backend imports:

```python
from dataclasses import asdict, dataclass, field

INPUT_CONTRACT_VERSION = 1
MAX_ISSUES = 20
MAX_SHEETS = 10

@dataclass(frozen=True, slots=True)
class ExcelInputIssue:
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    field: str | None = None
    value: str | None = None
    reason: str = ""

@dataclass(frozen=True, slots=True)
class ExcelInputFailure:
    code: str
    message: str
    action: str
    issues: tuple[ExcelInputIssue, ...] = ()
    sheets: tuple[str, ...] = ()
    meta: Mapping[str, object] = field(default_factory=dict)
    contract_version: int = INPUT_CONTRACT_VERSION

    def as_dict(self) -> dict[str, object]:
        issues = self.issues[:MAX_ISSUES]
        sheets = self.sheets[:MAX_SHEETS]
        meta = dict(self.meta)
        meta.update({
            "issue_count": len(self.issues),
            "issues_truncated": len(self.issues) > MAX_ISSUES,
            "sheet_count": len(self.sheets),
            "sheets_truncated": len(self.sheets) > MAX_SHEETS,
        })
        return {
            "code": self.code,
            "message": self.message,
            "action": self.action,
            "contract_version": self.contract_version,
            "issues": [asdict(issue) for issue in issues],
            "sheets": list(sheets),
            "meta": meta,
        }

class InputContractError(ValueError):
    def __init__(self, failure: ExcelInputFailure):
        super().__init__(failure.message)
        self.failure = failure
```

Centralize constructors for header, workbook/container, encoding, and row failures. Do not copy raw library exception text into the public payload.

- [ ] **Step 3: Replace every production-input `ValueError`/string-only contract error**

Map each existing rejection to exactly one code. Preserve useful candidate scoring only as bounded machine-readable metadata. For `.xls`:

- accept Tekla text export only after the source-intake detector confirms text;
- reject OLE/BIFF binary content with `EXCEL_INPUT_BINARY_XLS_UNSUPPORTED`;
- tell the operator to save it as a one-sheet `.xlsx` or export Tekla text;
- do not add a second `xlrd` interpretation path.

- [ ] **Step 4: Run Stage input tests**

```bash
cd Stages/excel_final
uv run pytest -q tests/test_input_contract.py tests/test_source_intake.py tests/test_reader_canonical.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Stages/excel_final/input_errors.py \
  Stages/excel_final/input_contract.py \
  Stages/excel_final/source_intake.py \
  Stages/excel_final/reader_init.py \
  Stages/excel_final/reader.py \
  Stages/excel_final/tests/test_input_contract.py \
  Stages/excel_final/tests/test_source_intake.py
git commit -m "feat(excel-stage1): structure input validation failures"
```

## Task 2: Add a Side-Effect-Free Stage Inspection Command and Versioned Error Protocol

**Files:**

- Modify: `backend/app/modules/excel_processing/stage_runner.py`
- Modify: `Stages/excel_final/tests/test_cli.py`
- Create: `Stages/excel_final/tests/test_inspection.py`

- [ ] **Step 1: Write failing CLI tests**

Cover:

- `inspect` succeeds on a canonical workbook and reports sheet/header/row information;
- `inspect` fails with one `DWG_EXCEL_FINAL_ERROR=<json>` record;
- `inspect` does not require hardware-handbook environment variables;
- malformed input never prints a traceback or absolute path in the sentinel payload;
- result/error protocol version is checked.

```python
def test_inspect_reports_actionable_missing_columns(stage_runner, bad_workbook):
    completed = stage_runner("inspect", "--input", str(bad_workbook))
    assert completed.returncode == 2
    payload = sentinel_json(completed.stdout, "DWG_EXCEL_FINAL_ERROR=")
    assert payload["operation"] == "inspect"
    assert payload["failure"]["code"] == "EXCEL_INPUT_REQUIRED_COLUMNS_MISSING"
    assert str(bad_workbook.resolve()) not in completed.stdout
```

Run:

```bash
cd Stages/excel_final
uv run pytest -q tests/test_cli.py tests/test_inspection.py
```

Expected: FAIL because `inspect` and the error sentinel do not exist.

- [ ] **Step 2: Add `inspect` parsing without handbook configuration**

Split Stage path setup from database setup:

```python
_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="
_ERROR_PREFIX = "DWG_EXCEL_FINAL_ERROR="
_PROTOCOL_VERSION = 1

def _inspect(args: argparse.Namespace) -> None:
    _configure_stage_imports(args.stage_root)
    from source_intake import read_production_source

    source = read_production_source(args.input.resolve())
    _emit_result({
        "protocol_version": _PROTOCOL_VERSION,
        "operation": "inspect",
        "input_contract_version": INPUT_CONTRACT_VERSION,
        "source_format": source.source_format.value,
        "sheet_name": source.sheet_name,
        "header_row": int(source.diagnostics["header_row"]),
        "part_count": len(source.parts),
        "component_count": len(source.component_rows),
    })
```

Only `process` and `lookup` call `_configure_handbook_database`. Wrap `main()` so a typed `InputContractError` emits exactly one bounded JSON error record and exits 2. Unexpected exceptions stay on stderr for server logs and do not masquerade as operator input errors.

- [ ] **Step 3: Reuse the real reader**

Inspection must call the same `read_production_source` path used by `run_auto_pipeline`. It must not calculate weights, query MySQL, create output workbooks, or write files.

- [ ] **Step 4: Run Stage CLI and regression tests**

```bash
cd Stages/excel_final
uv run pytest -q tests/test_cli.py tests/test_inspection.py tests/test_pipeline_end_to_end.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/excel_processing/stage_runner.py \
  Stages/excel_final/tests/test_cli.py \
  Stages/excel_final/tests/test_inspection.py
git commit -m "feat(excel-stage1): add side-effect-free input inspection"
```

## Task 3: Teach the Backend Adapter the Same Inspection and Failure Contract

**Files:**

- Modify: `backend/app/modules/excel_processing/stage_adapter.py`
- Modify: `backend/app/modules/excel_processing/interface.py`
- Modify: `backend/app/modules/excel_processing/schemas.py`
- Test: `backend/tests/excel_processing/test_excel_final_adapter.py`
- Test: `backend/tests/architecture/test_excel_processing_boundaries.py`

- [ ] **Step 1: Write failing adapter tests**

Test a successful inspect result, a structured child input error, duplicate sentinels, malformed JSON, wrong protocol version, timeout, and redaction.

```python
def test_inspect_failure_survives_process_boundary(monkeypatch, source_path):
    completed = CompletedProcess(
        args=[],
        returncode=2,
        stdout=(
            'DWG_EXCEL_FINAL_ERROR={"protocol_version":1,"operation":"inspect",'
            '"failure":{"code":"EXCEL_INPUT_COMPONENT_ONLY",'
            '"message":"输入只有构件汇总。","action":"请导出零件明细表。",'
            '"contract_version":1,"issues":[],"sheets":["Sheet1"],"meta":{}}}\n'
        ),
        stderr="",
    )
    monkeypatch.setattr(stage_adapter, "_run_stage", lambda *args: completed)

    with pytest.raises(ExcelFinalInputError) as caught:
        inspect_excel_stage1_path(source_path)

    assert caught.value.failure.code == "EXCEL_INPUT_COMPONENT_ONLY"
```

Run:

```bash
cd backend
uv run pytest -q tests/excel_processing/test_excel_final_adapter.py \
  tests/architecture/test_excel_processing_boundaries.py
```

Expected: FAIL because only process/lookup and generic process errors are supported.

- [ ] **Step 2: Add typed backend result/error records**

Add:

```python
@dataclass(frozen=True, slots=True)
class ExcelStage1Inspection:
    protocol_version: int
    input_contract_version: int
    source_format: str
    sheet_name: str | None
    header_row: int
    part_count: int
    component_count: int

class ExcelFinalInputError(ExcelFinalIntegrationError):
    def __init__(self, failure: ExcelInputFailurePayload):
        super().__init__(failure.message)
        self.failure = failure
```

Parse and validate exact fields for `operation=inspect`. Reject malformed/duplicated sentinels as runtime integration errors. Export only the supported function and result/error types through `excel_processing.interface`.

- [ ] **Step 3: Provide byte inspection for workflow storage**

The public backend boundary accepts `(file_name, payload, expected_sha256=None)`, writes a private temporary file with the original safe suffix, invokes Stage `inspect`, and always removes it. If `expected_sha256` is provided, compare before inspection and return `EXCEL_INPUT_OBJECT_CHANGED` on mismatch.

No workflow module may import `stage_adapter` or Stage modules directly.

- [ ] **Step 4: Run adapter/boundary tests**

```bash
cd backend
uv run pytest -q tests/excel_processing/test_excel_final_adapter.py \
  tests/architecture/test_excel_processing_boundaries.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/excel_processing/stage_adapter.py \
  backend/app/modules/excel_processing/interface.py \
  backend/app/modules/excel_processing/schemas.py \
  backend/tests/excel_processing/test_excel_final_adapter.py \
  backend/tests/architecture/test_excel_processing_boundaries.py
git commit -m "feat(excel-stage1): expose canonical backend inspection"
```

## Task 4: Preflight Standalone Uploads and Persist Async Failures

**Files:**

- Modify: `backend/app/modules/excel_processing/routes/processing.py`
- Modify: `backend/app/modules/excel_processing/uploads.py`
- Modify: `backend/app/modules/excel_processing/execution.py`
- Modify: `backend/app/modules/excel_processing/presentation.py`
- Modify: `backend/app/modules/excel_processing/schemas.py`
- Test: `backend/tests/excel_processing/test_excel_final_import.py`
- Test: `backend/tests/excel_processing/test_excel_final_quality.py`
- Test: `backend/tests/excel_processing/test_excel_final_retry.py`
- Test: `backend/tests/excel_processing/test_excel_final_live_flow.py`

- [ ] **Step 1: Write failing endpoint and worker tests**

Required behavior:

- `/excel-final/upload` and `/upload-and-process` inspect before accepting the operation;
- `/process?file_id=...` re-reads and inspects the stored object before job creation;
- invalid requests return 422 with `details.failure`;
- no Job is created for a synchronous input rejection;
- worker-side revalidation catches an object changed after preflight;
- `Job.progress_data.failure` is populated for typed input and runtime failures;
- retries clear a previous failure before running;
- successful quality warnings still return a successful Job.

```python
def test_upload_and_process_returns_table_error(client, auth_headers, invalid_excel):
    response = client.post(
        "/api/v1/excel-final/upload-and-process",
        headers=auth_headers,
        files={"upload": ("bad.xlsx", invalid_excel, EXCEL_MIME)},
    )
    assert response.status_code == 422
    failure = response.json()["details"]["failure"]
    assert failure["code"] == "EXCEL_INPUT_REQUIRED_COLUMNS_MISSING"
    assert failure["action"]
    assert db.scalar(select(func.count(Job.id))) == 0
```

Run:

```bash
cd backend
uv run pytest -q tests/excel_processing/test_excel_final_import.py \
  tests/excel_processing/test_excel_final_quality.py \
  tests/excel_processing/test_excel_final_retry.py
```

Expected: FAIL.

- [ ] **Step 2: Add one HTTP translation helper**

Translate `ExcelFinalInputError` into:

```python
raise AppHTTPException(
    422,
    failure.code,
    failure.message,
    {"failure": failure.as_dict()},
)
```

Do not duplicate code-to-message maps in routes.

- [ ] **Step 3: Revalidate in execution and persist failure state**

Before `run_excel_final_pipeline`, re-read the verified storage object and run inspection. Map known runtime points to the runtime code list. Store:

```python
job.progress_data = {
    **safe_existing_progress,
    "failure": failure.as_dict(),
}
job.error_code = failure.code
job.error_message = failure.message
```

Ensure `process_status()` exposes `failure` while keeping old fields.

- [ ] **Step 4: Run standalone API tests**

```bash
cd backend
uv run pytest -q tests/excel_processing
```

Expected: PASS, with the live marker skipped unless explicitly enabled.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/excel_processing/routes/processing.py \
  backend/app/modules/excel_processing/uploads.py \
  backend/app/modules/excel_processing/execution.py \
  backend/app/modules/excel_processing/presentation.py \
  backend/app/modules/excel_processing/schemas.py \
  backend/tests/excel_processing
git commit -m "feat(excel-stage1): preflight uploads and persist failures"
```

## Task 5: Persist Workflow Excel Validation and Revalidate on Freeze

**Files:**

- Modify: `backend/app/modules/workflows/models/intake.py`
- Modify: `backend/app/modules/workflows/schemas/intake.py`
- Modify: `backend/app/modules/workflows/intake/registration.py`
- Modify: `backend/app/modules/workflows/intake/freeze.py`
- Modify: `backend/app/modules/workflows/intake/presentation.py`
- Modify: `backend/app/modules/workflows/routes/intake.py`
- Create: `backend/migrations/versions/4e7c2a9b1d30_add_workflow_excel_validation.py`
- Test: `backend/tests/workflows/test_workflow_input_service.py`
- Test: `backend/tests/workflows/test_workflow_input_api.py`
- Test: `backend/tests/infrastructure/test_migrations.py`

- [ ] **Step 1: Write failing workflow registration tests**

Cover:

- valid Excel item stores validation, contract version, and validated SHA-256;
- invalid Excel item is persisted with `status=failed`, legacy fields, and structured validation;
- route commits the failed item and then returns 422;
- repeat registration is deterministic;
- freeze rejects a failed item;
- freeze detects content/SHA change and stores `EXCEL_INPUT_OBJECT_CHANGED`;
- DWG registration behavior is unchanged.

```python
def test_invalid_excel_is_recorded_before_422(client, workflow, bad_file):
    response = register_file(client, workflow.id, bad_file.id)
    assert response.status_code == 422
    item = persisted_item(workflow.id, bad_file.id)
    assert item.status == "failed"
    assert item.error_code == "EXCEL_INPUT_COMPONENT_ONLY"
    assert item.validation_json["failure"]["action"]
```

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_service.py \
  tests/workflows/test_workflow_input_api.py
```

Expected: FAIL.

- [ ] **Step 2: Add nullable validation columns**

Add:

```python
validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
validation_contract_version: Mapped[int | None] = mapped_column(Integer)
validated_sha256: Mapped[str | None] = mapped_column(String(64))
```

Set `revision = "4e7c2a9b1d30"` and `down_revision = "7c4d9e2a1b60"`, then verify immediately before implementation that `7c4d9e2a1b60` is still the single Alembic head. If it is not, stop and rebase the migration rather than creating a second head. Update model/schema/presentation together.

- [ ] **Step 3: Return a registration outcome instead of raising before persistence**

`register_input_file()` returns an outcome containing the item and optional failure. The route:

1. registers the item;
2. writes the audit record;
3. commits;
4. raises the safe 422 if the outcome contains a failure.

This avoids FastAPI session rollback deleting the failed item. A valid outcome returns normally.

- [ ] **Step 4: Replace `openpyxl`/`xlrd` shallow validation**

Delete `validate_excel_payload`. Call `excel_processing.interface.inspect_excel_stage1_bytes` during registration and freeze. Freeze requires:

- one `source_excel`;
- item status not failed;
- current object SHA equals `validated_sha256`;
- current inspection contract version equals the stored version;
- current inspection succeeds.

Only then attach the immutable `source_excel` artifact and complete `source_intake`.

- [ ] **Step 5: Verify migrations and workflow input tests**

```bash
cd backend
uv run alembic heads
uv run pytest -q tests/infrastructure/test_migrations.py \
  tests/workflows/test_workflow_input_service.py \
  tests/workflows/test_workflow_input_api.py
```

Expected: one Alembic head and all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/workflows/models/intake.py \
  backend/app/modules/workflows/schemas/intake.py \
  backend/app/modules/workflows/intake/registration.py \
  backend/app/modules/workflows/intake/freeze.py \
  backend/app/modules/workflows/intake/presentation.py \
  backend/app/modules/workflows/routes/intake.py \
  backend/migrations/versions/*_add_workflow_excel_validation.py \
  backend/tests/workflows/test_workflow_input_service.py \
  backend/tests/workflows/test_workflow_input_api.py \
  backend/tests/infrastructure/test_migrations.py
git commit -m "feat(workflows): persist canonical Excel validation"
```

## Task 6: Make Excel Processing the Only Main-Workflow Excel Stage

**Files:**

- Modify: `backend/app/modules/workflows/templates.py`
- Modify: `backend/app/modules/workflows/schemas/orchestration.py`
- Modify: `backend/app/modules/workflows/stage_execution.py`
- Modify: `backend/app/modules/workflows/job_sync.py`
- Modify: `backend/app/platform/config/constants.py` only if a display alias is required; keep the existing Celery task type where practical
- Create: `backend/migrations/versions/5f8d3b0c2e41_normalize_linux_excel_stage.py`
- Test: `backend/tests/workflows/test_workflow_framework.py`
- Test: `backend/tests/workflows/test_workflow_api.py`
- Test: `backend/tests/workflows/test_workflow_production.py`
- Test: `backend/tests/workflows/test_workflow_boundaries.py`
- Test: `backend/tests/contracts/test_stage1_boundaries.py`
- Test: `backend/tests/infrastructure/test_migrations.py`

- [ ] **Step 1: Write failing template and execution tests**

Assert the exact nine-stage order:

```python
assert [stage.code for stage in linux_template.stages] == [
    "source_intake",
    "dxf_classification",
    "drawing_processing",
    "excel_stage1",
    "design_barrier",
    "cam_packaging",
    "windows_cam",
    "result_acceptance",
    "delivery_archive",
]
```

Also assert:

- `excel_stage1.execution_kind == "excel_stage1"`;
- required input is `frozen_source_excel`;
- artifact type is `stage1_excel`;
- no Linux stage uses `dxf_to_excel` or code `excel_final`;
- execution request rejects `file_id`, `batch_name`, and unknown fields;
- execution resolves the frozen source Excel internally;
- missing, duplicate, inaccessible, changed, or invalid frozen source objects produce specific 409/422 errors;
- successful job sync binds `stage1_excel` and advances to `design_barrier`;
- standalone DXF→Excel task/API tests remain unaffected.

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_framework.py \
  tests/workflows/test_workflow_api.py \
  tests/workflows/test_workflow_production.py \
  tests/workflows/test_workflow_boundaries.py \
  tests/contracts/test_stage1_boundaries.py
```

Expected: FAIL on the current ten-stage template and browser-supplied parameters.

- [ ] **Step 2: Tighten the execute request**

```python
class WorkflowStageExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_kind: Literal[
        "steel_dxf_classification",
        "excel_stage1",
        "drawing_processing",
        "cam_packaging",
        "windows_cam",
        "result_acceptance",
    ]
```

Placeholder/external kinds remain representable so the existing 501 contract stays honest. Remove `batch_name` and `file_id`.

- [ ] **Step 3: Resolve and inspect the frozen source Excel**

Implement `_prepare_excel_stage1(db, workflow, current_user)`:

1. require the input batch to be frozen and have a manifest SHA;
2. require exactly one `source_excel` item and one matching artifact;
3. load the `StoredFile` and enforce project/user access;
4. verify size/SHA and rerun canonical inspection;
5. return `TASK_EXCEL_FINAL` with `{"file_id": stored.id, "workflow_id": workflow.id, "input_manifest_sha256": batch.manifest_sha256}`.

Keep the existing Excel Job implementation and idempotency key. The workflow stage name/artifact semantics change; the worker algorithm does not fork.

- [ ] **Step 4: Add fail-closed data migration**

Before mutating each existing `linux_production` workflow:

- fail the migration if `excel_stage1` or `excel_final` has a bound Job, result artifact, or other execution evidence that cannot be represented without ambiguity;
- allow the known pending/unstarted shape;
- delete the unstarted legacy `excel_final` stage;
- rename/update the unstarted legacy `excel_stage1` metadata to the new meaning;
- resequence following stages to 5–9;
- preserve current stage/status when it is before Excel;
- if current stage is the removable legacy `excel_final`, stop migration with an explicit exception instead of silently moving it.

The downgrade reconstructs only the unstarted legacy shape and is equally fail-closed.

Set `revision = "5f8d3b0c2e41"` and `down_revision = "4e7c2a9b1d30"`.

- [ ] **Step 5: Verify migration in an isolated database**

```bash
cd backend
uv run pytest -q tests/infrastructure/test_migrations.py \
  tests/workflows/test_workflow_framework.py \
  tests/workflows/test_workflow_api.py \
  tests/workflows/test_workflow_production.py \
  tests/workflows/test_workflow_boundaries.py \
  tests/contracts/test_stage1_boundaries.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/workflows/templates.py \
  backend/app/modules/workflows/schemas/orchestration.py \
  backend/app/modules/workflows/stage_execution.py \
  backend/app/modules/workflows/job_sync.py \
  backend/app/platform/config/constants.py \
  backend/migrations/versions/*_normalize_linux_excel_stage.py \
  backend/tests/workflows \
  backend/tests/contracts/test_stage1_boundaries.py \
  backend/tests/infrastructure/test_migrations.py
git commit -m "feat(workflows): make Excel processing the first-stage operation"
```

## Task 7: Add a Shared Frontend Table-Error Model and Operator Panel

**Files:**

- Modify: `frontend/src/shared/api/error.ts`
- Modify: `frontend/src/shared/api/index.ts`
- Create: `frontend/src/shared/components/ExcelInputFailurePanel.tsx`
- Modify: `frontend/src/shared/components/index.ts`
- Test: `frontend/tests/e2e/contracts/api-contract.spec.ts`
- Test: `frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts`

- [ ] **Step 1: Write failing contract/UI tests**

Mock a 422 response and verify the UI shows:

- human message;
- explicit operator action;
- missing fields;
- sheet/row/column where available;
- truncation notice;
- request ID for support;
- no raw JSON, traceback, or server path.

```ts
await expect(page.getByRole('alert')).toContainText('表格缺少 Excel 第一阶段所需列');
await expect(page.getByRole('alert')).toContainText('请补充');
await expect(page.getByRole('alert')).toContainText('Sheet1 · 第 7 行 · 零件号');
```

Run:

```bash
cd frontend
npx playwright test tests/e2e/contracts/api-contract.spec.ts \
  tests/e2e/excel-processing/excel-final-flow.spec.ts
```

Expected: FAIL because errors are currently flattened into generic text.

- [ ] **Step 2: Parse the shared failure shape once**

Add `ExcelInputFailure`, `ExcelInputIssue`, and `ParsedApiError.failure`. The parser accepts only known safe field types and applies client-side bounds as a second defense.

- [ ] **Step 3: Build a compact reusable panel**

Use Ant Design `Alert`, `Descriptions`, and a compact issue list. The panel starts with the operator action; technical code/request ID appear in a collapsed or secondary area.

- [ ] **Step 4: Run contract tests and frontend build**

```bash
cd frontend
npm run build
npx playwright test tests/e2e/contracts/api-contract.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/api/error.ts \
  frontend/src/shared/api/index.ts \
  frontend/src/shared/components/ExcelInputFailurePanel.tsx \
  frontend/src/shared/components/index.ts \
  frontend/tests/e2e/contracts/api-contract.spec.ts \
  frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts
git commit -m "feat(frontend): explain Excel table input errors"
```

## Task 8: Turn `/files/excel-final` into an Efficient Excel Work Center

**Files:**

- Modify: `frontend/src/features/excel-processing/ExcelFinalPage.tsx`
- Modify: `frontend/src/features/excel-processing/api.ts`
- Modify: `frontend/src/features/excel-processing/types.ts`
- Modify: `frontend/src/features/excel-processing/model/excelFinalUrlState.ts`
- Modify: `frontend/src/features/excel-processing/components/ExcelFinalOverview.tsx`
- Modify: `frontend/src/features/excel-processing/components/ExcelFinalBatchDrawer.tsx`
- Modify: `frontend/src/features/excel-processing/components/ExcelFinalTools.tsx`
- Modify: `frontend/src/features/excel-processing/components/ExcelFinalPage.css`
- Create: `frontend/src/features/excel-processing/components/ExcelProcessTab.tsx`
- Create: `frontend/src/features/excel-processing/components/ExcelBatchesTab.tsx`
- Create: `frontend/src/features/excel-processing/components/ExcelPartsTab.tsx`
- Create: `frontend/src/features/excel-processing/components/ExcelHandbookTab.tsx`
- Test: `frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts`

- [ ] **Step 1: Write failing navigation, upload, and lookup tests**

Assert:

- default URL/tab is processing;
- tabs are addressable by `?tab=process|batches|parts|handbook`;
- only the active tab issues its heavy list queries;
- invalid upload stays on the processing tab and shows the shared failure panel;
- successful processing status shows output and quality warnings separately;
- handbook lookup sends `category`, `spec`, and optional `material`;
- D-series material rules are explained before submit;
- round bar/rebar reject missing or conflicting material in the form;
- old `?job_id=` deep links still open processing status.

Run:

```bash
cd frontend
npx playwright test tests/e2e/excel-processing/excel-final-flow.spec.ts
```

Expected: FAIL.

- [ ] **Step 2: Split the page into URL-backed tabs**

Keep `ExcelFinalPage` as orchestration only. Make process the default:

```tsx
const tabs = [
  { key: 'process', label: '处理 Excel', children: <ExcelProcessTab /> },
  { key: 'batches', label: '处理批次', children: <ExcelBatchesTab /> },
  { key: 'parts', label: '零件查询', children: <ExcelPartsTab /> },
  { key: 'handbook', label: '五金手册', children: <ExcelHandbookTab /> },
];
```

Query keys include the active filters. Set `enabled` so inactive tabs do not load.

- [ ] **Step 3: Correct the handbook lookup contract**

Replace:

```ts
lookupExcelFinalWeight(spec: string)
```

with:

```ts
lookupExcelFinalWeight({
  category,
  spec,
  material,
}: {
  category: HandbookCategory;
  spec: string;
  material?: string;
})
```

Send all fields to `/weights/lookup`. Keep backend as the final authority for D-series routing:

- HPB/Q235B/Q355B D-series → `round_bar`;
- HRB D-series → `rebar`;
- material is mandatory for round bar and rebar;
- plate is density 7.85 rather than a handbook row;
- bolts, sleeves, and TT remain blank;
- a genuine handbook miss/conflict stays explicit.

- [ ] **Step 4: Remove obsolete coupled state/components**

Delete only code made unreachable by the four-tab composition. Preserve detail/download/retry behavior. Do not delete the standalone `/files/dxf2excel` route or page.

- [ ] **Step 5: Run Excel UI tests and build**

```bash
cd frontend
npm run build
npx playwright test tests/e2e/excel-processing/excel-final-flow.spec.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/excel-processing \
  frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts
git commit -m "feat(frontend): build the Excel processing work center"
```

## Task 9: Replace the Workflow Drawer with a Dedicated Detail Console

**Files:**

- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/features/workflows/index.ts`
- Modify: `frontend/src/features/workflows/WorkflowsPage.tsx`
- Create: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Create: `frontend/src/features/workflows/components/WorkflowHeader.tsx`
- Create: `frontend/src/features/workflows/components/WorkflowStageRail.tsx`
- Create: `frontend/src/features/workflows/components/WorkflowStageWorkspace.tsx`
- Create: `frontend/src/features/workflows/components/WorkflowArtifactsPanel.tsx`
- Create: `frontend/src/features/workflows/components/WorkflowFailurePanel.tsx`
- Modify: `frontend/src/features/workflows/ProductionInputPanel.tsx`
- Modify: `frontend/src/features/workflows/DxfClassificationPanel.tsx`
- Modify: `frontend/src/features/workflows/workflow.ts`
- Modify: `frontend/src/features/workflows/workflows.api.ts`
- Modify: `frontend/src/features/workflows/workflow-input.ts`
- Modify: `frontend/src/features/workflows/workflow-inputs.api.ts`
- Modify: `frontend/src/features/workflows/model/workflowPresentation.tsx`
- Modify: `frontend/src/features/workflows/styles.css`
- Test: `frontend/tests/e2e/workflows/workflow-input.spec.ts`

- [ ] **Step 1: Write failing workflow navigation and execution tests**

Assert:

- `/workflows` shows a focused list/create page only;
- clicking a workflow navigates to `/workflows/:workflowId`;
- refresh on the detail URL restores the same workflow;
- the detail console has header/status, stage rail, current-stage workspace, artifacts/output, and operation history/failure area;
- source intake reports invalid Excel table details inline;
- `excel_stage1` execute sends only `{"execution_kind":"excel_stage1"}`;
- no batch selector, file selector, DXF→Excel wording, or `excel_final` stage appears;
- output download remains reachable after success;
- placeholder/external stages clearly say that they require manual/external handling rather than showing a fake run button.

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-input.spec.ts
```

Expected: FAIL because the current page uses an embedded drawer and global selectors.

- [ ] **Step 2: Add the detail route**

```tsx
<Route path="/workflows" element={<WorkflowsPage />} />
<Route path="/workflows/:workflowId" element={<WorkflowDetailPage />} />
```

Parse and validate the numeric ID. A bad ID renders a clear local error; a missing workflow uses the API error boundary.

- [ ] **Step 3: Reduce the list page**

Keep filters, counts, creation, and row navigation. Remove detail queries, batch/file queries, stage execution state, and drawer markup from `WorkflowsPage`.

- [ ] **Step 4: Build a stage-driven detail workspace**

The selected stage comes from `?stage=` and defaults to `current_stage`, then the first stage. Stage components receive typed data and callbacks rather than owning global queries.

For `excel_stage1`:

- show the frozen source Excel name, SHA validation state, and inspection summary;
- show one primary “运行 Excel 第一阶段” action;
- explain that the server uses the frozen source automatically;
- show processing progress, quality status, structured failure, and output artifact;
- never ask for a file ID or batch name.

- [ ] **Step 5: Run workflow UI tests and build**

```bash
cd frontend
npm run build
npx playwright test tests/e2e/workflows/workflow-input.spec.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/router.tsx \
  frontend/src/features/workflows \
  frontend/tests/e2e/workflows/workflow-input.spec.ts
git commit -m "feat(workflows): add dedicated production detail console"
```

## Task 10: Synchronize Contracts, User-Facing Terminology, and Verification Gates

**Files:**

- Modify: `backend/app/modules/excel_processing/README.md`
- Modify: `backend/app/modules/excel_processing/routes/README.md`
- Modify: `backend/app/modules/workflows/README.md`
- Modify: `backend/app/modules/workflows/intake/README.md`
- Modify: `backend/app/modules/workflows/routes/README.md`
- Modify: `backend/app/modules/workflows/schemas/README.md`
- Modify: `frontend/src/features/excel-processing/README.md`
- Modify: `frontend/src/features/excel-processing/components/README.md`
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `frontend/src/features/workflows/model/README.md`
- Modify: `frontend/tests/e2e/excel-processing/README.md`
- Modify: `frontend/tests/e2e/workflows/README.md`
- Modify: `scripts/verify.sh`
- Test: `backend/tests/contracts/test_docs_consistency.py`
- Test: `backend/tests/contracts/test_frontend_contract.py`
- Test: `backend/tests/architecture/test_partition_docs.py`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Write failing consistency assertions**

Contract tests must reject:

- main-workflow `dxf_to_excel`;
- main-workflow stage code `excel_final`;
- docs that call the operation “Excel 最终合并”;
- old execute examples containing `file_id`/`batch_name`;
- `scripts/verify.sh` running only `multi_split/tests` while omitting the canonical `tests` suite.

Run:

```bash
cd backend
uv run pytest -q tests/contracts/test_docs_consistency.py \
  tests/contracts/test_frontend_contract.py \
  tests/architecture/test_partition_docs.py \
  tests/infrastructure/test_scripts.py
```

Expected: FAIL until docs and scripts are updated.

- [ ] **Step 2: Document the operator-visible algorithm boundary**

Use consistent wording:

- product feature: “Excel 第一阶段处理”;
- code/package compatibility name: `excel_final` only where it is an actual route/module identifier;
- workflow input: one frozen source Excel;
- output: standard 整理表 and part table;
- validation: immediate table check plus worker recheck;
- table errors: show what is wrong and what the operator must change;
- quality report: core issues and required human actions only.

Document that DXF→Excel is a separate utility and not part of Linux production.

- [ ] **Step 3: Correct the Stage test gate**

Change:

```bash
cd Stages/excel_final && uv run pytest -q multi_split/tests
```

to:

```bash
cd Stages/excel_final && uv run pytest -q tests multi_split/tests
```

Make it a required gate. `multi_split.profile` supplies the fabricated-profile geometry kernel used by the canonical pipeline, so both suites are part of the release boundary.

- [ ] **Step 4: Run documentation and script tests**

```bash
cd backend
uv run pytest -q tests/contracts/test_docs_consistency.py \
  tests/contracts/test_frontend_contract.py \
  tests/architecture/test_partition_docs.py \
  tests/infrastructure/test_scripts.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/excel_processing/README.md \
  backend/app/modules/excel_processing/routes/README.md \
  backend/app/modules/workflows \
  frontend/src/features/excel-processing \
  frontend/src/features/workflows \
  frontend/tests/e2e/excel-processing/README.md \
  frontend/tests/e2e/workflows/README.md \
  scripts/verify.sh \
  backend/tests/contracts \
  backend/tests/architecture/test_partition_docs.py \
  backend/tests/infrastructure/test_scripts.py
git commit -m "docs(workflows): align Excel stage one contracts"
```

## Task 11: Run Real Migration, API, Worker, and Browser Acceptance

**Files:**

- Modify only if a gate exposes a real defect in the files owned by Tasks 1–10.
- Do not add generated workbooks, uploaded objects, screenshots, Playwright output, `Stages/excel_final/data/`, or `output/` to Git.

- [ ] **Step 1: Run focused Python suites**

```bash
cd Stages/excel_final
uv run pytest -q tests multi_split/tests

cd ../../backend
uv run pytest -q tests/excel_processing tests/workflows \
  tests/contracts/test_stage1_boundaries.py \
  tests/architecture/test_excel_processing_boundaries.py \
  tests/infrastructure/test_migrations.py
```

Expected: PASS.

- [ ] **Step 2: Run backend and frontend full gates**

```bash
cd backend
uv run pytest -q
uv run ruff check .

cd ../frontend
npm run build
npx playwright test tests/e2e/contracts \
  tests/e2e/excel-processing \
  tests/e2e/workflows
```

Expected: PASS.

- [ ] **Step 3: Test upgrade and downgrade in the migration test harness**

```bash
cd backend
uv run alembic heads
uv run pytest -q tests/infrastructure/test_migrations.py
```

Expected: exactly one head; clean install, upgrade from the prior head, downgrade, and re-upgrade all PASS. Confirm fail-closed migration tests contain workflows with bound Jobs/artifacts.

- [ ] **Step 4: Run the repository verification gate**

```bash
cd ..
bash scripts/verify.sh full
```

Expected: all required gates PASS. Record any environment-only optional skips separately; do not describe them as verified.

- [ ] **Step 5: Apply the migration and exercise the live backend**

With the repository services running:

```bash
cd backend
uv run alembic upgrade head
```

At `http://localhost:8080` verify with real HTTP and the real worker:

1. upload a deliberately invalid workbook to `/files/excel-final`;
2. confirm the page explains the exact table problem and required correction;
3. upload a valid Tekla source and wait for the worker;
4. download and open the generated workbook;
5. create/open a Linux workflow at `/workflows/:id`;
6. register the same valid Excel plus required DWGs, freeze inputs, and advance through classification/drawing handling as supported;
7. execute `excel_stage1` without selecting a file;
8. confirm Job completion creates a `stage1_excel` artifact and advances to `design_barrier`;
9. repeat with an invalid workflow Excel and confirm the failed input item remains visible after the 422;
10. confirm `/files/dxf2excel` still works independently and is absent from the main workflow.

- [ ] **Step 6: Inspect live database semantics**

Read-only checks must show:

- every `linux_production` run has exactly the nine expected stage codes in order;
- no Linux stage uses legacy code `excel_final`;
- no ambiguous migrated stage has bound evidence;
- valid source Excel items have validation JSON/version/SHA;
- invalid attempted registrations retain a failed item and structured failure;
- completed Excel Jobs and workflow stages reference the same output through normal Job/Result/File/artifact relations.

- [ ] **Step 7: Clean generated artifacts and inspect Git**

Do not delete the pre-existing untracked user directories. Remove only newly generated temporary test output whose exact ownership is known.

```bash
git status --short --branch
git diff --check
git log --oneline --decorate -15
```

Expected: no unexpected tracked changes, no whitespace errors, and only the known user-owned untracked directories remain.

- [ ] **Step 8: Commit any acceptance-only fix, then push**

If acceptance required a real code fix, rerun its focused test and the affected full gate, stage only the exact files changed by that fix, inspect `git diff --cached`, and commit it separately as `fix(workflows): harden Excel stage one integration`.

Finally:

```bash
git push origin main
git status --short --branch
```

Expected: local `main` matches `origin/main`; the design, plan, implementation, tests, and docs are all present, while user-owned untracked data remains uncommitted.

## Completion Evidence

The task is complete only when all of the following are demonstrated:

- invalid standalone and workflow uploads return a specific table error and an operator action;
- the same invalid input is rejected consistently at preflight, freeze, and worker boundaries;
- valid input reaches a real Celery worker and produces a downloadable workbook;
- the workbook opens and retains the established 整理表/part output contract;
- Linux production exposes nine stages and only one Excel processing stage;
- workflow Excel execution needs no browser-supplied file or batch identifier;
- DXF→Excel is absent from the main workflow and remains usable as a standalone tool;
- the workflow detail route survives direct navigation and refresh;
- all focused suites, full backend tests, frontend build/E2E, migration checks, and `scripts/verify.sh full` pass;
- live MySQL state and actual output artifacts support the reported result;
- Git is pushed and clean apart from explicitly preserved user-owned untracked data.
