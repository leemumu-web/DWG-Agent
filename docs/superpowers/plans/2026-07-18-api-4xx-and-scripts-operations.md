# API 4xx and Scripts Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the observed folder-delete 405 and ZIP-download 409, then provide repeatable scripts that detect code/runtime drift and classify recent HTTP failures.

**Architecture:** ZIP preview and ZIP construction share one backend availability resolver so the UI and final consistency gate cannot drift. Local lifecycle scripts retain safe explicit restarts while status and doctor expose stale runtime state. HTTP diagnostics aggregate normalized, redacted access-log data and keep 499 separate from application 4xx.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy, React 19, TypeScript, Ant Design, Playwright, Bash, pytest, Nginx access logs.

---

### Task 1: ZIP availability resolver and preview API

**Files:**
- Modify: `backend/app/schemas/file_schema.py`
- Modify: `backend/app/services/file_service.py`
- Modify: `backend/app/api/v1/files_api.py`
- Modify: `backend/tests/test_file_service.py`
- Modify: `backend/tests/test_file_transfer_service.py`

- [ ] **Step 1: Write failing service tests**

Add tests that create two source files where only one has a converted result, then assert a new `preview_zip_availability()` returns complete source coverage, partial target coverage, accurate missing counts and bounded `missing_file_ids`. Add a test proving `build_zip_to_path()` rejects the same missing `(file_id, format)` pair.

```python
preview = preview_zip_availability(db, [source_a.id, source_b.id], ["dwg", "dxf"])
assert preview.file_count == 2
assert preview.by_format["dwg"].complete is True
assert preview.by_format["dxf"].missing_count == 1
assert preview.by_format["dxf"].missing_file_ids == [source_b.id]
```

- [ ] **Step 2: Run the service tests and confirm red**

Run: `cd backend && uv run pytest -q tests/test_file_service.py -k 'zip and preview'`

Expected: collection or assertion failure because the preview models and resolver do not exist.

- [ ] **Step 3: Add preview schemas and shared resolver**

Add Pydantic response models with these stable fields:

```python
class ZipFormatAvailability(BaseModel):
    format: str
    available_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    missing_file_ids: list[int]
    complete: bool

class ZipAvailabilityPreview(BaseModel):
    file_count: int = Field(ge=0)
    formats: list[ZipFormatAvailability]
    can_download: bool
```

In `file_service.py`, add a private resolver that deduplicates IDs/formats, validates supported formats, loads all non-deleted sources and the result map, and returns selected files plus missing pairs. Both `preview_zip_availability()` and `build_zip_to_path()` must call it. Cap missing ID samples at 20 but retain `missing_count`.

- [ ] **Step 4: Add the preview route and route tests**

Add `POST /download-zip/preview` before the dynamic `/batches/{batch_name}/...` and final download handlers. Reuse `_require_file_read_access()` for every resolved source and return `ok(preview, request.state.request_id)`.

Test success, partial format coverage, missing source 404, unauthorized access, invalid format 422 and duplicate IDs through the FastAPI test client.

- [ ] **Step 5: Run backend focused gates**

Run: `cd backend && uv run pytest -q tests/test_file_service.py tests/test_file_transfer_service.py tests/test_adversarial_files.py`

Expected: all tests pass and existing strict ZIP/storage inconsistency tests remain green.

- [ ] **Step 6: Commit backend preview**

```bash
git add backend/app/schemas/file_schema.py backend/app/services/file_service.py backend/app/api/v1/files_api.py backend/tests/test_file_service.py backend/tests/test_file_transfer_service.py
git commit -m "feat: preview ZIP export availability"
```

### Task 2: Recoverable ZIP modal

**Files:**
- Modify: `frontend/src/api/files.api.ts`
- Modify: `frontend/src/components/ZipDownloadModal.tsx`
- Modify: `frontend/src/components/ConversionPage.tsx`
- Modify: `frontend/tests/e2e/files-page-buttons.spec.ts`
- Modify: `frontend/tests/e2e/api-contract.spec.ts`

- [ ] **Step 1: Write failing Playwright tests**

Mock `/api/v1/files/download-zip/preview` for these cases: target format partially missing, both formats complete, preview failure and formal download 409. Assert the source format is selected, incomplete target is disabled with `可用 1 / 共 2`, and failures keep the dialog open with the folder name unchanged.

```ts
await expect(dialog.getByRole('checkbox', { name: /包含 DXF 文件/ })).toBeDisabled();
await expect(dialog.getByText(/DXF.*可用 1 \/ 共 2/)).toBeVisible();
await dialog.getByPlaceholder(/输入文件夹名称/).fill('保留名称');
await dialog.getByRole('button', { name: /开始下载/ }).click();
await expect(dialog).toBeVisible();
await expect(dialog.getByPlaceholder(/输入文件夹名称/)).toHaveValue('保留名称');
```

