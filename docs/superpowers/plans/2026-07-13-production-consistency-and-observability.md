# Production Consistency and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database-backed Excel Final request idempotency, DXF preview lifecycle cleanup, domain-safe queries, truthful infrastructure health, and URL-restorable data-console state.

**Architecture:** Keep the existing StoredFile/FileTransfer saga and Job worker lifecycle. Add a nullable scoped request key to Job with a database unique constraint, invalidate preview metadata inside the source soft-delete transaction, and expose existing database/storage health primitives through the Excel Final API. Keep page orchestration in `ExcelFinalPage`, isolate URL parsing/merging in a small utility, and keep search presentation in `ExcelFinalTools` through controlled props.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, MySQL 8/SQLite, MinIO/local storage adapters, React 19, TypeScript 6, React Router 7, TanStack Query 5, Ant Design 6, Pytest, Playwright.

**Execution status:** Completed on 2026-07-13. The implementation steps below are backed by the evidence section at the end of this plan; no acceptance item remains outstanding. Checkboxes are retained as the original execution recipe rather than rewritten as result claims.

---

## File map

- Create `backend/migrations/versions/d5e8a1c4b720_add_job_request_key.py`: schema upgrade and downgrade for the Job idempotency key.
- Modify `backend/app/models/job.py`: map `request_key` and its unique constraint.
- Modify `backend/app/services/job_service.py`: create or reuse a Job atomically and reject a scoped key reused with different parameters.
- Modify `backend/app/api/v1/excel_final_api.py`: validate Excel inputs, scope request keys, reuse uploads/jobs, constrain query domain, and return infrastructure health.
- Modify `backend/app/services/file_transfer_service.py`: settle a transfer as an explicit cache reuse outcome.
- Modify `backend/app/services/dxf_preview_service.py`: invalidate all current previews for a source and use cache-reuse ledger semantics.
- Modify `backend/app/api/v1/files_api.py`: cascade preview invalidation from both single-file and batch soft delete.
- Create `frontend/src/features/files/excel-final/excelFinalUrlState.ts`: parse, validate, and merge Excel Final query parameters.
- Modify `frontend/src/api/excel-final.api.ts`: send idempotency headers.
- Modify `frontend/src/types/excel-final.ts`: model `reused` and infrastructure health fields.
- Modify `frontend/src/features/files/ExcelFinalPage.tsx`: generate request keys, restore page/drawer/job state, and show refresh time.
- Modify `frontend/src/features/files/Dxf2ExcelPage.tsx`: derive a stable bridge request key.
- Modify `frontend/src/features/files/excel-final/ExcelFinalTools.tsx`: make applied search and pagination URL-controlled.
- Modify `frontend/src/features/files/excel-final/ExcelFinalOverview.tsx`: render actual database/storage backends and specific degradation reasons.
- Modify `frontend/src/features/files/excel-final/ExcelFinalPage.css`: style health details and refresh metadata without changing the existing design language.
- Extend focused backend and Playwright tests; update API, database, architecture, operations, and pipeline documentation.

### Task 1: Persist the Job request key

**Files:**
- Modify: `backend/app/models/job.py`
- Create: `backend/migrations/versions/d5e8a1c4b720_add_job_request_key.py`
- Test: `backend/tests/test_excel_final_idempotency.py`

- [ ] **Step 1: Write a failing model test**

```python
def test_job_request_key_is_unique_per_actor_and_task(db, user):
    first = Job(created_by=user.id, task_type=TASK_EXCEL_FINAL,
                precision_level="normal", status="queued", request_key="process:key-1")
    db.add(first)
    db.commit()
    db.add(Job(created_by=user.id, task_type=TASK_EXCEL_FINAL,
               precision_level="normal", status="queued", request_key="process:key-1"))
    with pytest.raises(IntegrityError):
        db.commit()
```

- [ ] **Step 2: Run the test and confirm the missing-column failure**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_idempotency.py::test_job_request_key_is_unique_per_actor_and_task -q`

Expected: FAIL because `Job` does not accept `request_key` or the duplicate row commits.

- [ ] **Step 3: Add the model column and constraint**

```python
class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "created_by", "task_type", "request_key",
            name="uq_jobs_actor_task_request_key",
        ),
    )
    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(128))
```

- [ ] **Step 4: Add the Alembic migration after `9c4e7b1a2d60`**

```python
revision = "d5e8a1c4b720"
down_revision = "9c4e7b1a2d60"

def upgrade() -> None:
    op.add_column("jobs", sa.Column("request_key", sa.String(128), nullable=True))
    op.create_unique_constraint(
        "uq_jobs_actor_task_request_key",
        "jobs",
        ["created_by", "task_type", "request_key"],
    )

def downgrade() -> None:
    op.drop_constraint("uq_jobs_actor_task_request_key", "jobs", type_="unique")
    op.drop_column("jobs", "request_key")
```

- [ ] **Step 5: Verify model and migration shape**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_idempotency.py::test_job_request_key_is_unique_per_actor_and_task -q && .venv/bin/alembic heads`

