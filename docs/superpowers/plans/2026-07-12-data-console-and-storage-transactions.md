# Data Console and Storage Transactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable MySQL/MinIO file-flow ledger, recoverable storage transactions, safe reconciliation/remediation APIs, and a five-tab frontend data console.

**Architecture:** Keep `files` as the business metadata authority and local/MinIO as the byte authority. Coordinate cross-system writes with a durable transfer saga, scan storage asynchronously into run/finding tables, and expose only allowlisted business administration APIs. Preserve existing upload/download URLs while moving their internals onto the ledger and strict streaming paths.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Celery SQL transport, MinIO SDK, pytest, React 19, TypeScript 6, TanStack Query, Ant Design 6, Playwright.

---

## File map

### Backend model and migration files

- Create `backend/app/models/file_transfer.py`: immutable identity and mutable transfer status.
- Create `backend/app/models/storage_scan.py`: scan runs and persisted findings.
- Modify `backend/app/models/file.py`: `deleted_at`, uniqueness, retention index.
- Modify `backend/app/models/__init__.py`: import new models for metadata discovery.
- Create `backend/migrations/versions/6d2f8a9c1b40_add_data_console_ledger.py`: additive schema migration and deleted-row backfill.

### Backend services and APIs

- Create `backend/app/services/file_transfer_service.py`: durable transfer intent/finalization and streaming settlement.
- Modify `backend/app/services/storage_service.py`: upload/generated-file saga integration.
- Modify `backend/app/storage/base.py`: object info, stat, existence, cursor-page contract.
- Modify `backend/app/storage/local_storage.py`: local inventory implementation.
- Modify `backend/app/storage/minio_storage.py`: MinIO inventory implementation.
- Create `backend/app/services/storage_reconciliation_service.py`: scan, classification, preview, execution.
- Modify `backend/app/services/file_service.py`: strict disk-spooled ZIP export.
- Modify `backend/app/api/v1/files_api.py`: ledger-aware upload/download/delete/export endpoints.
- Create `backend/app/schemas/data_admin_schema.py`: paged read/write contracts.
- Create `backend/app/api/v1/data_admin_api.py`: allowlisted administration endpoints.
- Modify `backend/app/api/v1/router.py`: register the new router.
- Modify `backend/app/workers/tasks_report.py`: asynchronous scan task.
- Modify `scripts/reap_storage.py`: per-item commits and correct `(bucket, key)` orphan logic.

### Frontend files

- Create `frontend/src/types/data-admin.ts`: data console contracts.
- Create `frontend/src/api/data-admin.api.ts`: paged/cursor API methods and mutations.
- Replace `frontend/src/features/admin/InfrastructurePage.tsx`: data console shell.
- Create `frontend/src/features/admin/data-console/OverviewTab.tsx`.
- Create `frontend/src/features/admin/data-console/FilesTab.tsx`.
- Create `frontend/src/features/admin/data-console/ObjectsTab.tsx`.
- Create `frontend/src/features/admin/data-console/TransfersTab.tsx`.
- Create `frontend/src/features/admin/data-console/ConsistencyTab.tsx`.
- Create `frontend/src/features/admin/data-console/RemediationDrawer.tsx`.
- Create `frontend/src/features/admin/data-console/format.ts`.
- Modify `frontend/src/api/files.api.ts`: add paged file reads and retain compatibility helpers.
- Modify `frontend/src/api/audit-logs.api.ts`: server-side pagination/filtering.
- Modify `frontend/src/components/ConversionPage.tsx`: stop loading every file/job page.
- Modify `frontend/src/features/admin/AuditLogsPage.tsx`: controlled server pagination.
- Modify `frontend/src/styles.css`: industrial data-console tokens and responsive rules.

### Test and documentation files

- Create `backend/tests/test_file_transfer_models.py`.
- Create `backend/tests/test_storage_inventory.py`.
- Create `backend/tests/test_file_transfer_service.py`.
- Create `backend/tests/test_storage_reconciliation.py`.
- Create `backend/tests/test_data_admin_api.py`.
- Modify `backend/tests/test_file_service.py`.
- Modify `backend/tests/test_storage_operations.py`.
- Modify `backend/tests/test_migrations.py`.
- Modify `backend/tests/test_frontend_contract.py`.
- Create `frontend/tests/e2e/data-console.spec.ts`.
- Modify `docs/database.md`, `docs/api.md`, `docs/architecture.md`, `docs/operations.md`, and `docs/workflow-verification.md`.

---

### Task 1: Add ledger and scan schema

**Files:**
- Create: `backend/tests/test_file_transfer_models.py`
- Create: `backend/app/models/file_transfer.py`
- Create: `backend/app/models/storage_scan.py`
- Modify: `backend/app/models/file.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/migrations/versions/6d2f8a9c1b40_add_data_console_ledger.py`
- Modify: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write failing model tests**

