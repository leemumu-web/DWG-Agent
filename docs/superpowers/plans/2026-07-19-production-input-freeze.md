# Production Input Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recoverable `linux_production` source-intake flow where an operator uploads multiple DWG files and exactly one Excel file, the server creates every DXF through the existing ODA pipeline, and a validated immutable manifest creates Drawing records and completes the workflow stage.

**Architecture:** Add a small workflow-scoped input ledger that references existing File, Job, AnalysisResult, and Drawing rows. Keep bytes and SHA-256 in `/files`, execution in existing conversion Jobs, and expose orchestration through `/workflows/{id}/input-batch`; the React workflow drawer becomes a four-step, server-authoritative intake console.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic/MySQL, Pydantic 2, existing Local/MinIO storage adapters, existing Celery/ODA conversion pipeline, React 19, TypeScript, TanStack Query, Ant Design, pytest, Playwright.

---

## File structure

- Create `backend/app/models/workflow_input.py`: durable batch and source-item rows only.
- Create `backend/app/schemas/workflow_input_schema.py`: request/read/diagnostic contracts.
- Create `backend/app/services/workflow_input_service.py`: normalization, integrity validation, Job/result sync, freeze transaction.
- Create `backend/app/api/v1/workflow_inputs_api.py`: project-scoped HTTP boundary.
- Create `backend/migrations/versions/f7a9c2d4e610_add_workflow_input_batches.py`: both new tables and constraints.
- Create `backend/tests/test_workflow_input_service.py`: state machine, validation, conversion and freeze tests.
- Create `backend/tests/test_workflow_input_api.py`: authentication, permissions and error-envelope tests.
- Create `frontend/src/types/workflow-input.ts`: UI contract types.
- Create `frontend/src/api/workflow-inputs.api.ts`: input-batch endpoints.
- Create `frontend/src/features/workflows/ProductionInputPanel.tsx`: focused four-step source-intake console.
- Create `frontend/tests/e2e/workflow-input.spec.ts`: browser error prevention and recovery checks.
- Modify model/router/workflow/frontend/doc generator files only where required to register these units.

### Task 1: Persist input batches and source items

**Files:**
- Create: `backend/app/models/workflow_input.py`
- Create: `backend/migrations/versions/f7a9c2d4e610_add_workflow_input_batches.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/workflow.py`
- Test: `backend/tests/test_workflow_input_service.py`

- [ ] **Step 1: Write the failing model test**

Add a test that creates one `WorkflowInputBatch` for a `linux_production` workflow, adds two `source_dwg` items and one `source_excel`, and asserts the workflow relationship and stable item ordering. Add a second assertion that a second batch for the same workflow raises `IntegrityError`.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/test_workflow_input_service.py -k model
```

Expected: collection/import failure because `app.models.workflow_input` does not exist.

- [ ] **Step 3: Implement the models**

Define `WorkflowInputBatch` with `workflow_run_id` unique, project/creator FKs, status/version/manifest/frozen/error fields and ordered `items`. Define `WorkflowInputItem` with batch/file unique constraint, role/status, normalized stem, conversion Job, derived DXF, Drawing and error fields. Add `input_batch` relationship to `WorkflowRun` using `cascade="all, delete-orphan"` and `uselist=False`.

- [ ] **Step 4: Add the Alembic revision**

Use revision `f7a9c2d4e610`, down revision `d5e8a1c4b720`. Create indexes on `(project_id, status)`, `(input_batch_id, role)`, conversion Job and Drawing. Add unique constraints for `workflow_run_id` and `(input_batch_id, file_id)`. Downgrade drops item table before batch table.

- [ ] **Step 5: Verify GREEN and migration metadata**

Run:

```bash
cd backend
uv run pytest -q tests/test_workflow_input_service.py -k model
uv run alembic check
```

Expected: model tests pass and Alembic reports no new upgrade operations.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models backend/migrations/versions backend/tests/test_workflow_input_service.py
git commit -m "feat: persist workflow input batches"
```

### Task 2: Validate existing files and register safe inputs

**Files:**
- Create: `backend/app/schemas/workflow_input_schema.py`
- Create: `backend/app/services/workflow_input_service.py`
- Modify: `backend/tests/test_workflow_input_service.py`