Expected: PASS and exactly `d5e8a1c4b720 (head)`.

- [ ] **Step 6: Commit the schema slice**

```bash
git add backend/app/models/job.py backend/migrations/versions/d5e8a1c4b720_add_job_request_key.py backend/tests/test_excel_final_idempotency.py
git commit -m "feat: persist job request idempotency keys"
```

### Task 2: Create or reuse Excel Final Jobs atomically

**Files:**
- Modify: `backend/app/services/job_service.py`
- Modify: `backend/app/api/v1/excel_final_api.py`
- Test: `backend/tests/test_excel_final_idempotency.py`

- [ ] **Step 1: Add failing serial-replay and changed-parameter tests**

```python
def test_process_replay_returns_same_job(client, admin_headers, excel_file):
    headers = {**admin_headers, "Idempotency-Key": "process-1"}
    first = client.post(f"/api/v1/excel-final/process?file_id={excel_file.id}", headers=headers)
    second = client.post(f"/api/v1/excel-final/process?file_id={excel_file.id}", headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert first.json()["data"]["reused"] is False
    assert second.json()["data"]["reused"] is True

def test_process_rejects_same_key_for_different_file(client, admin_headers, excel_files):
    headers = {**admin_headers, "Idempotency-Key": "process-conflict"}
    assert client.post(f"/api/v1/excel-final/process?file_id={excel_files[0].id}", headers=headers).status_code == 202
    response = client.post(f"/api/v1/excel-final/process?file_id={excel_files[1].id}", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
```

