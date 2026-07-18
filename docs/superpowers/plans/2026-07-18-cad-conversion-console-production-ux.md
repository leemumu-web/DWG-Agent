# CAD Conversion Console Production UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DWG -> DXF and DXF -> DWG submission, progress, recovery, packaging, and single/multi-folder deletion accurate and production-friendly.

**Architecture:** Keep `ConversionPage` shared by both directions, extract deterministic summary/submission helpers where that reduces UI coupling, and add one transactional backend endpoint for atomic multi-folder deletion. Reuse existing file soft-delete, Job state-machine, audit, preview invalidation, React Query, and Ant Design patterns.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pytest, React 19, TypeScript 6, React Query, Ant Design 6, Playwright.

---

## File map

- `backend/app/schemas/file_schema.py`: validated bulk folder deletion request and response types.
- `backend/app/api/v1/files_api.py`: atomic multi-folder deletion route and transaction orchestration.
- `backend/tests/test_adversarial_files.py`: API success, validation, access, cancellation, and rollback coverage.
- `frontend/src/api/files.api.ts`: typed atomic folder deletion client.
- `frontend/src/api/jobs.api.ts`: structured chunk submission that preserves partial success.
- `frontend/src/components/ConversionPage.tsx`: trustworthy loading/summary state, upload lifecycle, visible folder actions, and failure recovery.
- `frontend/src/components/FileUpload.tsx`: operation result feedback and shared busy-state integration.
- `frontend/src/styles.css`: accessible folder controls, busy/selection layout, reduced-motion-safe transitions.
- `frontend/tests/e2e/files-page-buttons.spec.ts`: parameterized DWG/DXF browser regression coverage.
- `backend/tests/test_frontend_contract.py`: static production contract checks for non-retry submission and atomic folder deletion.
- `docs/api.md`: generated API reference after route changes.

### Task 1: Atomic multi-folder deletion API

**Files:**
- Modify: `backend/app/schemas/file_schema.py`
- Modify: `backend/app/api/v1/files_api.py`
- Test: `backend/tests/test_adversarial_files.py`

- [ ] **Step 1: Write failing endpoint tests**

Add tests that create two batches containing source and result files, create active conversion Jobs for their source IDs, call one request, and assert the response counts, all files are soft-deleted, and active Jobs are cancelled. Add separate tests for duplicate names, one missing batch, one inaccessible batch, more than 100 names, blank names, and a monkeypatched mid-transaction exception that leaves every file unchanged.

```python
response = client.post(
    "/api/v1/files/batches/bulk-delete",
    headers=headers,
    json={"batch_names": ["batch-a", "batch-b"]},
)
assert response.status_code == 200
assert response.json()["data"] == {
    "deleted_batch_count": 2,
    "deleted_file_count": 4,
    "cancelled_job_count": 2,
}
```

- [ ] **Step 2: Run tests and confirm red state**

Run: `cd backend && uv run pytest -q tests/test_adversarial_files.py -k 'bulk_delete_batches'`

Expected: failures because the route and schemas do not exist.

- [ ] **Step 3: Add validated schemas**

Implement a request with `batch_names: list[str] = Field(min_length=1, max_length=100)` plus a validator that strips names, rejects blanks, and preserves order. Add a response model with `deleted_batch_count`, `deleted_file_count`, and `cancelled_job_count` non-negative integers.

```python
class BatchBulkDeleteRequest(BaseModel):
    batch_names: list[str] = Field(min_length=1, max_length=100)

    @field_validator("batch_names")
    @classmethod
    def validate_names(cls, names: list[str]) -> list[str]:
        cleaned = [name.strip() for name in names]
        if any(not name for name in cleaned):
            raise ValueError("batch_names must not contain blank names")
        return cleaned
```

- [ ] **Step 4: Implement one transactional route**

Deduplicate names, query all non-deleted `StoredFile` rows, reject if the set of returned batch names differs from the requested set, validate deletion access before mutation, find active bidirectional conversion Jobs by source `file_id`, cancel them through `transition_job_to_cancelled`, soft-delete every file with `_soft_delete_file_in_transaction`, write audit records, and commit once. Roll back on every exception.

```python
@router.post("/batches/bulk-delete")
def bulk_delete_batches(...):
    names = list(dict.fromkeys(payload.batch_names))
    stored_list = list(db.scalars(select(StoredFile).where(
        StoredFile.batch_name.in_(names),
        StoredFile.status != "deleted",
    )).all())
    found = {stored.batch_name for stored in stored_list}
    if found != set(names):
        raise not_found("Batch")
    for stored in stored_list:
        _require_file_delete_access(db, current_user, stored)
    # Cancel active source Jobs, then soft-delete every source/result file.
    db.commit()
    return ok(result, request.state.request_id)
```

