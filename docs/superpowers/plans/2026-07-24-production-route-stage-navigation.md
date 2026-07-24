# PRODUCTION ROUTE Stage Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every production route stage safely inspectable, add stage-scoped ZIP downloads, simplify classification details, and keep drawing processing an honest placeholder.

**Architecture:** Separate the browser's selected stage from the server's authoritative current stage, so navigation is read-only while mutations remain current-stage-only. Refactor the existing workflow ZIP route into shared member/stream helpers and add a stage-filtered endpoint without creating a second file-download system. Reuse the existing classification run and input batch reads for historical views.

**Tech Stack:** FastAPI, SQLAlchemy, Files transfer ledger, React 19, TypeScript, Ant Design 6, TanStack Query, Pytest, Playwright.

---

### Task 1: Stage-scoped workflow ZIP

**Files:**
- Modify: `backend/tests/workflows/test_workflow_input_api.py`
- Modify: `backend/app/modules/workflows/routes/archive.py`

- [ ] **Step 1: Write the failing stage ZIP API tests**

Add a test beside `test_workflow_download_is_one_zip_with_stage_folders` that creates artifacts in `source_intake` and `dxf_classification`, then requests:

```python
response = client.get(
    f"/api/v1/workflows/{workflow_id}/stages/dxf_classification/download-archive",
    headers=owner_headers,
)
assert response.status_code == 200, response.text
with zipfile.ZipFile(BytesIO(response.content)) as archive:
    names = archive.namelist()
    assert names
    assert all(
        name.startswith(f"workflow-{workflow_id}/02_dxf_classification/")
        for name in names
    )
    assert any("/classified_dxf/" in name for name in names)
    assert any("/classification_report/" in name for name in names)
    assert any("/classification_manifest/" in name for name in names)
    assert not any("/source_intake/" in name for name in names)
```

Add separate assertions for:

```python
assert empty.json()["error"]["code"] == "WORKFLOW_STAGE_ARCHIVE_EMPTY"
assert unknown.json()["error"]["code"] == "WORKFLOW_STAGE_UNKNOWN"
assert stranger.status_code == 403
assert "/api/v1/workflows/{workflow_id}/stages/{stage_code}/download-archive" in paths
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py \
  -k 'stage_archive or openapi_exposes_complete_guarded_surface'
```

Expected: FAIL because the stage archive route does not exist.

- [ ] **Step 3: Extract shared ZIP preparation and streaming**

In `archive.py`, create focused helpers:

```python
def _collect_archive_members(
    db: Session,
    current_user: CurrentUser,
    workflow: WorkflowRun,
    *,
    stage_code: str | None = None,
) -> tuple[list[tuple[int, str]], WorkflowStageRun | None]:
    stage_by_id = {stage.id: stage for stage in workflow.stages}
    selected_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == stage_code),
        None,
    )
    if stage_code is not None and selected_stage is None:
        return [], None
    artifacts = [
        artifact
        for artifact in workflow.artifacts
        if selected_stage is None or artifact.stage_run_id == selected_stage.id
    ]
    members: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    for artifact in sorted(
        artifacts,
        key=lambda value: (
            stage_by_id[value.stage_run_id].sequence
            if value.stage_run_id in stage_by_id
            else 999,
            value.id,
        ),
    ):
        file_id = artifact.file_id
        if file_id is None and artifact.result_id is not None:
            result = db.get(AnalysisResult, artifact.result_id)
            file_id = result.result_file_id if result is not None else None
        stored = db.get(StoredFile, file_id) if file_id is not None else None
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "WORKFLOW_ARCHIVE_ARTIFACT_MISSING",
                "A registered workflow artifact is unavailable.",
                {"artifact_id": artifact.id, "file_id": file_id},
            )
        require_file_read_access(db, current_user, stored)
        stage = stage_by_id.get(artifact.stage_run_id)
        sequence = stage.sequence if stage is not None else 99
        code = stage.stage_code if stage is not None else "workflow"
        original_name = sanitize_filename(stored.original_name)
        relative_path = (
            f"workflow-{workflow.id}/{sequence:02d}_{code}/"
            f"{artifact.artifact_type}/{original_name}"
        )
        if relative_path.casefold() in seen_paths:
            relative_path = (
                f"workflow-{workflow.id}/{sequence:02d}_{code}/"
                f"{artifact.artifact_type}/{stored.id}-{original_name}"
            )
        seen_paths.add(relative_path.casefold())
        members.append((stored.id, relative_path))
    return members, selected_stage


def _stream_prepared_archive(
    db: Session,
    request: Request,
    current_user: CurrentUser,
    workflow: WorkflowRun,
    members: list[tuple[int, str]],
    archive_name: str,
    *,
    operation: str,
    audit_action: str,
) -> StreamingResponse:
    prepared = build_registered_files_zip_to_path(db, members, archive_name)
    try:
        transfer = prepare_transfer_in_transaction(
            db,
            TransferSpec(
                direction="outbound",
                operation=operation,
                actor_user_id=current_user.id,
                request_id=request.state.request_id,
                idempotency_key=request.state.request_id,
                batch_ref=archive_name,
                original_name=prepared.filename,
                expected_bytes=prepared.size_bytes,
            ),
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action=audit_action,
            resource_type="workflow",
            resource_id=workflow.id,
            after_json={
                "file_ids": list(prepared.included_file_ids),
                "artifact_count": len(members),
            },
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        prepared.path.unlink(missing_ok=True)
        raise

    def stream_and_cleanup():
        try:
            with prepared.path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    yield chunk
        finally:
            prepared.path.unlink(missing_ok=True)

    return StreamingResponse(
        settle_stream(
            session_factory_for(db),
            transfer.transfer_uid,
            stream_and_cleanup(),
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(prepared.filename)}"
            ),
            "Content-Length": str(prepared.size_bytes),
        },
    )
```