- [ ] **Step 1: Write failing registration tests**

Cover these independent behaviors:

```python
def test_registers_multiple_real_dwgs_and_one_readable_excel(...): ...
def test_rejects_human_dxf_with_input_dxf_not_allowed(...): ...
def test_rejects_second_excel_without_changing_batch(...): ...
def test_rejects_dwg_whose_object_digest_no_longer_matches(...): ...
def test_rejects_fake_xlsx_container(...): ...
def test_same_file_registration_is_idempotent(...): ...
```

Use actual local-storage bytes: a supported `AC1027` DWG payload larger than the existing 1024-byte minimum and a workbook created with `openpyxl`. Exercise the `.xls` branch with a focused monkeypatch around `xlrd.open_workbook` plus separate corrupt-BIFF bytes; the service code must still call real `xlrd` in production.

- [ ] **Step 2: Verify RED**

Run `cd backend && uv run pytest -q tests/test_workflow_input_service.py -k 'register or rejects or idempotent'`.

Expected: failures because registration and integrity helpers are absent.

- [ ] **Step 3: Implement shared integrity reading**

In `workflow_input_service.py`, stream the File through `get_storage_backend().iter_file`, enforce the configured upload bound, recompute size and SHA-256, and return a spooled seekable object. Map missing object, read failure, size mismatch and digest mismatch to stable `INPUT_OBJECT_*` errors.

- [ ] **Step 4: Implement type-specific validation**

For `.dwg`, call existing `validate_dwg_header` and enforce the existing minimum size. For `.xlsx`, call `openpyxl.load_workbook(..., read_only=True, data_only=True)`; for `.xls`, call `xlrd.open_workbook(file_contents=...)`. Require at least one visible sheet. Reject `.dxf` as `INPUT_DXF_NOT_ALLOWED` and every other extension as `INPUT_FILE_TYPE_NOT_ALLOWED`.

- [ ] **Step 5: Implement registration and normalization**

Normalize stems with basename → extension removal → Unicode NFKC → trim → whitespace collapse → casefold. Register an accessible File as `source_dwg` or `source_excel`, preserve the original name, return an existing row for replay, and reject a second distinct Excel as `INPUT_EXCEL_ALREADY_EXISTS`. Recompute batch status and diagnostics after every mutation.

- [ ] **Step 6: Verify GREEN and commit**

Run the full service test file, then:

```bash
git add backend/app/schemas/workflow_input_schema.py backend/app/services/workflow_input_service.py backend/tests/test_workflow_input_service.py
git commit -m "feat: validate production input files"
```

### Task 3: Reuse DWG conversion Jobs and synchronize DXF lineage

**Files:**
- Modify: `backend/app/services/workflow_input_service.py`
- Modify: `backend/tests/test_workflow_input_service.py`

- [ ] **Step 1: Write failing conversion tests**

Test that conversion submission creates one `convert_dwg_to_dxf` Job per source DWG with project/file/batch params, binds Job IDs, and returns them. Test replay for active/succeeded Jobs, retry of failed/cancelled Jobs with attempt increment, feature-gate failure, and no-dispatch-before-commit behavior.

- [ ] **Step 2: Verify RED**

Run `cd backend && uv run pytest -q tests/test_workflow_input_service.py -k conversion` and confirm missing service behavior.

- [ ] **Step 3: Implement conversion submission**

Reuse `create_conversion_jobs`/existing request-key semantics where compatible and `dispatch_committed_conversion_batch`. Before dispatch, bind each Job to its source item, commit batch state as `converting`, then dispatch `(job_id, attempt)` pairs. Failed/cancelled bound Jobs must pass through existing guarded retry transition rather than creating a parallel Job.

- [ ] **Step 4: Write failing synchronization tests**

Create succeeded Jobs with current-attempt AnalysisResults and derived `.dxf` Files. Assert sync sets `derived_dxf_file_id`, validates source/derived normalized stems, marks paired items, and makes the batch `ready_to_freeze`. Add stale-attempt, missing Result, deleted DXF, mismatched name and duplicate normalized DWG tests.

- [ ] **Step 5: Implement sync and diagnostics**