- [ ] **Step 5: Run focused tests and commit**

Run: `cd backend && uv run pytest -q tests/test_adversarial_files.py -k 'bulk_delete_batches'`

Expected: all selected tests pass.

Commit: `git commit -m "feat: delete CAD folders atomically"`

### Task 2: Resilient conversion submission contract

**Files:**
- Modify: `frontend/src/api/jobs.api.ts`
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] **Step 1: Add failing contract checks**

Assert that conversion submission exposes `submittedJobs`, `submittedFileIds`, `unsubmittedFileIds`, and `errors`, uses `Promise.allSettled` over bounded chunks, and does not automatically retry non-idempotent POST requests.

```python
assert "unsubmittedFileIds" in jobs_api
assert "Promise.allSettled" in jobs_api
assert "apiClient.post<ApiEnvelope<{ jobs: Job[] }>>('/api/v1/jobs/batches'" in jobs_api
```

- [ ] **Step 2: Run the focused contract test and confirm red state**

Run: `cd backend && uv run pytest -q tests/test_frontend_contract.py -k 'conversion_submission'`

Expected: failure because `createConversionBatches` currently rejects on the first failed chunk.

- [ ] **Step 3: Return structured partial results**

Change `createConversionBatches` to deduplicate IDs, submit at most three 200-ID chunks concurrently with `Promise.allSettled`, map fulfilled chunks to Jobs, and map rejected chunks back to their file IDs and safe error messages.

```ts
export interface ConversionBatchSubmission {
  submittedJobs: Job[];
  submittedFileIds: number[];
  unsubmittedFileIds: number[];
  errors: string[];
}
```

The function must return an empty structure for no IDs and must never retry a failed POST automatically.

- [ ] **Step 4: Update callers to compile against the structured result**

Temporarily adapt all `ConversionPage` call sites to read `submission.submittedJobs.length`. Preserve explicit UI error handling for `unsubmittedFileIds`; Task 3 adds final messages.

- [ ] **Step 5: Run contract and production build, then commit**

Run: `cd backend && uv run pytest -q tests/test_frontend_contract.py`

Run: `cd frontend && npm run build`

Expected: both pass.

Commit: `git commit -m "fix: preserve partial CAD submissions"`

### Task 3: Trustworthy progress, loading, and recovery UI

**Files:**
- Modify: `frontend/src/components/ConversionPage.tsx`
- Modify: `frontend/src/components/FileUpload.tsx`
- Test: `frontend/tests/e2e/files-page-buttons.spec.ts`

- [ ] **Step 1: Add failing route-backed browser tests**

For both directions, mock file and Job responses so the page contains one succeeded Job, one active Job at 50%, one failed Job at 70%, and one file without a Job. Assert the displayed success progress is 38%, counts are `成功 1`, `失败 1`, `处理中 1`, `待提交/重试 2`, and the failed 70% does not raise the aggregate. Delay the Job response and assert rows show “正在加载状态” rather than “未转换”.

```ts
await expect(page.getByText(/成功 1.*失败 1.*处理中 1.*待提交\/重试 2/)).toBeVisible();
await expect(page.getByRole('progressbar').first()).toHaveAttribute('aria-valuenow', '38');
```

- [ ] **Step 2: Run the new tests and confirm red state**

Run: `cd frontend && npx playwright test tests/e2e/files-page-buttons.spec.ts -g 'trustworthy progress|loading status'`

Expected: failures showing the old average and false “未转换” state.

- [ ] **Step 3: Implement deterministic status classification**

Derive `succeeded`, `active`, `failed`, `unsubmitted`, `stuck`, `actionable`, and `successProgress` from scope files and latest Jobs. Count failed/cancelled/stuck/unsubmitted files as zero progress; count active Job progress; count succeeded as 100. Render loading placeholders until both range files and latest Jobs are settled.

- [ ] **Step 4: Implement submission/recovery feedback**

Use the Task 2 structured result for single, folder, ZIP, and submit/retry actions. Messages must distinguish uploads from Job submission and show submitted/unsubmitted counts. Disable all upload and folder mutation controls while a page operation is active. Before submit/retry, refresh scope files and Jobs and submit only the latest actionable IDs.

- [ ] **Step 5: Correct row semantics and error help**

Always show `p.tagPending` as the source badge. Show failed Job messages in a bounded tooltip with a visible “重新提交” button. When Job data is loading or failed, do not infer “未转换”.

