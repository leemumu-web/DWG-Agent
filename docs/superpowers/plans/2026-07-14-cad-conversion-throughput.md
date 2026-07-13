# CAD Conversion Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DWG→DXF and DXF→DWG conversion reliably fast for single files and batches while keeping per-file jobs and providing timely, scope-correct frontend progress.

**Architecture:** Conversion workers run behind one persistent Xvfb display per queue, so prefork children can execute ODA concurrently without `xvfb-run -a` races. Bulk requests create individual jobs but dispatch one direction-specific batch task, which groups files by ODA output version and uses ODA directory conversion. A multi-job SSE stream keeps the shared conversion page synchronized with the authoritative MySQL job rows.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/MySQL, Celery SQL transport, ODA File Converter, Xvfb, React 19, TypeScript 6, TanStack Query, Ant Design, pytest, Playwright.

---

## File map

- `Stages/dwg2dxf/src/dwg_converter/engines/oda_converter.py`: DISPLAY-aware ODA execution and batch result timing.
- `Stages/dxf2dwg/src/dxf_converter/engines/oda_converter.py`: matching reverse-conversion behavior.
- `Stages/dwg2dxf/tests/test_converter.py`, `Stages/dxf2dwg/tests/test_converter.py`: explicit DISPLAY and concurrent execution contracts.
- `scripts/run-cad-worker.sh`: owns Xvfb and the Celery worker lifecycle.
- `scripts/lib.sh`, `compose.yaml`, `.env.example`: one configurable concurrency/display contract.
- `backend/app/schemas/job_schema.py`: bulk create/cancel payloads and batch response types.
- `backend/app/api/v1/jobs_api.py`: bulk create, scoped cancellation, and multi-job SSE routes.
- `backend/app/services/job_service.py`: atomic bulk job creation/dispatch and guarded bulk cancellation.
- `backend/app/services/cad_batch_service.py`: direction-neutral batch staging/grouping/execution contracts.
- `backend/app/services/dxf_service.py`, `backend/app/services/dxf2dwg_service.py`: reusable per-file persistence functions shared by single and batch paths.
- `backend/app/workers/tasks_dxf.py`, `backend/app/workers/tasks_dxf2dwg.py`: batch Celery task entrypoints.
- `backend/app/services/job_events.py`: multi-job fingerprint and stream generator.
- `backend/tests/test_cad_batch_jobs.py`, `backend/tests/test_job_events_mysql.py`: backend behavioral coverage.
- `frontend/src/api/jobs.api.ts`: bulk create/cancel clients.
- `frontend/src/hooks/useJobListEvents.ts`: one SSE connection per conversion scope.
- `frontend/src/api/files.api.ts`: upload-only bounded folder pool with progress callbacks.
- `frontend/src/components/ConversionPage.tsx`: unified batch submission and truthful progress UI.
- `frontend/tests/e2e/files-page-buttons.spec.ts`, `frontend/tests/e2e/api-contract.spec.ts`: browser/API contracts.
- `scripts/benchmark_cad_conversion.py`: repeatable real-file benchmark and JSON summary.
- `docs/configuration.md`, `docs/processing-pipelines.md`, `docs/workflow-verification.md`, `docs/api.md`: runtime and evidence documentation.

### Task 1: Make both ODA engines DISPLAY-aware

**Files:**
- Modify: `Stages/dwg2dxf/src/dwg_converter/engines/oda_converter.py`
- Modify: `Stages/dxf2dwg/src/dxf_converter/engines/oda_converter.py`
- Modify: `Stages/dwg2dxf/tests/test_converter.py`
- Modify: `Stages/dxf2dwg/tests/test_converter.py`

- [ ] **Step 1: Write failing DISPLAY selection tests in both Stage suites**

```python
def test_existing_display_disables_per_call_xvfb(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":91")
    conv = OdaConverter(executable=Path("/fake/oda"))
    assert conv.xvfb_run is False


def test_missing_display_keeps_xvfb_fallback(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/xvfb-run" if name == "xvfb-run" else None)
    conv = OdaConverter(executable=Path("/fake/oda"))
    assert conv.xvfb_run is True


def test_explicit_xvfb_setting_wins_over_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":91")
    conv = OdaConverter(executable=Path("/fake/oda"), xvfb_run=True)
    assert conv.xvfb_run is True
```