- [ ] **Step 2: Run the focused tests and confirm duplicate Jobs are created**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_idempotency.py -k 'process_replay or different_file' -q`

Expected: FAIL because the endpoint ignores the header and omits `reused`.

- [ ] **Step 3: Add a generic atomic helper**

```python
def create_or_reuse_job(
    db: Session,
    payload: JobCreate,
    *,
    created_by: int,
    request_key: str | None,
) -> tuple[Job, bool]:
    if request_key is None:
        return create_job(db, payload, created_by), False
    existing = db.scalar(select(Job).where(
        Job.created_by == created_by,
        Job.task_type == payload.task_type,
        Job.request_key == request_key,
    ))
    if existing is not None:
        if existing.params_json != payload.params:
            raise AppHTTPException(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used with different parameters.")
        return existing, True
    try:
        with db.begin_nested():
            job = create_job(db, payload, created_by)
            job.request_key = request_key
            db.flush()
        return job, False
    except IntegrityError:
        existing = db.scalar(select(Job).where(
            Job.created_by == created_by,
            Job.task_type == payload.task_type,
            Job.request_key == request_key,
        ))
        if existing is None:
            raise
        if existing.params_json != payload.params:
            raise AppHTTPException(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was already used with different parameters.")
        return existing, True
```

- [ ] **Step 4: Scope and validate the process request key**

```python
def _scoped_request_key(endpoint: str, value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 96 or not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise AppHTTPException(422, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key has an invalid format.")
    return f"{endpoint}:{value}"
```

Use `Header(default=None, alias="Idempotency-Key")`, call `create_or_reuse_job()`, write audit/commit/dispatch only when `reused` is false, and include `reused` in the response.

- [ ] **Step 5: Add and run a two-session concurrency test**

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

def test_create_or_reuse_job_concurrent_sessions(session_factory, user):
    gate = Barrier(2)
    payload = JobCreate(task_type=TASK_EXCEL_FINAL, params={"file_id": 81})

    def submit() -> tuple[int, bool]:
        with session_factory() as session:
            gate.wait()
            job, reused = create_or_reuse_job(
                session, payload, created_by=user.id, request_key="process:race-1",
            )
            session.commit()
            return job.id, reused

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    with session_factory() as verify:
        count = verify.scalar(select(func.count()).select_from(Job).where(
            Job.created_by == user.id,
            Job.task_type == TASK_EXCEL_FINAL,
            Job.request_key == "process:race-1",
        ))
    assert len({job_id for job_id, _ in results}) == 1
    assert sorted(reused for _, reused in results) == [False, True]
    assert count == 1
```

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_idempotency.py -q`

Expected: all idempotency tests PASS on SQLite; the MySQL integration version is executed in Task 10.

- [ ] **Step 6: Commit the Job API slice**

```bash
git add backend/app/services/job_service.py backend/app/api/v1/excel_final_api.py backend/tests/test_excel_final_idempotency.py
git commit -m "feat: make excel final job submission idempotent"
```

### Task 3: Replay upload-and-process without duplicate objects

**Files:**
- Modify: `backend/app/api/v1/excel_final_api.py`
- Test: `backend/tests/test_excel_final_idempotency.py`

- [ ] **Step 1: Add a failing upload replay test**

```python
from io import BytesIO
from openpyxl import Workbook

def test_upload_and_process_replay_reuses_file_and_job(client, admin_headers, db, storage):
    headers = {**admin_headers, "Idempotency-Key": "upload-1"}
    stream = BytesIO()
    book = Workbook()
    book.active.append(["零件号", "规格", "材质"])
    book.active.append(["P-1", "L50x5", "Q235"])
    book.save(stream)
    workbook = stream.getvalue()
    first = client.post(
        "/api/v1/excel-final/upload-and-process",
        headers=headers,
        files={"upload": ("parts.xlsx", workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    second = client.post(
        "/api/v1/excel-final/upload-and-process",
        headers=headers,
        files={"upload": ("parts.xlsx", workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["file_id"] == second.json()["data"]["file_id"]
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert db.scalar(select(func.count()).select_from(StoredFile)) == 1
    assert storage.bucket_object_counts([settings.minio_bucket_reports])[settings.minio_bucket_reports] == 1
```

- [ ] **Step 2: Run the replay test and confirm duplicate files**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_idempotency.py::test_upload_and_process_replay_reuses_file_and_job -q`

Expected: FAIL because `_store_excel_upload()` has no idempotency key or succeeded-transfer reuse branch.

- [ ] **Step 3: Reuse a succeeded upload transfer**

```python
transfer = prepare_transfer_in_transaction(db, TransferSpec(
    direction="inbound", operation="upload", actor_user_id=current_user.id,
    request_id=request.state.request_id, idempotency_key=idempotency_key,
    original_name=sanitize_filename(upload.filename or "unnamed.xlsx"),
))
db.commit()
if transfer.status == "succeeded" and transfer.file_id is not None:
    stored = db.get(StoredFile, transfer.file_id)
    if stored is None or stored.status == "deleted":
        raise AppHTTPException(409, "IDEMPOTENT_RESULT_MISSING", "The previous upload result is unavailable.")
    return stored, True
```

Change `_store_excel_upload()` to return `(StoredFile, bool)` and pass the unscoped header value to the upload ledger. In `upload_and_process()`, pre-read an existing scoped Job before consuming the upload body; otherwise store/reuse the file, then call the Job helper.

- [ ] **Step 4: Reject active and failed transfer replays deterministically**

```python
if transfer.status not in ACTIVE_TRANSFER_STATUSES:
    raise AppHTTPException(
        409, "IDEMPOTENT_OPERATION_FAILED",
        "The previous upload with this idempotency key did not succeed.",
        {"transfer_uid": transfer.transfer_uid},
    )
```

Keep `TRANSFER_IN_PROGRESS` from `prepare_transfer_in_transaction()` for a truly concurrent upload. This prevents two writers while allowing the completed request to be replayed.

- [ ] **Step 5: Run upload saga and idempotency tests**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_idempotency.py tests/test_file_transfer_service.py -q`

Expected: PASS with one StoredFile, one inbound upload transfer, and one Job for a completed replay.

- [ ] **Step 6: Commit the upload replay slice**

```bash
git add backend/app/api/v1/excel_final_api.py backend/tests/test_excel_final_idempotency.py
git commit -m "feat: replay excel uploads without duplicate objects"
```

### Task 4: Enforce Excel input and query-domain boundaries

**Files:**
- Modify: `backend/app/api/v1/excel_final_api.py`
- Test: `backend/tests/test_job_access.py`
- Test: `backend/tests/test_excel_final_import.py`

- [ ] **Step 1: Add failing boundary tests**

```python
def test_process_rejects_non_excel_stored_file(client, admin_headers, dxf_file):
    response = client.post(f"/api/v1/excel-final/process?file_id={dxf_file.id}", headers=admin_headers)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "NOT_EXCEL"

def test_overview_and_search_ignore_non_excel_final_job_data(client, headers, anomalous_batch):
    overview = client.get("/api/v1/excel-final/overview", headers=headers).json()["data"]
    search = client.get("/api/v1/excel-final/parts/search?part_no=ANOMALY", headers=headers).json()
    assert overview["batch_count"] == 0
    assert search["pagination"]["total"] == 0
```

- [ ] **Step 2: Run and confirm the malformed records leak into results**

Run: `cd backend && .venv/bin/pytest tests/test_job_access.py tests/test_excel_final_import.py -k 'non_excel or ignore_non_excel' -q`

Expected: FAIL before extension and task-domain filters are added.

- [ ] **Step 3: Add exact input validation and domain filters**

```python
if (sfile.file_ext or "").lower() not in {".xls", ".xlsx"}:
    raise AppHTTPException(415, "NOT_EXCEL", "Only .xls or .xlsx files can be processed.")

stmt = stmt.where(Job.task_type == TASK_EXCEL_FINAL)
if not has_global_project_access(current_user):
    stmt = stmt.where(job_read_filter(current_user))
```

Apply the task predicate to overview, batch list, and cross-batch search before ordering/pagination.

- [ ] **Step 4: Run all Excel Final API/access tests**

Run: `cd backend && .venv/bin/pytest tests/test_job_access.py tests/test_excel_final_import.py tests/test_excel_final_retry.py tests/test_excel_final_idempotency.py -q`

Expected: PASS and existing permission behavior remains unchanged.

- [ ] **Step 5: Commit the domain-hardening slice**

```bash
git add backend/app/api/v1/excel_final_api.py backend/tests/test_job_access.py backend/tests/test_excel_final_import.py
git commit -m "fix: enforce excel final input and query domains"
```

### Task 5: Invalidate DXF previews with their source

**Files:**
- Modify: `backend/app/services/file_transfer_service.py`
- Modify: `backend/app/services/dxf_preview_service.py`
- Modify: `backend/app/api/v1/files_api.py`
- Test: `backend/tests/test_dxf_preview_service.py`
- Test: `backend/tests/test_dxf_preview_api.py`

- [ ] **Step 1: Add failing single and batch deletion tests**

```python
def test_source_soft_delete_invalidates_registered_preview(client, headers, generated_preview, db):
    response = client.delete(f"/api/v1/files/{generated_preview.source_id}", headers=headers)
    assert response.status_code == 204
    db.expire_all()
    preview = db.get(StoredFile, generated_preview.preview_id)
    assert preview.status == "deleted"
    transfer = db.scalar(select(FileTransfer).where(
        FileTransfer.file_id == preview.id,
        FileTransfer.operation == "preview_invalidate",
    ))
    assert transfer is not None and transfer.status == "succeeded"

def test_batch_soft_delete_invalidates_each_source_preview(
    client, headers, db, preview_sources_in_one_batch,
):
    batch_name, source_ids, preview_ids = preview_sources_in_one_batch
    response = client.delete(f"/api/v1/files/batches/{batch_name}", headers=headers)
    assert response.status_code == 204
    db.expire_all()
    assert all(db.get(StoredFile, source_id).status == "deleted" for source_id in source_ids)
    assert all(db.get(StoredFile, preview_id).status == "deleted" for preview_id in preview_ids)
```

- [ ] **Step 2: Run and confirm previews remain active**

Run: `cd backend && .venv/bin/pytest tests/test_dxf_preview_api.py -k 'delete_invalidates' -q`

Expected: FAIL because source deletion changes only the source StoredFile.

- [ ] **Step 3: Add a public lifecycle helper**

```python
def invalidate_dxf_previews_for_source(
    db: Session, source: StoredFile, *, actor_user_id: int, request_id: str,
) -> int:
    previews = db.scalars(select(StoredFile).where(
        StoredFile.batch_name == preview_batch_name(source),
        StoredFile.file_ext == ".svg",
        StoredFile.status != "deleted",
    )).all()
    for preview in previews:
        _invalidate_preview_file(
            db, preview, actor_user_id=actor_user_id, request_id=request_id,
        )
    return len(previews)
```

Call it from `_soft_delete_file_in_transaction()` before marking a DXF source deleted. The existing batch endpoint inherits the same behavior.

- [ ] **Step 4: Record cache races as reuse**

```python
def complete_reused_transfer_in_transaction(db: Session, transfer_uid: str, *, file: StoredFile):
    row = _transfer_for_update(db, transfer_uid)
    row.operation = "preview_cache_reuse"
    return _complete_row(row, file_id=file.id, bucket=file.bucket,
                         storage_key=file.storage_key, original_name=file.original_name,
                         transferred_bytes=0)
```

Use this helper only after the locked second cache check; normal first-level cache hits do not create a generation transfer.

- [ ] **Step 5: Run preview service/API and adversarial delete tests**

Run: `cd backend && .venv/bin/pytest tests/test_dxf_preview_service.py tests/test_dxf_preview_api.py tests/test_adversarial_files.py -q`

Expected: PASS; preview objects remain physically present until retention purge while metadata and content access are disabled.

- [ ] **Step 6: Commit the preview lifecycle slice**

```bash
git add backend/app/services/file_transfer_service.py backend/app/services/dxf_preview_service.py backend/app/api/v1/files_api.py backend/tests/test_dxf_preview_service.py backend/tests/test_dxf_preview_api.py
git commit -m "fix: couple dxf preview lifecycle to source files"
```

### Task 6: Expose truthful database and storage health

**Files:**
- Modify: `backend/app/api/v1/excel_final_api.py`
- Test: `backend/tests/test_excel_final_import.py`

- [ ] **Step 1: Add failing healthy and degraded adapter tests**

```python
def test_excel_final_health_reports_actual_backends(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "local")
    response = client.get("/api/v1/excel-final/health", headers=headers)
    data = response.json()["data"]
    assert data["database_backend"] == "sqlite"
    assert data["database_available"] is True
    assert data["storage_backend"] == "local"
    assert data["storage_available"] is True
    assert data["storage_bucket"] == settings.minio_bucket_reports

def test_excel_final_health_degrades_when_storage_fails(client, headers, monkeypatch):
    monkeypatch.setattr(storage, "check_health", Mock(side_effect=StorageError("secret host")))
    data = client.get("/api/v1/excel-final/health", headers=headers).json()["data"]
    assert data["storage_available"] is False
    assert data["ready"] is False
    assert "secret host" not in json.dumps(data)
```

- [ ] **Step 2: Run and confirm fields are missing**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_import.py -k 'health_reports_actual or health_degrades' -q`

Expected: FAIL because health currently checks only pipeline dependencies.

- [ ] **Step 3: Add safe adapter checks**

```python
database_backend = db.get_bind().dialect.name
db.execute(text("SELECT 1"))
database_available = True
storage = get_storage_backend()
try:
    storage.check_health()
    storage_available = True
except (StorageError, AppHTTPException):
    storage_available = False

ready = all((is_enabled, pkg_available, handbook_db_available,
             database_available, storage_available))
```

Return backend names, availability booleans, `storage_bucket`, and a stable `degraded_components` list. Do not return exception strings.

- [ ] **Step 4: Run focused and system health tests**

Run: `cd backend && .venv/bin/pytest tests/test_excel_final_import.py tests/test_health.py tests/test_storage_operations.py -q`

Expected: PASS without changing root readiness contracts.

- [ ] **Step 5: Commit the health API slice**

```bash
git add backend/app/api/v1/excel_final_api.py backend/tests/test_excel_final_import.py
git commit -m "feat: report excel final infrastructure health"
```

### Task 7: Send frontend idempotency keys

**Files:**
- Modify: `frontend/src/api/excel-final.api.ts`
- Modify: `frontend/src/types/excel-final.ts`
- Modify: `frontend/src/features/files/ExcelFinalPage.tsx`
- Modify: `frontend/src/features/files/Dxf2ExcelPage.tsx`
- Test: `frontend/tests/e2e/api-contract.spec.ts`
- Test: `frontend/tests/e2e/excel-final-flow.spec.ts`

- [ ] **Step 1: Add failing request-header assertions**

```typescript
expect(uploadRequest.headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/);
expect(bridgeRequest.headers()['idempotency-key'])
  .toBe(`dxf2excel-${extractionJobId}-${resultFileId}`);
```

- [ ] **Step 2: Run the focused Playwright tests**

Run: `cd frontend && npx playwright test tests/e2e/api-contract.spec.ts tests/e2e/excel-final-flow.spec.ts --grep 'idempotency'`

Expected: FAIL because the API functions do not accept or send request keys.

- [ ] **Step 3: Extend API and response types**

```typescript
export interface ExcelFinalSubmission {
  job_id: number;
  file_id: number;
  status: string;
  message: string;
  reused: boolean;
}

export async function uploadAndProcessExcel(file: File, requestKey: string) {
  // existing form
  return apiClient.post('/api/v1/excel-final/upload-and-process', form, {
    timeout: 300_000,
    headers: { 'Idempotency-Key': requestKey },
  });
}

export async function processExcelFinalFile(fileId: number, requestKey: string) {
  return apiClient.post('/api/v1/excel-final/process', undefined, {
    params: { file_id: fileId },
    headers: { 'Idempotency-Key': requestKey },
  });
}
```

- [ ] **Step 4: Generate keys at user-intent boundaries**

```typescript
const result = await uploadAndProcessExcel(selectedFile, crypto.randomUUID());

const requestKey = `dxf2excel-${extractionJob.id}-${excel.result_file_id}`;
const finalJob = await processExcelFinalFile(excel.result_file_id, requestKey);
message.success(finalJob.reused
  ? `零件清单任务 #${finalJob.job_id} 已存在，已继续跟踪`
  : `零件清单任务 #${finalJob.job_id} 已登记`);
```

- [ ] **Step 5: Run API/flow tests and TypeScript build**

Run: `cd frontend && npx playwright test tests/e2e/api-contract.spec.ts tests/e2e/excel-final-flow.spec.ts && npm run build`

Expected: PASS; no caller remains with the old function signatures.

- [ ] **Step 6: Commit the frontend idempotency slice**

```bash
git add frontend/src/api/excel-final.api.ts frontend/src/types/excel-final.ts frontend/src/features/files/ExcelFinalPage.tsx frontend/src/features/files/Dxf2ExcelPage.tsx frontend/tests/e2e/api-contract.spec.ts frontend/tests/e2e/excel-final-flow.spec.ts
git commit -m "feat: submit excel final requests idempotently"
```

### Task 8: Restore Excel Final monitor state from the URL

**Files:**
- Create: `frontend/src/features/files/excel-final/excelFinalUrlState.ts`
- Modify: `frontend/src/features/files/ExcelFinalPage.tsx`
- Modify: `frontend/src/features/files/excel-final/ExcelFinalTools.tsx`
- Test: `frontend/tests/e2e/excel-final-flow.spec.ts`

- [ ] **Step 1: Add failing deep-link and history tests**

```typescript
await page.goto('/files/excel-final?job_id=41&batch_page=3&batch_size=50&batch_id=7&part_no=P-1&search_page=2');
await expect(page.getByLabel('任务 41 状态')).toBeVisible();
await expect(page.getByRole('dialog')).toContainText('批次 #7');
await expect(page.getByLabel('跨批次零件号')).toHaveValue('P-1');
await expect(page.getByText('共 120 个批次')).toBeVisible();
await page.getByRole('button', { name: '关闭' }).click();
expect(new URL(page.url()).searchParams.has('batch_id')).toBe(false);
await page.goBack();
await expect(page.getByRole('dialog')).toContainText('批次 #7');
```

- [ ] **Step 2: Run and confirm only `job_id` is restored**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts --grep 'deep link|browser history'`

Expected: FAIL because pagination, drawer, and search use component-only state.

- [ ] **Step 3: Implement strict parsing and merged updates**

```typescript
export const BATCH_SIZES = [10, 20, 50, 100] as const;
export const SEARCH_SIZES = [10, 20, 50, 100] as const;

export function positiveInt(params: URLSearchParams, key: string, fallback: number): number {
  const value = Number(params.get(key));
  return Number.isSafeInteger(value) && value > 0 ? value : fallback;
}

export function mergeExcelFinalParams(
  current: URLSearchParams,
  changes: Record<string, string | number | null>,
): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === '') next.delete(key);
    else next.set(key, String(value));
  }
  return next;
}
```

Add `allowedPageSize()`, trimmed string parsing, and a `parseExcelFinalUrlState()` result containing job, batch, drawer, applied search, and search pagination.

- [ ] **Step 4: Make page and tools controlled by URL state**

```typescript
const urlState = parseExcelFinalUrlState(searchParams);
const selectedBatchId = urlState.batchId;

function updateUrl(changes: Record<string, string | number | null>, replace = false) {
  setSearchParams(mergeExcelFinalParams(searchParams, changes), { replace });
}

<ExcelFinalTools
  filters={urlState.searchFilters}
  page={urlState.searchPage}
  pageSize={urlState.searchPageSize}
  onSearch={(filters) => updateUrl({ ...filters, search_page: null })}
  onClear={() => updateUrl({ part_no: null, spec: null, material: null, search_page: null })}
  onPageChange={(page, size) => updateUrl({ search_page: page, search_size: size })}
/>
```

Use merged updates for batch pagination, job tracking, drawer open/close, and reset page to 1 when page size changes.

- [ ] **Step 5: Run deep-link flow, accessibility, and build checks**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts && npm run build`

Expected: PASS with browser back/forward restoring the drawer and all unrelated query parameters preserved.

- [ ] **Step 6: Commit the URL-state slice**

```bash
git add frontend/src/features/files/excel-final/excelFinalUrlState.ts frontend/src/features/files/ExcelFinalPage.tsx frontend/src/features/files/excel-final/ExcelFinalTools.tsx frontend/tests/e2e/excel-final-flow.spec.ts
git commit -m "feat: preserve excel final monitor state in urls"
```

### Task 9: Render accurate health and refresh metadata

**Files:**
- Modify: `frontend/src/types/excel-final.ts`
- Modify: `frontend/src/features/files/ExcelFinalPage.tsx`
- Modify: `frontend/src/features/files/excel-final/ExcelFinalOverview.tsx`
- Modify: `frontend/src/features/files/excel-final/ExcelFinalPage.css`
- Test: `frontend/tests/e2e/excel-final-flow.spec.ts`

- [ ] **Step 1: Add failing local/MinIO/degraded UI tests**

```typescript
await page.route('**/api/v1/excel-final/health', route => route.fulfill({
  json: { data: {
    pipeline_enabled: true, stage_available: true, dependencies_available: true,
    package_available: true, handbook_available: true, handbook_database_available: true,
    database_backend: 'sqlite', database_available: true,
    storage_backend: 'local', storage_available: true, storage_bucket: 'dwg-reports',
    degraded_components: [], ready: true,
  }, request_id: 'ui-health-local' },
}));
await page.goto('/files/excel-final');
await expect(page.getByText('SQLite 权威数据')).toBeVisible();
await expect(page.getByText('本地对象存储')).toBeVisible();
await expect(page.getByText('MinIO 文件对象')).toHaveCount(0);

await page.route('**/api/v1/excel-final/health', route => route.fulfill({
  json: { data: {
    pipeline_enabled: true, stage_available: true, dependencies_available: true,
    package_available: true, handbook_available: true, handbook_database_available: true,
    database_backend: 'mysql', database_available: true,
    storage_backend: 'minio', storage_available: false, storage_bucket: 'dwg-reports',
    degraded_components: ['object_storage'], ready: false,
  }, request_id: 'ui-health-minio-down' },
}));
await page.reload();
await expect(page.getByText(/对象存储不可用/)).toBeVisible();
```

- [ ] **Step 2: Run and confirm fixed MinIO/MySQL text fails**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts --grep 'health backend|degraded component'`

Expected: FAIL because the overview hard-codes MySQL and MinIO.

- [ ] **Step 3: Extend health types and label helpers**

```typescript
export interface ExcelFinalHealth {
  pipeline_enabled: boolean;
  stage_available: boolean;
  dependencies_available: boolean;
  package_available: boolean;
  handbook_available: boolean;
  handbook_database_available: boolean;
  ready: boolean;
  database_backend: string;
  database_available: boolean;
  storage_backend: 'local' | 'minio';
  storage_available: boolean;
  storage_bucket: string;
  degraded_components: string[];
}

const databaseLabel = health?.database_backend === 'mysql' ? 'MySQL' : 'SQLite';
const storageLabel = health?.storage_backend === 'minio' ? 'MinIO 对象存储' : '本地对象存储';
```

Map stable degraded component codes to Chinese messages and expose them in one warning description.

- [ ] **Step 4: Show last successful refresh time**

```typescript
const refreshedAt = Math.max(healthQ.dataUpdatedAt, overviewQ.dataUpdatedAt, batchesQ.dataUpdatedAt);
<span className="excel-final-refreshed" aria-live="polite">
  最近刷新 {refreshedAt ? new Date(refreshedAt).toLocaleTimeString('zh-CN') : '等待数据'}
</span>
```

Style this as secondary metadata beside the existing refresh button; keep a single-column layout below the current mobile breakpoint.

- [ ] **Step 5: Run UI flows and build**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts tests/e2e/data-console.spec.ts && npm run build`

Expected: PASS in both local and MinIO mocked states, with no hard-coded backend mismatch.

- [ ] **Step 6: Commit the observability UI slice**

```bash
git add frontend/src/types/excel-final.ts frontend/src/features/files/ExcelFinalPage.tsx frontend/src/features/files/excel-final/ExcelFinalOverview.tsx frontend/src/features/files/excel-final/ExcelFinalPage.css frontend/tests/e2e/excel-final-flow.spec.ts
git commit -m "feat: show truthful data console health"
```

### Task 10: Verify migrations and real transaction paths

**Files:**
- Modify: `backend/app/services/job_service.py`
- Modify: `backend/app/api/v1/excel_final_api.py`
- Test: `backend/tests/test_excel_final_idempotency_mysql.py`
- Test: `scripts/verify_storage_transactions.py`

- [ ] **Step 1: Add MySQL idempotency assertions to the integration suite**

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

def test_excel_final_request_key_is_unique_under_mysql(mysql_sessions, user):
    payload = JobCreate(task_type=TASK_EXCEL_FINAL, params={"file_id": 91})
    gate = Barrier(2)

    def submit() -> tuple[int, bool]:
        with mysql_sessions() as session:
            gate.wait()
            job, reused = create_or_reuse_job(
                session,
                payload,
                created_by=user.id,
                request_key="process:mysql-race-1",
            )
            session.commit()
            return job.id, reused

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: submit(), range(2)))
    with mysql_sessions() as verify:
        count = verify.scalar(select(func.count()).select_from(Job).where(
            Job.created_by == user.id,
            Job.task_type == TASK_EXCEL_FINAL,
            Job.request_key == "process:mysql-race-1",
        ))
    assert len({job_id for job_id, _ in jobs}) == 1
    assert sorted(reused for _, reused in jobs) == [False, True]
    assert count == 1
```

- [ ] **Step 2: Run the focused MySQL transaction tests**

Run: `MYSQL_INTEGRATION_DATABASE_URL="$DATABASE_URL" backend/.venv/bin/pytest backend/tests/test_excel_final_idempotency_mysql.py -q`

Expected: PASS against the configured test MySQL; if it is unavailable, start the repository Compose database before retrying.

- [ ] **Step 3: Verify empty-schema and incremental migrations**

Run: `bash scripts/db.sh migration-test`

Expected: one head, upgrade to `d5e8a1c4b720`, downgrade/upgrade cycle succeeds, and the unique key exists on `jobs`.

- [ ] **Step 4: Run both storage transaction probes**

Run: `cd backend && STORAGE_BACKEND=local .venv/bin/python ../scripts/verify_storage_transactions.py`

Run from `backend/` against the Compose-only MinIO network endpoint without publishing a host port:

```bash
MINIO_IP=$(docker inspect complete_framework-minio-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
MINIO_ACCESS_KEY=$(sed -n 's/^MINIO_ACCESS_KEY=//p' ../.env.docker | head -n 1)
MINIO_SECRET_KEY=$(sed -n 's/^MINIO_SECRET_KEY=//p' ../.env.docker | head -n 1)
STORAGE_BACKEND=minio MINIO_ENDPOINT="http://$MINIO_IP:9000" \
  MINIO_ACCESS_KEY="$MINIO_ACCESS_KEY" MINIO_SECRET_KEY="$MINIO_SECRET_KEY" \
  .venv/bin/python ../scripts/verify_storage_transactions.py
```

Expected: each reports registered source/result objects, succeeded inbound/internal/outbound transfers, and no orphan created by a replay.

- [ ] **Step 5: Commit integration-only fixes/tests**

```bash
git add backend/tests/test_mysql_transaction_boundaries.py scripts/verify_storage_transactions.py backend/app
git commit -m "test: verify idempotency on mysql and object storage"
```

### Task 11: Update durable documentation

**Files:**
- Modify: `docs/api.md`
- Modify: `docs/database.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Modify: `docs/processing-pipelines.md`
- Modify: `docs/configuration.md`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-13-production-consistency-and-observability.md`

- [ ] **Step 1: Document the API contract**

Add exact `Idempotency-Key`, `reused`, 409/422 error semantics, health response fields, and URL query parameters. Include a curl replay example whose two responses have the same `job_id`.

- [ ] **Step 2: Document schema and lifecycle**

Record `jobs.request_key`, `uq_jobs_actor_task_request_key`, nullable legacy behavior, source-to-preview soft-delete coupling, physical retention, and the `preview_cache_reuse` transfer operation.

- [ ] **Step 3: Document operation and recovery procedures**

Describe how to distinguish a replay from a retry, how to inspect one request key across Job/FileTransfer/AuditLog, how health degradation is classified, and how to run MySQL/MinIO verification without exposing credentials.

- [ ] **Step 4: Regenerate/check docs and scan drift**

Run: `make docs-generate && make docs-check`

Run: `rg -n "MinIO 文件对象|MySQL 权威数据|9c4e7b1a2d60|Idempotency-Key|request_key" README.md docs frontend/src backend/app`

Expected: generated API docs are current, stale fixed-backend claims are qualified, and the new head/request contract is documented.

- [ ] **Step 5: Record verified evidence in this plan**

Append a `## Verification evidence` section with the exact date, commands, pass counts, migration head, backend types used by real probes, and screenshot path. Do not write claims that were not observed.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md docs
git commit -m "docs: describe idempotent data monitoring workflows"
```

### Task 12: Full regression and final self-review

**Files:**
- Modify only for defects discovered by verification
- Test: repository-wide suites

- [ ] **Step 1: Run backend static and full tests**

Run: `cd backend && .venv/bin/ruff check app tests && .venv/bin/pytest -q`

Expected: Ruff PASS; Pytest PASS with only explicitly documented skips.

- [ ] **Step 2: Run Stage and infrastructure gates**

Run the repository's existing Stage 1, Stage 2, Excel Final adapter, infrastructure and Compose verification commands recorded in `docs/development.md` and `docs/operations.md`.

Expected: all configured gates PASS; external dependency skips are reported separately from passes.

- [ ] **Step 3: Run frontend full build and Playwright suite**

Run: `cd frontend && npm run build && npx playwright test`

Expected: TypeScript/Vite build PASS and all Playwright projects PASS except pre-existing documented skips.

- [ ] **Step 4: Capture the final data-console screenshot**

Run the Excel Final Playwright flow at desktop width and save `output/playwright/excel-final-production-observability.png`.

Expected: screenshot visibly shows actual database/storage labels, refresh time, batch pagination, task status, search tools, and no clipped controls.

- [ ] **Step 5: Review the complete branch diff**

Run: `git diff main...HEAD --check && git diff --stat main...HEAD && git status --short`

Check every new endpoint for authentication, every query for domain plus access filtering, every object write for FileTransfer registration, every response for stable error text, and every document claim against observed evidence.

- [ ] **Step 6: Commit any verification fixes and leave a clean tree**

```bash
git add backend/app backend/tests frontend/src frontend/tests docs scripts
git commit -m "fix: close final consistency review findings"
git status --short
```

Expected: no uncommitted files and no known acceptance criterion left unverified.

## Verification evidence

Observed on 2026-07-13 in `/home/Creeken/Paper/CAD_research/complete_framework`:

- Backend static gate: `cd backend && .venv/bin/ruff check app tests ../tests/run_full_verify.py ../scripts/check_docs.py ../scripts/generate_api_docs.py` passed.
- Backend full suite: `879 passed, 5 skipped`, including the live MySQL concurrency test; existing dependency/deprecation warnings only.
- MySQL concurrency: the focused two-session request-key race passed five consecutive runs. The unique-conflict loser uses a locking current read after savepoint rollback so MySQL `REPEATABLE READ` cannot hide the committed winner behind an older snapshot.
- Migration: single head `d5e8a1c4b720`; `alembic check` reported no new operations. `bash scripts/db.sh migration-test` upgraded an empty MySQL schema through all 13 revisions and verified 28 application tables, the request-key column/constraint, and seeds.
- Storage transactions: local/MySQL probe created Excel file #903 / Job #1080 and DXF #904 / SVG #905; Compose MinIO/MySQL probe created Excel file #906 / Job #1081 and DXF #907 / SVG #908. Both returned 677-byte SVG content and succeeded for inbound upload, preview generation, authenticated outbound preview, preview invalidation, and source soft delete. Probe-owned objects were removed afterward.
- Infrastructure and pipeline gates: `110 / 110`; Stage suites `28 + 28 + 259 passed`.
- Frontend: production build passed; full Playwright suite `72 passed, 1 skipped`. The skipped case requires an externally configured real XLS sample path.
- Browser review: authenticated 1440×1000 session, no console errors, truthful `MySQL` plus local-storage health, recent refresh time, server pagination, search tools and task ledger. The hero description uses a project-owned class rather than Ant Design's rendered tag; its final computed color is `rgb(185, 206, 216)`.
- Documentation: `make docs-generate && make docs-check` passed after recording the current head, API contract, probes, configuration boundary and evidence.
- Final screenshot: `output/playwright/excel-final-production-observability.png`.