```python
def test_file_storage_location_is_unique(db):
    first = stored_file(bucket="dwg-original", key="uploads/a.dwg")
    second = stored_file(bucket="dwg-original", key="uploads/a.dwg")
    db.add_all([first, second])
    with pytest.raises(IntegrityError):
        db.commit()


def test_transfer_defaults_to_prepared(db):
    row = FileTransfer(
        transfer_uid=str(uuid4()),
        direction="inbound",
        operation="upload",
        request_id="req-model",
    )
    db.add(row)
    db.commit()
    assert row.status == "prepared"
    assert row.transferred_bytes == 0


def test_scan_finding_is_unique_per_run_location_and_type(db):
    run = StorageScanRun(backend="local", status="queued")
    db.add(run)
    db.flush()
    finding = dict(
        run_id=run.id,
        finding_type="missing_object",
        bucket="dwg-original",
        storage_key="uploads/missing.dwg",
    )
    db.add_all([StorageScanFinding(**finding), StorageScanFinding(**finding)])
    with pytest.raises(IntegrityError):
        db.commit()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && uv run pytest -q tests/test_file_transfer_models.py`

Expected: collection fails because `FileTransfer` and `StorageScanRun` do not exist.

- [ ] **Step 3: Implement focused models**

```python
class FileTransfer(TimestampMixin, Base):
    __tablename__ = "file_transfers"
    __table_args__ = (
        UniqueConstraint("transfer_uid", name="uq_file_transfers_uid"),
        UniqueConstraint(
            "actor_user_id", "operation", "idempotency_key",
            name="uq_file_transfers_idempotency",
        ),
        Index("ix_file_transfers_direction_created", "direction", "created_at"),
        Index("ix_file_transfers_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    transfer_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="prepared")
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    batch_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    bucket: Mapped[str | None] = mapped_column(String(128))
    storage_key: Mapped[str | None] = mapped_column(String(512))
    original_name: Mapped[str | None] = mapped_column(String(255))
    expected_bytes: Mapped[int | None] = mapped_column(BigInteger)
    transferred_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Create `StorageScanRun` with explicit counters and `StorageScanFinding` with the uniqueness required by the test. Add `StoredFile.deleted_at`, `UniqueConstraint("bucket", "storage_key")`, and the retention index.

- [ ] **Step 4: Generate and harden the migration**

Run: `cd backend && uv run alembic revision --autogenerate --rev-id 6d2f8a9c1b40 -m "add data console ledger"`

Edit the generated migration so upgrade performs, in order: duplicate preflight query, add nullable `deleted_at`, backfill deleted rows from `updated_at`, create the unique constraint/index, create all three tables. Downgrade drops new tables first, then file index/constraint/column.

- [ ] **Step 5: Verify GREEN and migration consistency**

Run: `cd backend && uv run pytest -q tests/test_file_transfer_models.py tests/test_migrations.py && uv run alembic check`

Expected: all selected tests pass and Alembic reports no new upgrade operations.

- [ ] **Step 6: Commit schema foundation**

```bash
git commit --only backend/app/models backend/migrations/versions backend/tests/test_file_transfer_models.py backend/tests/test_migrations.py -m "feat: add file transfer and storage scan ledger"
```

### Task 2: Add local and MinIO inventory contracts

**Files:**
- Create: `backend/tests/test_storage_inventory.py`
- Modify: `backend/app/storage/base.py`
- Modify: `backend/app/storage/local_storage.py`
- Modify: `backend/app/storage/minio_storage.py`

- [ ] **Step 1: Write failing adapter contract tests**

```python
@pytest.mark.parametrize("page_size", [1, 2])
def test_local_inventory_is_cursor_paged(tmp_path, page_size):
    storage = LocalFileStorage(tmp_path)
    put(storage, "dwg-original", "uploads/b.dwg", b"bb")
    put(storage, "dwg-original", "uploads/a.dwg", b"a")
    first = storage.list_objects("dwg-original", prefix="uploads/", cursor=None, page_size=page_size)
    assert [item.storage_key for item in first.items] == ["uploads/a.dwg"][:page_size]
    if first.next_cursor:
        second = storage.list_objects(
            "dwg-original", prefix="uploads/", cursor=first.next_cursor, page_size=page_size
        )
        assert second.items[0].storage_key > first.items[-1].storage_key


def test_object_exists_distinguishes_missing_from_backend_error(storage):
    assert storage.object_exists("dwg-original", "missing.dwg") is False
    storage.stat_object = Mock(side_effect=StorageError("offline"))
    with pytest.raises(StorageError):
        storage.object_exists("dwg-original", "any.dwg")
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_storage_inventory.py`

Expected: failures for missing `ObjectInfo`, `ObjectPage`, `stat_object`, and `list_objects`.

- [ ] **Step 3: Add exact base contracts**

```python
@dataclass(frozen=True)
class ObjectInfo:
    bucket: str
    storage_key: str
    size_bytes: int
    last_modified: datetime | None


@dataclass(frozen=True)
class ObjectPage:
    items: list[ObjectInfo]
    next_cursor: str | None


def object_exists(self, bucket: str, storage_key: str) -> bool:
    try:
        self.stat_object(bucket, storage_key)
        return True
    except StorageObjectNotFound:
        return False