- [ ] **Step 2: Run the focused UI tests and confirm red**

Run: `cd frontend && npx playwright test tests/e2e/files-page-buttons.spec.ts -g 'zip modal|zip download'`

Expected: preview requests and availability UI are absent.

- [ ] **Step 3: Add typed preview client**

Define `ZipFormatAvailability`, `ZipAvailabilityPreview`, and:

```ts
export async function previewZip(
  fileIds: number[], formats: Array<'dwg' | 'dxf'>, folderName: string,
): Promise<ZipAvailabilityPreview>
```

Call `/api/v1/files/download-zip/preview` and return `res.data.data`.

- [ ] **Step 4: Implement modal state and recovery**

Add `sourceFormat: 'dwg' | 'dxf'` to the modal props. On each open/file-ID change, preview both formats using a cancellable effect guard. Reset selections to the source format, enable the target only when complete, and render loading/error/count states. Do not call `onClose()` in the download catch path; refresh preview instead.

- [ ] **Step 5: Pass the source format from both conversion flows**

In `ConversionPage`, derive `const sourceFormat = p.fileExt.slice(1) as 'dwg' | 'dxf'` and pass it to both row-selection and multi-folder ZIP modals.

- [ ] **Step 6: Run frontend gates and commit**

Run: `cd frontend && npm run build && npx playwright test tests/e2e/files-page-buttons.spec.ts -g 'zip|multi-folder package'`

Expected: TypeScript build and focused browser tests pass.

```bash
git add frontend/src/api/files.api.ts frontend/src/components/ZipDownloadModal.tsx frontend/src/components/ConversionPage.tsx frontend/tests/e2e/files-page-buttons.spec.ts frontend/tests/e2e/api-contract.spec.ts
git commit -m "fix: prevent incomplete ZIP submissions"
```

### Task 3: Detect and recover stale local runtime

**Files:**
- Modify: `scripts/lib.sh`
- Modify: `scripts/start-all.sh`
- Modify: `scripts/status.sh`
- Modify: `backend/tests/test_scripts.py`

- [ ] **Step 1: Add failing static and executable script tests**

Test that `start-all.sh` accepts `--restart-backend`, calls an owned-backend restart helper, and detects stale frontend output. Use temporary files and a helper function to assert a code file newer than a process-start epoch reports drift while an older file does not.

```python
assert "--restart-backend" in _read("scripts/start-all.sh")
assert "backend_runtime_stale" in _read("scripts/lib.sh")
assert "frontend_dist_stale" in _read("scripts/lib.sh")
assert "运行代码已过期" in _read("scripts/status.sh")
```

- [ ] **Step 2: Confirm script tests fail**

Run: `cd backend && uv run pytest -q tests/test_scripts.py -k 'stale or restart_backend or frontend_dist'`

Expected: missing helper/assertion failures.

- [ ] **Step 3: Add safe drift helpers**

In `lib.sh`, implement helpers that identify only the project-owned Uvicorn command/cwd, read `/proc/<pid>/stat` or `ps -o lstart` for process time, and compare it with the newest mtime under `backend/app`, `backend/pyproject.toml`, `backend/uv.lock`. Add a separate newest-mtime comparison for `frontend/src`, `package.json`, lockfile and Vite/TypeScript configs versus `frontend/dist/index.html`.

- [ ] **Step 4: Add explicit backend restart and stale build behavior**

Parse only known flags and reject unknown ones. `--restart-backend` must terminate the owned PID, wait for 8010 to free, then start and health-check the backend. Without the flag, stale backend code produces a warning and exact command; stale frontend dist triggers a rebuild.

- [ ] **Step 5: Surface drift in status and verify**

`status.sh` reports process PID/start, latest code mtime, frontend dist freshness and an overall nonzero status when runtime drift exists.

Run: `bash -n scripts/*.sh && cd backend && uv run pytest -q tests/test_scripts.py tests/test_forward_to_win11_script.py`

- [ ] **Step 6: Commit lifecycle hardening**

```bash
git add scripts/lib.sh scripts/start-all.sh scripts/status.sh backend/tests/test_scripts.py
git commit -m "fix: detect stale local application runtime"
```

### Task 4: HTTP doctor and unified verification

**Files:**
- Create: `scripts/doctor.sh`
- Create: `scripts/verify.sh`
- Modify: `backend/tests/test_scripts.py`

- [ ] **Step 1: Write failing doctor fixture tests**