- [ ] **Step 2: Run focused tests and verify the first test fails**

Run:

```bash
cd Stages/dwg2dxf && uv run pytest -q tests/test_converter.py -k 'display or xvfb'
cd ../dxf2dwg && uv run pytest -q tests/test_converter.py -k 'display or xvfb'
```

Expected: `test_existing_display_disables_per_call_xvfb` fails because current auto mode only checks whether `xvfb-run` exists.

- [ ] **Step 3: Implement identical explicit/auto behavior in each engine**

```python
def _resolve_xvfb(self) -> None:
    if self.xvfb_run is None:
        self.xvfb_run = not bool(os.environ.get("DISPLAY"))
    if self.xvfb_run and shutil.which("xvfb-run") is None:
        raise OdaConvertError(
            "xvfb_run=True 但未找到 xvfb-run。请安装 xorg-server-xvfb，"
            "或显式传 xvfb_run=False（需自备可用 DISPLAY）。"
        )
```

Add `import os` in each engine. Do not change explicit `True` or `False` semantics.

- [ ] **Step 4: Run both Stage suites**

Run:

```bash
cd Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
```

Expected: both suites pass.

- [ ] **Step 5: Commit the Stage change**

```bash
git add Stages/dwg2dxf Stages/dxf2dwg
git commit -m "fix: reuse persistent display for CAD conversion"
```

### Task 2: Give local and Compose workers one persistent Xvfb each

**Files:**
- Create: `scripts/run-cad-worker.sh`
- Modify: `scripts/lib.sh`
- Modify: `scripts/status.sh`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `backend/tests/test_scripts.py`
- Modify: `backend/tests/test_compose.py`

- [ ] **Step 1: Add failing script and Compose contract tests**

```python
def test_cad_worker_wrapper_owns_xvfb_lifecycle():
    text = (ROOT / "scripts/run-cad-worker.sh").read_text()
    assert "Xvfb" in text
    assert "trap cleanup" in text
    assert 'export DISPLAY="$display"' in text
    assert "celery" in text


def test_conversion_worker_concurrency_is_configurable(compose_text):
    assert "${DXF_WORKER_CONCURRENCY:-8}" in compose_text
    assert "${DXF2DWG_WORKER_CONCURRENCY:-8}" in compose_text
```

- [ ] **Step 2: Run the focused backend tests and verify failure**

Run: `cd backend && uv run pytest -q tests/test_scripts.py tests/test_compose.py`

Expected: failure because the wrapper and variables do not exist.

- [ ] **Step 3: Create the lifecycle wrapper**

The script interface is:

```bash
scripts/run-cad-worker.sh <queue> <concurrency> <node-name> <display>
```

It must validate queue/concurrency/display, start `Xvfb "$display" -screen 0 1024x768x24 -nolisten tcp`, wait up to 10 seconds for `/tmp/.X11-unix/X${display#:}`, export DISPLAY, start Celery with `--prefetch-multiplier=1`, forward TERM/INT, and kill/wait for Xvfb in `cleanup`.

- [ ] **Step 4: Wire one source of truth into local and Compose startup**

Load defaults without mutating user `.env`:

```bash
DXF_WORKER_CONCURRENCY="${DXF_WORKER_CONCURRENCY:-8}"
DXF2DWG_WORKER_CONCURRENCY="${DXF2DWG_WORKER_CONCURRENCY:-8}"
DXF_WORKER_DISPLAY="${DXF_WORKER_DISPLAY:-:91}"
DXF2DWG_WORKER_DISPLAY="${DXF2DWG_WORKER_DISPLAY:-:92}"
```

`WORKER_SPECS` must carry queue, concurrency, slug, and optional display; non-CAD workers keep the existing direct Celery path. Compose commands call the wrapper for `dxf` and `dxf2dwg`.

- [ ] **Step 5: Add duplicate-topology status warning**

When both a `*-local@` process and the corresponding Compose service are running, `scripts/status.sh` reports a warning naming the queue. It must not stop either topology automatically.

- [ ] **Step 6: Run script, Compose, shell syntax, and config checks**

Run:

```bash
bash -n scripts/run-cad-worker.sh scripts/lib.sh scripts/status.sh
cd backend && uv run pytest -q tests/test_scripts.py tests/test_compose.py
cd .. && docker compose config --quiet
```