```

Make `stat_object` and `list_objects` abstract. Local implementation returns sorted relative keys and rejects cursor/prefix traversal. MinIO implementation maps `stat_object` and `list_objects(..., start_after=cursor)` errors into storage exceptions without exposing credentials or endpoints.

- [ ] **Step 4: Verify both adapters**

Run: `cd backend && uv run pytest -q tests/test_storage_inventory.py tests/test_storage_consistency.py tests/test_storage_adversarial.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit adapter contract**

```bash
git commit --only backend/app/storage backend/tests/test_storage_inventory.py backend/tests/test_storage_consistency.py backend/tests/test_storage_adversarial.py -m "feat: add paged storage inventory contract"
```

### Task 3: Implement durable transfer intent and upload saga

**Files:**
- Create: `backend/tests/test_file_transfer_service.py`
- Create: `backend/app/services/file_transfer_service.py`
- Modify: `backend/app/services/storage_service.py`
- Modify: `backend/app/api/v1/files_api.py`

- [ ] **Step 1: Write RED tests for idempotency and compensation**

```python
def test_begin_transfer_reuses_succeeded_idempotent_operation(db_factory):
    first = begin_transfer(db_factory, TransferSpec(
        direction="inbound", operation="upload", actor_user_id=7,
        request_id="req-1", idempotency_key="same",
    ))
    mark_transfer_succeeded(db_factory, first.transfer_uid, transferred_bytes=10)
    second = begin_transfer(db_factory, TransferSpec(
        direction="inbound", operation="upload", actor_user_id=7,
        request_id="req-2", idempotency_key="same",
    ))
    assert second.transfer_uid == first.transfer_uid
    assert second.status == "succeeded"


def test_upload_db_failure_deletes_object_and_marks_transfer_failed(client, storage, monkeypatch):
    monkeypatch.setattr(Session, "commit", fail_the_file_metadata_commit_once)
    response = upload_valid_dwg(client, idempotency_key="db-fail")
    assert response.status_code == 500
    assert storage.keys() == set()
    assert transfer_status("db-fail") == "failed"


def test_upload_compensation_failure_is_persisted(client, storage, monkeypatch):
    monkeypatch.setattr(storage, "delete_object", Mock(side_effect=StorageError("offline")))
    monkeypatch.setattr(Session, "commit", fail_the_file_metadata_commit_once)
    upload_valid_dwg(client, idempotency_key="comp-fail")
    assert transfer_status("comp-fail") == "compensation_required"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_file_transfer_service.py`

Expected: import failures for `begin_transfer` and settlement helpers.

- [ ] **Step 3: Implement independent short-transaction helpers**

```python
def begin_transfer(factory: sessionmaker[Session], spec: TransferSpec) -> TransferSnapshot:
    with factory.begin() as db:
        existing = find_idempotent_transfer(db, spec)
        if existing is not None:
            return TransferSnapshot.from_model(existing)
        row = FileTransfer(
            transfer_uid=str(uuid4()),
            direction=spec.direction,
            operation=spec.operation,
            actor_user_id=spec.actor_user_id,
            request_id=spec.request_id,
            idempotency_key=spec.idempotency_key,
            status="prepared",
        )
        db.add(row)
        db.flush()
        return TransferSnapshot.from_model(row)
```

Implement settlement with guarded status updates. Sanitize stored errors to a known error code and a short public message. Never persist raw exception repr, DSN, endpoint, or traceback.

- [ ] **Step 4: Wire `save_upload_file` and `save_bytes_as_file`**

Pass a `TransferContext` from routes/workers. After object PUT, set location and in-progress status. Commit file metadata, transfer success, and audit together through the request/worker session. On rollback, compensate the exact object and settle through a fresh session. Remove the global undifferentiated pending-object list after all callers migrate.

- [ ] **Step 5: Verify upload endpoints and generated files**

Run: `cd backend && uv run pytest -q tests/test_file_transfer_service.py tests/test_storage_consistency.py tests/test_api_regressions.py tests/test_celery_minio_deployment.py`

Expected: all selected tests pass, including previous upload contracts.

- [ ] **Step 6: Commit upload saga**

```bash
git commit --only backend/app/services/file_transfer_service.py backend/app/services/storage_service.py backend/app/api/v1/files_api.py backend/tests/test_file_transfer_service.py -m "feat: record recoverable file intake transactions"
```

### Task 4: Make single-file and ZIP output settlement truthful

**Files:**
- Modify: `backend/tests/test_file_service.py`
- Modify: `backend/app/services/file_service.py`
- Modify: `backend/app/api/v1/files_api.py`
- Modify: `backend/app/services/file_transfer_service.py`

- [ ] **Step 1: Write RED tests for strict export and stream settlement**