The collector resolves `result_id` through `AnalysisResult`, applies existing file access checks and duplicate-path handling, and filters by the selected stage before building member paths. The streamer continues to use `build_registered_files_zip_to_path`, `prepare_transfer_in_transaction`, `settle_stream`, and cleanup in `finally`.

- [ ] **Step 4: Add the stage endpoint**

Add:

```python
@router.get(
    "/{workflow_id}/stages/{stage_code}/download-archive",
    summary="下载阶段结果压缩包",
    response_class=StreamingResponse,
)
def download_workflow_stage_archive(
    workflow_id: int,
    stage_code: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    members, stage = _collect_archive_members(
        db, current_user, workflow, stage_code=stage_code
    )
    if stage is None:
        raise AppHTTPException(404, "WORKFLOW_STAGE_UNKNOWN", "Workflow stage was not found.")
    if not members:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_ARCHIVE_EMPTY",
            "The workflow stage has no downloadable production artifacts.",
        )
    return _stream_prepared_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-{stage.sequence:02d}_{stage.stage_code}",
        operation="workflow_stage_download_zip",
        audit_action="workflow_stage_archives.download",
    )
```

Keep the existing full-workflow endpoint behavior and error codes unchanged.

- [ ] **Step 5: Run backend tests and verify GREEN**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_api.py
uv run ruff check app/modules/workflows/routes/archive.py \
  tests/workflows/test_workflow_input_api.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/workflows/routes/archive.py \
  backend/tests/workflows/test_workflow_input_api.py
git commit -m "feat(workflows): download stage result archives"
```

### Task 2: Safe stage selection

**Files:**
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Modify: `frontend/src/features/workflows/styles.css`

- [ ] **Step 1: Write the failing navigation E2E test**

Extend the workflow fixture so `source_intake` and `dxf_classification` are succeeded, `excel_stage1` is current, and `drawing_processing` is pending. Assert:

```typescript
await page.getByRole('button', { name: /DXF 分类与分流/ }).click();
await expect(page.getByRole('heading', { name: 'DXF 分类与分流' })).toBeVisible();
await expect(page.getByRole('button', { name: '运行 Excel 第一阶段' })).toHaveCount(0);

await page.getByRole('button', { name: /图纸分类与拆板/ }).click();
await expect(page.getByText('该阶段尚未解锁')).toBeVisible();
await expect(page.getByText('拆板执行能力尚未接入')).toBeVisible();
await expect(page.getByText('实时速度')).toBeVisible();
await expect(page.getByText('未接入', { exact: true })).toBeVisible();

await page.getByRole('button', { name: '返回当前阶段' }).click();
await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
await expect(page.getByRole('button', { name: '运行 Excel 第一阶段' })).toBeVisible();
```

Record all execution requests and assert no request occurs while inspecting a non-current stage.

- [ ] **Step 2: Run the E2E test and verify RED**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
```

