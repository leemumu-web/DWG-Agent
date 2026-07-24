# Production Workflow Stage 2 and Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This repository must be executed inline because the user explicitly prohibited sub-agents.

**Goal:** Add an honest Excel Stage 2 placeholder to the authoritative production workflow and refine the workflow UI into a concise industrial console with muted waiting-launch stages and grouped production evidence.

**Architecture:** The backend template remains the single source of truth for stage order, capability status, execution kind, and artifact flow. New workflows use definition revision 4 and ten persisted stages; existing workflows keep their persisted stage rows. The frontend derives future-capability styling from stable stage codes and backend capability metadata, while focused components own the artifact summary and waiting-launch presentation.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, React, TypeScript, Ant Design, TanStack Query, CSS, Pytest, Playwright.

---

## File map

- Modify `backend/app/modules/workflows/templates.py`: ten-stage authoritative template and `stage2_excel` flow.
- Modify `backend/app/modules/workflows/lifecycle.py`: new Linux workflow definition revision.
- Modify `backend/app/modules/workflows/schemas/orchestration.py`: closed `excel_stage2` execution-kind contract.
- Modify `backend/tests/workflows/test_workflow_production.py`: stage order, capability, revision, and no-Job execution rejection.
- Modify `backend/tests/workflows/test_workflow_dxf_contracts.py`: exact drawing/Excel/CAM artifact lineage.
- Modify `backend/tests/contracts/test_docs_consistency.py`: documented ten-stage contract.
- Create `frontend/src/features/workflows/WorkflowArtifactSummary.tsx`: grouped evidence count and full archive download.
- Create `frontend/src/features/workflows/FutureStageNotice.tsx`: waiting-launch boundary with no actions.
- Modify `frontend/src/features/workflows/model/workflowPresentation.tsx`: waiting-launch stage classification and labels.
- Modify `frontend/src/features/workflows/WorkflowStageRail.tsx`: muted rail state and accessible waiting-launch text.
- Modify `frontend/src/features/workflows/WorkflowDetailPage.tsx`: compose the focused panels and simplify drawing placeholder.
- Modify `frontend/src/features/workflows/styles.css`: polished industrial hierarchy, responsive behavior, and muted future states.
- Modify `frontend/tests/e2e/workflows/workflow-detail.spec.ts`: ten-stage rail, future-state safety, grouped evidence, and download.
- Modify `frontend/tests/e2e/workflows/workflow-input.spec.ts`: ten-stage fixture consistency.
- Modify workflow READMEs and architecture/reference documents discovered by the final stale-term scan.

### Task 1: Lock the ten-stage backend contract

**Files:**
- Modify: `backend/tests/workflows/test_workflow_production.py`
- Modify: `backend/tests/workflows/test_workflow_dxf_contracts.py`
- Modify: `backend/tests/contracts/test_docs_consistency.py`

- [ ] **Step 1: Write the failing order and capability assertions**

Update the exact stage list to:

```python
[
    "source_intake",
    "dxf_classification",
    "drawing_processing",
    "excel_stage1",
    "excel_stage2",
    "design_barrier",
    "cam_packaging",
    "windows_cam",
    "result_acceptance",
    "delivery_archive",
]
```

Assert revision 4 and this capability:

```python
stage2 = stages["excel_stage2"]
assert stage2.execution_mode == "placeholder"
assert stage2.implementation_status == "placeholder"
assert stage2.execution_kind == "excel_stage2"
assert stage2.required_inputs == ["stage1_excel", "processed_dxf"]
assert stage2.artifact_types == ["stage2_excel"]
assert stage2.required_outputs == ["stage2_excel"]
```

Change downstream expectations so `design_barrier`, `cam_packaging`, and
`delivery_archive` consume `stage2_excel`.

- [ ] **Step 2: Write the failing no-execution assertion**

Create a Linux workflow, move its persisted current stage to `excel_stage2`, call
`prepare_stage_execution()` with:

```python
WorkflowStageExecutionCreate(execution_kind="excel_stage2")
```

Assert status 501, code `WORKFLOW_STAGE_NOT_IMPLEMENTED`, and that the Job count is unchanged.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q \
  tests/workflows/test_workflow_production.py \
  tests/workflows/test_workflow_dxf_contracts.py \
  tests/contracts/test_docs_consistency.py
```

Expected: failures for the absent `excel_stage2`, revision 3, old artifact lineage, and the schema literal.

- [ ] **Step 4: Commit the failing tests**

```bash
git add backend/tests/workflows/test_workflow_production.py \
  backend/tests/workflows/test_workflow_dxf_contracts.py \
  backend/tests/contracts/test_docs_consistency.py