```python
def test_build_zip_fails_if_required_object_is_missing(db, fake_storage):
    source = add_file(db, ext=".dwg", payload=None)
    with pytest.raises(AppHTTPException) as exc:
        build_zip_to_path(db, [source.id], ["dwg"], "export")
    assert exc.value.detail["code"] == "STORAGE_INCONSISTENT"


def test_download_transfer_succeeds_only_after_iterator_exhaustion(client, valid_file):
    response = client.get(signed_download(valid_file.id), headers=admin_headers())
    assert response.status_code == 200
    transfer = latest_outbound(valid_file.id)
    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == valid_file.size_bytes


def test_download_iterator_error_marks_transfer_failed(db_factory):
    iterator = settle_stream(
        transfer_uid="transfer-1", chunks=raising_chunks(), factory=db_factory
    )
    with pytest.raises(StorageError):
        b"".join(iterator)
    assert transfer_by_uid("transfer-1").status == "failed"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_file_service.py -k 'missing or transfer or stream'`

Expected: old `build_zip` returns an incomplete archive and no stream settlement API exists.

- [ ] **Step 3: Replace in-memory ZIP assembly**

Implement `build_zip_to_path` with `NamedTemporaryFile`, `zipfile.ZipFile.write/writestr` fed from spooled per-object files, strict missing-object errors, duplicate filename disambiguation, and unconditional cleanup ownership passed to the response iterator.

```python
@dataclass(frozen=True)
class PreparedExport:
    path: Path
    filename: str
    size_bytes: int
    included_file_ids: tuple[int, ...]
```

- [ ] **Step 4: Settle all downloads after actual iteration**

Use `StreamingResponse` for local and MinIO so the same iterator counts bytes. Create the outbound transfer before returning the response. Mark success only after exhaustion; mark cancelled on `GeneratorExit`; mark failed on storage errors. Retain `files.download_url` as an audit action but not as a succeeded transfer.

- [ ] **Step 5: Run file and browser API contracts**

Run: `cd backend && uv run pytest -q tests/test_file_service.py tests/test_api_regressions.py tests/test_security_boundaries.py`

Expected: all selected tests pass and no test accepts a partial ZIP.

- [ ] **Step 6: Commit truthful outbound flows**

```bash
git commit --only backend/app/services/file_service.py backend/app/services/file_transfer_service.py backend/app/api/v1/files_api.py backend/tests/test_file_service.py -m "feat: settle file exports after streaming completes"
```

### Task 5: Repair storage reaper transaction boundaries

**Files:**
- Modify: `backend/tests/test_storage_operations.py`
- Modify: `scripts/reap_storage.py`

- [ ] **Step 1: Add the mixed-result regression test**

```python
def test_mixed_reaper_batch_keeps_db_and_storage_consistent(db, reaper):
    module, storage = reaper
    ok_row = deleted_file("ok.dwg")
    failed_row = deleted_file("failed.dwg")
    db.add_all([ok_row, failed_row])
    db.commit()
    storage.delete_object.side_effect = [None, StorageError("offline")]

    result = module.reap(retention_days=1, dry_run=False)

    assert result["rows_deleted"] == 1
    assert result["errors"] == 1
    assert db.get(StoredFile, ok_row.id) is None
    assert db.get(StoredFile, failed_row.id) is not None
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_storage_operations.py -k mixed`

Expected: the successful row survives because the old final commit is skipped when any error occurs.

- [ ] **Step 3: Commit each successful deletion independently**

After deleting one object, delete its row and commit before advancing. On a storage failure, rollback only the current row transaction and continue. Change orphan keys from `set[str]` to `set[tuple[str, str]]`. Exclude active temp files and health probes by prefix/age rules.

- [ ] **Step 4: Verify reaper tests**

Run: `cd backend && uv run pytest -q tests/test_storage_operations.py`

Expected: all storage operation tests pass.

- [ ] **Step 5: Commit reaper fix**

```bash
git commit --only scripts/reap_storage.py backend/tests/test_storage_operations.py -m "fix: preserve storage consistency during mixed reaping"
```

### Task 6: Implement asynchronous consistency scans

**Files:**
- Create: `backend/tests/test_storage_reconciliation.py`
- Create: `backend/app/services/storage_reconciliation_service.py`
- Modify: `backend/app/workers/tasks_report.py`

- [ ] **Step 1: Write classification and concurrency tests**

```python
def test_scan_classifies_by_bucket_and_key(db, storage):
    add_file(db, bucket="a", key="same", status="available", size=10)
    storage.add(bucket="b", key="same", size=10)
    run = execute_scan(db_factory(), storage, scope_bucket=None)
    assert finding_types(run.id) == {"missing_object", "untracked_object"}


def test_deleted_object_is_retained_not_orphan(db, storage):
    row = add_file(db, status="deleted", size=10)
    storage.add(row.bucket, row.storage_key, size=10)
    run = execute_scan(db_factory(), storage, scope_bucket=row.bucket)
    assert one_finding(run.id).finding_type == "retained_deleted"


def test_only_one_active_scan_per_scope(db):
    create_scan_run(db, scope_bucket="dwg-original", status="running")
    with pytest.raises(AppHTTPException) as exc:
        queue_scan(db, scope_bucket="dwg-original", actor_user_id=1)
    assert exc.value.detail["code"] == "CONSISTENCY_SCAN_ACTIVE"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_storage_reconciliation.py`

