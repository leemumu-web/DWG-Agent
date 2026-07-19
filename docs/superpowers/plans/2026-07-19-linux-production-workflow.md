# Linux Production Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a front-to-back Linux production workflow that reuses Files, Jobs, DXF-to-Excel, and Excel Final while exposing honest placeholder contracts for unfinished CAD/CAM stages.

**Architecture:** Extend the existing workflow registry and service instead of adding a second scheduler or storage model. Workflow stages bind existing Jobs and artifacts; route handlers validate HTTP permissions and feature gates, then dispatch only after commit. The React console consumes template metadata so implemented, manual, and placeholder stages stay synchronized with the backend.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2, Celery, React 19, TypeScript, TanStack Query, Ant Design, pytest, Playwright.

---

## File map

- `backend/app/schemas/workflow_schema.py`: template, artifact-binding, stage-execution, and response contracts.
- `backend/app/services/workflow_service.py`: canonical template registry, state invariants, idempotent artifact synchronization, and Job binding.
- `backend/app/api/v1/workflows_api.py`: permission-aware template/artifact/execution HTTP routes and post-commit dispatch.
- `backend/tests/test_workflow_production.py`: focused service and API behavior for the Linux production template.
- `backend/tests/test_frontend_contract.py`: source-level frontend/API contract regression.
- `frontend/src/types/workflow.ts`: backend template/execution/artifact contracts.
- `frontend/src/api/workflows.api.ts`: new template, artifact, and execution calls.
- `frontend/src/features/workflows/WorkflowsPage.tsx`: production workflow creation, file binding, execution forms, capability presentation, and artifact download.
- `docs/workflow-framework.md`, `docs/processing-pipelines.md`, `docs/workflow-verification.md`, `docs/architecture.md`, `README.md`: current behavior, boundaries, and verification evidence.
- `docs/api.md`: generated OpenAPI reference.

### Task 1: Lock the production template contract

**Files:**
- Modify: `backend/app/schemas/workflow_schema.py`
- Modify: `backend/app/services/workflow_service.py`
- Create: `backend/tests/test_workflow_production.py`

- [ ] **Step 1: Write failing template tests**

Add tests asserting `linux_production` creates exactly the nine ordered stage codes from the design and template metadata marks `excel_stage1`/`excel_final` implemented, CAM stages placeholder/external, and manual stages manual.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k template`

Expected: fail because `linux_production` and template metadata do not exist.

- [ ] **Step 3: Implement the registry and schemas**

Define typed `WorkflowStageCapability`, `WorkflowTemplateRead`, execution-mode/status literals, and one canonical registry consumed by both workflow creation and template serialization. Add `linux_production` to `WORKFLOW_TYPES`.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k template tests/test_workflow_framework.py tests/test_workflow_boundaries.py`

Expected: all selected tests pass and legacy templates keep their original stage order.

- [ ] **Step 5: Commit**

Run: `git add backend/app/schemas/workflow_schema.py backend/app/services/workflow_service.py backend/tests/test_workflow_production.py && git commit -m "feat: define Linux production workflow template"`

### Task 2: Add safe file and result artifact binding

**Files:**
- Modify: `backend/app/schemas/workflow_schema.py`
- Modify: `backend/app/services/workflow_service.py`
- Modify: `backend/app/api/v1/workflows_api.py`
- Modify: `backend/tests/test_workflow_production.py`

- [ ] **Step 1: Write failing artifact API tests**

Cover member success, non-member rejection, missing/deleted file rejection, unknown stage rejection, empty reference rejection, and idempotent repeat returning one artifact. Assert `source_intake` cannot complete until at least one file artifact exists.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k 'artifact or source_intake'`

Expected: 404 for the missing endpoint or missing invariant failures.

- [ ] **Step 3: Implement artifact service and route**

Add `WorkflowArtifactCreate`, validate StoredFile/AnalysisResult read access with existing helpers, and implement idempotent lookup on workflow/stage/type/file/result before insert. Keep bytes in existing storage; store references only.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k 'artifact or source_intake' tests/test_adversarial_files.py`

Expected: all tests pass without weakening file authorization.

- [ ] **Step 5: Commit**

