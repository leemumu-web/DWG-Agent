# DXF Splitting Complete Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the production `drawing_processing` stage with real batch progress, auditable per-drawing review decisions, ZIP-only outputs, and a safe handoff to Excel.

**Architecture:** Extend the existing `dxf_splitting` domain instead of replacing it. Keep machine outcomes immutable in `DxfSplitItem`, store human decisions in a separate versioned table, expose current-attempt review and archive routes through the workflow adapter, and render the projection in the existing `DrawingProcessingPanel`.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, MySQL, Celery, MinIO-backed Files, React, TypeScript, Ant Design, TanStack Query, Playwright, Pytest.

---

## File Map

- Create `backend/migrations/versions/a7d9e4c1b620_add_dxf_split_review_decisions.py`: run progress, candidate file references and review decision persistence.
- Modify `backend/app/modules/dxf_splitting/models.py`: isolated candidate references, human decision relationship and row.
- Modify `backend/app/modules/dxf_splitting/schemas.py`: progress, review page, decision, and completion contracts.
- Create `backend/app/modules/dxf_splitting/review.py`: current-attempt checks, locking, decision validation, completion, and final handoff rules.
- Modify `backend/app/modules/dxf_splitting/persistence.py`: progress projection and result archive members.
- Modify `backend/app/modules/dxf_splitting/adapter.py`: consume the CLI progress sidecar.
- Modify `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/cli.py`: atomically publish per-file progress without changing geometry behavior.
- Modify `backend/app/modules/dxf_splitting/presentation.py`: public run/read projection.
- Modify `backend/app/modules/dxf_splitting/interface.py`: stable public boundary.
- Modify `backend/app/modules/workflows/routes/splitting.py`: review and result ZIP endpoints.
- Modify `backend/app/modules/workflows/job_sync.py`: review completion to stage completion.
- Modify `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`: domain and HTTP behavior.
- Modify `backend/tests/workflows/test_workflow_production.py`: workflow transition and Excel handoff.
- Modify architecture, migration, runtime-contract, API and database contract files required by repository gates.
- Modify `frontend/src/features/workflows/workflow.ts`: frontend contracts.
- Modify `frontend/src/features/workflows/workflows.api.ts`: review and ZIP calls.
- Modify `frontend/src/features/workflows/DrawingProcessingPanel.tsx`: complete operator console.
- Modify `frontend/src/features/workflows/styles.css`: compact industrial layout.
- Modify `frontend/tests/e2e/workflows/workflow-detail.spec.ts`: real operator flow.

### Task 1: Persist immutable human review decisions

**Files:**

- Create: `backend/migrations/versions/a7d9e4c1b620_add_dxf_split_review_decisions.py`
- Modify: `backend/app/modules/dxf_splitting/models.py`
- Modify: `backend/app/bootstrap/model_registry.py`
- Test: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`
- Test: `backend/tests/infrastructure/test_migrations.py`

- [ ] **Step 1: Write the failing model test**

Add a test that creates a manual-review split item and persists:

```python
decision = DxfSplitReviewDecision(
    split_item_id=item.id,
    decision="accept_candidate",
    final_dxf_file_id=item.normal_dxf_file_id,
    comment="人工核对轮廓和孔位后采用",
    decided_by=user.id,
    version=1,
)
db.add(decision)
db.commit()
assert decision.split_item_id == item.id
assert decision.version == 1
```

Also assert candidate normal/allowance/report references are stored on the item, one decision row exists per item, and an invalid decision string fails the domain validation added in Task 2.

- [ ] **Step 2: Run the model test and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting/test_dxf_splitting_pipeline.py -k review_decision_model
```

Expected: collection or assertion failure because `DxfSplitReviewDecision` does not exist.

- [ ] **Step 3: Add the model and migration**

Model contract:

```python
class DxfSplitReviewDecision(TimestampMixin, Base):
    __tablename__ = "dxf_split_review_decisions"
    __table_args__ = (
        UniqueConstraint("split_item_id", name="uq_dxf_split_review_item"),
        Index("ix_dxf_split_review_decider", "decided_by", "decided_at"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    split_item_id: Mapped[int] = mapped_column(
        ForeignKey("dxf_split_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    final_normal_dxf_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    final_weld_allowance_dxf_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id"), index=True
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

Add non-null `processed_count` with default zero to `dxf_split_runs`. Add nullable `candidate_normal_dxf_file_id`, `candidate_weld_allowance_dxf_file_id`, `candidate_split_report_file_id`, and `candidate_weld_allowance_report_file_id` columns to `dxf_split_items`. Register the decision model and create an Alembic revision after `f9c4b7e2a610`. The migration must create all foreign keys, unique constraints and indexes and downgrade only its own additions.

- [ ] **Step 4: Run model and migration tests**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting/test_dxf_splitting_pipeline.py -k review_decision_model
uv run pytest -q tests/infrastructure/test_migrations.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/dxf_splitting/models.py \
  backend/app/bootstrap/model_registry.py \
  backend/migrations/versions/a7d9e4c1b620_add_dxf_split_review_decisions.py \
  backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py \
  backend/tests/infrastructure/test_migrations.py
git commit -m "feat(splitting): persist human review decisions"
```

### Task 2: Add current-attempt review domain rules

**Files:**

- Create: `backend/app/modules/dxf_splitting/review.py`
- Modify: `backend/app/modules/dxf_splitting/schemas.py`
- Modify: `backend/app/modules/dxf_splitting/interface.py`
- Test: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`

- [ ] **Step 1: Write failing service tests**

Cover these cases separately:

```python
decision = decide_split_item(
    db,
    workflow=workflow,
    run_id=run.id,
    item_id=item.id,
    actor_id=owner.id,
    payload=DxfSplitReviewDecisionWrite(
        decision="accept_candidate",
        comment="轮廓和孔位已核对",
        expected_version=0,
    ),
)
assert decision.final_normal_dxf_file_id == item.candidate_normal_dxf_file_id
assert (
    decision.final_weld_allowance_dxf_file_id
    == item.candidate_weld_allowance_dxf_file_id
)
```

```python
with pytest.raises(AppHTTPException) as exc:
    decide_split_item(
        db,
        workflow=workflow,
        run_id=run.id,
        item_id=manual_item_without_candidate.id,
        actor_id=owner.id,
        payload=DxfSplitReviewDecisionWrite(
            decision="accept_candidate",
            comment="请求采用不存在的候选",
            expected_version=0,
        ),
    )
assert exc.value.detail["code"] == "DXF_SPLIT_CANDIDATE_UNAVAILABLE"
```

Also test stale run, non-current attempt, non-review item, empty comment, version conflict, idempotent identical resubmission, and `manual_processing` leaving `final_dxf_file_id` empty.

- [ ] **Step 2: Run service tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting/test_dxf_splitting_pipeline.py -k "review_decision_service"
```

Expected: FAIL because the review service and schemas do not exist.

- [ ] **Step 3: Implement schemas and service**

Schemas:

```python
class DxfSplitReviewDecisionWrite(BaseModel):
    decision: Literal["accept_candidate", "manual_processing"]
    comment: str = Field(min_length=2, max_length=1000)
    expected_version: int = Field(ge=0)


class DxfSplitReviewDecisionRead(BaseModel):
    decision: Literal["accept_candidate", "manual_processing"]
    final_normal_dxf_file_id: int | None
    final_weld_allowance_dxf_file_id: int | None
    comment: str
    decided_by: int
    decided_at: datetime
    version: int
```

The service must:

1. Lock `DxfSplitRun`, `DxfSplitItem`, and an existing decision with `FOR UPDATE`.
2. Verify workflow, project, stage Job ID and attempt.
3. Permit only `automation_route == "manual_review"`.
4. Permit `accept_candidate` only when the isolated normal/allowance candidate pair exists, is structurally readable, and remains available.
5. Preserve the machine `disposition` and `automation_route`.
6. Update the decision version atomically or return `DXF_SPLIT_REVIEW_VERSION_CONFLICT`.

- [ ] **Step 4: Run service tests and full split domain**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/dxf_splitting/review.py \
  backend/app/modules/dxf_splitting/schemas.py \
  backend/app/modules/dxf_splitting/interface.py \
  backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py