- [ ] **Step 6: Run browser tests and build, then commit**

Run: `cd frontend && npx playwright test tests/e2e/files-page-buttons.spec.ts -g 'trustworthy progress|loading status|upload single file|继续任务'`

Run: `cd frontend && npm run build`

Expected: selected tests and build pass.

Commit: `git commit -m "fix: make CAD conversion progress trustworthy"`

### Task 4: Complete folder packaging and deletion UX

**Files:**
- Modify: `frontend/src/api/files.api.ts`
- Modify: `frontend/src/components/ConversionPage.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/e2e/files-page-buttons.spec.ts`
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] **Step 1: Add failing folder interaction tests**

For both directions, assert the folder header contains select-all, selected count, package, and delete controls before the grid; folder names are keyboard-focusable buttons; selecting two folders and confirming deletion sends exactly one request with both names; server failure preserves selection; success clears selection and refreshes batches.

```ts
expect(await deleteRequest.postDataJSON()).toEqual({ batch_names: ['batch-a', 'batch-b'] });
expect(deleteCalls).toBe(1);
```

- [ ] **Step 2: Run focused browser tests and confirm red state**

Run: `cd frontend && npx playwright test tests/e2e/files-page-buttons.spec.ts -g 'folder actions|multiple folders'`

Expected: failure because actions follow the grid and deletion uses file IDs.

- [ ] **Step 3: Add the typed API client**

Implement `bulkDeleteBatches(batchNames)` against `POST /api/v1/files/batches/bulk-delete` and return the three response counters. Remove unused direct folder-delete helpers only after all callers are migrated.

- [ ] **Step 4: Rebuild folder controls**

Move the selection action bar into the folder header before `.folder-grid`, add select-all/clear controls, use semantic buttons for folder names, preserve tooltips for long names, and add a confirmation summary containing selected folder count, known source count, first three names, and remaining-name count.

- [ ] **Step 5: Wire atomic deletion and robust packaging**

Send one deletion request for all selected names. On success, show all returned counters, clear selection, and refresh. On failure, preserve selection, show the actionable backend error, and refresh authoritative data. While gathering selected folder IDs for packaging, show loading and keep selection on errors.

- [ ] **Step 6: Apply accessibility and motion fixes**

Replace `.upload-toast { transition: all ... }` with explicit `opacity`, `transform`, and `max-height` transitions; add a `prefers-reduced-motion: reduce` override; keep visible focus for folder buttons and ensure interactive hit targets remain usable on mobile.

- [ ] **Step 7: Run tests, build, and commit**

Run: `cd backend && uv run pytest -q tests/test_frontend_contract.py`

Run: `cd frontend && npm run build && npx playwright test tests/e2e/files-page-buttons.spec.ts`

Expected: all pass.

Commit: `git commit -m "fix: complete CAD folder operations"`

### Task 5: Full verification and documentation

**Files:**
- Regenerate: `docs/api.md`
- Modify: `docs/processing-pipelines.md` only if generated/behavioral documentation requires clarification.

- [ ] **Step 1: Generate and verify API docs**

Run: `make docs-generate && make docs-check`

Expected: generated API includes `/api/v1/files/batches/bulk-delete`; documentation checks pass.

- [ ] **Step 2: Run static and backend gates**

Run: `cd backend && uv run ruff check app tests ../tests/run_full_verify.py && uv run pytest -q && uv run alembic check`

Expected: all checks pass.

- [ ] **Step 3: Run Stage and infrastructure gates relevant to unchanged CAD pipelines**

Run: `cd Stages/dwg2dxf && uv run pytest -q`

Run: `cd Stages/dxf2dwg && uv run pytest -q`

Run from repository root: `bash infra/verify.sh && docker compose config --quiet`

Expected: all commands pass.

- [ ] **Step 4: Run complete frontend verification**

Run: `cd frontend && npm run build && npx playwright test`

Expected: build and complete Playwright suite pass.

- [ ] **Step 5: Inspect the live Nginx pages**

Use the authenticated browser at `http://127.0.0.1:8080/files/dwg2dxf` and `/files/dxf2dwg`. Confirm no false-zero flash, coherent progress/counts, visible folder actions at desktop and 390-pixel width, and no console errors. Use isolated fixture folders for deletion; do not delete existing production batches.

- [ ] **Step 6: Final diff audit and commit**

Run: `git diff --check && git status --short`

Confirm the pre-existing deletion of `Stages/dwg2dxf/convert/output_dxf/.gitkeep` remains untouched and unstaged.

Commit documentation and any final test-only adjustments with: `git commit -m "docs: document atomic CAD folder deletion"`.