Expected: all pass.

- [ ] **Step 7: Commit worker lifecycle changes**

```bash
git add scripts/run-cad-worker.sh scripts/lib.sh scripts/status.sh compose.yaml .env.example backend/tests/test_scripts.py backend/tests/test_compose.py
git commit -m "perf: run CAD workers on persistent displays"
```

### Task 3: Add transactional bulk job creation and scoped cancellation

**Files:**
- Modify: `backend/app/schemas/job_schema.py`
- Modify: `backend/app/services/job_service.py`
- Modify: `backend/app/api/v1/jobs_api.py`
- Create: `backend/tests/test_cad_batch_jobs.py`

- [ ] **Step 1: Write failing API tests**

Cover these exact cases:

```python
def test_create_conversion_batch_returns_one_job_per_file(
    client, admin_headers, two_dwg_file_ids, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        "app.api.v1.jobs_api.dispatch_committed_conversion_batch",
        lambda **kwargs: dispatched.append(kwargs),
    )
    response = client.post("/api/v1/jobs/batches", headers=admin_headers, json={
        "task_type": "convert_dwg_to_dxf",
        "file_ids": two_dwg_file_ids,
        "precision_level": "normal",
    })
    assert response.status_code == 202
    jobs = response.json()["data"]["jobs"]
    assert [j["params_json"]["file_id"] for j in jobs] == two_dwg_file_ids
    assert len(dispatched) == 1
    assert dispatched[0]["jobs"] == [(job["id"], 1) for job in jobs]


def test_create_conversion_batch_rejects_wrong_extension_without_partial_rows(
    client, admin_headers, dwg_file_id, dxf_file_id
):
    before = client.get("/api/v1/jobs", headers=admin_headers).json()["pagination"]["total"]
    response = client.post("/api/v1/jobs/batches", headers=admin_headers, json={
        "task_type": "convert_dwg_to_dxf",
        "file_ids": [dwg_file_id, dxf_file_id],
    })
    assert response.status_code == 422
    after = client.get("/api/v1/jobs", headers=admin_headers).json()["pagination"]["total"]
    assert after == before


def test_scoped_cancel_only_changes_requested_jobs(client, admin_headers, three_queued_jobs):
    requested = [three_queued_jobs[0], three_queued_jobs[2]]
    response = client.post(
        "/api/v1/jobs/cancellation-requests",
        headers=admin_headers,
        json={"job_ids": requested},
    )
    assert response.status_code == 202
    states = {
        job_id: client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers).json()["data"]["status"]
        for job_id in three_queued_jobs
    }
    assert states == {
        three_queued_jobs[0]: "cancelled",
        three_queued_jobs[1]: "queued",
        three_queued_jobs[2]: "cancelled",
    }
```

The same module creates a second user with no project role and asserts a 403 leaves every requested Job unchanged. The dispatch assertion above is the one-message contract; do not add a second mock-only test for the same behavior.

- [ ] **Step 2: Run the new test module and verify 404/schema failures**

Run: `cd backend && uv run pytest -q tests/test_cad_batch_jobs.py`

- [ ] **Step 3: Add explicit schemas**

```python
class ConversionBatchCreate(BaseModel):
    task_type: Literal["convert_dwg_to_dxf", "convert_dxf_to_dwg"]
    file_ids: list[int] = Field(min_length=1, max_length=200)
    precision_level: str = "normal"


class JobBulkCancellation(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=200)
```

Normalize duplicate IDs while retaining first-seen order in the service, not in the schema.

- [ ] **Step 4: Implement atomic creation and post-commit dispatch**

`create_conversion_jobs` validates every StoredFile before adding any Job. It returns jobs in input order. The route commits once, then calls:

```python
dispatch_committed_conversion_batch(
    task_type=payload.task_type,
    jobs=[(job.id, job.attempt) for job in jobs],
)
```

Single-job dispatch remains unchanged.

- [ ] **Step 5: Implement all-or-nothing authorization and guarded cancellation**

Resolve every Job and call `require_job_write_access` before making the first transition. Then call the existing `cancel_job` transition for each requested active Job and commit once.

- [ ] **Step 6: Run focused and regression tests**

Run:

```bash
cd backend
uv run pytest -q tests/test_cad_batch_jobs.py tests/test_job_access.py tests/test_job_lifecycle.py tests/test_api_regressions.py
```

