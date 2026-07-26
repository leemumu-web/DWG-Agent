# Container Storage Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and execute this plan inline. The project owner explicitly prohibits subagents.

**Goal:** Make the Docker Compose production path fail-closed and observable across MySQL and MinIO, then provide a safe full-Workflow backup-before-purge path for terminal production data.

**Architecture:** Keep readiness side-effect-free and add a separate real transaction release gate. Expose storage capacity as a backend contract with independent connectivity and capacity states. Model whole-Workflow retention separately from the existing four-category batch export, stream the immutable backup manifest directly to the client, and run physical purge asynchronously on the maintenance queue only after the server records a complete download.

**Tech Stack:** Docker Compose, Bash, FastAPI, SQLAlchemy/Alembic, Celery, MySQL, MinIO, React/TypeScript, pytest, Vitest, Playwright.

---

## Task 1: Bound container logs and add a real storage release gate

**Files:**
- Modify: `backend/tests/infrastructure/test_compose.py`
- Modify: `backend/tests/infrastructure/test_scripts.py`
- Modify: `compose.yaml`
- Modify: `scripts/lib/compose.sh`
- Modify: `scripts/README.md`
- Modify: `docs/guides/deployment.md`

- [ ] Add failing static-contract tests requiring 20 MiB × 5 `json-file` rotation on every long-lived service, internal-only MinIO metrics configuration, and a `verify-storage` command.
- [ ] Add failing script behavior tests proving `verify-storage` validates the environment, requires a healthy backend container, invokes only `scripts/storage/verify_transactions.py`, propagates a probe failure, and never prints secrets.
- [ ] Run the focused tests and confirm they fail for the missing contracts.
- [ ] Add reusable Compose logging and MinIO metrics environment anchors without publishing MySQL or MinIO ports.
- [ ] Implement `compose_verify_storage`, document its release-gate role, and keep `/health/ready` side-effect-free.
- [ ] Run focused infrastructure tests and `bash scripts/docker.sh check`.

## Task 2: Make Local and MinIO capacity a stable backend contract

**Files:**
- Modify: `backend/tests/platform/test_storage.py`
- Modify: `backend/tests/operations/test_data_catalog.py`
- Modify: `backend/app/platform/storage/base.py`
- Modify: `backend/app/platform/storage/local.py`
- Modify: `backend/app/platform/storage/minio.py`
- Modify: `backend/app/platform/config/settings.py`
- Modify: `backend/app/modules/operations/data_catalog/infrastructure.py`
- Modify: `.env.docker.example`
- Modify: `.env.example`
- Modify: `frontend/src/features/operations/api/system.ts`

- [ ] Add failing tests for Local capacity, valid MinIO Prometheus metrics, missing or malformed metrics, request failure, and exact 80/90 percent threshold boundaries.
- [ ] Add an immutable `StorageCapacity` value object; capacity failures must return `unknown` independently from object connectivity.
- [ ] Add configured MinIO metrics retrieval with bounded timeout and strict numeric parsing; never convert unknown capacity to zero.
- [ ] Extend infrastructure responses with total, used, available, percent, state, reason, and checked time while preserving current fields.
- [ ] Run focused backend tests, type checks, and schema snapshots.

## Task 3: Persist immutable whole-Workflow retention exports

**Files:**
- Create: `backend/app/modules/workflows/models/retention.py`
- Modify: `backend/app/modules/workflows/models/__init__.py`
- Modify: `backend/app/platform/database/models.py`
- Create: `backend/alembic/versions/<revision>_add_workflow_retention_exports.py`
- Create: `backend/tests/workflows/test_workflow_retention_models.py`
- Modify: `backend/tests/infrastructure/test_migrations.py`

- [ ] Add failing model and migration tests for export UID uniqueness, valid states, manifest digest, counters, capability expiry, task/transfer identifiers, purge result, timestamps, and indexed Workflow lookup.
- [ ] Implement the independent `workflow_retention_exports` model and the forward/downgrade migration without altering `workflow_batch_exports`.
- [ ] Run migration upgrade/downgrade checks against a clean test database and import all ORM models.

## Task 4: Build and validate the complete retention manifest

**Files:**
- Create: `backend/app/modules/workflows/retention.py`
- Create: `backend/tests/workflows/test_workflow_retention.py`
- Modify: `backend/app/modules/workflows/errors.py`

- [ ] Add failing tests for terminal versus active Workflow status, active stage/Job blockers, complete input/artifact/current-and-history-result collection, classifier/split ledger references, file-ID deduplication, deterministic paths and digest, unavailable or mismatched files, preview reclaim accounting, and cross-Workflow shared-reference conflicts.
- [ ] Implement relationship-based collection without enumerating a bucket or filesystem tree.
- [ ] Validate every source object's registered size and SHA, create a deterministic immutable manifest, and return explicit structured blocker codes and safe Chinese operator actions.
- [ ] Run the focused service tests including large-manifest query-count coverage.