git commit -m "feat(splitting): enforce current-attempt review decisions"
```

### Task 3: Project real progress, speed, ETA, and review pages

**Files:**

- Modify: `backend/app/modules/dxf_splitting/persistence.py`
- Modify: `backend/app/modules/dxf_splitting/execution.py`
- Modify: `backend/app/modules/dxf_splitting/validation.py`
- Modify: `backend/app/modules/dxf_splitting/adapter.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/cli.py`
- Modify: `backend/app/modules/dxf_splitting/presentation.py`
- Modify: `backend/app/modules/dxf_splitting/schemas.py`
- Test: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`

- [ ] **Step 1: Write failing projection tests**

Build a run with 10 inputs, `processed_count=4` and a start time two minutes ago:

```python
read = build_dxf_split_run_read(db, run, now=started_at + timedelta(minutes=2))
assert read.processed_count == 4
assert read.failed_count == 1
assert read.reviewed_count == 1
assert read.throughput_per_minute == pytest.approx(2.0)
assert read.estimated_remaining_seconds == 180
```

Test zero elapsed time returns `None` speed/ETA and a terminal run returns zero ETA.

- [ ] **Step 2: Run projection tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting/test_dxf_splitting_pipeline.py -k progress_projection
```

Expected: FAIL because the fields are absent.

- [ ] **Step 3: Implement projection**

Compute:

```python
processed_count = run.processed_count
elapsed_seconds = max(0, int((now - run.started_at).total_seconds()))
throughput = (
    processed_count / (elapsed_seconds / 60)
    if processed_count and elapsed_seconds
    else None
)
remaining = max(run.input_count - processed_count, 0)
eta = round((remaining / throughput) * 60) if throughput else None
```

`failed_count` counts manual items without a candidate; `reviewed_count` counts manual items with a decision. Return a paginated review page rather than embedding every item in the run summary.

When an automatic result has structurally readable paired DXF/report files but fails independent business checks, keep its paths on `ValidatedSplitItem` as review candidates. Persist them under a `candidates/` prefix, bind them to the candidate columns, and do not attach workflow artifacts. A splitter-declared manual route or unsupported type without generated files continues to have no candidate.

Add an optional `--progress-json` CLI argument. After every input, atomically replace that file with:

```json
{
  "schema": "STEEL-DXF-SPLIT-PROGRESS-1",
  "processed_count": 4,
  "input_count": 10,
  "elapsed_seconds": 120.0
}
```

The adapter launches the same immutable CLI algorithm, polls this sidecar, and invokes a platform callback. The worker callback updates `DxfSplitRun.processed_count` and Job progress. Cancellation, timeout, malformed progress, decreasing counts, or mismatched totals fail closed. This changes orchestration only, not BH/BOX geometry.

- [ ] **Step 4: Run projection and split tests**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/dxf_splitting/persistence.py \
  backend/app/modules/dxf_splitting/execution.py \
  backend/app/modules/dxf_splitting/validation.py \
  backend/app/modules/dxf_splitting/adapter.py \
  Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/cli.py \
  backend/app/modules/dxf_splitting/presentation.py \
  backend/app/modules/dxf_splitting/schemas.py \
  backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py
git commit -m "feat(splitting): expose real batch progress and review summary"
```

### Task 4: Expose review completion and ZIP-only result routes

**Files:**