- [ ] **Step 7: Commit the bulk API**

```bash
git add backend/app/schemas/job_schema.py backend/app/services/job_service.py backend/app/api/v1/jobs_api.py backend/tests/test_cad_batch_jobs.py
git commit -m "feat: create and cancel conversion jobs in batches"
```

### Task 4: Execute DWG→DXF batches with per-file state

**Files:**
- Create: `backend/app/services/cad_batch_service.py`
- Modify: `backend/app/services/dxf_service.py`
- Modify: `backend/app/workers/tasks_dxf.py`
- Modify: `backend/app/services/job_service.py`
- Modify: `backend/tests/test_cad_batch_jobs.py`
- Modify: `backend/tests/test_dxf_pipeline.py`

- [ ] **Step 1: Add failing service tests for version grouping and partial failure**

```python
def test_dwg_batch_groups_sources_by_detected_output_version(
    seeded_dwg_batch_jobs, fake_dwg_batch_result
):
    with patch("dwg_converter.convert_directory") as convert:
        convert.return_value = fake_dwg_batch_result
        job_2000, job_2018 = seeded_dwg_batch_jobs
        run_dwg_to_dxf_batch([(job_2000, 1), (job_2018, 1)])
    assert {call.kwargs["version"] for call in convert.call_args_list} == {"ACAD2000", "ACAD2018"}


def test_dwg_batch_partial_oda_failure_only_fails_matching_job(
    client, admin_headers, seeded_dwg_batch_jobs, partial_dwg_batch_result
):
    with patch("dwg_converter.convert_directory", return_value=partial_dwg_batch_result):
        run_dwg_to_dxf_batch([(job_id, 1) for job_id in seeded_dwg_batch_jobs])
    statuses = [
        client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers).json()["data"]["status"]
        for job_id in seeded_dwg_batch_jobs
    ]
    assert statuses == ["succeeded", "failed"]


def test_dwg_batch_ignores_cancelled_attempt_before_persist(
    db, seeded_running_dwg_job, successful_dwg_convert_result, monkeypatch
):
    monkeypatch.setattr(
        "app.services.dxf_service.complete_job_attempt",
        lambda *args, **kwargs: None,
    )
    persisted = persist_dxf_conversion_result(
        db,
        job_id=seeded_running_dwg_job.id,
        attempt=seeded_running_dwg_job.attempt,
        source_file_id=seeded_running_dwg_job.params_json["file_id"],
        source_path=successful_dwg_convert_result.source,
        result=successful_dwg_convert_result,
        worker_name="test-worker",
    )
    assert persisted is False
```

The persistence test additionally fetches `/steps` and `/results`, asserts all three canonical step names exist, asserts one result points at a `.dxf` StoredFile, and asserts the Job reached progress 100.

- [ ] **Step 2: Run focused tests and verify missing batch service failure**

Run: `cd backend && uv run pytest -q tests/test_cad_batch_jobs.py tests/test_dxf_pipeline.py`

- [ ] **Step 3: Extract reusable per-file persistence from `run_dxf_conversion`**

Create a function with an explicit guarded contract:

```python
def persist_dxf_conversion_result(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    source_file_id: int,
    source_path: Path,
    result: ConvertResult,
    worker_name: str,
) -> bool:
    """Persist and complete only if job_id/attempt is still running."""
```

The existing single-file flow calls this function so batch and single behavior cannot drift.

- [ ] **Step 4: Implement batch staging and grouping**

Use one batch temporary root, one unique source subdirectory per ODA version, and one output directory per version. Source filenames must be collision-safe; retain a mapping from staged path to `(job_id, attempt, source_file_id, original_name)`.

- [ ] **Step 5: Add and route the batch Celery task**

```python
@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf_batch", bind=True)
def convert_dwg_to_dxf_batch_task(self, jobs: list[list[int]]) -> dict[str, int | str]:
    pairs = [(int(job_id), int(attempt)) for job_id, attempt in jobs]
    return run_dwg_to_dxf_batch(pairs, worker_name=self.request.hostname or "celery_dxf")
```

`dispatch_committed_conversion_batch` sends this task only once.

- [ ] **Step 6: Run DWG focused tests and Stage tests**

Run:

```bash
cd backend && uv run pytest -q tests/test_cad_batch_jobs.py tests/test_dxf_pipeline.py tests/test_job_attempts.py
cd ../Stages/dwg2dxf && uv run pytest -q
```

- [ ] **Step 7: Commit DWG batch execution**

```bash
git add backend/app/services/cad_batch_service.py backend/app/services/dxf_service.py backend/app/workers/tasks_dxf.py backend/app/services/job_service.py backend/tests
git commit -m "perf: batch DWG to DXF conversion by version"
```

### Task 5: Execute DXF→DWG batches with per-file state

**Files:**
- Modify: `backend/app/services/cad_batch_service.py`
- Modify: `backend/app/services/dxf2dwg_service.py`
- Modify: `backend/app/workers/tasks_dxf2dwg.py`
- Modify: `backend/app/services/job_service.py`
- Modify: `backend/tests/test_cad_batch_jobs.py`
- Modify: `backend/tests/test_dxf2dwg_pipeline.py`

- [ ] **Step 1: Add failing reverse version-group tests**

```python
def test_dxf_batch_groups_by_analysis_result_then_header_then_default(
    three_seeded_dxf_jobs, fake_dxf_batch_result
):
    with patch("dxf_converter.convert_directory", return_value=fake_dxf_batch_result) as convert:
        run_dxf_to_dwg_batch([(job_id, 1) for job_id in three_seeded_dxf_jobs])
    assert [call.kwargs["version"] for call in convert.call_args_list] == [
        "ACAD2000", "ACAD2010", settings.oda_converter_version,
    ]


def test_dxf_batch_partial_failure_only_fails_matching_job(
    client, admin_headers, two_seeded_dxf_jobs, partial_dxf_batch_result
):
    with patch("dxf_converter.convert_directory", return_value=partial_dxf_batch_result):
        run_dxf_to_dwg_batch([(job_id, 1) for job_id in two_seeded_dxf_jobs])
    jobs = [
        client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers).json()["data"]
        for job_id in two_seeded_dxf_jobs
    ]
    assert [job["status"] for job in jobs] == ["succeeded", "failed"]
    assert jobs[0]["progress"] == 100
```

The cancellation test changes the active attempt immediately before persistence and asserts no StoredFile or AnalysisResult is created. The tool-version test reads the successful AnalysisResult and asserts its `tool_version` equals the version used for that file's ODA group.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd backend && uv run pytest -q tests/test_cad_batch_jobs.py tests/test_dxf2dwg_pipeline.py`

- [ ] **Step 3: Extract reusable reverse persistence**

```python
def persist_dwg_conversion_result(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    source_file_id: int,
    source_path: Path,
    output_version: str,
    result: ConvertResult,
    worker_name: str,
) -> bool:
    """Persist DWG and finish only the active attempt."""
```

Single and batch paths both call it.

- [ ] **Step 4: Implement version-grouped reverse execution and task dispatch**

Use `_resolve_dwg_output_version` before grouping. Unknown AnalysisResult tool versions must fall back to `$ACADVER`, matching existing single-job behavior.

- [ ] **Step 5: Run reverse and shared regression tests**

Run:

```bash
cd backend && uv run pytest -q tests/test_cad_batch_jobs.py tests/test_dxf2dwg_pipeline.py tests/test_job_attempts.py
cd ../Stages/dxf2dwg && uv run pytest -q
```

- [ ] **Step 6: Commit reverse batch execution**

```bash
git add backend/app/services/cad_batch_service.py backend/app/services/dxf2dwg_service.py backend/app/workers/tasks_dxf2dwg.py backend/app/services/job_service.py backend/tests
git commit -m "perf: batch DXF to DWG conversion by version"
```

### Task 6: Add one authoritative SSE stream for a conversion scope

**Files:**
- Modify: `backend/app/services/job_events.py`
- Modify: `backend/app/api/v1/jobs_api.py`
- Modify: `backend/tests/test_job_events_mysql.py`
- Modify: `backend/tests/test_cad_batch_jobs.py`

- [ ] **Step 1: Add failing generator and endpoint tests**