Expected: FAIL because route entries are not buttons and the workspace always displays the current stage.

- [ ] **Step 3: Implement separate selected/current stage state**

Add:

```typescript
const [selectedStageCode, setSelectedStageCode] = useState<string | null>(null);
const authoritativeCurrentStage = detail?.stages.find(
  (stage) => stage.stage_code === detail.current_stage,
);
const selectedStage = detail?.stages.find(
  (stage) => stage.stage_code === selectedStageCode,
) ?? authoritativeCurrentStage;
const selectedCapability = selectedStage
  ? capabilities.get(selectedStage.stage_code)
  : undefined;
const selectedIsCurrent = Boolean(
  selectedStage
  && authoritativeCurrentStage
  && selectedStage.stage_code === authoritativeCurrentStage.stage_code
);
```

Use an effect to select the authoritative current stage on initial load and after a successful stage mutation. Do not reset an existing valid historical selection during polling.

Change `StageRail` to render one full-width button per stage and pass `selectedCode` plus `onSelect`. Keep `aria-current="step"` on the authoritative current stage and style selected, current, complete, failed, and locked states independently.

- [ ] **Step 4: Bind actions only to the authoritative current stage**

The complete and execute mutations must use `authoritativeCurrentStage`. Render upload, execute, retry and confirmation controls only when `selectedIsCurrent` is true. Historical `source_intake` uses `ProductionInputPanel sourceIntakeActive={false}`; historical classification uses `DxfClassificationPanel isCurrent={false}`.

Render a stage-context alert and “返回当前阶段” button whenever `selectedIsCurrent` is false.

- [ ] **Step 5: Render the drawing processing placeholder metrics contract**

When `selectedStage.stage_code === "drawing_processing"`, render a compact placeholder card containing:

```typescript
[
  ['项目总进度', '未接入'],
  ['已完成 / 总数', '未接入'],
  ['实时速度', '未接入'],
  ['预计剩余时间', '未接入'],
]
```

Do not render a progress percentage, `0 张/分钟`, start, retry, confirm, or download action for the placeholder.

- [ ] **Step 6: Run the E2E test and build**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
npm run build
```

Expected: E2E passes and TypeScript/Vite build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/tests/e2e/workflows/workflow-detail.spec.ts \
  frontend/src/features/workflows/WorkflowDetailPage.tsx \
  frontend/src/features/workflows/styles.css
git commit -m "feat(workflows): navigate production route stages safely"
```

### Task 3: Stage archive download in the frontend

**Files:**
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`
- Modify: `frontend/src/features/workflows/workflows.api.ts`
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Modify: `frontend/src/features/workflows/DxfClassificationPanel.tsx`

- [ ] **Step 1: Write the failing download E2E test**

Give the classification stage three artifacts and intercept:

```typescript
await page.route(
  '**/api/v1/workflows/41/stages/dxf_classification/download-archive',
  async (route) => {
    stageArchiveRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/zip',
      headers: {
        'content-disposition':
          "attachment; filename*=UTF-8''workflow-41-02_dxf_classification.zip",
      },
      body: 'PK-stage-archive',
    });
  },
);
```

Navigate to classification, click “下载分流结果压缩包”, and assert `stageArchiveRequests === 1`. Navigate to drawing processing and assert no enabled stage-download button exists.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts \
  --grep "stage archive"
```

Expected: FAIL because no stage download function or button exists.

- [ ] **Step 3: Reuse the Blob download helper**

Extract the current Blob-to-browser save code inside `workflows.api.ts` into a private `downloadArchive(url, fallbackName, errorMessage)` helper. The helper must retain `responseType: 'blob'`, the 300-second timeout, `describeApiErrorAsync`, `URL.createObjectURL`, and `URL.revokeObjectURL`.

Add:

```typescript
export async function downloadWorkflowStageArchive(
  workflowId: number,
  stageCode: string,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/stages/${stageCode}/download-archive`,
    `workflow-${workflowId}-${stageCode}.zip`,
    '阶段结果压缩包下载失败',
  );
}
```

- [ ] **Step 4: Add a stage-scoped artifact card**

Filter:

```typescript
const selectedArtifacts = detail.artifacts.filter(
  (artifact) => artifact.stage_run_id === selectedStage?.id,
);
```

Render stage artifact counts and a download button only when the selected capability is implemented and `selectedArtifacts.length > 0`. Use “下载分流结果压缩包” for `dxf_classification`, otherwise “下载本阶段结果压缩包”. Keep the existing full production archive card below it.

- [ ] **Step 5: Run and verify GREEN**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
npm run build
```

