# Production Input Split Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the combined production-folder upload with independent single-Excel and DWG-folder uploads, including confirmation before ignoring unrelated files.

**Architecture:** Keep `WorkflowInputBatch`, `WorkflowInputItem`, Files registration, DWG conversion, freeze, audit, and compensation as the single production-input core. Split only the intake validation and HTTP routes into a one-file Excel command and a DWG-folder command; the React panel exposes two selectors and filters unrelated folder entries only after an explicit modal confirmation.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, React, TypeScript, Ant Design, TanStack Query, Pytest, Playwright.

---

## File Structure

- `backend/app/modules/workflows/intake/registration.py`: independent Excel and DWG-folder manifest validation.
- `backend/app/modules/workflows/routes/intake.py`: two multipart commands sharing authorization, batch locking, Files storage, input registration, audit, and commit behavior.
- `backend/tests/workflows/test_workflow_input_api.py`: public HTTP contract and rollback coverage for both commands.
- `frontend/src/features/workflows/ProductionInputPanel.tsx`: two selectors, local DWG-folder classification, confirmation modal, and upload state.
- `frontend/src/features/workflows/workflow-inputs.api.ts`: independent multipart request builders.
- `frontend/tests/e2e/workflows/workflow-input.spec.ts`: observable selector, confirmation, request-body, conversion, and freeze behavior.
- Workflow partition READMEs and architecture/reference documents: synchronized production contract.
- OpenAPI/runtime/module snapshots: exact public surface after replacing one route with two.

### Task 1: Split server-side input validators

**Files:**
- Modify: `backend/app/modules/workflows/intake/registration.py`
- Test: `backend/tests/workflows/test_workflow_input_api.py`

- [ ] **Step 1: Write failing validator tests**

Add tests proving the Excel validator accepts only one `.xls`/`.xlsx` upload name and the DWG
manifest accepts only canonical paths whose filenames end in `.dwg`:

```python
@pytest.mark.parametrize("name", ["parts.xls", "parts.xlsx", "PARTS.XLSX"])
def test_excel_upload_name_accepts_supported_extensions(name):
    validate_input_excel_name(name)


@pytest.mark.parametrize("name", ["parts.csv", "parts.xlsm", "drawing.dwg", ""])
def test_excel_upload_name_rejects_other_extensions(name):
    with pytest.raises(AppHTTPException) as raised:
        validate_input_excel_name(name)
    assert raised.value.detail["code"] == "INPUT_EXCEL_FILE_TYPE_NOT_ALLOWED"


def test_dwg_folder_manifest_rejects_non_dwg_upload():
    with pytest.raises(AppHTTPException) as raised:
        validate_input_dwg_folder_manifest(
            ["A.dwg", "notes.pdf"],
            ["图纸/A.dwg", "图纸/notes.pdf"],
        )
    assert raised.value.detail["code"] == "INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED"
```

- [ ] **Step 2: Run the validator tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py -k "excel_upload_name or dwg_folder_manifest"
```

Expected: collection/import failure because the two new validators do not exist.

- [ ] **Step 3: Extract canonical path validation and implement both validators**

Keep the current NFKC, control-character, empty-segment, dot-segment, Windows-drive,
single-root, duplicate-normalized-path, and duplicate-DWG-stem checks behind one private
helper. Expose:

```python
def validate_input_excel_name(upload_name: str) -> None:
    if Path(upload_name).suffix.lower() not in EXCEL_FILE_EXTENSIONS:
        raise AppHTTPException(
            422,
            "INPUT_EXCEL_FILE_TYPE_NOT_ALLOWED",
            "The production Excel input must be one .xls or .xlsx file.",
        )


def validate_input_dwg_folder_manifest(
    upload_names: list[str],
    relative_paths: list[str],
) -> str:
    folder_name, paths = _validate_canonical_folder_paths(upload_names, relative_paths)
    if any(Path(path.name).suffix.lower() != ".dwg" for path in paths):
        raise AppHTTPException(
            422,
            "INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED",
            "The DWG folder upload may contain only DWG files.",
        )
    return folder_name