```python
def test_job_list_event_stream_emits_initial_and_changed_snapshots(session_factory, two_jobs):
    stream = jobs_event_stream(session_factory, [job.id for job in two_jobs], poll_interval=0)
    initial = next(stream)
    assert initial is not None
    assert [item["job_id"] for item in initial] == [job.id for job in two_jobs]
    with session_factory() as db:
        db_job = db.get(Job, two_jobs[0].id)
        db_job.progress = 70
        db.commit()
    changed = next(item for item in stream if item is not None)
    assert changed[0]["progress"] == 70


def test_job_list_event_stream_closes_when_all_terminal(session_factory, succeeded_jobs):
    frames = list(jobs_event_stream(
        session_factory,
        [job.id for job in succeeded_jobs],
        poll_interval=0,
        max_duration=1,
    ))
    assert len(frames) == 1
    assert all(item["status"] == "succeeded" for item in frames[0])
```

The endpoint module sends 201 comma-separated IDs and asserts 422. Its authorization case requests one readable and one unreadable Job as a project member and asserts 403 rather than silently leaking or dropping the unreadable row.

- [ ] **Step 2: Run tests and verify missing stream failure**

Run: `cd backend && uv run pytest -q tests/test_job_events_mysql.py tests/test_cad_batch_jobs.py`

- [ ] **Step 3: Implement list fingerprinting and short-session polling**

```python
def jobs_event_stream(
    session_factory: Callable[[], Session],
    job_ids: Sequence[int],
    *,
    poll_interval: float = 0.5,
    max_duration: float = 600.0,
) -> Iterator[list[dict[str, Any]] | None]:
    """Yield ordered snapshots when any selected Job changes."""
```

Order snapshots by requested ID. The fingerprint includes `id`, `status`, `attempt`, `progress`, `error_code`, `error_message`, and `progress_data`.

- [ ] **Step 4: Add `/events/stream` before dynamic `/{job_id}` routes**

The route validates task type, parses 1–200 file IDs, resolves matching readable jobs, sends an initial JSON object `{ "type": "snapshot", "jobs": job_payloads }`, then changed frames. Use the same no-buffer/no-cache headers as the existing single-job stream.

- [ ] **Step 5: Run event/API tests**

Run: `cd backend && uv run pytest -q tests/test_job_events_mysql.py tests/test_cad_batch_jobs.py tests/test_api_regressions.py`

- [ ] **Step 6: Commit aggregate SSE**

```bash
git add backend/app/services/job_events.py backend/app/api/v1/jobs_api.py backend/tests
git commit -m "feat: stream conversion job progress in one connection"
```

### Task 7: Unify frontend bulk submission and realtime progress

**Files:**
- Modify: `frontend/src/api/jobs.api.ts`
- Modify: `frontend/src/api/files.api.ts`
- Create: `frontend/src/hooks/useJobListEvents.ts`
- Modify: `frontend/src/components/ConversionPage.tsx`
- Modify: `frontend/tests/e2e/api-contract.spec.ts`
- Modify: `frontend/tests/e2e/files-page-buttons.spec.ts`

- [ ] **Step 1: Add failing API and browser contracts**

Verify that folder/ZIP/resume call `POST /jobs/batches` once, pause calls scoped `/jobs/cancellation-requests`, the page creates one EventSource URL containing task type and file IDs, and the summary distinguishes successful and terminal counts.

- [ ] **Step 2: Run build/Playwright and confirm new assertions fail**

Run:

```bash
cd frontend
npm run build
npx playwright test tests/e2e/api-contract.spec.ts tests/e2e/files-page-buttons.spec.ts
```

- [ ] **Step 3: Add typed bulk API clients**

```typescript
export async function createConversionBatch(taskType: string, fileIds: number[]): Promise<Job[]>;
export async function cancelJobs(jobIds: number[]): Promise<{ cancelled_count: number }>;
```

- [ ] **Step 4: Make folder upload return StoredFiles and progress**

```typescript
export interface FolderUploadProgress { total: number; completed: number; succeeded: number; failed: number; }

export async function uploadFolder(
  files: File[],
  batchName: string,
  opts: { fileExt: string; concurrency?: number; onProgress?: (p: FolderUploadProgress) => void },
): Promise<{ files: StoredFile[]; total: number; failed: number }>;
```

Default upload concurrency is 6 and is independent of ODA worker concurrency.

- [ ] **Step 5: Implement `useJobListEvents`**

The hook accepts `taskType`, `fileIds`, and the exact TanStack query key. It merges each snapshot into the paginated `PageEnvelope<Job>` cache and invalidates files/batches once when all visible jobs become terminal.

