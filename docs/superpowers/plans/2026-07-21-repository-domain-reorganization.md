# Repository Domain Reorganization Implementation Plan

> **For agentic workers:** REQUIRED EXECUTION MODE: implement this plan inline in the current session. The user explicitly prohibits subagents. Track every checkbox, run each stated gate, and commit at every task boundary.

**Goal:** Reorganize the repository into traceable platform and business-domain modules without reducing HTTP, database, Celery, frontend, Stage, test, or operational behavior.

**Architecture:** Keep stable external interfaces (`app.main:app`, HTTP method/path/operationId, database schema, Celery task names, frontend URLs, root operator commands) while moving implementation behind feature-first modules. Establish machine-checked contract snapshots and a module catalog before moving code, then migrate one domain at a time so each commit is independently reversible.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery, MySQL, Local/MinIO storage, React 19, TypeScript 6, Vite 8, Ant Design, TanStack Query, Playwright, Docker Compose, Nginx, shell scripts.

---

## Global invariants

Every task must preserve these facts unless the task explicitly adds coverage:

```text
OpenAPI paths:             114
OpenAPI operations:        135
OpenAPI contract SHA-256:  ef35e9cbb2e613a0f0b37f6fdf87a5001375c88d518a0346ce8db2fea5e63019
ORM model tables:          36
Alembic revisions:         17, single head e2f4b8c6a130
Celery public task names:  11 app.workers.* names
Backend collected tests:   at least 1010
Frontend public routes:    unchanged
Compose service names:     unchanged
Stage package names:       unchanged
```

The three pre-existing backend failures at commit `4d93ed5` are caused by deleted documentation. Task 1 must make the suite green before structural code movement begins.

## Task 1: Rebuild a coherent documentation contract

**Files:**

- Create: `docs/README.md`
- Create: `docs/architecture/overview.md`
- Create: `docs/architecture/workflow.md`
- Create: `docs/architecture/platform-specification.md`
- Create: `docs/architecture/implementation-status.md`
- Create: `docs/reference/database.md`
- Create: `docs/reference/configuration.md`
- Create: `docs/guides/development.md`
- Create: `docs/guides/deployment.md`
- Create: `docs/guides/operations.md`
- Create: `docs/guides/security.md`
- Create: `docs/verification/current.md`
- Modify: `scripts/generate_api_docs.py`
- Modify: `scripts/check_docs.py`
- Modify: `backend/tests/test_docs_consistency.py`
- Modify: `backend/tests/test_celery_minio_deployment.py`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `backend/README.md`
- Modify: `frontend/README.md`
- Modify: `infra/README.md`
- Modify: `backend/migrations/README.md`
- Modify: Stage Markdown files that reference deleted `docs/*.md`
- Move content from: `DWG-Agent企业平台技术规范.md`
- Move and refresh content from: `目标架构实现进度报告.md`
- Delete after references change: the two root source Markdown files

- [x] **Step 1: Confirm the known red gate**

Run:

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_docs_consistency.py \
  tests/test_celery_minio_deployment.py::test_deployment_docs_match_mysql_derived_celery_url_behavior
```

Expected: three failures caused only by missing documentation paths.

- [x] **Step 2: Point generated API documentation at the classified reference directory**

Change `scripts/generate_api_docs.py` to use one constant:

```python
API_DOC_PATH = ROOT / "docs" / "reference" / "api.md"

def main() -> int:
    API_DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    API_DOC_PATH.write_text(render(), encoding="utf-8")
    return 0
```

Update generator prose and Makefile-facing messages to name `docs/reference/api.md`.

- [x] **Step 3: Make the documentation checker report missing files instead of crashing**

Use a checked read helper in `scripts/check_docs.py`:

```python
def _read_required(path: Path, errors: list[str]) -> str:
    if not path.is_file():
        errors.append(f"missing required document: {path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8")
```

Set `DOCS` to the new categorized files and compare generated content with `docs/reference/api.md`. A missing document must produce a normal non-zero checker result, never a traceback.

- [x] **Step 4: Write the categorized documentation set**

Each guide must contain real commands and current limitations. In particular, `docs/guides/deployment.md` must state all of these current facts because the contract test enforces them:

```text
MySQL SQLAlchemy transport is the current Celery broker.
RabbitMQ is the target architecture and is not yet deployed.
Celery Result Backend rows are bounded runtime data, not formal business results.
The production Compose storage adapter is MinIO; local development may use local storage.
The Compose stack currently exposes HTTP and does not claim completed TLS.
```

`docs/architecture/workflow.md` must include the existing public input and workflow routes, the ten-stage `linux_production` framework, the server-side DWG→DXF rule, classification 1.1.0, and explicit placeholder stages.

- [x] **Step 5: Update all owned Markdown links**

Run:

```bash
rg -n 'docs/(api|architecture|database|configuration|development|deployment|operations|security|workflow-framework|workflow-verification|processing-pipelines|roadmap|developer-preview|audit-report)[^)]*\.md' \
  --glob '*.md' .
```

Replace every result with a real new path. Do not keep compatibility files whose only content is a link.

- [x] **Step 6: Generate API reference and run the focused gate**

Run:

```bash
backend/.venv/bin/python scripts/generate_api_docs.py
backend/.venv/bin/python scripts/check_docs.py
cd backend
.venv/bin/pytest -q \
  tests/test_docs_consistency.py \
  tests/test_celery_minio_deployment.py::test_deployment_docs_match_mysql_derived_celery_url_behavior