Expected: reconciliation service imports fail.

- [ ] **Step 3: Implement bounded merge/classification**

Read DB locations in ordered pages and storage objects through cursor pages. Compare exact `(bucket, key)`. Persist only retained-deleted and abnormal findings in batches; update counters after each batch with a guarded run-status update. On failure, store a public error code and mark the run failed.

- [ ] **Step 4: Wire the report task**

```python
@celery_app.task(name="app.tasks.scan_storage_consistency", queue="report")
def scan_storage_consistency(scan_run_id: int) -> None:
    execute_scan_run(scan_run_id, session_factory=SessionLocal, storage=get_storage_backend())
```

Queue only after the scan run is committed. Repeated delivery must no-op for terminal runs.

- [ ] **Step 5: Verify scan and Celery tests**

Run: `cd backend && uv run pytest -q tests/test_storage_reconciliation.py tests/test_celery_recovery.py tests/test_job_claim.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit reconciliation engine**

```bash
git commit --only backend/app/services/storage_reconciliation_service.py backend/app/workers/tasks_report.py backend/tests/test_storage_reconciliation.py -m "feat: scan MySQL and object storage consistency"
```

### Task 7: Add preview-token remediation service

**Files:**
- Modify: `backend/tests/test_storage_reconciliation.py`
- Modify: `backend/app/services/storage_reconciliation_service.py`
- Create: `backend/app/schemas/data_admin_schema.py`

- [ ] **Step 1: Write RED tests for stale preview and idempotent execution**

```python
def test_execute_rejects_changed_target_after_preview(db, storage, admin):
    finding = add_untracked_finding(db, storage)
    preview = preview_remediation(db, admin, [finding.id], "purge")
    storage.delete_object(finding.bucket, finding.storage_key)
    with pytest.raises(AppHTTPException) as exc:
        execute_remediation(db, admin, preview.token, idempotency_key="purge-1")
    assert exc.value.detail["code"] == "REMEDIATION_PREVIEW_STALE"


def test_register_existing_computes_digest_and_is_idempotent(db, storage, admin):
    finding = add_untracked_finding(db, storage, payload=b"DXF")
    preview = preview_remediation(
        db, admin, [finding.id], "register_existing",
        metadata={"original_name": "recovered.dxf"},
    )
    first = execute_remediation(db, admin, preview.token, idempotency_key="register-1")
    second = execute_remediation(db, admin, preview.token, idempotency_key="register-1")
    assert first.transfer_uid == second.transfer_uid
    assert stored_file(first.file_id).sha256 == hashlib.sha256(b"DXF").hexdigest()
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_storage_reconciliation.py -k 'preview or register_existing'`

Expected: preview and execution functions do not exist.

- [ ] **Step 3: Implement signed, expiring preview payloads**

Sign a compact JSON payload containing actor ID, action, sorted finding IDs, target digest, count, bytes, and expiry with the application HMAC secret. Do not include credentials or absolute paths. Verify signature, expiry, actor, allowed action, and digest at execution.

- [ ] **Step 4: Implement guarded actions**

Use `SELECT ... FOR UPDATE` for linked file rows, then stat objects again. Implement exactly four actions: `restore`, `register_existing`, `soft_delete_missing`, `purge_untracked`. Enforce batch count/byte limits. Write transfer and audit rows in the same MySQL commit as file/finding changes.

- [ ] **Step 5: Verify remediation tests**

Run: `cd backend && uv run pytest -q tests/test_storage_reconciliation.py`

Expected: all scan and remediation tests pass.

- [ ] **Step 6: Commit remediation service**

```bash
git commit --only backend/app/services/storage_reconciliation_service.py backend/app/schemas/data_admin_schema.py backend/tests/test_storage_reconciliation.py -m "feat: add previewed storage remediation actions"
```

### Task 8: Expose read-only data administration APIs

**Files:**
- Create: `backend/tests/test_data_admin_api.py`
- Create: `backend/app/api/v1/data_admin_api.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/services/infrastructure_service.py`

- [ ] **Step 1: Write RED API tests**

```python
def test_data_admin_overview_identifies_environment(admin_client):
    response = admin_client.get("/api/v1/data-admin/overview")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["environment"]["app_env"] == settings.app_env
    assert data["environment"]["storage_backend"] == settings.storage_backend
    assert "database_url" not in json.dumps(data)


def test_auditor_can_read_but_viewer_cannot(auditor_client, viewer_client):
    assert auditor_client.get("/api/v1/data-admin/files").status_code == 200
    assert viewer_client.get("/api/v1/data-admin/files").status_code == 403