```

Delete the combined validator after all callers move in Task 2.

- [ ] **Step 4: Run validator tests and existing manifest tests**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py -k "manifest or upload_name"
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit validator slice**

```bash
git add backend/app/modules/workflows/intake/registration.py \
  backend/tests/workflows/test_workflow_input_api.py
git commit -m "refactor(workflows): split production input validation"
```

### Task 2: Replace the combined HTTP command

**Files:**
- Modify: `backend/app/modules/workflows/routes/intake.py`
- Modify: `backend/app/modules/workflows/routes/README.md`
- Test: `backend/tests/workflows/test_workflow_input_api.py`

- [ ] **Step 1: Write a failing Excel endpoint test**

Create a batch, upload one valid Excel fixture to `/input-excel`, and assert HTTP 201,
one `source_excel` item, zero DWG, and stable duplicate rejection:

```python
response = client.post(
    f"/api/v1/workflows/{workflow_id}/input-excel",
    headers=owner_headers,
    files={"upload": ("parts.xlsx", valid_excel_bytes, XLSX_CONTENT_TYPE)},
)
assert response.status_code == 201
assert response.json()["data"]["counts"] == {"dwg": 0, "excel": 1, "paired": 0, "failed": 0}
duplicate = client.post(...same request...)
assert duplicate.status_code == 409
assert duplicate.json()["error"]["code"] == "INPUT_EXCEL_ALREADY_IMPORTED"
```

- [ ] **Step 2: Run the Excel endpoint test and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py::test_import_excel_input_separately
```

Expected: HTTP 405 or 404 because the route is absent.

- [ ] **Step 3: Implement the Excel command**

Add `POST /{workflow_id}/input-excel`. Reuse `get_workflow_or_404`,
`require_project_role`, `lock_input_batch`, `save_upload_file`, `register_input_file`,
`write_audit_log`, `describe_input_batch`, and the current transaction. Before object storage:

```python
validate_input_excel_name(upload.filename or "")
batch = lock_input_batch(db, get_input_batch(db, workflow_id))
if any(item.role == "source_excel" for item in batch.items):
    raise AppHTTPException(
        409,
        "INPUT_EXCEL_ALREADY_IMPORTED",
        "Remove the current production input before uploading another Excel file.",
    )
```

Audit action: `workflow_input_excel.import`.

- [ ] **Step 4: Run the Excel endpoint test and verify GREEN**

Run the exact Step 2 command. Expected: pass.

- [ ] **Step 5: Write a failing DWG-folder endpoint test**

POST two DWGs and matching relative paths to `/input-dwg-folder`; assert both become
`source_dwg`. Add public-API cases for non-DWG multipart input, noncanonical paths, multiple
roots, duplicate stems, and a second DWG-folder import.

- [ ] **Step 6: Run the DWG endpoint tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py -k "import_dwg_folder"
```

Expected: HTTP 405 or 404.

- [ ] **Step 7: Implement the DWG-folder command**

Add `POST /{workflow_id}/input-dwg-folder` with `uploads` and JSON `relative_paths`.
Parse the JSON using the current stable `INPUT_FOLDER_MANIFEST_INVALID` errors, call
`validate_input_dwg_folder_manifest`, reject existing `source_dwg` items with
`INPUT_DWG_FOLDER_ALREADY_IMPORTED`, then reuse the current loop that saves and registers
each upload. Audit action: `workflow_input_dwg_folders.import`.

- [ ] **Step 8: Remove the old combined command**

Delete `POST /{workflow_id}/input-folder`. Keep the existing
`DELETE /{workflow_id}/input-folder` as the whole-batch reset command.

- [ ] **Step 9: Prove conversion gating across separate requests**

Update the end-to-end backend input test to:

1. upload only Excel and assert conversion cannot start;
2. upload the DWG folder;
3. request conversion and verify server-created DXF;
4. freeze and verify both input roles are included.

- [ ] **Step 10: Run backend workflow input tests**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py \
  tests/workflows/test_workflow_input_service.py
```