- Modify: `backend/app/modules/workflows/routes/splitting.py`
- Modify: `backend/app/modules/dxf_splitting/interface.py`
- Modify: `backend/app/modules/dxf_splitting/persistence.py`
- Modify: `backend/app/modules/workflows/job_sync.py`
- Test: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`
- Test: `backend/tests/workflows/test_workflow_production.py`

- [ ] **Step 1: Write failing API tests**

Add HTTP tests for:

```text
GET  /drawing-processing/runs/{run_id}/review-items?page=1&page_size=20
PUT  /drawing-processing/runs/{run_id}/review-items/{item_id}/decision
POST /drawing-processing/runs/{run_id}/review-completion
GET  /drawing-processing/runs/{run_id}/results-archive
GET  /drawing-processing/runs/{run_id}/review-candidates-archive
```

Assert:

- project viewers may read/download but cannot decide or complete;
- owner/engineer may decide;
- stale run and attempt are rejected;
- completion fails with `DXF_SPLIT_REVIEW_INCOMPLETE`;
- completion fails with `DXF_SPLIT_MANUAL_PROCESSING_REQUIRED`;
- all accepted candidates complete the stage;
- result ZIP contains only registered current-run outputs, reports, ledger and manifest;
- candidate review ZIP contains current-run original DXF, isolated candidate pairs/reports and a diagnostic manifest, but those candidates are absent from the formal result ZIP until accepted;
- direct file download remains `WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED`.

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting/test_dxf_splitting_pipeline.py \
  tests/workflows/test_workflow_production.py -k "review_api or results_archive or review_completion"
```

Expected: 404 or missing-symbol failures.

- [ ] **Step 3: Implement route adapters and completion**

Use the existing workflow permission boundaries:

```python
require_project_member(db, current_user, workflow.project_id)
require_workflow_write_access(db, current_user, workflow)
```

For completion, lock and recheck current stage/run, require every manual item to have `accept_candidate`, set the reviewed run to `completed`, rebuild the final handoff projection, synchronize the workflow, commit, and return the current run. Never mutate historical attempts.

Build result ZIP members from current-run file IDs only. Use `stream_registered_workflow_archive` so names, audit records and ZIP-only policy remain consistent.

- [ ] **Step 4: Run API and workflow tests**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting tests/workflows
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/workflows/routes/splitting.py \
  backend/app/modules/workflows/job_sync.py \
  backend/app/modules/dxf_splitting/interface.py \
  backend/app/modules/dxf_splitting/persistence.py \
  backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py \
  backend/tests/workflows/test_workflow_production.py
git commit -m "feat(workflows): complete split review and ZIP output flow"
```

### Task 5: Build the compact production split console

**Files:**

- Modify: `frontend/src/features/workflows/workflow.ts`
- Modify: `frontend/src/features/workflows/workflows.api.ts`
- Modify: `frontend/src/features/workflows/DrawingProcessingPanel.tsx`
- Modify: `frontend/src/features/workflows/styles.css`
- Test: `backend/tests/contracts/test_frontend_contract.py`
- Test: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`

- [ ] **Step 1: Write failing contract and E2E tests**

The E2E fixture must show:

- running state with total progress, `2.0 张/分钟`, and ETA;
- completed state with a results ZIP button;
- review state with both the original-only ZIP and candidate review materials ZIP;
- review state with paginated exception rows;
- a review drawer that submits `accept_candidate` plus a required comment;
- review completion disabled until every item is decided;
- no single-file download button;
- historical-stage view has no decision or completion actions;
- server stage change prevents a write request.

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/contracts/test_frontend_contract.py
cd ../frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts --reporter=line
```

Expected: FAIL because the UI and APIs do not exist.

- [ ] **Step 3: Add API contracts**

Add typed functions:

```typescript
getDxfSplitReviewItems(workflowId, runId, page, pageSize)
decideDxfSplitReviewItem(workflowId, runId, itemId, payload)
completeDxfSplitReview(workflowId, runId)
downloadDxfSplitResultsArchive(workflowId, runId)
```

Every mutation must first call `getWorkflow(workflowId)` and require `current_stage === 'drawing_processing'`.

- [ ] **Step 4: Implement the operator console**

Keep `DrawingProcessingPanel` as the stage boundary but split focused display components in the same feature folder if it exceeds 300 lines:

- `SplitProgressSummary.tsx`
- `SplitReviewDrawer.tsx`

Use the existing visual language: strong numeric metrics, compact status chips, one prominent project progress bar, and collapsed details. Do not render all successful files by default.

- [ ] **Step 5: Run frontend contract, E2E and build**

Run:

```bash
cd backend
uv run pytest -q tests/contracts/test_frontend_contract.py
cd ../frontend
npx playwright test tests/e2e/workflows --reporter=line
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/workflows \
  frontend/tests/e2e/workflows/workflow-detail.spec.ts \
  backend/tests/contracts/test_frontend_contract.py