def test_file_list_filters_in_sql_and_paginates(admin_client):
    response = admin_client.get(
        "/api/v1/data-admin/files",
        params={"status": "available", "search": "sample", "page": 1, "page_size": 20},
    )
    assert response.status_code == 200
    assert response.json()["pagination"]["page_size"] == 20
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_data_admin_api.py -k 'overview or read or file_list'`

Expected: 404 for the unregistered router.

- [ ] **Step 3: Implement overview, files, objects, transfers, and scan reads**

Use schema response models, SQL pagination, allowlisted sorts, role dependencies, and cursor validation. Overview must isolate database and storage failures so one failing source yields a structured degraded section instead of a whole-response 500. It must not enumerate every object.

- [ ] **Step 4: Register the router**

```python
api_router.include_router(
    data_admin_api.router,
    prefix="/data-admin",
    tags=["data-admin"],
)
```

- [ ] **Step 5: Verify read API**

Run: `cd backend && uv run pytest -q tests/test_data_admin_api.py -k 'not remediation'`

Expected: all read API tests pass.

- [ ] **Step 6: Commit read API**

```bash
git commit --only backend/app/api/v1/data_admin_api.py backend/app/api/v1/router.py backend/app/services/infrastructure_service.py backend/tests/test_data_admin_api.py -m "feat: expose safe data administration reads"
```

### Task 9: Expose scan and remediation mutations

**Files:**
- Modify: `backend/tests/test_data_admin_api.py`
- Modify: `backend/app/api/v1/data_admin_api.py`

- [ ] **Step 1: Write RED mutation/RBAC tests**

```python
def test_admin_can_queue_scan_and_auditor_cannot(auditor_client, admin_client):
    assert admin_client.post("/api/v1/data-admin/scans", json={}).status_code == 202
    assert auditor_client.post("/api/v1/data-admin/scans", json={}).status_code == 403


def test_auditor_can_preview_but_not_execute(auditor_client, finding_id):
    preview = auditor_client.post(
        "/api/v1/data-admin/remediations/preview",
        json={"finding_ids": [finding_id], "action": "purge_untracked"},
    )
    assert preview.status_code == 200
    execute = auditor_client.post(
        "/api/v1/data-admin/remediations/execute",
        json={"preview_token": preview.json()["data"]["token"], "idempotency_key": "x"},
    )
    assert execute.status_code == 403
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_data_admin_api.py -k 'scan or preview or execute'`

Expected: mutation endpoints return 404.

- [ ] **Step 3: Implement mutation endpoints**

Return 202 for queued scans, 200 for preview, and 200 for idempotent execution. Require `admin` or `super_admin` for scan queue and execution; allow `auditor` for preview only. Write audit actions `storage.scan_queue`, `storage.remediation_preview`, and `storage.remediation_execute` with counts but no secret token.

- [ ] **Step 4: Verify all data-admin API tests**

Run: `cd backend && uv run pytest -q tests/test_data_admin_api.py tests/test_storage_reconciliation.py`

Expected: all tests pass.

- [ ] **Step 5: Commit mutations**

```bash
git commit --only backend/app/api/v1/data_admin_api.py backend/tests/test_data_admin_api.py -m "feat: expose guarded consistency operations"
```

### Task 10: Add frontend API contracts and console shell

**Files:**
- Create: `frontend/src/types/data-admin.ts`
- Create: `frontend/src/api/data-admin.api.ts`
- Replace: `frontend/src/features/admin/InfrastructurePage.tsx`
- Create: `frontend/src/features/admin/data-console/format.ts`
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] **Step 1: Add failing source-contract tests**

```python
def test_data_console_has_five_deep_link_tabs():
    source = (FRONTEND / "src/features/admin/InfrastructurePage.tsx").read_text()
    for key in ("overview", "files", "objects", "transfers", "consistency"):
        assert f"key: '{key}'" in source
    assert "useSearchParams" in source


def test_frontend_uses_data_admin_api_module():
    source = (FRONTEND / "src/api/data-admin.api.ts").read_text()
    assert "/api/v1/data-admin/overview" in source
    assert "/api/v1/data-admin/transfers" in source
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest -q tests/test_frontend_contract.py -k data_console`

Expected: missing frontend API/type files and tab keys.

- [ ] **Step 3: Define exact TypeScript contracts**

```typescript
export type TransferStatus =
  | 'prepared' | 'in_progress' | 'succeeded'
  | 'failed' | 'cancelled' | 'compensation_required';

export type FindingType =
  | 'missing_object' | 'untracked_object'
  | 'size_mismatch' | 'retained_deleted';

export interface DataAdminOverview {
  checked_at: string;
  status: 'ok' | 'degraded';
  environment: { app_env: string; database_engine: string; database: string; storage_backend: string };
  catalog: { available_files: number; deleted_files: number; tracked_bytes: number };
  transfers_today: { inbound: number; outbound: number; failed: number; compensation_required: number };
  latest_scan: StorageScanRun | null;
}
```

Define paged files/transfers/findings and cursor-paged objects without `any`.

- [ ] **Step 4: Build the URL-controlled shell**

Use `useSearchParams` for `tab`. Render five lazy child tabs. Show environment identity and stale timestamp in the header. Keep the route `/admin/infrastructure` and label “数据控制台”.

- [ ] **Step 5: Verify frontend shell**

Run: `cd frontend && npm run build`; then `cd ../backend && uv run pytest -q tests/test_frontend_contract.py -k data_console`

Expected: TypeScript build and focused contract tests pass.

- [ ] **Step 6: Commit console contracts**

```bash
git commit --only frontend/src/types/data-admin.ts frontend/src/api/data-admin.api.ts frontend/src/features/admin/InfrastructurePage.tsx frontend/src/features/admin/data-console/format.ts backend/tests/test_frontend_contract.py -m "feat: scaffold unified data console"
```

### Task 11: Implement overview, files, objects, and transfers tabs

**Files:**
- Create: `frontend/src/features/admin/data-console/OverviewTab.tsx`
- Create: `frontend/src/features/admin/data-console/FilesTab.tsx`
- Create: `frontend/src/features/admin/data-console/ObjectsTab.tsx`
- Create: `frontend/src/features/admin/data-console/TransfersTab.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add Playwright RED assertions for the four tabs**