git commit -m "test(workflows): define ten-stage production contract"
```

### Task 2: Implement the backend template and execution boundary

**Files:**
- Modify: `backend/app/modules/workflows/templates.py`
- Modify: `backend/app/modules/workflows/lifecycle.py`
- Modify: `backend/app/modules/workflows/schemas/orchestration.py`

- [ ] **Step 1: Add the Stage 2 capability**

Insert after `excel_stage1`:

```python
_stage(
    "excel_stage2",
    "Excel 第二阶段处理",
    "预留第一阶段 Excel 与已处理图纸的最终合并和生产表生成接口。",
    execution_mode="placeholder",
    implementation_status="placeholder",
    execution_kind="excel_stage2",
    required_inputs=("stage1_excel", "processed_dxf"),
    artifact_types=("stage2_excel",),
    required_outputs=("stage2_excel",),
),
```

Replace downstream `stage1_excel` final-input references with `stage2_excel` while retaining
`stage1_excel` only as the direct output of `excel_stage1`.

- [ ] **Step 2: Bump the new-workflow definition revision**

In `lifecycle.py`, set:

```python
config["definition_revision"] = 4
```

Do not migrate or rewrite existing workflow stage rows.

- [ ] **Step 3: Admit the closed execution payload**

Add `"excel_stage2"` to `WorkflowStageExecutionCreate.execution_kind`. The generic
`implementation_status != "implemented"` guard must return 501 before any Job is created.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Task 1 command.

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/workflows/templates.py \
  backend/app/modules/workflows/lifecycle.py \
  backend/app/modules/workflows/schemas/orchestration.py
git commit -m "feat(workflows): add Excel second-stage contract"
```

### Task 3: Lock the refined frontend behavior

**Files:**
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`
- Modify: `frontend/tests/e2e/workflows/workflow-input.spec.ts`
- Modify: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: Expand fixtures to ten stages**

Insert an `excel_stage2` stage/capability after `excel_stage1`, shift later sequences, and set
`definition_revision: 4`. Mark Stage 2 as placeholder with `stage2_excel`.

- [ ] **Step 2: Add waiting-launch assertions**

Assert the rail contains ten stages. For Excel Stage 2, CAM packaging, Windows CAM,
result acceptance, and delivery archive, assert visible `等待上线`. Select each and assert:

```typescript
await expect(page.getByText('能力等待上线')).toBeVisible();
await expect(page.getByRole('button', { name: /运行|确认当前阶段/ })).toHaveCount(0);
```

Assert drawing processing uses `能力预留` and no longer renders `项目总进度` or the repeated
`未接入` metric grid.

- [ ] **Step 3: Add grouped artifact assertions**

Provide duplicate artifact types in the fixture and assert:

```typescript
await expect(page.getByText('已登记 4 项')).toBeVisible();
await expect(page.getByText('classified_dxf × 2')).toBeVisible();
await expect(page.getByText('source_excel × 1')).toBeVisible();
await expect(page.getByText(/版本 v/)).toHaveCount(0);
await expect(page.getByText(/已登记 ·/)).toHaveCount(0);
await expect(page.getByRole('button', { name: '下载全部' })).toBeVisible();
```

Add an empty fixture assertion for the single-line `暂无已登记产物`.

- [ ] **Step 4: Run and verify RED**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows
cd ../backend
uv run pytest -q tests/contracts/test_frontend_contract.py
```

Expected: failures for missing Stage 2, old placeholder text/metrics, and the per-artifact ledger.

- [ ] **Step 5: Commit the failing tests**

```bash
git add frontend/tests/e2e/workflows \
  backend/tests/contracts/test_frontend_contract.py
git commit -m "test(workflows): lock polished ten-stage console"
```

### Task 4: Implement focused frontend presentation components

**Files:**
- Create: `frontend/src/features/workflows/WorkflowArtifactSummary.tsx`
- Create: `frontend/src/features/workflows/FutureStageNotice.tsx`
- Modify: `frontend/src/features/workflows/model/workflowPresentation.tsx`
- Modify: `frontend/src/features/workflows/WorkflowStageRail.tsx`
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`

- [ ] **Step 1: Add future-stage classification**

Export:

```typescript
export const WAITING_LAUNCH_STAGES = new Set([
  'excel_stage2',
  'cam_packaging',
  'windows_cam',
  'result_acceptance',
  'delivery_archive',
]);