## Task 5: Stream the backup and enforce download-before-purge

**Files:**
- Create: `backend/app/modules/workflows/routes/retention.py`
- Modify: `backend/app/modules/workflows/routes/router.py`
- Create: `backend/app/modules/workflows/schemas/retention.py`
- Create: `backend/tests/workflows/test_workflow_retention_routes.py`
- Modify: `backend/app/platform/storage/streaming_zip.py`

- [ ] Add failing route tests for permissions, preview, export creation, capability scope/expiry, stable streaming ZIP entries, interrupted download, server-side transfer completion, wrong confirmation phrase, download-not-complete rejection, status drift, and duplicate requests.
- [ ] Implement the five approved endpoints and path-scoped HttpOnly capability without exposing object keys or local paths.
- [ ] Stream Local/MinIO objects directly to the response; do not create a second ZIP object or a server temporary archive.
- [ ] Record `downloaded` only after the complete response iterator finishes, and keep interrupted transfers non-purgeable.
- [ ] Run route and storage streaming tests.

## Task 6: Purge asynchronously and preserve recoverable evidence

**Files:**
- Create: `backend/app/modules/workflows/retention_tasks.py`
- Modify: `backend/app/platform/messaging/celery_app.py`
- Modify: `backend/app/workers/tasks_maintenance.py`
- Create: `backend/tests/workflows/test_workflow_retention_purge.py`
- Modify: `backend/tests/infrastructure/test_celery_minio_deployment.py`

- [ ] Add failing tests for maintenance dispatch failure, row locking, idempotent re-entry, object delete failure, partial delete, already-missing objects, preview cleanup, cross-reference drift, MySQL commit failure, tombstones, artifact removal, preserved Workflow/Job/stage/input/audit rows, and transfer `compensation_required` evidence.
- [ ] Implement purge request dispatch with 202 response and one active task per export.
- [ ] Delete only manifest objects and their preview caches, tolerate already-missing targets, then atomically tombstone files and remove downloadable artifacts.
- [ ] Preserve a safe `purge_failed` record and compensation evidence whenever physical object state and MySQL cannot be committed together.
- [ ] Run focused worker tests and Celery route/queue contract tests.

## Task 7: Keep the worker-facing UI simple and actionable

**Files:**
- Modify: `frontend/src/features/operations/pages/InfrastructurePage.tsx`
- Modify: `frontend/src/features/operations/pages/InfrastructurePage.test.tsx`
- Create: `frontend/src/features/workflows/WorkflowRetentionControl.tsx`
- Create: `frontend/src/features/workflows/WorkflowRetentionControl.test.tsx`
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Modify: `frontend/src/features/workflows/workflows.api.ts`
- Modify: `frontend/src/features/workflows/types.ts`
- Modify: `frontend/src/shared/components/ApiErrorAlert.tsx`
- Modify: `frontend/e2e/workflow-detail.spec.ts`

- [ ] Add failing component tests for a compact capacity state, `unknown` explanation, warning/critical colors, and no cleanup table in the data console.
- [ ] Add failing Workflow-detail tests for terminal-only visibility, the three fixed steps, persistent server state, interrupted download lockout, typed confirmation, asynchronous polling, duplicate-submit prevention, actual reclaimed bytes, and structured safe errors.
- [ ] Implement only the concise status presentation in the data console and put full retention controls in the corresponding Workflow detail.
- [ ] Reuse `ApiErrorAlert` so each failure states the fact, impact, next action, code, and request ID; never render secrets, keys, paths, DSNs, or tracebacks.
- [ ] Run Vitest, TypeScript build, and focused Playwright tests at desktop and narrow widths.

## Task 8: Prove the full container path and release it

**Files:**
- Modify: `docs/guides/operations.md`
- Modify: `docs/guides/deployment.md`
- Modify: `docs/api/openapi.snapshot.json`
- Modify: `scripts/README.md`

- [ ] Start an isolated fresh Compose project with MySQL and MinIO named volumes; prove all configured services become healthy.
- [ ] Run `bash scripts/docker.sh verify-storage` and record a successful MySQL-register/MinIO-write-read-SHA-delete transaction with no residual probe object.
- [ ] Restart MySQL, MinIO, API, and maintenance worker; prove persistence and recovery without rebuilding data.
- [ ] Stop MinIO and prove readiness fails, capacity becomes unknown without becoming zero, and structured UI guidance remains; restart MinIO and prove automatic recovery.
- [ ] Create a terminal test Workflow, download and open the full ZIP, request purge, verify async completion, actual reclaimed bytes, MinIO object absence, MySQL tombstones, and preserved workflow lineage.
- [ ] Run backend full tests, frontend full tests/build, migration checks, Compose checks, docs checks, and `bash scripts/verify.sh full`.
- [ ] Inspect generated artifacts and git diff, remove obsolete code introduced by the change, preserve user-owned untracked files, commit cohesive changes, push `main`, restart the deployed stack, and repeat readiness plus storage verification.