Expected: all pass.

- [ ] **Step 11: Commit route slice**

```bash
git add backend/app/modules/workflows/routes/intake.py \
  backend/app/modules/workflows/routes/README.md \
  backend/tests/workflows/test_workflow_input_api.py
git commit -m "feat(workflows): upload Excel and DWG inputs separately"
```

### Task 3: Split the browser upload controls

**Files:**
- Modify: `frontend/src/features/workflows/ProductionInputPanel.tsx`
- Modify: `frontend/src/features/workflows/workflow-inputs.api.ts`
- Modify: `frontend/src/features/workflows/styles.css`
- Test: `frontend/tests/e2e/workflows/workflow-input.spec.ts`

- [ ] **Step 1: Change E2E to the new observable controls and verify RED**

Require:

```typescript
await expect(page.getByRole('button', { name: '上传 Excel 文件' })).toBeVisible();
await expect(page.getByRole('button', { name: '选择 DWG 文件夹' })).toBeVisible();
await expect(page.locator('input[type=file][accept=\".xls,.xlsx\"]')).toHaveCount(1);
await expect(page.locator('input[webkitdirectory]')).toHaveCount(1);
```

Mock `/input-excel` and `/input-dwg-folder`; remove the old `/input-folder` POST mock.

- [ ] **Step 2: Run E2E and verify RED**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-input.spec.ts
```

Expected: new control names or endpoint assertions fail.

- [ ] **Step 3: Add independent API request builders**

Replace `uploadWorkflowInputFolder` with:

```typescript
export async function uploadWorkflowInputExcel(workflowId: number, file: File) {
  const form = new FormData();
  form.append('upload', file, file.name);
  return (await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-excel`,
    form,
    { timeout: 300_000 },
  )).data.data;
}

export async function uploadWorkflowInputDwgFolder(workflowId: number, files: File[]) {
  const form = new FormData();
  files.forEach((file) => form.append('uploads', file, file.name));
  form.append('relative_paths', JSON.stringify(files.map((file) => file.webkitRelativePath)));
  return (await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-dwg-folder`,
    form,
    { timeout: 300_000 },
  )).data.data;
}
```

- [ ] **Step 4: Implement the Excel selector**

Add a hidden non-multiple file input with `accept=".xls,.xlsx"`. Reject an unexpected extension
before mutation with “Excel 只能是 .xls 或 .xlsx 文件”; otherwise call the Excel mutation.
Disable it when an Excel item exists or either upload mutation is pending.

- [ ] **Step 5: Implement DWG-folder classification and direct upload**

Classify the browser selection:

```typescript
const dwgFiles = selected.filter((file) => /\.dwg$/i.test(file.name));
const ignoredFiles = selected.filter((file) => !/\.dwg$/i.test(file.name));
```

Reject no-DWG and invalid/multiple roots locally. If `ignoredFiles` is empty, upload
`dwgFiles` directly.

- [ ] **Step 6: Implement the ignore-confirmation modal**

Use `modal.confirm` from `App.useApp()` with:

- title `文件夹包含其他文件`;
- DWG and ignored counts;
- scrollable relative-path list;
- `okText: '确认，仅上传 DWG'`;
- `cancelText: '取消'`;
- `onOk` calling the DWG mutation with only `dwgFiles`.

Do not upload before `onOk`.

- [ ] **Step 7: Update panel copy and state**

Replace the combined-folder alert with explicit Excel and DWG instructions. Keep server batch
counts authoritative. Enable conversion only when `counts.excel === 1` and `counts.dwg > 0`;
keep whole-batch clear and freeze unchanged.

- [ ] **Step 8: Complete E2E RED→GREEN slices**

Cover pure DWG direct upload, mixed folder cancellation with no request, mixed folder
confirmation whose multipart body excludes PDF/DXF/Excel, invalid Excel rejection, then the
existing conversion/freeze flow.

- [ ] **Step 9: Run frontend gates**

Run:

```bash
cd frontend
npm run build
npm run test:e2e:workflows
```

Expected: build passes and both Workflow Playwright tests pass.

- [ ] **Step 10: Commit frontend slice**

```bash
git add frontend/src/features/workflows/ProductionInputPanel.tsx \
  frontend/src/features/workflows/workflow-inputs.api.ts \
  frontend/src/features/workflows/styles.css \
  frontend/tests/e2e/workflows/workflow-input.spec.ts