```typescript
test('data console links files, objects, and transfers', async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto('/admin/infrastructure?tab=overview');
  await expect(page.getByText('当前数据源')).toBeVisible();
  await page.getByRole('tab', { name: '文件登记' }).click();
  await expect(page).toHaveURL(/tab=files/);
  await expect(page.getByPlaceholder('文件名、ID 或 SHA-256')).toBeVisible();
  await page.getByRole('tab', { name: '存储对象' }).click();
  await expect(page.getByText('对象 Key')).toBeVisible();
  await page.getByRole('tab', { name: '流转流水' }).click();
  await expect(page.getByText('实际字节')).toBeVisible();
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npx playwright test tests/e2e/data-console.spec.ts -g 'links files'`

Expected: missing tab content assertions fail.

- [ ] **Step 3: Implement overview behavior**

Use a 30-second query interval only while visible. Preserve previous data on refetch error and render a stale banner. Show catalog/transfer/risk metrics, latest scan, bucket summary, and recent failed transfers. Use text plus color for status.

- [ ] **Step 4: Implement server-paged files and objects**

Files use controlled Ant Table pagination and URL filters. Object listing uses cursor history for next/previous navigation, bucket/prefix filters, and registration status. Details open in drawers and link to related file/transfer filters.

- [ ] **Step 5: Implement server-paged transfers**

Add direction/status/operation/time filters, byte progress, request ID copy action, error detail drawer, and file deep links. `compensation_required` must have its own high-risk treatment.

- [ ] **Step 6: Add restrained industrial styling**

Define `--console-ink`, `--console-steel`, `--console-paper`, `--console-warning`, and `--console-danger`. Add responsive tab/table/drawer behavior and visible focus states. Avoid global redesign outside the console.

- [ ] **Step 7: Verify tabs**

Run: `cd frontend && npm run build && npx playwright test tests/e2e/data-console.spec.ts -g 'links files'`

Expected: build and focused test pass.

- [ ] **Step 8: Commit read tabs**

```bash
git commit --only frontend/src/features/admin/data-console frontend/src/styles.css frontend/tests/e2e/data-console.spec.ts -m "feat: add data console monitoring views"
```

### Task 12: Implement consistency and remediation UI

**Files:**
- Create: `frontend/src/features/admin/data-console/ConsistencyTab.tsx`
- Create: `frontend/src/features/admin/data-console/RemediationDrawer.tsx`
- Modify: `frontend/src/api/data-admin.api.ts`
- Modify: `frontend/tests/e2e/data-console.spec.ts`

- [ ] **Step 1: Add RED Playwright flow**

```typescript
test('scan findings require preview before execution', async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto('/admin/infrastructure?tab=consistency');
  await page.getByRole('button', { name: '开始扫描' }).click();
  await expect(page.getByText(/扫描中|排队中/)).toBeVisible();
  await page.getByRole('row', { name: /未登记对象/ }).getByRole('checkbox').check();
  await page.getByRole('button', { name: '处置预检' }).click();
  await expect(page.getByText('影响范围')).toBeVisible();
  await expect(page.getByRole('button', { name: '确认执行' })).toBeDisabled();
  await page.getByLabel('确认词').fill('PURGE');
  await expect(page.getByRole('button', { name: '确认执行' })).toBeEnabled();
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npx playwright test tests/e2e/data-console.spec.ts -g 'require preview'`

Expected: consistency controls are absent.

- [ ] **Step 3: Implement scan history and active polling**

Poll every 3 seconds only for queued/running scans. Show explicit counters and progress. Once terminal, stop polling and invalidate overview/findings. Keep historical runs selectable.

- [ ] **Step 4: Implement preview and execution drawer**

Render action-specific risk text, count, bytes, expiry, and fields required for `register_existing`. Purge requires the confirmation word returned by preview. Submit an idempotency key generated once when the drawer opens. On `REMEDIATION_PREVIEW_STALE`, close execution state and force a new preview.

- [ ] **Step 5: Verify consistency UI**

Run: `cd frontend && npm run build && npx playwright test tests/e2e/data-console.spec.ts -g 'require preview'`

Expected: build and focused flow pass.

- [ ] **Step 6: Commit consistency UI**