git commit -m "feat(workflows): operate and review complete split batches"
```

### Task 6: Refresh architecture, API and database contracts

**Files:**

- Modify: `backend/tests/architecture/test_workflow_boundaries.py`
- Modify: `backend/tests/architecture/test_module_catalog.py`
- Modify: `backend/tests/architecture/test_contract_snapshot.py`
- Modify: `docs/architecture/runtime-contract.json`
- Modify: `docs/reference/api.md`
- Modify: `docs/reference/database.md`
- Modify: `backend/app/modules/dxf_splitting/README.md`
- Modify: `frontend/src/features/workflows/README.md`

- [ ] **Step 1: Run architecture and documentation checks to observe RED**

Run:

```bash
cd backend
uv run pytest -q tests/architecture
cd ..
cd backend && uv run python ../scripts/docs/check.py
```

Expected: route, table, operation-count, snapshot or documentation mismatch.

- [ ] **Step 2: Refresh owned contracts**

Document:

- current-attempt-only review writes;
- review decision table;
- all split status, review, completion and ZIP routes;
- ZIP-only outputs;
- final Excel handoff rules;
- feature flag rollout boundary.

Regenerate the runtime snapshot with the repository script rather than editing generated counts by hand.

- [ ] **Step 3: Run architecture and docs checks**

Run:

```bash
cd backend
uv run pytest -q tests/architecture
cd ..
cd backend && uv run python ../scripts/docs/check.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/architecture \
  backend/app/modules/dxf_splitting/README.md \
  frontend/src/features/workflows/README.md \
  docs/architecture/runtime-contract.json \
  docs/reference/api.md \
  docs/reference/database.md
git commit -m "docs(splitting): publish review and archive contracts"
```

### Task 7: Verify, migrate, enable locally, deploy and push

**Files:**

- Modify only if tests prove required: `.env`, deployment documentation, or verification record.
- Preserve: `Stages/excel_final/data/`, `output/`, and unrelated operations-console work.

- [ ] **Step 1: Run complete gates**

Run:

```bash
cd backend
uv run ruff check app tests
uv run pytest -q tests/dxf_splitting tests/workflows tests/jobs tests/contracts tests/architecture
cd ../frontend
npx playwright test tests/e2e/workflows --reporter=line
npm run build
cd ..
./scripts/verify.sh quick
```

Expected: all commands PASS.

- [ ] **Step 2: Apply the migration**

Run:

```bash
bash scripts/db.sh migrate
```

Expected: Alembic head becomes `a7d9e4c1b620` and the review table exists.

- [ ] **Step 3: Execute a real local split batch**

Use the running FastAPI/MySQL/MinIO/Celery stack and an actual frozen classified project. Confirm:

- `worker-dxf-split` consumes the Job;
- run and item rows are stored;
- progress changes while processing;
- generated DXF and reports exist in MinIO;
- review and results ZIPs can be opened and contain the contracted members.

Do not enable the flag if this real batch cannot be completed.

- [ ] **Step 4: Enable the local production deployment**

Set the live local deployment’s `DXF_SPLIT_PIPELINE_ENABLED=true` only after Step 3. Keep `.env.example` and `.env.docker.example` at the safe default `false`.

- [ ] **Step 5: Restart and verify runtime**

Run:

```bash
bash scripts/start-all.sh --restart-backend --rebuild
bash scripts/status.sh
cd frontend
npx playwright test tests/e2e/workflows --reporter=line
```

Expected: MySQL, `worker-dxf-split`, FastAPI, Nginx and current frontend are healthy; workflow E2E passes against `:8080`.

- [ ] **Step 6: Commit any verified rollout record**

```bash
git add docs/verification/current.md
git commit -m "docs(verification): record complete split workflow release"
```

- [ ] **Step 7: Push**

Run:

```bash
git push origin main
git status --short --branch
git rev-parse HEAD origin/main
```

Expected: local and remote `main` match; only user-owned or unrelated untracked files remain.