```

Expected: documentation checker passes and all focused tests pass.

- [x] **Step 7: Run the full backend baseline**

Run:

```bash
cd backend
.venv/bin/pytest -q
```

Expected: at least `1004 passed, 6 skipped`, with no failures. Record the exact current number in `docs/verification/current.md`.

- [x] **Step 8: Commit**

```bash
git add README.md README_EN.md backend/README.md frontend/README.md infra/README.md \
  backend/migrations/README.md docs scripts/generate_api_docs.py scripts/check_docs.py \
  backend/tests/test_docs_consistency.py backend/tests/test_celery_minio_deployment.py \
  Stages DWG-Agent企业平台技术规范.md 目标架构实现进度报告.md
git commit -m "docs: rebuild categorized project documentation"
```

## Task 2: Add machine-checked architecture contracts and module catalog

**Files:**

- Create: `docs/architecture/module-catalog.json`
- Create: `docs/architecture/module-catalog.md`
- Create: `docs/architecture/runtime-contract.json`
- Create: `docs/architecture/traceability.md`
- Create: `scripts/architecture/snapshot_contracts.py`
- Create: `scripts/architecture/check_module_catalog.py`
- Create: `backend/tests/architecture/test_contract_snapshot.py`
- Create: `backend/tests/architecture/test_module_catalog.py`
- Modify: `scripts/verify.sh`
- Modify: `Makefile`
- Modify: `scripts/README.md`

- [x] **Step 1: Write failing contract tests**

The snapshot test must assert exact HTTP, table and task sets, not only counts:

```python
def test_runtime_contract_matches_committed_snapshot() -> None:
    actual = build_contract_snapshot()
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert actual == expected
```

The catalog test must assert:

```python
assert set(catalog["owned_tables"]) == set(Base.metadata.tables)
assert len(all_owned_operations) == 135
assert len(all_owned_tasks) == 11
assert not duplicate_primary_owners
assert not missing_paths
```

Run the two tests and expect failure because scripts/catalog do not yet exist.

- [x] **Step 2: Implement deterministic snapshot generation**

The script must emit sorted JSON containing:

```json
{
  "http_operations": ["GET /api/v1/... operation_id"],
  "orm_tables": ["agent_memory", "..."],
  "celery_tasks": ["app.workers.tasks_dxf.convert_dwg_to_dxf", "..."],
  "frontend_routes": ["/login", "/dashboard", "..."],
  "compose_services": ["backend-api", "..."],
  "alembic_head": "e2f4b8c6a130"
}
```

Use JSON parsing for Compose only if a YAML parser is already available in the repo environment; otherwise extract services through `docker compose config --format json` in the command mode and keep the pure-Python test focused on application contracts.

- [x] **Step 3: Populate module ownership**

Use these primary table owners:

```text
identity:          sys_users, sys_roles, sys_permissions, sys_user_roles,
                   sys_role_permissions, token_blacklist
projects:          projects, project_members, drawings, drawing_versions
files:             files, file_transfers, storage_scan_runs, storage_scan_findings
jobs:              jobs, job_steps, analysis_results, review_records
workflows:         workflow_runs, workflow_stage_runs, workflow_artifacts,
                   workflow_input_batches, workflow_input_items
dxf_classification:dxf_classification_runs, dxf_classification_items
excel_processing:  excel_final_batches, excel_final_parts, excel_final_components
operations:        audit_logs, control_plane_events, platform_messages,
                   worker_runtimes, daily_archive_runs