```bash
git commit --only frontend/src/features/admin/data-console/ConsistencyTab.tsx frontend/src/features/admin/data-console/RemediationDrawer.tsx frontend/src/api/data-admin.api.ts frontend/tests/e2e/data-console.spec.ts -m "feat: add previewed consistency remediation UI"
```

### Task 13: Remove unbounded frontend list loading

**Files:**
- Modify: `frontend/src/api/files.api.ts`
- Modify: `frontend/src/api/jobs.api.ts`
- Modify: `frontend/src/components/ConversionPage.tsx`
- Modify: `frontend/src/api/audit-logs.api.ts`
- Modify: `frontend/src/features/admin/AuditLogsPage.tsx`
- Modify: `frontend/tests/e2e/files-page-buttons.spec.ts`
- Modify: `frontend/tests/e2e/jobs-page-buttons.spec.ts`

- [ ] **Step 1: Add RED network assertions**

```typescript
test('conversion page requests one server page instead of every page', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/api/v1/files')) requests.push(request.url());
  });
  await loginAsAdmin(page);
  await page.goto('/files/dwg2dxf');
  await expect(page.getByRole('table')).toBeVisible();
  expect(requests.filter(url => url.includes('page=')).length).toBe(1);
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npx playwright test tests/e2e/files-page-buttons.spec.ts -g 'one server page'`

Expected: the old fetch-all loop makes multiple requests when total exceeds 200.

- [ ] **Step 3: Add paged API functions**

```typescript
export async function listFilesPage(params: FileListParams) {
  const res = await apiClient.get<PageEnvelope<StoredFile>>('/api/v1/files', { params });
  return { items: res.data.data, pagination: res.data.pagination };
}
```

Add equivalent job and audit log functions. Retain `listFiles` only for small compatibility callers and remove it from conversion/audit pages.

- [ ] **Step 4: Make tables controlled**

Store page, page size, filters and sort in URL/query state. Fetch jobs/files for the current page and use backend totals. After mutation, refetch the current page and move back one page if the last row was deleted.

- [ ] **Step 5: Verify existing flows**

Run: `cd frontend && npm run build && npx playwright test tests/e2e/files-page-buttons.spec.ts tests/e2e/jobs-page-buttons.spec.ts`

Expected: all existing button flows and the new single-page assertion pass.

- [ ] **Step 6: Commit bounded lists**

```bash
git commit --only frontend/src/api/files.api.ts frontend/src/api/jobs.api.ts frontend/src/components/ConversionPage.tsx frontend/src/api/audit-logs.api.ts frontend/src/features/admin/AuditLogsPage.tsx frontend/tests/e2e/files-page-buttons.spec.ts frontend/tests/e2e/jobs-page-buttons.spec.ts -m "perf: paginate operational frontend lists on the server"
```

### Task 14: Document and verify the complete system

**Files:**
- Modify: `docs/database.md`
- Modify: `docs/api.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Modify: `docs/workflow-verification.md`
- Modify: `README.md`

- [ ] **Step 1: Run focused backend quality gates**

Run:

```bash
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q tests/test_file_transfer_models.py tests/test_storage_inventory.py tests/test_file_transfer_service.py tests/test_storage_reconciliation.py tests/test_data_admin_api.py tests/test_file_service.py tests/test_storage_operations.py
uv run alembic check
```

Expected: all commands pass with no new warnings from project code.

- [ ] **Step 2: Run the full backend and migration gates**

Run:

```bash
cd backend && uv run pytest -q && cd ..
bash scripts/db.sh migration-test
```

Expected: full pytest passes; a disposable real MySQL schema upgrades to the new head and downgrades/upgrades as defined by the migration test.

- [ ] **Step 3: Generate and update documentation**

Run: `make docs-generate`

Update database table counts, data-authority boundaries, transfer statuses, scan/remediation operations, roles, new endpoints, and exact verification evidence. Do not claim that current real orphan objects were cleaned.

- [ ] **Step 4: Run frontend and documentation gates**

Run:

```bash
cd frontend
npm run build
npx playwright test
cd ..
make docs-check
bash infra/verify.sh
docker compose config --quiet
```

Expected: all commands pass.

- [ ] **Step 5: Rebuild Compose and execute a scoped real MinIO/MySQL loop**

Run:

```bash
docker compose up -d --build backend-api nginx worker-report
docker compose ps
```

Use a generated valid test object under a unique `verification/<uuid>/` prefix. Through public HTTP: upload it, verify the file row/MinIO object/inbound transfer, download it to exhaustion, verify outbound bytes, create one temporary orphan and one temporary missing-object record, scan, preview and resolve them, then delete all scoped verification records and objects. Record only IDs/counts, never secrets or signed URLs.

- [ ] **Step 6: Run the completion audit**

For every completion criterion in the design spec, point to a current file, test result, API response, browser result, or Compose runtime result. Treat missing evidence as incomplete and continue implementation.

- [ ] **Step 7: Commit documentation and final verification record**

```bash
git commit --only README.md docs backend/tests/test_docs_consistency.py -m "docs: record data console and storage transaction verification"
```