Run: `git add backend/app/schemas/workflow_schema.py backend/app/services/workflow_service.py backend/app/api/v1/workflows_api.py backend/tests/test_workflow_production.py && git commit -m "feat: bind workflow file artifacts safely"`

### Task 3: Execute the real DXF-to-Excel stage

**Files:**
- Modify: `backend/app/schemas/workflow_schema.py`
- Modify: `backend/app/services/workflow_service.py`
- Modify: `backend/app/api/v1/workflows_api.py`
- Modify: `backend/tests/test_workflow_production.py`

- [ ] **Step 1: Write failing execution tests**

Test that `excel_stage1` accepts a non-empty accessible DXF batch, creates one `extract_dxf_to_excel` Job with the workflow project, binds its attempt, returns 202, uses an idempotency key on replay, and honors `DXF2EXCEL_PIPELINE_ENABLED=false` with 503. Test wrong/current stage and inaccessible batch rejection.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k excel_stage1`

Expected: fail because stage executions are not routed.

- [ ] **Step 3: Implement minimal execution bridge**

Add `WorkflowStageExecutionCreate`; validate current actionable stage and batch access; call existing `create_or_reuse_job()` with `TASK_DXF_TO_EXCEL`; bind the Job; commit before `dispatch_committed_job()`; expose OpenAPI summaries and typed response.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k excel_stage1 tests/test_dxf2excel_pipeline.py`

Expected: workflow and existing pipeline tests pass.

- [ ] **Step 5: Commit**

Run: `git add backend/app/schemas/workflow_schema.py backend/app/services/workflow_service.py backend/app/api/v1/workflows_api.py backend/tests/test_workflow_production.py && git commit -m "feat: run DXF to Excel from production workflows"`

### Task 4: Execute Excel Final and synchronize results

**Files:**
- Modify: `backend/app/services/workflow_service.py`
- Modify: `backend/app/api/v1/workflows_api.py`
- Modify: `backend/tests/test_workflow_production.py`

- [ ] **Step 1: Write failing Excel Final and sync tests**

Cover accessible Excel input, extension rejection, feature gate, idempotent Job creation, stage binding, generic successful Job advancing the next stage, result-file artifact creation, and repeated GET producing no duplicate artifacts.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k 'excel_final or sync'`

Expected: fail because the execution kind and generic result sync are absent.

- [ ] **Step 3: Implement Excel Final bridge and result sync**

Reuse `TASK_EXCEL_FINAL`, `create_or_reuse_job`, existing file access checks, feature flag, and committed dispatch. Generalize successful bound-Job advancement beyond the legacy `excel_process` name and idempotently attach each AnalysisResult/result file to the stage.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k 'excel_final or sync' tests/test_excel_final_idempotency.py tests/test_workflow_boundaries.py`

Expected: all tests pass, including replay and old workflow behavior.

- [ ] **Step 5: Commit**

Run: `git add backend/app/services/workflow_service.py backend/app/api/v1/workflows_api.py backend/tests/test_workflow_production.py && git commit -m "feat: connect Excel Final workflow stage"`

### Task 5: Expose honest placeholder and cancellation behavior

**Files:**
- Modify: `backend/app/services/workflow_service.py`
- Modify: `backend/app/api/v1/workflows_api.py`
- Modify: `backend/tests/test_workflow_production.py`

- [ ] **Step 1: Write failing boundary tests**

Assert placeholder/external execution returns 501 `WORKFLOW_STAGE_NOT_IMPLEMENTED` with required contract details, manual completion cannot bypass placeholder/automated stages, and workflow cancellation transitions the bound active Job through the existing guarded cancel service.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py -k 'placeholder or cancel or bypass'`

Expected: fail until new boundary checks exist.

- [ ] **Step 3: Implement the boundaries**

Use template execution metadata as the only decision source. Return business-safe details with no traceback, DSN, path, or secret. Cancel an active bound Job before cancelling open workflow stages.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && uv run pytest -q tests/test_workflow_production.py tests/test_workflow_boundaries.py tests/test_security_boundaries.py`

Expected: all pass.

- [ ] **Step 5: Commit**