automation:        agent_memory, agent_runs, agent_run_steps
cad_processing:    no owned table; owns task and Stage interfaces
```

Every catalog entry also lists its architecture node IDs, current status (`implemented`, `partial`, `placeholder`, `external`), paths, HTTP prefixes, tasks, queues, Stage packages, tests and docs.

- [x] **Step 4: Add architecture gates to normal verification**

Add to `scripts/verify.sh quick` and Makefile:

```bash
backend/.venv/bin/python scripts/architecture/check_module_catalog.py
```

Expose `make architecture-check` and document it.

- [x] **Step 5: Verify and commit**

Run:

```bash
backend/.venv/bin/python scripts/architecture/check_module_catalog.py
cd backend
.venv/bin/pytest -q tests/architecture
cd ..
git diff --check
```

Expected: catalog path, table, operation and task ownership checks pass.

```bash
git add docs/architecture scripts/architecture backend/tests/architecture scripts/verify.sh Makefile scripts/README.md
git commit -m "test: lock repository architecture contracts"
```

## Task 3: Organize root product boundaries and infrastructure

**Files:**

- Move: `infra/nginx/*` → `infra/gateway/nginx/`
- Move: `infra/mysql/*` → `infra/database/mysql/`
- Move: `infra/minio/*` → `infra/storage/minio/`
- Move: `infra/verify.sh` → `infra/verification/verify.sh`
- Create: `infra/messaging/rabbitmq/README.md`
- Create: `infra/operations/backup/README.md`
- Create: `infra/operations/monitoring/README.md`
- Replace: `infra/README.md`
- Move: `cad-worker/README.md` → `windows/README.md`
- Create: `windows/node-agent/README.md`
- Create: `windows/cam-runner/README.md`
- Create: `windows/sinocam-adapter/README.md`
- Create: `windows/protocols/README.md`
- Modify: `compose.yaml`
- Modify: `backend/Dockerfile`
- Modify: root scripts referencing `infra/nginx`
- Modify: infrastructure/config/compose tests
- Modify: `docs/architecture/module-catalog.json`
- Delete: duplicate root `image.png` after README points to `frontend/public/logo.png`

- [x] **Step 1: Extend tests with target path assertions**

Update infrastructure tests to assert the new categorized paths and current truthful RabbitMQ state:

```python
assert (REPO_ROOT / "infra/gateway/nginx/nginx.conf").is_file()
assert (REPO_ROOT / "infra/database/mysql/init.sql").is_file()
assert (REPO_ROOT / "infra/storage/minio").is_dir()
assert "rabbitmq" not in compose["services"]
assert "not implemented" in rabbitmq_readme.lower()
```

Run focused tests and expect failure before moves.

- [x] **Step 2: Move infrastructure files with history-preserving patches**

Move files without editing SQL or Nginx behavior. Update Compose mounts exactly:

```yaml
- ./infra/database/mysql/init.sql:/docker-entrypoint-initdb.d/01-platform.sql:ro
- ./infra/database/mysql/hardware_handbook.sql:/docker-entrypoint-initdb.d/02-hardware-handbook.sql:ro
```

Update local Nginx config and log paths in scripts to `infra/gateway/nginx`.

- [x] **Step 3: Build truthful target placeholders**

RabbitMQ, backup and monitoring README files must use a status block:

```markdown
Status: target contract, not deployed in current Compose.
Current runtime: MySQL SQLAlchemy Celery transport.
Completion evidence required: Compose service, healthcheck, durable volume,
application configuration, worker recovery tests and operations runbook.
```

Windows README files must describe interfaces and required evidence, not executable success.

- [x] **Step 4: Verify paths and behavior**

Run:

```bash
bash -n scripts/*.sh
docker compose config --quiet
bash infra/verification/verify.sh
cd backend
.venv/bin/pytest -q tests/test_compose.py tests/test_config_drift.py \
  tests/test_nginx_contract.py tests/test_mysql_runtime.py
```

Expected: all static and activity-safe checks pass; no service names change.

- [x] **Step 5: Commit**

```bash
git add infra windows compose.yaml backend/Dockerfile scripts backend/tests docs README.md README_EN.md frontend/public/logo.png image.png cad-worker
git commit -m "refactor: classify infrastructure and Windows contracts"
```

## Task 4: Split script interfaces from implementation

**Files:**

- Create: `scripts/lib/common.sh`
- Create: `scripts/lib/local_stack.sh`
- Create: `scripts/lib/database.sh`
- Create: `scripts/lib/compose.sh`
- Create: `scripts/lib/cad_worker.sh`
- Move: `benchmark_cad_conversion.py` → `scripts/cad/benchmark_conversion.py`
- Move: `forward-to-win11.sh` → `scripts/windows/forward_to_win11.sh`
- Move: `reap_storage.py` → `scripts/storage/reap.py`
- Move: `verify_storage_transactions.py` → `scripts/storage/verify_transactions.py`
- Move: `check_docs.py` → `scripts/docs/check.py`
- Move: `generate_api_docs.py` → `scripts/docs/generate_api.py`
- Keep as facades: `start-all.sh`, `start-dev.sh`, `stop-all.sh`, `status.sh`, `doctor.sh`, `db.sh`, `docker.sh`, `verify.sh`, `run-cad-worker.sh`
- Modify: `scripts/lib.sh` into a compatibility facade sourcing classified libraries
- Modify: Dockerfile, Compose, Makefile, tests, docs

- [x] **Step 1: Add tests for stable command interfaces and classified implementation**

Assert every root facade remains executable and sources a classified implementation. Add a shell syntax parameterized test over both root facades and `scripts/lib/*.sh`.

- [x] **Step 2: Extract common and local lifecycle functions**

Move color/log/path primitives to `lib/common.sh`, MySQL helpers to `lib/database.sh`, Compose backup/restore helpers to `lib/compose.sh`, worker/Xvfb lifecycle to `lib/cad_worker.sh`, and local process ownership to `lib/local_stack.sh`.

Root facade shape:

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/database.sh"
db_main "$@"
```

Keep output, exit codes and accepted arguments unchanged.

- [x] **Step 3: Update internal Python script paths**

Replace references with the categorized locations in Makefile, Dockerfile, tests and docs. `scripts/db.sh reap-storage` must invoke `scripts/storage/reap.py`; users still call the stable shell command. `make docs` and `make docs-check` must invoke `scripts/docs/generate_api.py` and `scripts/docs/check.py`; update checker imports and contract tests in the same commit so no invocation points at the retired root Python paths.

- [x] **Step 4: Run script gates**

```bash
find scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n
cd backend
.venv/bin/pytest -q tests/test_scripts.py tests/test_forward_to_win11_script.py \
  tests/test_storage_operations.py tests/test_compose.py \
  tests/test_docs_consistency.py tests/test_frontend_contract.py
cd ..
bash scripts/status.sh
backend/.venv/bin/python scripts/docs/check.py
```

Expected: all tests pass and status remains read-only.

- [x] **Step 5: Commit**

```bash
git add scripts Makefile backend/Dockerfile compose.yaml backend/tests docs
git commit -m "refactor: separate script commands from implementations"
```

## Task 5: Establish backend platform modules

**Files:**

- Create/move under: `backend/app/platform/config/`
- Create/move under: `backend/app/platform/database/`
- Create/move under: `backend/app/platform/http/`
- Create/move under: `backend/app/platform/messaging/`
- Create/move under: `backend/app/platform/observability/`
- Create/move under: `backend/app/platform/security/`
- Create/move under: `backend/app/platform/storage/`
- Create: `backend/app/bootstrap/application.py`
- Create: `backend/app/bootstrap/model_registry.py`
- Create: `backend/app/bootstrap/task_registry.py`
- Modify: `backend/app/main.py` into stable facade
- Modify: Alembic env and all production/test/script imports
- Delete after migration: old `core/`, `db/`, `storage/`, `utils/` implementation files
- Keep temporarily: `api/deps.py` until identity/project access migration

- [x] **Step 1: Add platform dependency tests**

Use AST to reject platform imports from business modules:

```python
def test_platform_never_imports_business_modules() -> None:
    violations = imports_with_prefix(APP / "platform", "app.modules")
    assert violations == []
```

Add registry tests asserting all 36 tables and 11 tasks are loaded from explicit registry imports.

- [x] **Step 2: Move pure platform seams first**

Move database, storage adapters, security primitives, config and logging without changing function/class bodies. Add narrow package exports; do not wildcard-export implementation internals.

- [x] **Step 3: Move Celery application while retaining public task names**

Official runtime path becomes:

```text
app.platform.messaging.celery_app:celery_app
```

Update Compose, `run-cad-worker.sh`, local worker helpers and tests. The decorators in task files retain exact strings such as:

```python
@celery_app.task(name="app.workers.tasks_dxf.convert_dwg_to_dxf", bind=True)
```

This preserves already queued message compatibility.

- [x] **Step 4: Create explicit registries and bootstrap**

`model_registry.py` imports every domain model module; `task_registry.py` imports each task module. `main.py` contains only:

```python
from app.bootstrap.application import app

__all__ = ["app"]
```

- [x] **Step 5: Update imports and string patch targets atomically**

Search both code and strings:

```bash
rg -n 'app\.(core|db|storage|utils|workers\.celery_app)' backend scripts compose.yaml
```

No old production import may remain. Tests patch the location where a dependency is looked up, not where it was originally defined.

- [x] **Step 6: Verify**

```bash
cd backend
.venv/bin/python -m compileall -q app
.venv/bin/ruff check app tests
.venv/bin/pytest --collect-only -q
.venv/bin/pytest -q tests/architecture tests/test_db_session.py \
  tests/test_storage_consistency.py tests/test_storage_inventory.py \
  tests/test_celery_recovery.py tests/test_celery_minio_deployment.py
.venv/bin/alembic check
cd ..
docker compose config --quiet
```

Expected: at least 1010 tests collected; contract snapshot unchanged.

- [x] **Step 7: Commit**

```bash
git add backend/app backend/tests backend/migrations compose.yaml compose.dev.yaml scripts docs
git commit -m "refactor: establish backend platform modules"
```

## Task 6: Move identity and project catalog domains

**Files:**

- Create: `backend/app/modules/identity/README.md`
- Move identity routes/models/schemas/application code into `modules/identity/`
- Create: `backend/app/modules/identity/interface.py`
- Create: `backend/app/modules/projects/README.md`
- Move project/drawing routes/models/schemas/application code into `modules/projects/`
- Create: `backend/app/modules/projects/interface.py`
- Split `api/deps.py` into platform authentication dependency plus domain access functions
- Update router/model registry/imports/tests/catalog
- Delete migrated old files

Implementation refinement after import-graph audit:

- `identity/` uses `routes/`, `models/` and `schemas/` because each concern has multiple real files; authentication, role access and user application logic stay directly visible at the domain root.
- `projects/` uses `routes/`, `models/`, `schemas/` and `services/`; `interface.py` is the only cross-domain import path.
- Generic `DbSession/get_db` belongs to `platform/http/dependencies.py`; bearer/cookie auth belongs to identity, and project membership belongs to projects.
- HTTP router composition and identity-dependent seed data belong to `bootstrap/`; platform must remain unable to import any business module.
- Audit writes use `modules/operations/audit/interface.py` so identity/projects do not depend on a legacy private service.

- [x] **Step 1: Add domain interface and ownership tests**

Assert identity owns six RBAC/token tables and projects owns four project/drawing tables. Assert role/project permission outcomes through the public interfaces.

- [x] **Step 2: Move identity as one vertical slice**

Preserve router paths `/auth`, `/users`, `/roles`; preserve token cookie scopes, password invalidation, self-protection and audit calls. Run identity and security tests before continuing.

- [x] **Step 3: Move projects and drawings**

Preserve `/projects`, `/drawings`, membership SQL filtering, version increment behavior and drawing preview delegation.

- [x] **Step 4: Verify and commit**

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/pytest -q tests/test_adversarial_auth.py tests/test_rbac_deep.py \
  tests/test_token_lifecycle.py tests/test_security_boundaries.py \
  tests/test_service_layer.py tests/test_rigorous.py tests/architecture
.venv/bin/alembic check
```

Expected: focused tests and contract snapshot pass.

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: group identity and project catalog domains"
```

## Task 7: Deepen the file registry module

**Files:**

- Create: `backend/app/modules/files/README.md`
- Create: `backend/app/modules/files/interface.py`
- Create: `backend/app/modules/files/models.py`
- Create: `backend/app/modules/files/schemas.py`
- Create: `backend/app/modules/files/access.py`
- Create: `backend/app/modules/files/validation.py`
- Create: `backend/app/modules/files/storage_transactions.py`
- Create routes: `uploads.py`, `catalog.py`, `batches.py`, `previews.py`, `downloads.py`, `router.py`
- Move file/file-transfer/storage-scan code into the module
- Update imports, patches, registry, catalog and tests
- Delete old files only after zero references

Implementation refinement after route, model and storage-call-graph audit:

- The file domain owns four persistence facts: `files`, `file_transfers`, `storage_scan_runs` and `storage_scan_findings`.  Reconciliation execution/remediation remains in the legacy operations service until Task 12, and may consume those models only through `files.interface`.
- `platform/storage/factory.py` owns backend selection, cache reset, health and local-path resolution.  It must not import ORM models or file-domain rules.
- `validation.py` owns filename/path, MIME and DWG-header rules; `registration.py` owns upload/generated-byte/generated-path registration; `storage_transactions.py` owns the transfer ledger plus SQLAlchemy commit/rollback compensation hooks.
- `exports.py` owns signed URLs, format pairing and ZIP creation; `lifecycle.py` owns file soft deletion and preview invalidation.  These explicit files prevent the old 846-line storage service from being recreated under a new name.
- Routes are split into uploads, catalog, batches, previews and downloads.  Static catalog/download endpoints are registered before any `/{file_id}` route; all 17 existing method/path/function-name contracts remain fixed.
- Other business modules import StoredFile, schemas, access, registration, transfer and storage-factory facades only through `files.interface`; tests may import private modules when validating their internal responsibility.

- [x] **Step 1: Add router order and interface tests**

Assert `/files/batches`, `/files/download-zip/preview` and `/files/{file_id}` resolve to their intended endpoints. Add tests for interface exports used by jobs/workflows/operations.

- [x] **Step 2: Split `files_api.py` by use case**

Maintain this include order:

```python
router.include_router(uploads.router)
router.include_router(catalog.static_router)
router.include_router(batches.router)
router.include_router(previews.router)
router.include_router(downloads.static_router)
router.include_router(catalog.item_router)
router.include_router(downloads.item_router)
```

Function names and operation IDs remain unchanged.

- [x] **Step 3: Deepen storage transactions**

`storage_transactions.py` owns MySQL/object compensation hooks and file-transfer settlement. `platform.storage` owns only adapter behavior. `validation.py` owns filename, MIME, DWG header and safe-path rules.

- [x] **Step 4: Verify**

```bash
cd backend
.venv/bin/pytest -q tests/test_adversarial_files.py tests/test_file_service.py \
  tests/test_file_transfer_models.py tests/test_file_transfer_service.py \
  tests/test_storage_adversarial.py tests/test_storage_consistency.py \
  tests/test_dxf_preview_api.py tests/architecture
.venv/bin/alembic check
```

- [x] **Step 5: Commit**

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: deepen the file registry module"
```

## Task 8: Deepen Job, result and review lifecycle

**Files:**

- Create module files under `backend/app/modules/jobs/`
- Split routes into `queries.py`, `commands.py`, `events.py`, `results.py`, `reviews.py`
- Split implementation into `creation.py`, `lifecycle.py`, `dispatch.py`, `access.py`, `event_stream.py`, `stub_execution.py`, `recovery.py`
- Create `interface.py`, `models.py`, `schemas.py`, `README.md`
- Update Celery callers, workflow/CAD/Excel callers, tests, catalog
- Delete old job/result/review files

Implementation refinement after auditing all 18 HTTP operations, four tables, six worker callers and the source architecture:

- The module owns `jobs`, `job_steps`, `analysis_results` and `review_records`. It does not own Celery transport tables or file bytes.
- `creation.py` owns pipeline selection, create/batch-create and request-key idempotent reuse. `lifecycle.py` owns every status/attempt guarded mutation, including claim, progress, complete/fail, single cancel/retry and bulk active cancellation. Pending result/step rows remain in the same transaction so a stale attempt rolls them back.
- `dispatch.py` owns pipeline-to-task routing, public Celery names and post-commit dispatch compensation. It must state that this is the currently implemented direct-dispatch seam, not the planned Outbox.
- `event_stream.py` owns current-row snapshot construction and bounded short-session polling. The current schema has no durable event IDs/replay; route and module documentation must preserve that truthful limitation.
- `stub_execution.py` owns the executable framework smoke result and keeps its explicit placeholder message. `recovery.py` owns authoritative Job summaries and stale-running recovery.
- `platform.messaging` remains the stable Celery app/SQL transport adapter and exposes a generic worker-ready callback registry. Bootstrap registers Job recovery, so platform imports no jobs business model while Compose/CAD worker commands remain unchanged.
- `routes/router.py` exports `jobs_router`, `results_router` and `reviews_router`. Job static routes (`/batches`, `/cancellation-requests`, `/events/stream`, `/cancel-all-active`) are composed before `/{job_id}` routes; all 13 Job + 4 Result + 1 Review method/path/function-name contracts remain fixed.
- Other business modules use only `app.modules.jobs.interface`; bootstrap alone may import the route aggregator. Tests may import private files only to validate the responsibility itself.
- `interface.py` never imports routes. Old route/model/schema/service files are removed only after a zero-reference audit.

- [x] **Step 1: Add lifecycle and boundary contract tests**

Test the deep interface across create → claim → progress → complete/fail and cancel/retry. Assert stale attempts cannot mutate current attempt. Lock exact table ownership, the 13 Job + 4 Result + 1 Review routes, static-path precedence, public exports, platform independence and retired paths.

- [x] **Step 2: Split HTTP routes without changing operation IDs**

Register static `/events/stream`, `/batches`, `/cancellation-requests` before `/{job_id}`. Keep SSE response behavior and cookie authentication unchanged.

- [x] **Step 3: Split job implementation**

`lifecycle.py` owns state transitions; `dispatch.py` maps pipeline to stable task name; HTTP `routes/commands.py` orchestrates cancel/retry/audit; `event_stream.py` owns current snapshot streaming; `recovery.py` plugs stale recovery into the generic platform callback seam. Other domains import only `jobs.interface`.

- [x] **Step 4: Verify**

```bash
cd backend
.venv/bin/pytest -q tests/test_adversarial_jobs.py tests/test_job_access.py \
  tests/test_job_attempts.py tests/test_job_claim.py tests/test_job_events_mysql.py \
  tests/test_job_lifecycle.py tests/test_celery_recovery.py tests/architecture
.venv/bin/alembic check
```

- [x] **Step 5: Commit**

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: deepen job and result lifecycle"
```

## Task 9: Group CAD conversion and DXF classification

**Files:**

- Create: `backend/app/modules/cad_processing/{README.md,interface.py,execution.py,statistics.py,tasks.py}`
- Create preview rendering/cache files and conversion subpackages for `dwg_to_dxf`, `dxf_to_dwg`, `dxf_to_excel`
- Move CAD batch and conversion implementation
- Create: `backend/app/modules/dxf_classification/{README.md,interface.py,models.py,schemas.py,adapter.py,persistence.py,execution.py,tasks.py}`
- Update imports, task registry, tests, module catalog
- Delete migrated old service/task/model/schema files

Refined responsibility map after auditing 3,882 backend lines, six public Celery tasks, four Stage packages and the production diagrams:

- `cad_processing` owns conversion orchestration and DXF interpretation but no ORM tables or HTTP prefix. `/files` continues to own preview authorization/streaming and `/jobs` continues to own task state; both call the CAD public interface.
- `execution.py` owns only genuinely shared worker primitives: exception normalization, attempt-aware failure, JobStep construction, source file-id parsing and Local/MinIO source staging. Direction-specific error codes, metadata and result rules do not enter the common layer.
- `dwg_to_dxf/` and `dxf_to_dwg/` each separate version resolution, result persistence, single-job execution and batch execution. Shared ODA group invocation remains in a small batch adapter; Stage internals are not copied into the backend.
- `dxf_to_excel/` remains a CAD-processing conversion because it consumes DXF and produces the first material workbook; Excel Final alone moves to `excel_processing` in Task 10. Its batch staging and extraction execution remain distinct from one-file ODA conversion.
- DXF preview is split into bounded inspection/rendering and cache/registration responsibilities. Generated SVG remains a registered `files` row and uses the existing transfer saga; moving the renderer must not move file ownership.
- `cad_processing.tasks` registers the five existing conversion tasks with their exact historical `app.workers.tasks_*` names. `dxf_classification.tasks` registers the sixth. Celery include-module paths may change, while task names, queue routes, worker commands and Compose services do not.
- `dxf_classification` owns `dxf_classification_runs` and `dxf_classification_items`. `adapter.py` enforces the Steel DXF Classifier 1.1.0 CLI/schema/exit-code and naming contract; `persistence.py` owns source/output ledgers; `execution.py` owns Job/workflow orchestration.
- Other business modules may use only `cad_processing.interface` or `dxf_classification.interface`. Until Workflow moves in Task 11, classification has an explicitly documented transitional dependency on the old workflow input/artifact services; it must not be described as a fully decoupled workflow domain.
- Stable Stage product seams remain exactly `dwg-converter 0.1.0`, `dxf-converter 0.1.0`, `dxf2excel 0.1.0` and `steel-dxf-classifier 1.1.0` under their current `Stages/*` paths. No Stage source, sample corpus, lock file or CLI entry point moves in this task.

- [x] **Step 1: Lock Stage and task contracts**

Add assertions for package names/versions, task names, queue routes, two classification tables, public interfaces, cross-domain imports, retired paths and Stage availability. Do not move `Stages/*` paths.

- [x] **Step 2: Extract shared conversion execution behavior**

Move repeated source staging, error text, JobStep creation and attempt-aware failure logic to `execution.py`. Direction-specific version detection and result metadata remain in their conversion subpackage.

- [x] **Step 3: Move preview/statistics and classification**

Files routes use the CAD preview interface. Workflow routes use the classification interface. Classification continues to invoke Steel DXF Classifier 1.1.0 and persist JSON/CSV/DXF outputs.

- [x] **Step 4: Verify**

```bash
cd backend
.venv/bin/pytest -q tests/test_cad_batch_jobs.py tests/test_dxf_pipeline.py \
  tests/test_dxf2dwg_pipeline.py tests/test_dxf2excel_pipeline.py \
  tests/test_dxf_preview_api.py tests/test_dxf_preview_service.py \
  tests/test_dxf_classification_pipeline.py tests/architecture
cd ../Stages/dwg2dxf && .venv/bin/pytest -q
cd ../dxf2dwg && .venv/bin/pytest -q
cd ../dxf2excel && .venv/bin/pytest -q
cd ../steel_dxf_classifier_v1.1.0 && .venv/bin/pytest -q
```

- [x] **Step 5: Commit**

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: group CAD processing and classification"
```

## Task 10: Group Excel processing

**Files:**

- Create module under `backend/app/modules/excel_processing/`
- Split HTTP routes into upload/process, catalog, tools, health
- Move Excel models/schemas/application/adapter/task
- Create `interface.py` and `README.md`
- Update imports, task registry, tests, catalog
- Delete old Excel files

- [ ] **Step 1: Add router and idempotency interface tests**

Preserve upload/process request keys, project access, batch pagination, part/component detail, weight lookup and health behavior.

- [ ] **Step 2: Split the 896-line route module**

Register `/parts/search` and `/weights/lookup` before parameterized batch routes. Preserve function names and responses.

- [ ] **Step 3: Move adapter and execution implementation**

Keep subprocess isolation and Stage path resolution behind `stage_adapter.py`; relationship import and Job lifecycle calls remain in domain implementation. Expose the Excel temporary-row cleanup operation through `excel_processing.interface`, then replace the transitional direct `jobs.lifecycle/recovery -> app.models.excel_final.ExcelFinalBatch` dependency without changing cancellation or stale-recovery behavior.

- [ ] **Step 4: Verify**

```bash
cd backend
.venv/bin/pytest -q tests/test_excel_final_adapter.py tests/test_excel_final_import.py \
  tests/test_excel_final_models.py tests/test_excel_final_retry.py \
  tests/test_excel_final_idempotency.py tests/architecture
cd ../Stages/excel_final
.venv/bin/pytest -q multi_split/tests
```

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: group Excel processing"
```

## Task 11: Group production workflow intake and orchestration

**Files:**

- Create module under `backend/app/modules/workflows/`
- Create intake files `registration.py`, `conversion.py`, `freeze.py`, `presentation.py`
- Split workflow routes into templates/query/commands/artifacts/execution/classification
- Move workflow/input models and schemas
- Create `interface.py`, `README.md`
- Update imports/tests/catalog and delete old files

- [ ] **Step 1: Lock the server-derived DXF invariant**

Add an architecture-level test asserting workflow input registration accepts DWG/Excel and rejects human DXF. Keep one Excel limit and freeze manifest behavior.

- [ ] **Step 2: Split intake implementation by state transition**

Registration owns format/readability and file linking; conversion owns Job plans/sync; freeze owns name collision, manifest hash, Drawing creation and artifact attachment; presentation owns response diagnostics.

- [ ] **Step 3: Split orchestration routes and preserve stage contracts**

Keep all ten `linux_production` stages, implemented capability flags and placeholder handoff requirements unchanged.

- [ ] **Step 4: Verify**

```bash
cd backend
.venv/bin/pytest -q tests/test_workflow_api.py tests/test_workflow_boundaries.py \
  tests/test_workflow_framework.py tests/test_workflow_input_api.py \
  tests/test_workflow_input_service.py tests/test_workflow_production.py \
  tests/architecture
.venv/bin/alembic check
```

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: group production workflow orchestration"
```

## Task 12: Group operations and truthful automation contracts

**Files:**

- Create: operations submodules `daily_archive`, `data_catalog`, `storage_reconciliation`, `control_plane`, `audit`
- Move corresponding models/schemas/routes/implementation/tasks
- Split `data_admin_api.py` into submodule routers
- Move agent model/routes/memory into `modules/automation/agent/`
- Move ZWCAD/MCP placeholders into `modules/automation/contracts/`
- Delete empty Python files: old `tasks_agent.py`, `tasks_cad.py`, `tasks_dispatch.py`, one-line placeholder adapters
- Update registry, imports, tests, catalog

- [ ] **Step 1: Add honesty tests**

Assert placeholder/external automation modules have no registered Celery tasks and return explicit disabled/not-implemented responses. Assert current maintenance tasks remain registered.

- [ ] **Step 2: Split operations by owner**

Daily archive owns preview/run/manifest; reconciliation owns scan/finding/remediation; data catalog is read-only operational projection; control plane owns runtime messages/events; audit owns append/read.

- [ ] **Step 3: Remove misleading empty implementation modules**

Represent unimplemented Windows/Agent capabilities in README, schemas and explicit interface methods. Remove 1-line Python files that imply concrete adapters exist.

- [ ] **Step 4: Verify**

```bash
cd backend
.venv/bin/pytest -q tests/test_daily_archive.py tests/test_data_admin_api.py \
  tests/test_storage_reconciliation.py tests/test_infrastructure_api.py \
  tests/test_control_plane_api.py tests/test_agent_memory.py \
  tests/test_adversarial_jobs.py tests/architecture
.venv/bin/alembic check
```

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests docs/architecture
git commit -m "refactor: group operations and automation contracts"
```

## Task 13: Reorganize backend tests to mirror domains

**Files:**

- Keep: `backend/tests/conftest.py`
- Create: `backend/tests/support/paths.py`
- Move tests into: `architecture`, `contracts`, `identity`, `projects`, `files`, `jobs`, `workflows`, `cad_processing`, `dxf_classification`, `excel_processing`, `operations`, `security`, `infrastructure`, `regression`
- Update imports, patch strings, path calculations, verification scripts and catalog
- Delete empty root `backend/tests/__init__.py` if package semantics are unnecessary

- [ ] **Step 1: Add stable repository path helper**

```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
STAGES_ROOT = REPO_ROOT / "Stages"
```

Moved tests import this helper instead of counting `parents[n]` locally.

- [ ] **Step 2: Move tests by primary behavior**

Cross-domain adversarial/regression suites go to `regression/`; do not split test functions merely to force one-owner purity. File names remain recognizable for Git history.

- [ ] **Step 3: Validate monkeypatch targets**

Add a test that statically extracts string targets passed to `monkeypatch.setattr`/`patch`, imports the module and verifies the final attribute exists. Exclude dynamically constructed targets with an explicit list and explanation.

- [ ] **Step 4: Verify collection and full suite**

```bash
cd backend
.venv/bin/pytest --collect-only -q | tail -1
.venv/bin/pytest -q
```

Expected: collected count is no lower than the pre-move count and all non-conditional tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests scripts docs/architecture
git commit -m "refactor: mirror backend tests to domain modules"
```

## Task 14: Colocate frontend feature requests, types and shared infrastructure

**Files:**

- Create: `frontend/src/shared/api/{client,error}.ts`
- Create: `frontend/src/shared/auth/`
- Create: `frontend/src/shared/components/`
- Create: `frontend/src/shared/hooks/`
- Create feature directories from the design
- Move each feature's `api/*.ts` and `types/*.ts` beside its pages
- Update all imports and router lazy imports
- Delete empty `hooks/.gitkeep`, `utils/.gitkeep`, old `api/`, old `types/`, old `components/` when empty

- [ ] **Step 1: Add a frontend architecture checker**

Create a Node script that rejects imports from `features/*` into another feature's private files; cross-feature calls must use the target feature's `index.ts` public interface. Reject any re-creation of top-level `src/api` or `src/types` after migration.

- [ ] **Step 2: Move shared HTTP/auth first**

Move Axios client/error and authentication store/guard/init hook. Preserve refresh coalescing, session storage and error enrichment.

- [ ] **Step 3: Move vertical feature contracts**

For each feature, create `index.ts` exporting only its public route/page/types needed elsewhere. Move request and type files without changing runtime logic.

- [ ] **Step 4: Verify after each feature**

Run:

```bash
npm --prefix frontend run build
cd backend
.venv/bin/pytest -q tests/contracts/test_frontend_contract.py
```

Expected: build succeeds; router URL snapshot unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/package.json backend/tests docs/architecture scripts
git commit -m "refactor: colocate frontend feature contracts"
```

## Task 15: Split oversized frontend implementation and E2E layout

**Files:**

- Split `ConversionPage.tsx`, `InfrastructurePage.tsx`, `Dxf2ExcelPage.tsx`, `WorkflowsPage.tsx`, `ExcelPreview.tsx`
- Split `frontend/src/styles.css` into shared and feature styles
- Move E2E specs into feature directories
- Modify `frontend/package.json` test scripts
- Modify Playwright configuration only if recursive discovery needs explicit pattern
- Update module catalog and frontend README

- [ ] **Step 1: Add focused interaction coverage before splits**

Ensure existing E2E tests cover upload, bulk actions, retry/cancel, preview/download, production submission, archive, consistency and control-plane refresh. Add only missing assertions before extracting implementation.

- [ ] **Step 2: Split stateful pages by hook/view seam**

Hooks own queries/mutations/derived state; view files receive explicit typed props. Do not move permission checks into presentation-only code. Preserve accessible labels and error/request-id feedback.

- [ ] **Step 3: Split styles by owner**

Keep tokens/layout/surface styles in `shared/styles`; move `.conversion-*`, `.data-console-*`, `.daily-archive-*`, `.production-*`, `.login-*` selectors to their feature styles. Import styles from the owning feature entry.

- [ ] **Step 4: Move Playwright specs and use directory scripts**

Example package scripts:

```json
{
  "test:e2e": "playwright test tests/e2e",
  "test:e2e:files": "playwright test tests/e2e/files",
  "test:e2e:operations": "playwright test tests/e2e/operations"
}
```

- [ ] **Step 5: Verify and commit**

```bash
npm --prefix frontend run build
npm --prefix frontend run test:e2e
git add frontend docs/architecture
git commit -m "refactor: split frontend workspaces by feature"
```

## Task 16: Final documentation, runtime and completion audit

**Files:**

- Update every module README
- Update docs architecture/reference/guides/verification
- Update generated API reference
- Update catalog current paths/status
- Update README tree and commands
- No compatibility file may point to a missing path

- [ ] **Step 1: Prove no legacy layout remains**

Run:

```bash
test ! -d backend/app/services
test ! -d backend/app/models
test ! -d backend/app/schemas
test ! -d backend/app/workers
test ! -d frontend/src/api
test ! -d frontend/src/types
test ! -d cad-worker
rg -n 'app\.(services|models|schemas|workers)|frontend/src/(api|types)|infra/(nginx|mysql|minio)' \
  backend frontend scripts infra compose.yaml docs README.md README_EN.md
```

Expected: no obsolete production import/path; permitted historical Celery task-name strings are documented exceptions.

- [ ] **Step 2: Run every static and contract gate**

```bash
git diff --check
find scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n
backend/.venv/bin/python scripts/architecture/check_module_catalog.py
backend/.venv/bin/python scripts/docs/check.py
cd backend
.venv/bin/python -m compileall -q app
.venv/bin/ruff check app tests ../scripts
.venv/bin/pytest --collect-only -q
.venv/bin/pytest -q
.venv/bin/alembic check
cd ..
docker compose config --quiet
bash infra/verification/verify.sh
```

Expected: all non-external gates pass, test collection is at least 1010, HTTP/table/task snapshots match.

- [ ] **Step 3: Run all Stage gates**

```bash
cd Stages/dwg2dxf && .venv/bin/pytest -q
cd ../dxf2dwg && .venv/bin/pytest -q
cd ../dxf2excel && .venv/bin/pytest -q
cd ../steel_dxf_classifier_v1.1.0 && .venv/bin/pytest -q
cd ../excel_final && .venv/bin/pytest -q multi_split/tests
```

Record exact pass counts; no test file may be removed.

- [ ] **Step 4: Run frontend and browser gates**

```bash
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Record exact pass/skip counts and inspect at least the production workflow, conversion, Excel Final and operations console in a real browser with zero console errors.

- [ ] **Step 5: Verify current runtime safely**

```bash
bash scripts/status.sh
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/health/ready
```

If running source is stale, selectively restart only owned FastAPI/workers after static gates. Do not execute destructive archive/remediation actions against business data.

- [ ] **Step 6: Update evidence and audit requirements one by one**

In `docs/verification/current.md`, record:

```text
repository classification complete
module catalog coverage
HTTP/table/task/frontend route equality
backend and Stage counts
frontend build and Playwright counts
Alembic and MySQL evidence
Compose/infra evidence
current local versus production storage distinction
external/blocked gates with exact reasons
```

- [ ] **Step 7: Final commit and push**

```bash
git add -A
git commit -m "docs: complete repository reorganization evidence"
git status --short --branch
git log --oneline --decorate -20
git push origin main
git fetch origin main --quiet
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
```

Expected: clean `main`, local and remote SHA identical.
