# PL Stage and Workflow Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the complete PL splitter into its own Stage package and connect PL-only production splitting plus independent validation to the `pl_xbox_split` workflow stage defined by the frontend contract.

**Architecture:** `Stages/steel_dxf_split_pl` becomes the deep PL Module and owns parsing, geometry, development, writing, batch orchestration, and CLI behavior behind `split_pl()` and `steel-dxf-split-pl`. The backend remains an Adapter: it freezes classifier-confirmed PL inputs, invokes the Stage in a subprocess, independently validates saved DXFs, records results in the existing split ledger, and projects the PL run through the workflow interface. XBOX is not a candidate in this delivery; it remains classification-only and the interface documents its future seam.

**Tech Stack:** Python 3.12, ezdxf 1.4.4, Shapely 2.1, FastAPI, SQLAlchemy, Celery, pytest, React/TypeScript, Vitest.

**Spec:** `docs/architecture/pl-xbox-split-frontend-contract.md` from frontend commit `2c1d8b71`, refined by the 2026-08-30 user decision that this delivery processes PL only and reserves XBOX for a later implementation.

## Global Constraints

- Only classifier-confirmed `PL` drawings enter the new Stage; `XBOX`, `BH`, `BOX`, and all other types remain classification-only in this stage.
- Do not call or modify BH, BOX, XBOX, or HW geometry implementations.
- PL produces one normal DXF per accepted drawing and no weld-allowance DXF.
- PL geometry rules remain unchanged: `K=0.5`, target length is rounded upward to one decimal place, and output must never be shorter than the proved source requirement.
- Backend validation must read the saved DXF independently; a successful Stage exit or report is not sufficient for automatic acceptance.
- The existing `drawing_processing` BH/BOX stage and its routes remain backward compatible.
- Frontend and interface documentation must state that XBOX is reserved and not processed by this delivery.
- Push only the PL feature branch; do not force-push or rewrite `origin/main`.

---

### Task 1: Preserve the verified PL baseline and synchronize the frontend contract

**Files:**
- Commit existing verified changes under `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/`
- Commit existing PL regressions in `backend/tests/dxf_splitting/test_pl_splitter.py`
- Merge: `origin/main` including frontend commit `2c1d8b71`

**Interfaces:**
- Consumes: current `steel_dxf_split.pl.split_pl()` behavior and the remote frontend contract.
- Produces: one branch containing the verified PL baseline and the frontend `pl_xbox_split` UI/types/client.

- [ ] **Step 1: Re-run the current DXF splitting suite**

Run the repository test environment against `backend/tests/dxf_splitting` and require `271 passed, 52 skipped` before checkpointing.

- [ ] **Step 2: Commit the existing verified PL corrections**

Stage only the seven existing PL implementation/test files and commit them as `fix(pl): preserve professional flat plate outlines`.

- [ ] **Step 3: Merge the current remote main**

Run `git merge --no-edit origin/main`, inspect every changed path, and resolve only genuine PL/frontend integration conflicts.

- [ ] **Step 4: Re-run the DXF splitting baseline**

Run `python -m pytest backend/tests/dxf_splitting -q --disable-warnings`; any failure introduced by the merge must be resolved before Task 2.

### Task 2: Make `steel_dxf_split_pl` own the complete PL implementation

**Files:**
- Modify: `Stages/steel_dxf_split_pl/pyproject.toml`
- Modify: `Stages/steel_dxf_split_pl/README.md`
- Replace: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/cli.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/compiler.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/contracts.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/development.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/geometry.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/longitudinal.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/source.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/writer.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/dxf_io.py`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/part_mark_layout.py`
- Modify: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/__init__.py`
- Delete: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/`
- Modify tests: `backend/tests/dxf_splitting/test_pl_splitter.py`, `test_pl_real_corpus.py`, `test_pl_paired_corpus.py`, `test_pl_carrier_unfolding.py`

**Interfaces:**
- Consumes: only ezdxf, Shapely, and Python standard-library dependencies.
- Produces: `steel_dxf_split_pl.split_pl(input_path: str | Path, output_dir: str | Path, *, overwrite: bool = False) -> PLBatchResult` and the `steel-dxf-split-pl` command.

- [ ] **Step 1: Write the failing ownership test**

Add a clean-subprocess test that imports `steel_dxf_split_pl`, splits a generated PL source, and asserts that no `steel_dxf_split.pl`, `steel_dxf_split.pipeline`, BH, or BOX implementation module is loaded.

- [ ] **Step 2: Verify RED**

Run the ownership test and confirm it fails because `steel_dxf_split_pl` still forwards to `steel_dxf_split.pl`.

- [ ] **Step 3: Move the implementation and remove shared-package imports**

Move the nine PL modules into `steel_dxf_split_pl`; internalize the two generic DXF/text-layout helpers used by PL; declare direct `ezdxf==1.4.4` and `shapely>=2.1,<3` dependencies; remove the `steel-dxf-split` dependency and old PL package.

- [ ] **Step 4: Update tests to use the new interface**