Only consume a Job whose ID and attempt match the bound generation. Select one succeeded AnalysisResult for `convert_dwg_to_dxf`, require an available `.dxf`, verify storage integrity and normalized name, then expose structured issues with item ID, filename, code, message and recommended action. Never advance a failed item silently.

- [ ] **Step 6: Verify GREEN and commit**

```bash
cd backend
uv run pytest -q tests/test_workflow_input_service.py -k 'conversion or sync or pairing'
git add app/services/workflow_input_service.py tests/test_workflow_input_service.py
git commit -m "feat: convert production DWG inputs to DXF"
```

### Task 4: Freeze a canonical manifest and create Drawings atomically

**Files:**
- Modify: `backend/app/services/workflow_input_service.py`
- Modify: `backend/app/services/workflow_service.py`
- Modify: `backend/tests/test_workflow_input_service.py`

- [ ] **Step 1: Write failing freeze tests**

Test rejection for zero DWG, zero/two Excel, active/failed conversion, duplicate stems, broken DXF lineage and already terminal workflow. Test a successful freeze creates exactly one Drawing and DWG DrawingVersion per source DWG, stable canonical manifest SHA-256, source artifacts, frozen timestamps and completed `source_intake`. Test identical replay returns the same manifest and Drawing IDs.

- [ ] **Step 2: Verify RED**

Run `cd backend && uv run pytest -q tests/test_workflow_input_service.py -k freeze`.

- [ ] **Step 3: Implement locked freeze**

Lock the batch and its items, re-run storage/format/job/result/pairing checks, sort manifest entries by normalized stem then file ID, and hash compact UTF-8 canonical JSON with sorted keys. Create Drawings with `drawing_no` equal to the preserved stem and a version sourced as `workflow_input_dwg`; record `drawing_id` on each item.

- [ ] **Step 4: Attach artifacts and complete the stage**

Attach each DWG as `source_file`, the single Excel as `source_excel`, and each generated DXF as `derived_dxf`. Extend the `source_intake` template artifact whitelist accordingly. Complete the stage through the existing workflow service only after every row is durable in the same transaction.

- [ ] **Step 5: Verify GREEN and commit**

```bash
cd backend
uv run pytest -q tests/test_workflow_input_service.py
git add app/services tests/test_workflow_input_service.py
git commit -m "feat: freeze validated production inputs"
```

### Task 5: Expose the workflow input API with permissions and audit