export function isWaitingLaunchStage(stageCode?: string) {
  return Boolean(stageCode && WAITING_LAUNCH_STAGES.has(stageCode));
}
```

Render `等待上线` for this set and retain `能力预留` for drawing processing.

- [ ] **Step 2: Build the artifact summary**

Aggregate with a `Map<string, number>`, sort by type, render `已登记 N 项`, tags formatted as
`${type} × ${count}`, and one `下载全部` mutation button. Render only
`暂无已登记产物` when empty.

- [ ] **Step 3: Build the waiting-launch notice**

Render a compact semantic section with title `能力等待上线`, a `等待上线` tag, and:

```text
流程位置与数据接口已经预留，执行能力将在后续版本接入。
```

It must contain no mutation or completion control.

- [ ] **Step 4: Simplify the detail workspace**

Replace the local artifact ledger with `WorkflowArtifactSummary`. Suppress stage archive and
manual confirmation controls for waiting-launch stages. Replace the drawing placeholder metrics
with one boundary card containing its required inputs and outputs.

- [ ] **Step 5: Run type checking and focused tests**

Run:

```bash
cd frontend
npm run build
npx playwright test tests/e2e/workflows
```

Expected: production build and all workflow specs pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/workflows \
  frontend/tests/e2e/workflows \
  backend/tests/contracts/test_frontend_contract.py
git commit -m "feat(workflows): polish staged production console"
```

### Task 5: Apply the refined industrial visual system

**Files:**
- Modify: `frontend/src/features/workflows/styles.css`

- [ ] **Step 1: Style the implemented hierarchy**

Use existing workflow CSS variables. Tighten headings and cards; apply clear focus-visible outlines;
keep current stage high contrast and completed stages low-saturation green.

- [ ] **Step 2: Style waiting-launch states**

Add `.is-waiting-launch` rail styling with a gray-blue surface, dashed border, muted text, and a
text badge. Do not reduce text contrast below readable levels.

- [ ] **Step 3: Style evidence and responsive behavior**

Use a wrapping flex row for type tags, a compact single-line empty state, and stack the summary
button below content under the existing narrow-screen breakpoint.

- [ ] **Step 4: Verify build and browser behavior**

Run:

```bash
cd frontend
npm run build
npx playwright test tests/e2e/workflows
```

Expected: no overflow or locator failures; all workflow specs pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/workflows/styles.css
git commit -m "style(workflows): refine industrial stage hierarchy"
```

### Task 6: Synchronize documentation and architecture contracts

**Files:**
- Modify: `backend/app/modules/workflows/README.md`
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `frontend/tests/e2e/workflows/README.md`
- Modify: `docs/architecture/workflow.md`
- Modify: `docs/architecture/overview.md`
- Modify: `docs/architecture/implementation-status.md`
- Modify: `docs/reference/api.md` through the repository generator if OpenAPI changes.
- Modify: any non-historical file returned by the stale-term scan.

- [ ] **Step 1: Replace current nine-stage claims**

Run:

```bash
rg -n "九阶段|9 个阶段|definition_revision 3|stage1_excel.*最终|唯一.*Excel.*阶段" \
  README.md README_EN.md backend frontend docs \
  --glob '!**/superpowers/**' --glob '!frontend/node_modules/**'
```

Update normative documents to ten stages, definition revision 4, Stage 2 waiting launch, and
`stage2_excel` downstream lineage. Preserve dated historical verification sections.

- [ ] **Step 2: Document the visual boundary**

State that Stage 2 and CAM-through-delivery are visually muted and non-executable, while the
production evidence section groups artifacts by type and retains the full archive download.

- [ ] **Step 3: Refresh generated contracts**

Run:

```bash
cd backend
uv run python ../scripts/architecture/snapshot_contracts.py --write
uv run python ../scripts/docs/generate_api.py
cd ..
make docs-check
```

Expected: generated references match source and documentation check passes.

- [ ] **Step 4: Commit**

```bash
git add README.md README_EN.md backend frontend docs
git commit -m "docs(workflows): publish ten-stage production console"
```

### Task 7: Full verification, runtime refresh, and remote release

**Files:**
- Modify: `docs/verification/current.md`

- [ ] **Step 1: Run focused and full gates**

Run:

```bash
bash scripts/verify.sh quick
cd backend && uv run pytest -q && uv run alembic check
cd ../frontend && npx playwright test
cd ..
```

Expected: quick gate has no failures; backend and Playwright complete without failures.

- [ ] **Step 2: Verify the active service**

Ensure no active workflow Job will be interrupted, then restart stale FastAPI only if required:

```bash
bash scripts/status.sh
bash scripts/start-all.sh --restart-backend
bash scripts/status.sh
```

Expected: runtime source current, health/readiness 200, frontend dist current.

- [ ] **Step 3: Record exact evidence**

Append the actual test counts, skipped tests, OpenAPI path count, active definition revision, and
runtime status to `docs/verification/current.md`. Do not claim Excel Stage 2 or CAM execution.

- [ ] **Step 4: Final repository audit**

Run:

```bash
git diff --check
git status --short
git log --oneline origin/main..HEAD
```

Confirm `Stages/excel_final/data/` and `output/` remain untracked and unstaged.

- [ ] **Step 5: Commit and push**

```bash
git add docs/verification/current.md
git commit -m "docs(verification): record ten-stage console release"
git fetch origin
git push origin main
git rev-parse HEAD
git rev-parse origin/main
```

Expected: local and remote main hashes are identical.