Create a temporary access log containing 405, 409, 422, 499 and a signed download URL. Execute doctor with `NGINX_ACCESS_LOG` and `DOCTOR_SINCE_MINUTES=0`, then assert grouped counts, separate 499 labeling, request-ID sample, and absence of `signature=` values.

```python
result = subprocess.run(
    ["bash", str(PROJECT_ROOT / "scripts/doctor.sh"), "--log-only"],
    env={**os.environ, "NGINX_ACCESS_LOG": str(access_log), "DOCTOR_SINCE_MINUTES": "0"},
    text=True, capture_output=True, check=False,
)
assert "405" in result.stdout and "409" in result.stdout
assert "客户端断开 (499)" in result.stdout
assert "secret-signature" not in result.stdout
```

- [ ] **Step 2: Confirm doctor tests fail**

Run: `cd backend && uv run pytest -q tests/test_scripts.py -k 'doctor or verify_entrypoint'`

Expected: scripts do not exist.

- [ ] **Step 3: Implement bounded, redacted doctor output**

Use Bash orchestration plus awk for log aggregation. Normalize query strings away from endpoint grouping, retain only a bounded request-ID sample, classify 499 separately, recognize fixed test IDs without trusting User-Agent alone, and return nonzero for service 5xx, route 405 or runtime drift. `--log-only` must avoid network/service probes for deterministic tests.

- [ ] **Step 4: Implement quick/full verification**

`verify.sh quick` runs shell syntax, focused script/backend tests, docs checks and frontend build. `verify.sh full` adds complete backend, infrastructure, Compose and browser gates. Preserve each command status using a runner function; `--allow-blocked` only converts recognized external-dependency exits to blocked.

- [ ] **Step 5: Run and commit script gates**

Run: `bash -n scripts/*.sh && cd backend && uv run pytest -q tests/test_scripts.py tests/test_forward_to_win11_script.py tests/test_compose.py`

```bash
git add scripts/doctor.sh scripts/verify.sh backend/tests/test_scripts.py
git commit -m "feat: add production diagnostics and verification"
```

### Task 5: API and operations documentation

**Files:**
- Create: `scripts/README.md`
- Modify: `scripts/generate_api_docs.py`
- Modify: `docs/api.md` (generated)
- Modify: `docs/operations.md`
- Modify: `docs/README.md`
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] **Step 1: Add documentation contract assertions**

Assert the generated API source contains `/api/v1/files/download-zip/preview`, `missing_count`, `FILE_EXPORT_FORMAT_UNAVAILABLE`, and `STORAGE_INCONSISTENT`. Assert scripts README lists every operational entrypoint.

- [ ] **Step 2: Update generated API source and operations guidance**

Document request/response examples, permission errors, metadata-only preview, preflight/download race, strict 409 behavior, 405 runtime drift and separate 499 semantics. Add a scripts command matrix with normal exit codes and examples for quick/full verification.

- [ ] **Step 3: Regenerate and validate docs**

Run: `make docs-generate && make docs-check`

Expected: generated files are current, links valid and no stale API snapshot exists.

- [ ] **Step 4: Commit documentation**

```bash
git add scripts/README.md scripts/generate_api_docs.py docs/api.md docs/operations.md docs/README.md backend/tests/test_frontend_contract.py
git commit -m "docs: publish 4xx operations runbook"
```

### Task 6: Live recovery and full verification

**Files:**
- No source changes expected; only tracked verification artifacts if existing repository conventions require refresh.

- [ ] **Step 1: Run quick and focused gates**

Run: `bash scripts/verify.sh quick`

Expected: all internal gates pass.

- [ ] **Step 2: Restart the stale backend safely**

Run: `bash scripts/start-all.sh --restart-backend`

Expected: the owned old Uvicorn exits, port 8010 returns, `/health/ready` reports ok and Nginx remains available.

- [ ] **Step 3: Verify route capability without deleting data**

Use the authenticated API contract test or OpenAPI route inspection from the live app to prove `POST /api/v1/files/batches/bulk-delete` exists. Do not submit real batch names.

- [ ] **Step 4: Run full gates**

Run: `bash scripts/verify.sh full --allow-blocked`

Expected: code/test/doc gates pass; only explicitly unavailable Windows/sudo/external checks may be marked blocked.

- [ ] **Step 5: Inspect fresh logs**

Run: `bash scripts/doctor.sh`

Expected: historical 405/409 are reported with timestamps, runtime drift is clear, no secrets are printed and no new 5xx appears during verification.

- [ ] **Step 6: Final repository audit**

Run: `git status --short && git diff --check`

Expected: only the user's pre-existing `Stages/dwg2dxf/convert/output_dxf/.gitkeep` deletion remains unstaged; all implementation commits are present.