- [ ] **Step 6: Refactor `ConversionPage` around one scope**

Fetch all source file IDs for the selected batch when computing aggregate progress; keep the paginated table for rendering. Folder/ZIP/resume submit one bulk request. Pause only sends active job IDs from that scope. Replace “当前页转换进度” with the explicit current batch/filter label.

Compute:

```typescript
const terminal = succeeded + failed + cancelled;
const completionPercent = totalJobs ? Math.round(terminal / totalJobs * 100) : 0;
const conversionPercent = activeJobs.length
  ? Math.round(jobs.reduce((sum, job) => sum + job.progress, 0) / jobs.length)
  : completionPercent;
```

Show both successful count and processed count so failure never looks like success.

- [ ] **Step 7: Build and run focused Playwright tests**

Run:

```bash
cd frontend
npm run build
npx playwright test tests/e2e/api-contract.spec.ts tests/e2e/files-page-buttons.spec.ts
```

- [ ] **Step 8: Commit frontend integration**

```bash
git add frontend/src frontend/tests
git commit -m "feat: show realtime batch conversion progress"
```

### Task 8: Add a repeatable real-file benchmark

**Files:**
- Create: `scripts/benchmark_cad_conversion.py`
- Modify: `backend/tests/test_scripts.py`

- [ ] **Step 1: Add failing CLI contract test**

Test `--help`, reject a missing input directory, and unit-test concurrency parsing without running ODA.

- [ ] **Step 2: Implement the benchmark CLI**

Required arguments and output:

```text
--input-dir PATH
--limit N
--concurrency 1,2,4,8
--direction dwg2dxf|dxf2dwg|roundtrip
--json-output PATH (optional)
```

Each result records direction, mode, concurrency, total, succeeded, failed, elapsed seconds, files/second, input bytes, and error filenames. Temporary outputs live outside the source directory and are deleted unless `--keep-output` is passed.

- [ ] **Step 3: Run CLI tests and a 16-file smoke benchmark**

Run:

```bash
cd backend && uv run pytest -q tests/test_scripts.py
uv run python ../scripts/benchmark_cad_conversion.py \
  --input-dir '/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图' \
  --limit 16 --concurrency 8 --direction roundtrip
```

Expected: 16/16 succeed in both directions.

- [ ] **Step 4: Commit the benchmark**

```bash
git add scripts/benchmark_cad_conversion.py backend/tests/test_scripts.py
git commit -m "test: benchmark bidirectional CAD conversion"
```

### Task 9: Document, verify, and run the full real-data acceptance audit

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/processing-pipelines.md`
- Modify: `docs/workflow-verification.md`
- Regenerate: `docs/api.md`

- [ ] **Step 1: Document the concurrency, display, batch, and progress contracts**

Record defaults, tuning guidance, mixed-version grouping, scope-correct cancel semantics, and the fact that a healthy worker does not prove ODA conversion success.

- [ ] **Step 2: Regenerate and check API documentation**

Run:

```bash
make docs-generate
make docs-check
```

- [ ] **Step 3: Run static and automated regression gates**

Run:

```bash
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ../Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../../frontend && npm run build
cd .. && docker compose config --quiet
```

- [ ] **Step 4: Run full-folder real conversion acceptance**

Use the benchmark CLI on every `.dwg` in the specified directory, including the combined drawing. Run DWG→DXF and then DXF→DWG with the selected production settings. Require zero failed files and validate output magic/header plus file counts.

- [ ] **Step 5: Run live backend/frontend acceptance**

With exactly one worker topology active, upload a folder or ZIP, verify one bulk API request, observe queued/running/per-file progress without manual refresh, pause the current scope, resume it, and download results from both directions. Capture the measured end-to-end elapsed time and counts in `docs/workflow-verification.md`.

- [ ] **Step 6: Inspect the final diff and worktree**

Run:

```bash
git diff --check
git status --short
git log --oneline -12
```

Confirm the unrelated `scripts/forward-to-win11.sh` remains untouched.

- [ ] **Step 7: Commit verification documentation**

```bash
git add docs/configuration.md docs/processing-pipelines.md docs/workflow-verification.md docs/api.md
git commit -m "docs: record CAD conversion throughput verification"
```