Replace `steel_dxf_split.pl` imports with `steel_dxf_split_pl` and keep every existing behavior assertion unchanged.

- [ ] **Step 5: Verify GREEN and build isolation**

Run all PL unit/corpus tests, build a wheel for `Stages/steel_dxf_split_pl`, install it into a clean target directory, and run the CLI smoke test without `steel_dxf_split_v1.5.2` on `PYTHONPATH`.

- [ ] **Step 6: Commit the Stage migration**

Commit as `refactor(pl): move splitter into standalone stage`.

### Task 3: Add the PL-only classifier and subprocess seams

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/modules/dxf_classification/persistence.py`
- Modify: `backend/app/modules/dxf_classification/interface.py`
- Create: `backend/app/modules/dxf_splitting/pl_adapter.py`
- Test: `backend/tests/dxf_splitting/test_pl_workflow.py`

**Interfaces:**
- Consumes: classifier items and the Stage CLI.
- Produces: `list_pl_split_candidate_inputs(db, workflow_id) -> list[DxfSplitCandidateInput]`, `PL_SOURCE_CONTRACT_ID = "project_tekla_pl_dxf_v1"`, and `invoke_pl_splitter(input_dir, output_dir, *, timeout_seconds) -> PlSplitterResult`.

- [ ] **Step 1: Write failing candidate-routing tests**

Create a real database workflow fixture containing PL, XBOX, BH, BOX, and unresolved classification items. Assert that the PL loader returns only PL and the existing BH/BOX loader remains unchanged.

- [ ] **Step 2: Verify RED**

Run the new tests and confirm failure because `list_pl_split_candidate_inputs` does not exist.

- [ ] **Step 3: Implement the narrow classifier seam**

Add the PL-only loader without widening the existing `{BH, BOX}` predicate. Add the local path dependency for `steel-dxf-split-pl` and implement a subprocess Adapter that authorizes only `project_tekla_pl_dxf_v1`.

- [ ] **Step 4: Verify GREEN**

Run the new routing/adapter tests and the existing classified-dispatch tests.

- [ ] **Step 5: Commit**

Commit as `feat(pl): add classifier and stage adapters`.

### Task 4: Independently validate and persist PL results

**Files:**
- Create: `backend/app/modules/dxf_splitting/pl_validation.py`
- Create: `backend/app/modules/dxf_splitting/pl_execution.py`
- Modify: `backend/app/modules/dxf_splitting/persistence.py`
- Modify: `backend/app/modules/dxf_splitting/presentation.py`
- Modify: `backend/app/modules/dxf_splitting/interface.py`
- Test: `backend/tests/dxf_splitting/test_pl_workflow.py`

**Interfaces:**
- Consumes: frozen PL inputs, `pl_split_report.json`, and generated DXFs.
- Produces: `validate_pl_output(source, output, report_item) -> PlIndependentValidation`, `run_pl_dxf_splitting(job_id, worker_name=None) -> None`, and a `DxfSplitRun`/`DxfSplitItem` ledger bound to the PL job attempt.

- [ ] **Step 1: Write failing independent-validation tests**

Use real DXFs to cover: valid one-polygon output, open outline, audit error, wrong `p=` label, downward length, width mismatch, and Stage success with missing output. Assert only the valid output receives `automation_route="auto_accepted"`.

- [ ] **Step 2: Verify RED**

Run each test and confirm it fails at the missing PL validation/execution interface.

- [ ] **Step 3: Implement independent validation**

Read the saved DXF with backend ezdxf, polygonize native `PLATE_CUT` curves, verify one valid closed material polygon, exact `p=<part_number>` label, millimetre units, width tolerance `0.1 mm`, target length rounded upward to one decimal, and no downward result.

- [ ] **Step 4: Implement attempt-bound persistence**

Reuse `DxfSplitRun` and `DxfSplitItem` keyed by the PL job/attempt; store family `PL`, source contract `project_tekla_pl_dxf_v1`, normal output only, null allowance output, diagnostics, validation, manifest, and a generic projected `split_ledger_file`.

- [ ] **Step 5: Verify GREEN and retry safety**

Run validation, persistence, idempotency, stale-attempt, storage-failure, and partial-success tests.

- [ ] **Step 6: Commit**

Commit as `feat(pl): persist independently validated split results`.

### Task 5: Insert `pl_xbox_split` into workflow execution

**Files:**
- Modify: `backend/app/modules/workflows/templates.py`
- Modify: `backend/app/modules/workflows/schemas/orchestration.py`
- Modify: `backend/app/modules/workflows/stage_execution.py`
- Modify: `backend/app/modules/dxf_splitting/tasks.py`
- Modify: `backend/app/bootstrap/task_registry.py`
- Modify: `backend/app/modules/jobs/dispatch.py`
- Modify: `backend/app/modules/workflows/job_sync.py`
- Modify: workflow and splitting tests

**Interfaces:**
- Consumes: generic stage execution request with `execution_kind="pl_xbox_split"`.
- Produces: a PL job dispatched after `dxf_classification` and completed before `drawing_processing`.

- [ ] **Step 1: Write failing workflow-order and dispatch tests**

Assert the production template order is `dxf_classification -> pl_xbox_split -> drawing_processing`; executing the new stage creates one PL split Job; XBOX-only input completes as `no_split_candidates` without calling BOX.

- [ ] **Step 2: Verify RED**

Run the focused workflow tests and confirm the stage and execution kind are rejected.

- [ ] **Step 3: Implement stage preparation, task dispatch, and synchronization**

Wire `pl_xbox_split` to the PL-only loader and `run_pl_dxf_splitting`; retain the existing drawing-processing retry and output contracts unchanged.

- [ ] **Step 4: Verify GREEN**

Run workflow production, job synchronization, stage migration, and DXF contract tests.

- [ ] **Step 5: Commit**

Commit as `feat(workflows): execute PL split before drawing processing`.

### Task 6: Expose the frontend-compatible PL run and exports

**Files:**
- Modify: `backend/app/modules/workflows/routes/splitting.py`
- Modify: `backend/app/modules/workflows/schemas/exports.py`
- Modify: `backend/app/modules/dxf_splitting/selective_exports.py`
- Modify: `backend/app/modules/workflows/batch_exports.py`
- Modify: `backend/tests/contracts/test_frontend_contract.py`
- Modify: splitting/workflow export tests

**Interfaces:**
- Consumes: the PL job/attempt ledger.
- Produces: `GET /{workflow_id}/pl-xbox-split`, PL selective export preview/create routes, and normal-result batch export membership.

- [ ] **Step 1: Write failing HTTP contract tests**

Assert the GET response matches every `PlXboxSplitRun` field, only the current PL attempt is visible, selective categories are `failed_pl`, `failed_xbox`, and `other`, `failed_xbox` is empty/reserved, and the normal result ZIP contains only independently accepted PL outputs.

- [ ] **Step 2: Verify RED**

Run the focused route tests and confirm 404/missing-schema failures.

- [ ] **Step 3: Implement the route and export projections**

Resolve runs by the `pl_xbox_split` stage's exact job/attempt rather than “latest run”; expose `split_ledger_file`; include PL normal artifacts in generic `split_result_normal`; never emit an allowance artifact for PL.

- [ ] **Step 4: Verify GREEN**

Run the route, selective export, batch export, authorization, stale-generation, and archive tests.

- [ ] **Step 5: Commit**

Commit as `feat(workflows): expose PL split run and exports`.

### Task 7: Align frontend copy and handoff documentation with PL-only delivery

**Files:**
- Modify: `frontend/src/features/workflows/PlXboxDrawingProcessingPanel.tsx`
- Modify: `docs/architecture/pl-xbox-split-frontend-contract.md`
- Modify: `E:/桌面/PL板材/pl-xbox-split-frontend-contract(1).md`
- Test: frontend workflow tests and backend frontend-contract tests

**Interfaces:**
- Consumes: the implemented PL backend interface.
- Produces: truthful UI text and a handoff contract that marks XBOX endpoints/types as reserved for the next implementer.

- [ ] **Step 1: Write the failing frontend behavior test**

Render the panel with PL accepted and XBOX classification-only items; assert the page says “当前仅处理 PL” and does not claim that XBOX was split or independently validated.

- [ ] **Step 2: Verify RED**

Run the focused frontend test and confirm the current PL/XBOX success copy fails the assertion.

- [ ] **Step 3: Update UI copy and both contract copies**

Keep the `pl_xbox_split` code and route names stable, document current PL behavior, list XBOX as classification-only/reserved, and enumerate the exact XBOX extension seams for the next implementer.

- [ ] **Step 4: Verify GREEN**

Run frontend typecheck/tests and backend contract tests.

- [ ] **Step 5: Commit**

Commit as `docs(workflows): hand off reserved XBOX integration`.

### Task 8: Full verification and push

**Files:**
- No new production files.

**Interfaces:**
- Consumes: all previous deliverables.
- Produces: a verified remote PL feature branch and an updated handoff document.

- [ ] **Step 1: Run Stage verification**

Run all PL tests, wheel build, clean-install CLI smoke test, Ruff, and `git diff --check`.

- [ ] **Step 2: Run backend verification**

Run `backend/tests/dxf_splitting`, workflow production/contracts/exports, architecture tests, and migrations against the repository test environment.

- [ ] **Step 3: Run frontend verification**

Run frontend typecheck, lint, and workflow tests using the repository package manager lockfile.

- [ ] **Step 4: Run one real PL smoke batch**

Invoke the backend Stage Adapter on `FJ-F3-cb-121.dxf`; require one accepted PL item, seven closed cut segments, `p=FJ-F3-cb-121`, `1399.5 x 675.0 mm`, and zero DXF audit errors.

- [ ] **Step 5: Audit scope**

Confirm no BH/BOX/HW geometry file changed, no XBOX output was produced, no generated build/cache artifact is tracked, and the external handoff document matches the repository copy.

- [ ] **Step 6: Push the feature branch**

Push `codex/pl-professional-compare` without force and report the exact remote commit and handoff-document path.