Expected: all workflow-detail tests pass and the build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/tests/e2e/workflows/workflow-detail.spec.ts \
  frontend/src/features/workflows/workflows.api.ts \
  frontend/src/features/workflows/WorkflowDetailPage.tsx \
  frontend/src/features/workflows/DxfClassificationPanel.tsx
git commit -m "feat(workflows): download selected stage archives"
```

### Task 4: Compact classification result details

**Files:**
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`
- Modify: `frontend/src/features/workflows/DxfClassificationPanel.tsx`
- Modify: `frontend/src/features/workflows/styles.css`

- [ ] **Step 1: Write the failing compact-results test**

Return 12 classification items and assert:

```typescript
await expect(page.getByRole('table')).toHaveCount(0);
await expect(page.getByText('文件 #', { exact: false })).toHaveCount(0);
await page.getByRole('button', { name: /查看文件明细（12）/ }).click();
await expect(page.getByRole('table')).toBeVisible();
await expect(page.getByRole('row')).toHaveCount(11);
await expect(page.getByText(/文件 #/)).toHaveCount(0);
```

The 11 rows are one header plus 10 first-page items.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts \
  --grep "compact classification"
```

Expected: FAIL because the current table is always visible, unpaginated, and displays file IDs.

- [ ] **Step 3: Implement compact default and paginated details**

Wrap the table in Ant Design `Collapse`:

```typescript
<Collapse
  className="workflow-classification-details"
  items={[{
    key: 'files',
    label: `查看文件明细（${run.items.length}）`,
    children: (
      <Table<DxfClassificationItem>
        rowKey="id"
        dataSource={run.items}
        columns={columns}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        scroll={{ x: 720 }}
      />
    ),
  }]}
/>
```

Keep only columns for source name, result/type and diagnostics. Remove internal file IDs and byte sizes. Add wrapping styles for long names and target directories.

- [ ] **Step 4: Run and verify GREEN**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
npm run build
```

Expected: workflow E2E and build pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/workflows/workflow-detail.spec.ts \
  frontend/src/features/workflows/DxfClassificationPanel.tsx \
  frontend/src/features/workflows/styles.css
git commit -m "refactor(workflows): compact classification results"
```

### Task 5: Documentation and release verification

**Files:**
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `backend/app/modules/workflows/README.md`

- [ ] **Step 1: Update operator-facing module documentation**

Document these exact boundaries:

```text
PRODUCTION ROUTE buttons select a read-only stage workspace; only current_stage
may expose mutations. Stage output is downloaded as one ZIP. Classification
details are collapsed and paginated. drawing_processing remains a placeholder;
its future total progress and speed fields show no fabricated values.
```

- [ ] **Step 2: Run focused and full proportional gates**

Run:

```bash
cd backend
uv run pytest -q tests/workflows
uv run ruff check app/modules/workflows tests/workflows

cd ../frontend
npx playwright test tests/e2e/workflows
npm run build

cd ..
./scripts/verify.sh quick
```

Expected: all commands pass.

- [ ] **Step 3: Inspect a real generated stage ZIP**

Use the backend API test or live local stack to generate a classification stage ZIP, then check:

```bash
unzip -t /tmp/workflow-stage.zip
unzip -Z1 /tmp/workflow-stage.zip
```

Expected: CRC succeeds; every member is under `02_dxf_classification`; drawing members end in `.dxf`; report and manifest members are present; no DWG or other-stage member is present.

- [ ] **Step 4: Run a real browser smoke test**

Open a workflow detail page and verify stage keyboard/click navigation, safe historical/future views, collapsed classification details, stage ZIP download, full ZIP download, and responsive route layout.

- [ ] **Step 5: Commit docs and push main**

```bash
git add frontend/src/features/workflows/README.md \
  backend/app/modules/workflows/README.md \
  docs/superpowers/plans/2026-07-24-production-route-stage-navigation.md
git commit -m "docs(workflows): document stage navigation and archives"
git push origin main
```