git commit -m "feat(workflows): confirm filtered DWG folder uploads"
```

### Task 4: Synchronize contracts and documentation

**Files:**
- Modify: `backend/app/modules/workflows/README.md`
- Modify: `backend/app/modules/workflows/routes/README.md`
- Modify: `backend/tests/workflows/README.md`
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `docs/architecture/workflow.md`
- Modify: `docs/architecture/platform-specification.md`
- Modify: `docs/architecture/implementation-status.md`
- Regenerate: `docs/reference/api.md`
- Regenerate: `docs/architecture/runtime-contract.json`
- Modify contract/architecture tests only where the intentional operation replacement changes exact counts.

- [ ] **Step 1: Update business documentation**

State the exact latest contract everywhere:

- Excel is the sole single-file production upload and accepts only `.xls`/`.xlsx`;
- DWG is folder-only;
- mixed DWG folders require browser confirmation and only DWGs reach the server;
- both independent inputs are required before conversion/freeze;
- old combined POST route is removed.

- [ ] **Step 2: Regenerate OpenAPI and runtime snapshots**

Run:

```bash
make docs-generate
backend/.venv/bin/python scripts/architecture/snapshot_contracts.py --write
```

Expected: one old POST operation disappears and two new POST operations appear. Update exact
path/operation assertions and module catalog ownership to those generated values; do not edit
unrelated snapshot sections.

- [ ] **Step 3: Run contract and documentation gates**

Run:

```bash
make docs-check
make architecture-check
cd backend
uv run pytest -q tests/architecture tests/contracts
```

Expected: all pass with no documentation drift.

- [ ] **Step 4: Commit synchronized contracts**

```bash
git add README.md backend/app/modules/workflows frontend/src/features/workflows/README.md \
  backend/tests docs/architecture docs/reference/api.md
git commit -m "docs(workflows): document split production inputs"
```

### Task 5: Full release verification and live deployment

**Files:**
- No source changes expected.

- [ ] **Step 1: Run backend full gate**

```bash
cd backend
uv run ruff check .
uv run pytest -q
```

Expected: Ruff passes; Pytest has zero failures.

- [ ] **Step 2: Re-run frontend release gate**

```bash
cd frontend
npm run build
npm run test:e2e:workflows
```

Expected: build and Workflow E2E pass.

- [ ] **Step 3: Confirm reviewable worktree**

```bash
git diff --check
git status --short
```

Expected: only intentionally preserved `Stages/excel_final/data/` and `output/` remain
untracked after commits.

- [ ] **Step 4: Restart the stale backend and rebuild the served frontend**

The earlier live diagnosis proved the process predates commit `6665ab4` and its OpenAPI is
missing `/api/v1/workflows/production-projects`. Deploy the completed commits with:

```bash
bash scripts/start-all.sh --restart-backend --rebuild
```

Expected: backend PID/start time changes, frontend stale warning disappears, and status is healthy.

- [ ] **Step 5: Verify live Nginx and OpenAPI**

```bash
bash scripts/status.sh
curl -fsS http://127.0.0.1:8080/openapi.json \
  | jq '.paths["/api/v1/workflows/production-projects"],
        .paths["/api/v1/workflows/{workflow_id}/input-excel"],
        .paths["/api/v1/workflows/{workflow_id}/input-dwg-folder"]'
```

Expected: all three path objects exist; the old combined input path has no POST operation.

- [ ] **Step 6: Push completed implementation**

```bash
git push origin main
git ls-remote --heads origin main
```

Expected: remote `main` equals local HEAD.