**Files:**
- Create: `backend/app/api/v1/workflow_inputs_api.py`
- Create: `backend/tests/test_workflow_input_api.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing HTTP contract tests**

Cover create/get/register/remove/convert/freeze, stable 200/201/202 codes, idempotent replay, project member vs owner/engineer permissions, cross-project File denial, frozen mutation rejection, and structured issue/error bodies. Assert the OpenAPI contains all six paths and descriptions.

- [ ] **Step 2: Verify RED**

Run `cd backend && uv run pytest -q tests/test_workflow_input_api.py` and confirm 404 route failures.

- [ ] **Step 3: Implement API routes**

Register a router below `/workflows/{workflow_id}/input-batch`. Load the workflow, require `linux_production`, apply existing project role helpers, call only service functions, write audit records for batch create, file register/remove, conversion submit/retry and freeze, commit before conversion dispatch, and return Pydantic read models.

- [ ] **Step 4: Verify GREEN and commit**

```bash
cd backend
uv run pytest -q tests/test_workflow_input_api.py tests/test_workflow_input_service.py tests/test_workflow_production.py
git add app/api/v1 app/schemas tests
git commit -m "feat: expose production input batch API"
```

### Task 6: Build the operator-safe React intake console

**Files:**
- Create: `frontend/src/types/workflow-input.ts`
- Create: `frontend/src/api/workflow-inputs.api.ts`
- Create: `frontend/src/features/workflows/ProductionInputPanel.tsx`
- Modify: `frontend/src/features/workflows/WorkflowsPage.tsx`
- Modify: `frontend/src/api/files.api.ts`
- Modify: `frontend/tests/e2e/workflow-input.spec.ts`

- [ ] **Step 1: Add a failing frontend contract test**

Extend the backend source contract test to require distinct `上传 DWG` and `上传 Excel` controls, accepted extensions, input-batch API use, conversion feedback, issue codes, disabled freeze reasons and confirmation text `冻结后不可修改`.

- [ ] **Step 2: Verify RED**

Run `cd backend && uv run pytest -q tests/test_frontend_contract.py -k workflow_input`.

- [ ] **Step 3: Add types and API client**

Model batch counts, item status/job/result/drawing fields, diagnostics, freeze readiness and manifest. Add create/get/register/remove/convert/freeze calls. Extend `uploadFile` to accept an optional request key without changing existing callers.

- [ ] **Step 4: Implement `ProductionInputPanel`**

Render a four-step rail and a dense file table. DWG input is multiple and accepts `.dwg`; Excel input accepts one `.xls,.xlsx`. Each selection gets a stable UUID key, uploads through `/files`, then registers its returned file ID. Preserve separate `uploaded` and `registered` outcomes so registration can be retried without re-upload. Poll while converting and invalidate queries after every mutation.

- [ ] **Step 5: Implement prevention and feedback**

Reject DXF/unknown types client-side with an aria-live message, disable second Excel selection, display exact backend issue code and recommended action per row, disable conversion without valid inputs, and disable freeze with a visible list of unmet conditions. Freeze uses `Popconfirm` with counts and irreversible wording. Frozen state is read-only and shows manifest digest plus Drawing links.

- [ ] **Step 6: Integrate only for `source_intake`**

In `WorkflowsPage`, replace the generic artifact picker with `ProductionInputPanel` only when the template is `linux_production` and current stage is `source_intake`; preserve generic controls for legacy and later stages.

- [ ] **Step 7: Add Playwright route-fixture tests**

Verify multi-DWG + one-Excel selection, second Excel blocking, DXF rejection without network call, partial upload error persistence, conversion transition, freeze disabled reasons, confirmation summary, successful frozen manifest and reload recovery.

- [ ] **Step 8: Build, run focused browser tests and commit**

```bash
cd frontend
npm run build
npx playwright test tests/e2e/workflow-input.spec.ts
git add src tests/e2e/workflow-input.spec.ts
git commit -m "feat: build production input freeze console"
```

### Task 7: Synchronize documentation and execute release gates

**Files:**
- Modify: `scripts/generate_api_docs.py`
- Regenerate: `docs/api.md`
- Modify: `docs/workflow-framework.md`
- Modify: `docs/architecture.md`
- Modify: `docs/processing-pipelines.md`
- Modify: `docs/database.md`
- Modify: `docs/workflow-verification.md`
- Modify: `README.md`, `CHANGELOG.md`, `CLAUDE.md` when counts/contracts change

- [ ] **Step 1: Document the exact boundary**

State consistently that human input is multiple DWG plus exactly one Excel; DXF is server-derived only. Document statuses, six endpoints, normalization, artifact whitelist, error codes, retry rules, manifest fields and “no unfreeze” boundary.

- [ ] **Step 2: Regenerate and check API docs**

```bash
cd backend
uv run python ../scripts/generate_api_docs.py
cd ..
make docs-check
```

- [ ] **Step 3: Run backend and migration gates**

```bash
cd backend
uv run ruff check app tests ../scripts
uv run pytest -q
uv run alembic check
cd ..
bash scripts/db.sh migration-test
```

- [ ] **Step 4: Run stage, frontend and infrastructure gates**

```bash
cd Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../dxf2excel && uv run pytest -q
cd ../excel_final && uv run pytest -q multi_split/tests
cd ../../frontend && npm run build && npx playwright test
cd ..
bash infra/verify.sh
docker compose config --quiet
```

- [ ] **Step 5: Run a real source-intake probe with the known CAD corpus**

Use two DWGs from `/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图` and generate one probe-owned readable `.xlsx` with `openpyxl`. Create one test workflow/project, upload these three files, submit conversion, wait for current attempts, verify both derived DXF headers/download digests, freeze, and assert Drawing count is two. Record IDs/counts/digests only; clean only probe-owned rows/objects.

- [ ] **Step 6: Review and final commit**

Run `git diff --check`, review permissions, transaction/dispatch boundaries, storage compensation and frozen mutation guards, then:

```bash
git add README.md CHANGELOG.md CLAUDE.md docs scripts backend frontend
git commit -m "docs: verify production input freeze release"
```