Run: `git add backend/app/services/workflow_service.py backend/app/api/v1/workflows_api.py backend/tests/test_workflow_production.py && git commit -m "feat: expose workflow placeholder boundaries"`

### Task 6: Build the synchronized production workflow UI

**Files:**
- Modify: `frontend/src/types/workflow.ts`
- Modify: `frontend/src/api/workflows.api.ts`
- Modify: `frontend/src/features/workflows/WorkflowsPage.tsx`
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing frontend contract tests**

Assert the frontend declares and calls template, artifact, and execution endpoints; supports `linux_production`; imports existing file list/download APIs; and renders implementation status instead of a blanket “CAD out of scope” statement.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_frontend_contract.py -k workflow`

Expected: fail on missing calls and stale wording.

- [ ] **Step 3: Implement the UI**

Add typed query/mutations; use backend template metadata; add a file management panel with batch/extension filtering, file binding and artifact download; add execution forms for batch name and Excel file; present placeholder contracts visibly; retain responsive tables and accessible labels.

- [ ] **Step 4: Verify GREEN and build**

Run: `cd backend && uv run pytest -q tests/test_frontend_contract.py -k workflow && cd ../frontend && npm run build`

Expected: contract tests pass and Vite production build exits 0.

- [ ] **Step 5: Commit**

Run: `git add frontend/src/types/workflow.ts frontend/src/api/workflows.api.ts frontend/src/features/workflows/WorkflowsPage.tsx backend/tests/test_frontend_contract.py && git commit -m "feat: build production workflow control surface"`

### Task 7: Synchronize OpenAPI and project documentation

**Files:**
- Modify: `docs/workflow-framework.md`
- Modify: `docs/processing-pipelines.md`
- Modify: `docs/workflow-verification.md`
- Modify: `docs/architecture.md`
- Modify: `README.md`
- Generate: `docs/api.md`

- [ ] **Step 1: Write a failing documentation assertion**

Add a focused docs consistency assertion for `linux_production`, stage execution/artifact routes, implemented-vs-placeholder matrix, and removal of the old “route not wired” statement.

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_docs_consistency.py -k workflow`

Expected: fail until documentation is updated.

- [ ] **Step 3: Update hand-written docs and generate API docs**

Document exact current behavior, flags, external dependencies, error contracts, and verification date. Run `make docs-generate` only after route/test changes.

- [ ] **Step 4: Verify GREEN**

Run: `make docs-check`

Expected: generated path/operation counts and all cross-links are consistent.

- [ ] **Step 5: Commit**

Run: `git add README.md docs backend/tests/test_docs_consistency.py && git commit -m "docs: document Linux production workflow API"`

### Task 8: End-to-end verification and review

**Files:**
- Modify if needed: files identified by failing tests or review
- Update: `docs/workflow-verification.md`

- [ ] **Step 1: Run focused and full backend gates**

Run: `cd backend && uv run ruff check app tests ../tests/run_full_verify.py && uv run pytest -q && uv run alembic check`

Expected: no failures; existing documented warnings/skips only.

- [ ] **Step 2: Run Stage, infrastructure, and frontend gates**

Run the commands from `CLAUDE.md`: four Stage pytest suites, `bash scripts/db.sh migration-test`, `bash infra/verify.sh`, `docker compose config --quiet`, `frontend/npm run build`, and `npx playwright test`.

Expected: all available gates pass; any sample-dependent skip remains described as unverified, not passed.

- [ ] **Step 3: Run a server-side workflow probe**

Against an isolated TestClient or configured local stack, create a project/workflow, bind files, advance manual stages, execute enabled Linux stages with valid input, poll Job synchronization, and verify result artifact references/download bytes. Exercise every placeholder endpoint and confirm 501 contracts.

- [ ] **Step 4: Request code review and fix findings**

Review the complete diff against the design, with special attention to authorization, dispatch-after-commit, idempotency, active Job cancellation, artifact duplication, UI/backend contract drift, and unsupported production claims. Fix every critical/important finding and rerun impacted gates.

- [ ] **Step 5: Record final evidence and commit**

Update `docs/workflow-verification.md` with exact commands/counts/date/boundaries, then run `make docs-check` and commit as `test: verify Linux production workflow end to end`.
