# DWG-Agent Platform -- Architecture Document

> **Target audience:** Senior engineer taking over maintenance or extension of this codebase.
> **Stage:** 1 (platform skeleton). Stages 2-6 are planned but not started.
> **Spec authority:** `DWG-Agent企业平台技术规范.md` (repo root, v1.0, 25 sections, 2455 lines).

---

## 1. System Overview

DWG-Agent is an enterprise CAD intelligent processing platform for internal company use. It accepts DWG drawing uploads, manages projects/drawings/files with full RBAC, and will eventually route natural-language tasks through an LLM Agent to two processing pipelines: a low-precision Linux DXF pipeline (Python/ezdxf) and a high-precision Windows CAD Worker pipeline (C#/ZWCAD API).

At Stage 1, the platform provides a complete RESTful API, authentication/RBAC, project/file/drawing/job lifecycle management, file upload with DWG validation, and audit logging -- everything a user needs to upload and manage DWG files before the actual CAD processing pipelines come online.

```
DWG-Agent Platform (Stage 1)
═══════════════════════════════════════════════════
  User → React SPA → Nginx → FastAPI → MySQL (metadata)
                                    → Local FS (files)
                                    → Redis/Valkey (cache/memory/blasklist)
```

---

## 2. Physical Topology

### 2.1 Target Production Topology (Spec Section 2)

The spec defines a two-node deployment with all Linux services containerized via Docker Compose and a separate Windows node for CAD processing:

```
┌─────────────────────────────────────────────────────────────┐
│                    Ubuntu Main Server                        │
│                                                             │
│  Docker Compose Network                                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Nginx :80/:443                                        │  │
│  │  - React static hosting                               │  │
│  │  - /api/v1/* → backend-api:8000                       │  │
│  │  - rate limiting, upload size caps                    │  │
│  └───────────────────┬───────────────────────────────────┘  │
│                      │                                      │
│  ┌───────────────────▼──────────────────────────────────┐  │
│  │ FastAPI Backend :8000                                 │  │
│  │  - RESTful API, Auth/RBAC                             │  │
│  │  - Project/File/Drawing/Job/Review/Audit              │  │
│  │  - Celery task dispatch                               │  │
│  └───────┬────────────┬──────────────┬──────────────────┘  │
│          │            │              │                      │
│          ▼            ▼              ▼                      │
│  ┌────────────┐ ┌────────────┐ ┌───────────────────────┐   │
│  │ MySQL :3306│ │ Redis:6379 │ │ MinIO :9000            │   │
│  │ metadata   │ │ cache/     │ │ DWG/DXF/result files   │   │
│  │            │ │ memory/     │ │                        │   │
│  │            │ │ progress    │ │                        │   │
│  └────────────┘ └─────┬──────┘ └───────────┬───────────┘   │
│                       │                    │                │
│  ┌────────────────────┼────────────────────┘                │
│  │  Celery Workers    │                                     │
│  │  - worker-agent    │                                     │
│  │  - worker-dxf      │                                     │
│  │  - worker-report   │                                     │
│  │  - worker-cad-dispatch                                    │
│  └────────────────────┘                                     │
└──────────────────────────┼──────────────────────────────────┘
                           │ Internal network (API Key / mTLS)
┌──────────────────────────▼──────────────────────────────────┐
│                 Windows CAD Worker Node                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ C# ASP.NET Core Worker Service                         │  │
│  │  - Polls GET /api/v1/internal/cad-worker/jobs/next    │  │
│  │  - Downloads DWG to local sandbox                     │  │
│  │  - Invokes ZWCAD API / C# plugin                      │  │
│  │  - Exports JSON/PNG/reports                           │  │
│  │  - Uploads results to MinIO                           │  │
│  │  - PATCHes job status back to FastAPI                 │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          ▼                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ZWCAD / CAD .NET API / C# Plugin                      │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Current Development Topology (Stage 1)

The current implementation runs everything on a single Linux development machine. Docker Compose config is written and validated but not used in production yet. The local dev workflow is:

```
Browser (localhost:5173)
  │ Vite dev proxy → localhost:8000
  ▼
FastAPI (localhost:8000)
  │ SQLAlchemy 2.x sync
  ▼
MySQL 8.x (localhost:3306)
  │
Redis/Valkey 9.1 (localhost:6379, systemd)
  │
Celery worker-report (report queue, local pidfile)
  │
Local FS (backend/var/storage/)
```

**Key difference from spec:** Local development still uses local FS by default and has no Windows CAD node. Docker deployment uses MinIO by default and starts `worker-report` for the Stage 1 fake task; Agent/DXF workers and Flower remain behind `workers` / `monitoring` profiles.

---

## 3. Logical Layered Architecture

The codebase follows the six-layer architecture defined in Spec Section 6. Below is every layer, its directory, what it does, what it explicitly does NOT do, and its implementation status.

### 3.1 Layer Map

```
┌──────────────────────────────────────────────────────────────┐
│ 1. API Layer              app/api/v1/         11 modules     │
│    Routes, param parsing, auth deps, response wrapping       │
│    DOES NOT: contain business logic, DB queries, file I/O    │
├──────────────────────────────────────────────────────────────┤
│ 2. Schema Layer           app/schemas/         10 modules    │
│    Pydantic v2 request/response validation                   │
│    DOES NOT: contain business rules, DB access               │
├──────────────────────────────────────────────────────────────┤
│ 3. Service Layer          app/services/        12 modules    │
│    Business logic orchestration, cross-cutting workflows     │
│    DOES NOT: depend on FastAPI Request, do raw SQL           │
├──────────────────────────────────────────────────────────────┤
│ 4. Repository Layer       app/repositories/    placeholder   │
│    DB read/write encapsulation (future extraction)           │
│    DOES NOT: handle business rules (currently n/a)           │
├──────────────────────────────────────────────────────────────┤
│ 5. Model Layer            app/models/          10 modules    │
│    SQLAlchemy 2.x ORM models (17 tables)                     │
│    DOES NOT: contain biz logic, validation (that's schemas)  │
├──────────────────────────────────────────────────────────────┤
│ 6. Core / Infrastructure  app/core/            7 modules     │
│    Config, security, permissions, exceptions, Redis, logging │
│    DOES NOT: contain domain logic                            │
└──────────────────────────────────────────────────────────────┘

Horizontal (cross-cutting):
┌──────────────────────────────────────────────────────────────┐
│ Agent Layer     app/agents/          3 stubs    (Stage 2)    │
│ MCP Layer       app/mcp_client/      2 stubs    (Stage 2)    │
│ Worker Layer    app/workers/         celery_app + report task │
│                                      + agent/dxf/cad stubs    │
│ Storage Layer   app/storage/          local dev + MinIO       │
│                                      Docker backend           │
│ Integration     app/integrations/zwcad/ 2 stubs (Stage 4)   │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Layer Details

#### API Layer -- `app/api/v1/` (11 route modules, 63 under /api/v1 + 1 health = 64 total endpoints)

| Module | Endpoints | Spec Section | Status |
|--------|-----------|-------------|--------|
| `auth_api.py` | POST sessions, DELETE sessions, POST refresh, GET me, PATCH password | 7.5 | Done |
| `users_api.py` | GET/POST users, GET/PATCH/DELETE user, POST/DELETE user roles, password-reset/enable/disable requests | 7.6 | Done |
| `roles_api.py` | GET/POST roles, GET permissions, PUT role permissions | 7.6 | Done |
| `projects_api.py` | GET/POST projects, GET/PATCH/DELETE project, GET/POST members, PATCH/DELETE member | 7.7 | Done |
| `files_api.py` | POST files, GET files, GET/DELETE file, GET download-url, GET download | 7.8 | Done |
| `drawings_api.py` | GET/POST drawings, GET/PATCH/DELETE drawing, GET/POST versions, GET preview | 7.9 | Done |
| `jobs_api.py` | GET/POST jobs, GET job, POST cancel/retry, GET steps/logs/results | 7.10 | Done |
| `agent_runs_api.py` | POST agent-runs, GET agent-run, GET agent-run steps, GET agent-tools | 7.11 | Done (503 gated) |
| `results_api.py` | GET result, GET download-url, POST review, GET review history | 7.12 | Done |
| `reviews_api.py` | GET pending reviews | 7.12 | Done |
| `audit_logs_api.py` | GET audit-logs, GET audit-log | 7.13 | Done |

**DOES:**
- Parse path/query/body parameters via FastAPI dependency injection
- Apply authentication via `CurrentUser` dependency (all business endpoints)
- Apply RBAC checks via `require_roles`, `require_project_member`, etc.
- Wrap all responses in `{data, meta}` or `{error, meta}` envelopes
- Map domain exceptions to HTTP status codes via exception handlers in `main.py`

**DOES NOT:**
- Contain business logic -- delegates to service layer
- Issue raw SQL queries
- Access filesystem directly
- Import `app.models` directly (uses schemas for I/O)

**Router assembly** (`router.py`): All 11 route modules are assembled into a single `api_router` mounted at `/api/v1` in `main.py`.

#### Schema Layer -- `app/schemas/` (10 Pydantic v2 modules)

All schemas use `model_config = ConfigDict(from_attributes=True)` for ORM-mode deserialization.

**DOES:**
- Validate request bodies, query params, and path params
- Define response shapes
- Provide type-safe I/O boundaries between API and service layers

**DOES NOT:**
- Contain business rules
- Access the database
- Perform side effects

#### Service Layer -- `app/services/` (12 modules, ~1100 lines)

| Service | Responsibility | Key Dependencies |
|---------|---------------|-----------------|
| `project_service.py` | Project CRUD, member management, role assignment | `Project`/`ProjectMember` models, `audit_service` |
| `file_service.py` | File metadata management, permission checks | `StoredFile` model |
| `drawing_service.py` | Drawing CRUD, version management (auto-increment version_no) | `Drawing`/`DrawingVersion` models |
| `review_service.py` | Review submission, approval/rejection decisions | `ReviewRecord` model |
| `agent_service.py` | Agent run orchestration (Stage 2 stub) | `AgentRun` model |
| `auth_service.py` | Login with timing-safe user lookup, JWT issuance, token blacklisting | `security.py`, `redis_client`, `User` model |
| `user_service.py` | User CRUD, profile, atomic status transitions, soft delete | `User` model, `audit_service` |
| `job_service.py` | Job creation, Celery stub dispatch, status lifecycle updates | `Job`/`JobStep` models |
| `storage_service.py` | File save/retrieve/delete, DWG header validation, SHA-256 hashing, download URL signing | `path_utils.py`, `file_hash.py`, `StoredFile` model |
| `audit_service.py` | Structured audit trail writes (who, what, resource, before/after, IP, UA) | `AuditLog` model |
| `redis_memory.py` | Agent session memory store (`agent:memory:{session_id}`, JSON list, TTL=7200s, max 20 msgs) | `redis_client` |
| `cache_service.py` | Generic key-value cache (`cache:{namespace}:{key}`, graceful degradation when Redis down) | `redis_client` |

**DOES:**
- Orchestrate business workflows across models, schemas, and external services
- Enforce business rules (status transitions, permission checks at data level)
- Coordinate transactional boundaries

**DOES NOT:**
- Depend on `fastapi.Request` or `fastapi.Response`
- Contain route-level logic (param extraction, HTTP response construction)
- Issue raw SQL (uses SQLAlchemy ORM)

#### Model Layer -- `app/models/` (10 files, 17 tables, 401 lines)

All models inherit from `Base` (SQLAlchemy `DeclarativeBase`) and `TimestampMixin` (provides `created_at`, `updated_at`).

| File | Tables | Spec Section |
|------|--------|-------------|
| `user.py` | `sys_users` | 9.2 |
| `role.py` | `sys_roles`, `sys_permissions`, `sys_user_roles`, `sys_role_permissions` | 8.3, 9.2 |
| `project.py` | `projects`, `project_members` | 9.2 |
| `file.py` | `files` | 9.2 |
| `drawing.py` | `drawings`, `drawing_versions` | 9.2 |
| `job.py` | `jobs`, `job_steps` | 9.2 |
| `result.py` | `analysis_results`, `review_records` | 9.2 |
| `agent_run.py` | `agent_runs`, `agent_run_steps` | 9.2 |
| `audit_log.py` | `audit_logs` | 9.2 |

**DOES:**
- Define table structure, columns, types, constraints, relationships
- Provide ORM-level cascade and lazy-loading configuration

**DOES NOT:**
- Contain business logic
- Define validation rules (that is the schema layer)
- Know about HTTP or API concerns

#### Core Layer -- `app/core/` (7 modules, ~343 lines)

| Module | Responsibility |
|--------|---------------|
| `config.py` | pydantic-settings from `.env`, computed properties for URLs, feature flags |
| `security.py` | Password hashing (Argon2id via `pwdlib`), JWT create/decode (HS256, jti claim) |
| `permissions.py` | Canonical import surface for `app/api/deps` (permission check functions) |
| `exceptions.py` | `AppHTTPException` base + factory functions (`not_found`, `forbidden`, `service_unavailable`) |
| `redis_client.py` | Lazy-init sync Redis client with hiredis, graceful degradation when unavailable |
| `constants.py` | File size limits, allowed extensions, user status constants |
| `logger.py` | Logging configuration helpers |

**DOES:**
- Provide infrastructure that every other layer depends on
- Centralize configuration, security primitives, error types

**DOES NOT:**
- Contain domain logic
- Know about request/response shapes

#### Agent Layer -- `app/agents/` (3 files, all stubs)

All three files (`agent_factory.py`, `prompts.py`, `tool_registry.py`) are single-line docstring stubs. Target: Stage 2.

Spec reference: Section 11 (Agent technical spec). When implemented:
- `agent_factory.py` will create LangGraph `create_react_agent` with `ChatOpenAI` model
- `prompts.py` will contain the system prompt for CAD task decomposition
- `tool_registry.py` will adapt MCP tools to LangChain tool format

Feature flag: `AGENT_ENABLED` (Currently `false` -- `/api/v1/agent-runs` returns 503)

#### MCP Layer -- `app/mcp_client/` (2 files, all stubs)

Spec reference: Section 12 (MCP tool layer). When implemented:
- `cad_mcp_client.py` will manage stdio MCP connections to CAD tool servers (`connect()`, `disconnect()`, `list_tools()`, `call_tool()`)
- `mcp_tool_adapter.py` will convert MCP tool definitions to LangChain-compatible callables

Critical design constraint from spec (Section 11.4): MCP connection failure must NOT crash the service; tools unavailable returns 503 on agent-runs.

#### Worker Layer -- `app/workers/` (5 files)

`celery_app.py` defines the real Celery application using Redis broker/result backend from config. `tasks_report.py` registers the Stage 1 `run_stub_job` task used by normal job creation. `tasks_agent.py`, `tasks_dxf.py`, and `tasks_cad.py` remain placeholders because concrete Agent/DXF/CAD processing is intentionally deferred.

Spec reference: Section 13 (Celery design). At Stage 1, jobs are asynchronous Celery fake tasks; the fake task only proves dispatch, status transitions, result file creation, and audit/review plumbing.

Future stages will add real work to the `agent`, `dxf`, and `cad` queues while preserving the same task state machine.

#### Storage Layer -- `app/storage/` (3 files)

`base.py` defines `AbstractStorageBackend`; `local_storage.py` implements filesystem storage for local development; `minio_storage.py` implements the S3-compatible MinIO backend used by Docker deployment. `storage_service.py` performs DWG validation, hashing, and metadata creation, then writes bytes through the selected backend.

Spec reference: Section 10 (file storage). Four buckets defined: `dwg-original`, `dwg-derived`, `dwg-reports`, `dwg-temp`.

#### Integration Layer -- `app/integrations/zwcad/` (2 files, all stubs)

Both `client.py` and `schemas.py` are single-line stubs. Target: Stage 4.

Spec reference: Section 15 (ZWCAD high-precision pipeline). When implemented, this layer will communicate with the Windows CAD Worker node via internal HTTP API with API Key or mTLS authentication.

#### Repository Layer -- `app/repositories/` (empty `__init__.py`)

Designated placeholder for future extraction of DB read/write patterns from services. At Stage 1, services access models directly via SQLAlchemy session.

---

## 4. Component Dependency Diagram

```
                    ┌──────────────────┐
                    │   FastAPI main.py │
                    │  (lifespan, CORS, │
                    │   error handlers) │
                    └────────┬─────────┘
                             │ mounts
                    ┌────────▼─────────┐
                    │  api/v1/router.py │
                    │  (11 route mods)  │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
       │  schemas/   │ │ api/deps │ │  services/  │
       │ (Pydantic)  │ │(auth,    │ │ (business   │
       │             │ │ RBAC)    │ │  logic)     │
       └─────────────┘ └────┬─────┘ └──────┬──────┘
                            │              │
                            │       ┌──────┼──────┐
                            │       │      │      │
                     ┌──────▼───────▼─┐ ┌──▼──────▼──┐
                     │   models/      │ │  core/     │
                     │ (SQLAlchemy)   │ │ (config,   │
                     │ 17 tables      │ │  security, │
                     └───────┬────────┘ │  redis,    │
                             │          │  exceptions│
                     ┌───────▼────────┐ └──────┬─────┘
                     │    MySQL 8.x   │        │
                     │  (runtime DB)  │  ┌─────▼──────┐
                     └────────────────┘  │ Valkey 9.1 │
                                         │  (Redis)   │
                                         └────────────┘

Future (Stage 2-4) additions below this line:
- - - - - - - - - - - - - - - - - - - - - - - -
┌──────────┐  ┌──────────┐  ┌─────────────────┐
│ agents/  │  │mcp_client│  │ workers/        │
│ (LangGr) │──│/ (MCP)   │  │ (Celery real,   │
│          │  │          │  │ tasks Stage 2+) │
└──────────┘  └──────────┘  └─────────────────┘
                                   │
┌──────────────────────────────────┼──────────┐
│ storage/ (local dev,             │          │
│   MinIO Docker backend)          │          │
│ integrations/zwcad/ (C# Worker)  │          │
└──────────────────────────────────────────────┘
```

### Dependency Rules

1. **API → Schemas → Services → Models → DB** (strict top-down call chain)
2. **Core is omnipresent** -- every layer may import from `app.core.*`
3. **Schemas never import models directly** -- they define shapes, not queries
4. **Services never import FastAPI Request/Response** -- they return domain objects
5. **Worker tasks call Services** -- they never duplicate business logic (spec Section 6.2.4)
6. **Agent code** has no direct DB or filesystem access (spec Section 6.2.5)

---

## 5. Data Flow

### 5.1 Request Lifecycle (Stage 1)

```
HTTP Request
  │
  ▼
Nginx (future) / Vite proxy (dev)
  │
  ▼
FastAPI main.py
  ├── X-Request-ID middleware (generates or passes through)
  ├── CORS middleware
  ▼
api/v1/router.py
  ├── Route matching (/api/v1/{resource})
  ▼
Route handler
  ├── FastAPI param parsing (path/query/body → Pydantic schema)
  ├── Auth dependency (CurrentUser → JWT verify → DB user lookup)
  ├── RBAC dependency (role check / project membership check)
  ▼
Service layer
  ├── Business logic orchestration
  ├── DB queries via SQLAlchemy session (injected by get_db dependency)
  ├── Audit log writes (audit_service)
  ▼
Response construction
  ├── Success: {"data": {...}, "meta": {"request_id": ..., "timestamp": ...}}
  ├── List: adds {"pagination": {"page": ..., "page_size": ..., "total": ...}}
  ├── Error: {"error": {"code": ..., "message": ..., "details": {}}, "meta": {...}}
  ▼
Exception handlers (main.py)
  ├── AppHTTPException → structured error response
  ├── StarletteHTTPException → generic HTTP error
  ├── RequestValidationError → 422 with field-level errors
  └── Exception → 500 (logs traceback, never leaks to client unless DEBUG=true)
```

### 5.2 Authentication Flow

```
POST /api/v1/auth/sessions {username, password}
  │
  ▼
auth_service.authenticate_user(db, username, password)
  ├── SELECT User WHERE username = ?
  ├── User not found or status != active:
  │   └── Argon2id verify against DUMMY_HASH (constant-time, prevents timing oracle)
  │       → return None → 401
  ├── User found + active:
  │   ├── Argon2id verify(password, stored_hash)
  │   ├── Match: update last_login_at, return user
  │   └── No match: return None → 401
  ▼
build_login_token(user) + build_refresh_token(user)
  ├── JWT HS256, sub=user.id, jti=UUID4, exp=now+30min (access) / +14d (refresh)
  ▼
Response: {access_token, refresh_token, token_type, expires_in, user}
```

### 5.3 Logout / Token Blacklisting

```
DELETE /api/v1/auth/sessions/current
  │
  ▼
Extract jti from current access token (decode without verification)
  │
  ▼
Redis SETEX "blacklist:jti:{jti}" TTL=(exp - now) value="1"
  ├── TTL matches token's remaining lifetime → keys self-clean
  ├── Redis unavailable → log warning, skip (degraded mode)
  ▼
auth dependency checks is_token_blacklisted(jti) on every authenticated request
  ├── Redis hit → 401
  └── Redis miss / unavailable → allow (fail-open for availability)
```

### 5.4 File Upload Flow

```
POST /api/v1/files (multipart/form-data, file field)
  │
  ▼
Route handler
  ├── Auth: CurrentUser
  ├── Validate: file extension (.dwg)
  ├── Validate: file size (max_upload_size_mb setting)
  ▼
storage_service.save_uploaded_file(db, user, file)
  ├── Compute SHA-256 hash
  ├── Read first 6 bytes → validate DWG magic header (AC1012–AC1032)
  ├── Validate minimum 1024 bytes
  ├── ensure_within_root(storage_root, target_path) → path traversal guard
  ├── Write file through StorageBackend (local dev / MinIO Docker)
  ├── INSERT INTO files (bucket, storage_key, original_name, sha256, size, ...)
  ├── Write audit log (FILE_UPLOADED)
  ▼
Response: {data: {id, original_name, file_ext, size_bytes, sha256, storage_key, status}, meta: ...}
```

### 5.5 Download URL Flow

```
GET /api/v1/files/{file_id}/download-url
  │
  ▼
Auth + file ownership / project membership check
  │
  ▼
storage_service.generate_download_url(file)
  ├── HMAC-SHA256(file_id + exp_timestamp, secret) → signature
  ├── URL = /api/v1/files/{file_id}/download?exp={ts}&sig={hex}
  ├── TTL = 300 seconds
  ▼
GET /api/v1/files/{file_id}/download?exp={ts}&sig={hex}
  ├── Verify exp not expired
  ├── Recompute HMAC → compare with sig (constant-time)
  └── Stream file with Content-Disposition header
```

### 5.6 Job Lifecycle (Stage 1 -- Celery Fake Task)

```
POST /api/v1/jobs {drawing_id, task_type, precision_level, params}
  │
  ▼
job_service.create_job(db, user, data)
  ├── Validate drawing exists and user has project access
  ├── INSERT INTO jobs (status="queued", ...)
  ├── Write audit log (JOB_CREATED)
  ▼
job_service.enqueue_stub_job(job_id)
  │
  ▼
Celery worker-report consumes app.workers.tasks_report.run_stub_job
  ├── status → "running"
  ├── INSERT INTO job_steps (step_name="dispatch_stub_worker")
  ├── Write JSON result file via StorageBackend (local or MinIO)
  ├── INSERT INTO analysis_results
  ├── INSERT INTO job_steps (step_name="write_stub_result")
  └── status → "succeeded"
```

The task body is intentionally fake; Agent/DXF/CAD processing stays deferred.

---

## 6. Key Architectural Decisions

### 6.1 Synchronous API + Celery Worker Boundary

**Decision:** FastAPI request handlers use SQLAlchemy 2.x sync sessions and sync Redis clients. Job execution crosses an explicit Celery boundary and runs in worker processes.

**Why:** API operations stay short-lived and simple, while even the Stage 1 fake job follows the production task-dispatch shape. This keeps request latency bounded and avoids running long CAD work inside FastAPI.

**Trade-off:** Under very high concurrency (>200 req/s) the sync model will need more gunicorn workers. The spec's Docker Compose config uses `--workers 4` with `--timeout 120`. For now this exceeds Stage 1 needs.

### 6.2 MySQL Runtime + SQLite Test Isolation

**Decision:** Runtime uses MySQL 8.x via `mysql+pymysql://`. Tests use in-memory SQLite via `StaticPool`.

**Why MySQL for runtime:**
- Spec Section 4 mandates MySQL 8.x for production
- Enterprise environment: existing MySQL ops knowledge, backup tooling, monitoring
- Features needed: row-level locking (`SELECT FOR UPDATE`), proper concurrency, connection pooling

**Why SQLite for tests:**
- Zero setup -- no external MySQL server dependency for CI/dev
- `StaticPool` ensures full isolation (each test gets its own in-memory DB)
- WAL mode + `foreign_keys=ON` + `busy_timeout=5000` applied per-connection
- 350 tests run with fast collection and execution

**MySQL connection pool:** `pool_recycle=3600` (recycle before MySQL's default `wait_timeout` of 28800s), `pool_size=10`, `max_overflow=20`. Applied only when `database_url` starts with `mysql`.

### 6.3 Timing Oracle Defense

**Decision:** When a user does not exist or is inactive, `authenticate_user()` still performs an Argon2id hash verification against a pre-computed dummy hash with identical parameters (m=65536, t=3, p=4).

```python
_DUMMY_VERIFY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHRzb21lc2FsdHNhbHQ$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
```

**Why:** Without this, a non-existent user returns immediately (fast), while a valid user triggers Argon2id verification (slow). An attacker can measure response time differences to enumerate valid usernames. The dummy hash burns equivalent CPU time regardless.

### 6.4 JWT with jti-Based Token Blacklisting

**Decision:** JWTs include a `jti` (JWT ID) claim (UUID4). On logout, the `jti` is stored in Redis with TTL matching the token's remaining lifetime. The auth dependency checks the blacklist on every request.

**Why:** JWTs are stateless by design -- you cannot "revoke" them without a blacklist. The alternative (short-lived tokens + frequent refresh) creates a worse UX. jti-based blacklisting with Redis TTL gives us:
- Immediate logout (next request rejects the token)
- No permanent storage growth (keys auto-expire with TTL matching token expiry)
- Graceful degradation (Redis down = fail-open, tokens still work)

**Trade-off:** Every authenticated request does a Redis `EXISTS` call. This adds ~0.1ms latency. Acceptable for an internal enterprise platform.

### 6.5 Atomic Status Transitions

**Decision:** User status changes (active ↔ disabled) use `UPDATE WHERE + rowcount` instead of read-modify-write:

```python
def transition_user_status(db, user_id, new_status):
    result = db.execute(
        update(User).where(User.id == user_id, User.status != new_status)
        .values(status=new_status)
    )
    if result.rowcount == 0:
        raise ConflictError("Status already transitioned or user not found")
```

**Why:** Prevents race conditions where two admins simultaneously toggle a user's status. `rowcount == 0` means someone else already made the change (or the user was deleted). This is a common pattern in optimistic concurrency control.

### 6.6 SELECT FOR UPDATE for Write Protection

**Decision:** `get_user_or_404(db, user_id, for_update=True)` uses `SELECT ... FOR UPDATE` to lock the row within the current transaction.

**Why:** Prevents concurrent writes to the same user row (e.g., role assignment + profile update racing). The `FOR UPDATE` clause acquires a row-level exclusive lock in MySQL that is released at transaction commit.

### 6.7 Cascading Project Status Check

**Decision:** `require_active_project()` is embedded inside `require_project_member()`, not called independently in each route. When a project is deleted/archived, all member-based access automatically returns 404.

**Why:** Every resource (drawings, files, jobs) is scoped to a project. Checking project status once in the membership dependency means no route can accidentally forget to check. It also means deleted projects cascade-clean all downstream access without code changes.

### 6.8 File Security

**Path traversal protection:** `ensure_within_root(root, candidate)` resolves both paths and verifies the candidate starts with the root prefix. Any `../` or symlink escape raises a 400 error.

**DWG header validation:** Upload validation reads the first 6 bytes and checks for DWG magic bytes (`AC1012` through `AC1032`). Minimum 1024-byte file size enforced.

**HMAC-signed download URLs:** Download endpoints are time-limited (TTL=300s) with HMAC-SHA256 signatures that prevent URL tampering.

**Why:** These are spec requirements (Sections 10.4, 19.3). DWG files from external sources are untrusted input. Path traversal, file type spoofing, and direct file access must all be blocked at the platform boundary.

### 6.9 Feature Flags for Staged Rollout

**Decision:** Three boolean feature flags in `Settings`: `agent_enabled`, `dxf_pipeline_enabled`, `cad_worker_enabled`. All default to `False`.

**Why:** The spec defines a 6-stage rollout. Feature flags let us merge code to main while keeping it dark, test individual subsystems independently, and do canary rollouts. At Stage 1, `agent_enabled=false` returns 503 from `/api/v1/agent-runs` with a clear error message (`AGENT_NOT_AVAILABLE`).

### 6.10 Config from Component Fields, Not Monolithic URLs

**Decision:** Config uses component fields (`mysql_host`, `mysql_port`, `mysql_database`, `mysql_user`, `mysql_password`) with computed `mysql_url` and `redis_url` properties, rather than a single `DATABASE_URL` string.

**Why:** Spec Section 18 defines this pattern. It enables:
- Docker override per-component (e.g., `MYSQL_HOST=mysql` in Docker, `127.0.0.1` in dev)
- URL-encoding of special characters in passwords (via `urllib.parse.quote`)
- Clear separation of concerns in `.env` files
- Programmatic assembly of Celery broker/result URLs from the same Redis components

---

## 7. RBAC Model

### 7.1 Schema (5 tables)

```
sys_users ──< sys_user_roles >── sys_roles ──< sys_role_permissions >── sys_permissions
     │
     └── projects ──< project_members >── sys_users (project_role: owner/engineer/reviewer/viewer)
```

### 7.2 Global Roles (7)

| Role | Scope | Key Permissions |
|------|-------|----------------|
| `super_admin` | Global | All permissions bypass -- no permission check needed |
| `admin` | Global | Manage users, projects, view all jobs |
| `engineer` | Global + Project | Upload files, create jobs, view project results |
| `reviewer` | Global + Project | View pending reviews, approve/reject results |
| `operator` | Project | Execute assigned tasks |
| `viewer` | Project | Read-only access within projects |
| `auditor` | Global | Read audit logs only |

### 7.3 Permission Hierarchy (Spec Section 8.3)

```
Is authenticated?
  → No → 401
  → Yes
Is user enabled (status = 'active')?
  → No → 403
  → Yes
Does user have super_admin role?
  → Yes → ALLOW ALL
  → No
Does user have global admin role for this resource type?
  → Yes → ALLOW
  → No
Is user a member of the target project?
  → No → check global roles only (may still have access)
  → Yes
Does user's project role permit this action?
  → Yes → ALLOW
  → No → 403
```

**Implementation note:** `super_admin` bypasses all permission checks. The `require_roles()` dependency checks for `super_admin` first (short-circuit). Project-level checks happen via `require_project_member()` which also embeds `require_active_project()`.

---

## 8. Database -- Physical Schema

### 8.1 Engine Configuration

```python
# MySQL runtime
engine = create_engine(
    settings.database_url,  # mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent
    pool_pre_ping=True,     # verify connections before use
    pool_recycle=3600,      # recycle before MySQL wait_timeout
    pool_size=10,
    max_overflow=20,
)

# SQLite test (per-test via conftest.py)
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
# Per-connection pragmas: WAL mode, foreign_keys=ON, busy_timeout=5000
```

### 8.2 Session Factory

```python
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,       # manual flush control
    autocommit=False,      # explicit commit
    expire_on_commit=False # avoid lazy-load after commit
)

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 8.3 All 17 Tables

| # | Table | Primary Keys | Foreign Keys | Notes |
|---|-------|-------------|--------------|-------|
| 1 | `sys_users` | id | -- | username UNIQUE, password_hash, status, soft-delete |
| 2 | `sys_roles` | id | -- | code UNIQUE, is_system flag |
| 3 | `sys_permissions` | id | -- | code UNIQUE, resource + action |
| 4 | `sys_user_roles` | (user_id, role_id) | users.id, roles.id | M2M join |
| 5 | `sys_role_permissions` | (role_id, permission_id) | roles.id, permissions.id | M2M join |
| 6 | `projects` | id | owner_id → users.id | code UNIQUE, status |
| 7 | `project_members` | id | project_id → projects.id, user_id → users.id | project_role enum |
| 8 | `files` | id | uploaded_by → users.id | storage_key, sha256, status |
| 9 | `drawings` | id | project_id → projects.id | current_version_id self-ref |
| 10 | `drawing_versions` | id | drawing_id → drawings.id, file_id → files.id, created_by → users.id | version_no |
| 11 | `jobs` | id | project_id, drawing_id, created_by | task_type, precision_level, pipeline, status, params_json |
| 12 | `job_steps` | id | job_id → jobs.id | step_name, worker_name, status, input/output_json |
| 13 | `agent_runs` | id | user_id, project_id, drawing_id, file_id | session_id, task, status, answer, output_file_id |
| 14 | `agent_run_steps` | id | agent_run_id → agent_runs.id | step_type, tool_name, arguments_json, status |
| 15 | `analysis_results` | id | job_id, drawing_id | result_type, result_json, confidence, result_file_id |
| 16 | `review_records` | id | result_id → analysis_results.id, reviewer_id → users.id | decision (approved/rejected), comment |
| 17 | `audit_logs` | id | actor_user_id → users.id | action, resource_type, resource_id, before/after_json, ip_address |

Tables 1-12 are active in Stage 1. Tables 13-14 (agent_runs, agent_run_steps) are created and queryable but only written to in Stage 2. Tables 15-16 (analysis_results, review_records) are created and partially used (simulated job results write to analysis_results; review records are functional).

### 8.4 Migrations

Two Alembic versions in `backend/migrations/versions/`:

1. `40452ddd24e7` -- **initial**: Creates all 17 tables with columns, constraints, indexes
2. `b8f9e7d6c5a4` -- **add_missing_timestamp_columns**: Backfills `created_at`/`updated_at` for tables that missed the `TimestampMixin` in the initial migration

Alembic targets MySQL: `sqlalchemy.url = mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent`

---

## 9. Redis / Valkey Infrastructure

### 9.1 Server

Valkey 9.1 (Redis-compatible fork), running locally via systemd as `redis.service`. No password for local development. Docker deployment uses `ghcr.io/valkey-io/valkey:9.0-alpine` with `requirepass`.

### 9.2 Client (`app/core/redis_client.py`)

- Sync `redis-py` 5.x with `hiredis` parser for performance
- Lazy initialization: `get_redis()` creates the connection pool on first call
- Returns `None` instead of crashing when Redis is unavailable (all callers handle this)
- `close_redis()` called during FastAPI shutdown (lifespan)

### 9.3 Usage Patterns

| Service | Key Pattern | Data | TTL | Stage |
|---------|-----------|------|-----|-------|
| Token blacklist | `blacklist:jti:{jti}` | "1" | remaining token lifetime | 1 (active) |
| Agent memory | `agent:memory:{session_id}` | JSON list of messages | 7200s | 1 (infra only) |
| Cache | `cache:{namespace}:{key}` | arbitrary | variable | 1 (infra only) |
| Celery broker | `redis://.../0` | task messages | -- | 2+ |
| Celery results | `redis://.../1` | task results | -- | 2+ |

### 9.4 Testing Strategy

Dual-layer Redis testing:
1. **FakeRedis** (`fakeredis[lua]`): Autouse fixture in `conftest.py` monkeypatches `get_redis()` to return a `FakeRedis` instance. This covers 337 non-real-Redis tests (350 total - 13 real-Redis-only) with zero external dependency.
2. **Real Redis** (`test_redis_real.py`): Integration tests against the actual local Valkey instance. Auto-skipped (`pytest.skip`) when Redis is unreachable.

---

## 10. Storage Architecture

### 10.1 Current (Stage 1)

Files are stored through `AbstractStorageBackend`. Local development defaults to `LocalFileStorage` under `backend/var/storage/`; Docker deployment defaults to `MinioStorage` at `http://minio:9000`. The `storage_service.py` handles:
- File save (write bytes through selected backend)
- File retrieval (local `FileResponse` or MinIO streaming response)
- File deletion (remove file, soft-delete DB record)
- Download URL generation (HMAC-signed, 300s TTL)

### 10.2 Storage Backends

The `app/storage/` directory contains:

```
app/storage/
├── base.py           # AbstractStorageBackend ABC + storage exceptions
├── local_storage.py  # LocalFileStorage
└── minio_storage.py  # MinioStorage (S3-compatible)
```

The storage backend is selected via `STORAGE_BACKEND=local|minio`. MinIO uses four buckets per Spec Section 10.2:
- `dwg-original` -- raw DWG uploads (never overwritten)
- `dwg-derived` -- DXF, JSON, PNG, SVG derivatives
- `dwg-reports` -- Excel, PDF, ZIP reports
- `dwg-temp` -- temporary worker sandbox files (auto-cleaned)

---

## 11. API Design Conventions

### 11.1 URL Structure

```
/api/v1/{resource}                    # collection
/api/v1/{resource}/{id}               # individual resource
/api/v1/{resource}/{id}/{subresource} # nested subresource
```

### 11.2 Response Envelope

All responses follow a consistent envelope:

**Success (single):**
```json
{"data": {...}, "meta": {"request_id": "...", "timestamp": "..."}}
```

**Success (list):**
```json
{"data": [...], "pagination": {"page": 1, "page_size": 20, "total": 120}, "meta": {...}}
```

**Error:**
```json
{"error": {"code": "ERROR_CODE", "message": "Human-readable", "details": {...}}, "meta": {...}}
```

### 11.3 HTTP Status Code Usage

| Code | Usage |
|------|-------|
| 200 | Successful read or update |
| 201 | Resource created (POST that returns the created entity) |
| 202 | Accepted for async processing (job submit, agent run) |
| 204 | Success with no body (delete, logout) |
| 400 | Client semantic error (invalid parameter combination) |
| 401 | Not authenticated (missing/invalid/expired/blacklisted token) |
| 403 | Authenticated but not authorized |
| 404 | Resource not found |
| 409 | Conflict (duplicate username, invalid state transition) |
| 413 | Upload exceeds size limit |
| 415 | Unsupported file type |
| 422 | Pydantic validation failure |
| 429 | Rate limited (login failures) |
| 500 | Unhandled server error |
| 503 | Dependency unavailable (agent disabled, MCP down, CAD Worker unreachable) |

### 11.4 Error Code Convention

Error codes are `UPPER_SNAKE_CASE` strings that are machine-parseable and stable. Examples: `NOT_FOUND`, `FORBIDDEN`, `FILE_TYPE_NOT_ALLOWED`, `AGENT_NOT_AVAILABLE`, `INVALID_STORAGE_PATH`. Frontend code can switch on `error.code` without parsing `error.message`.

---

## 12. Testing Architecture

### 12.1 Test Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Runner | pytest | Test discovery and execution |
| HTTP client | `fastapi.testclient.TestClient` | In-process API testing |
| DB isolation | SQLite `:memory:` + `StaticPool` | Per-test isolated database |
| Redis isolation | `fakeredis[lua]` autouse monkeypatch | Zero-dependency Redis simulation |
| Redis integration | Real Valkey 9.1 local instance | Integration safety net (`test_redis_real.py`) |
| Fixtures | `conftest.py` | DB setup/teardown, auth headers, test data factories |

### 12.2 Test Categories (21 files, 350 tests)

| Category | Files | Focus |
|----------|-------|-------|
| API regression | `test_api_regressions.py` | All 64 endpoints return correct status codes and shapes |
| Security boundaries | `test_security_boundaries.py` | Auth required, RBAC enforcement, path traversal defense |
| Token lifecycle | `test_token_lifecycle.py` | Login, refresh, blacklist, expiry, jti validation |
| Redis stack | `test_redis_client.py`, `test_redis_memory.py`, `test_cache_service.py`, `test_redis_real.py` | Client init, memory TTL, cache fallback, real integration |
| Config | `test_config.py` | MySQL/Redis URL assembly, component fields, feature flags |
| DB session | `test_db_session.py` | Engine creation, health check, WAL pragmas |
| Edge cases | `test_edge_cases.py`, `test_rigorous.py`, `test_deep_verify.py` | Concurrent ops, large payloads, Unicode, null handling |
| Stage 1 boundaries | `test_stage1_boundaries.py` | Agent 503, Celery fake task, feature flag gates |
| Flow tests | `test_smoke_flow.py` | End-to-end: register → login → upload → job → result |
| Migration tests | `test_migrations.py` | Alembic version count, table existence |
| Compose tests | `test_compose.py` | YAML parse, service count, required services present |
| Health | `test_health.py` | `/health` endpoint, DB health check function |

### 12.3 Test Isolation Mechanics

```python
# conftest.py (simplified)
@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """Every test gets FakeRedis -- no test talks to real Redis by accident."""
    fake = FakeRedis()
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake)
    monkeypatch.setattr("app.core.redis_client._redis", fake)

@pytest.fixture
def db():
    """Each test gets its own in-memory SQLite database."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    with Session(bind=engine) as session:
        yield session
```

---

## 13. Implementation Status Matrix

### Stage 1 -- Done

| Component | Status | Lines | Tests | Notes |
|-----------|--------|-------|-------|-------|
| FastAPI app (main.py) | Done | 125 | Covered | Lifespan, CORS, X-Request-ID, 4 exception handlers, /health |
| API routes (11 modules) | Done | 1,911 | Covered | All 64 endpoints return proper envelopes |
| Pydantic schemas (10 modules) | Done | 491 | Covered | All use v2 `from_attributes=True` |
| Business services (12 modules) | Done | ~1100 | Covered | Auth, user, job, project, file, drawing, review, agent, storage, audit, redis_memory, cache |
| SQLAlchemy models (17 tables) | Done | 401 | Covered | All with TimestampMixin, relationships, constraints |
| Core infrastructure (7 modules) | Done | ~343 | Covered | Config, security, permissions, exceptions, Redis, logger |
| DB session + pool | Done | -- | Covered | MySQL pool config, SQLite WAL pragmas, health check |
| DB init + seed data | Done | -- | Covered | Super admin, 7 roles, 8 permissions |
| Alembic migrations | Done | 2 | Covered | Initial 17 tables + TimestampMixin backfill |
| Redis/Valkey client | Done | 80 | Covered | Lazy init, graceful degradation, FakeRedis + real |
| Token blacklist | Done | -- | Covered | jti-based, TTL-matched, fail-open |
| File upload + validation | Done | -- | Covered | DWG header, SHA-256, path traversal guard, HMAC URLs |
| Audit logging | Done | 44 | Covered | Structured audit trail writes |
| Docker Compose (9 services) | Done | 236 | Covered | worker-report default, Agent/DXF + monitoring profiles |
| Dockerfile (backend) | Done | -- | Validated | Multi-stage, non-root, HEALTHCHECK, uv sync |
| Nginx config (Docker + local) | Done | -- | Validated | Rate limiting, proxy, static serving |
| Frontend (React 19 + TS + Vite) | Done | -- | Manual | 10 page features, 12 API clients, auth store, router |
| 350 tests | Done | -- | -- | 21 test files, all passing |

### Stage 2 -- Not Started (Agent, MCP, Real CAD Processing)

| Component | Status | Lines | Notes |
|-----------|--------|-------|-------|
| LangGraph agent factory | Stub | 1 | `app/agents/agent_factory.py` |
| System prompts | Stub | 1 | `app/agents/prompts.py` |
| Tool registry | Stub | 1 | `app/agents/tool_registry.py` |
| MCP CAD client | Stub | 1 | `app/mcp_client/cad_mcp_client.py` |
| MCP tool adapter | Stub | 1 | `app/mcp_client/mcp_tool_adapter.py` |
| Celery app | Done | -- | Redis broker/result backend configured |
| Agent tasks | Stub | 1 | `app/workers/tasks_agent.py` |
| DXF tasks | Stub | 1 | `app/workers/tasks_dxf.py` |
| CAD dispatch tasks | Stub | 1 | `app/workers/tasks_cad.py` |
| Report tasks | Stage 1 stub | -- | `run_stub_job` creates fake result files |
| Agent runs API | Real (503) | 90 | Returns 503 when `AGENT_ENABLED=false` |
| Redis memory runtime | Infra only | 78 | Validated by tests, not called in request path |
| Cache runtime | Infra only | 84 | Validated by tests, not called in request path |

### Stage 3 -- Not Started (DXF Pipeline)

| Component | Status | Notes |
|-----------|--------|-------|
| DWG→DXF converter | Not started | Abstract layer, expected to use ODA File Converter or LibreDWG |
| ezdxf parsing worker | Not started | Layer/text/block/geometry extraction |
| entities.json output | Not started | Structured JSON per spec Section 14.5 |
| Low confidence → review | Not started | Auto-flag results with confidence < 0.85 |

### Stage 4 -- Not Started (Windows CAD Worker)

| Component | Status | Notes |
|-----------|--------|-------|
| ASP.NET Core Worker Service | Not started | Task polling, sandbox management |
| ZWCAD API plugin (C#) | Not started | Layer/text/dimension/block extraction |
| ZWCAD client | Stub | `app/integrations/zwcad/client.py` |
| ZWCAD schemas | Stub | `app/integrations/zwcad/schemas.py` |
| CAD Worker safety | Not started | Process crash recovery, license check, sandbox per task |

### Stage 5-6 -- Not Started

Business algorithms (LaR, material takeoff, batch processing), production hardening (RabbitMQ, Prometheus, Grafana, Loki, CI/CD, multi-node scaling).

---

## 14. Feature Flag Inventory

All flags are in `app/core/config.py` / `.env`:

| Flag | Default | Stage | Effect When False |
|------|---------|-------|--------------------|
| `AGENT_ENABLED` | `false` | 2 | `POST /api/v1/agent-runs` → 503 `AGENT_NOT_AVAILABLE` |
| `DXF_PIPELINE_ENABLED` | `false` | 3 | DXF-related Celery tasks not processed |
| `CAD_WORKER_ENABLED` | `false` | 4 | CAD Worker endpoints return 503 |
| `DEBUG` | `true` (dev) | All | Controls stack trace in 500 responses; must be `false` in production |

---

## 15. Directory Map (Complete)

```
complete_framework/
├── DWG-Agent企业平台技术规范.md          ← Spec v1.0 (2455 lines, 25 sections)
├── README.md
├── .env.example                          ← Local dev env template (tracked)
├── .env.docker.example                   ← Docker env template (tracked)
├── compose.yaml                          ← 9 services, 3 volumes, 2 networks
├── CLAUDE.md                             ← Agent instructions for this repo
│
├── backend/                              ← Python 3.12, uv, FastAPI
│   ├── pyproject.toml                    ← Dependencies + ruff config
│   ├── uv.lock                           ← Locked deps (COMMITTED)
│   ├── .python-version                   ← 3.12
│   ├── Dockerfile                        ← Multi-stage, non-root
│   ├── .dockerignore
│   ├── alembic.ini                       ← Targets MySQL
│   ├── migrations/versions/              ← 2 Alembic versions
│   ├── tests/                            ← 21 files, 350 tests
│   │   └── conftest.py                   ← FakeRedis autouse + SQLite isolation
│   ├── var/storage/                      ← Runtime file storage (gitignored)
│   └── app/
│       ├── main.py                       ← FastAPI app, lifespan, middleware
│       ├── api/v1/                       ← 11 route modules
│       │   └── router.py                 ← Central router assembly
│       ├── schemas/                      ← 10 Pydantic v2 modules
│       ├── services/                     ← 12 business logic modules
│       ├── models/                       ← 10 ORM model files (17 tables)
│       ├── core/                         ← 7 infrastructure modules
│       ├── db/                           ← session, base, init_db
│       ├── utils/                        ← path_utils, file_hash, time_utils
│       ├── agents/                       ← 3 stubs (Stage 2)
│       ├── mcp_client/                   ← 2 stubs (Stage 2)
│       ├── workers/                      ← celery_app (real) + 4 task modules (1 Stage 1 stub, 3 Stage 2 stubs)
│       ├── storage/                      ← 3 files (base + local dev + MinIO deploy backend)
│       ├── integrations/zwcad/           ← 2 stubs (Stage 4)
│       └── repositories/                 ← Empty placeholder
│
├── frontend/                             ← React 19 + TypeScript + Vite
│   ├── package.json                      ← All deps pinned
│   └── src/
│       ├── api/                          ← 12 API client modules
│       ├── features/                     ← 10 page modules
│       ├── components/                   ← 8 shared components (2 real, 6 stubs)
│       ├── stores/                       ← Zustand auth store
│       ├── types/                        ← 9 TypeScript type files
│       └── app/                          ← Router, layout, providers
│
├── docs/                                 ← 7 documentation files
├── infra/                                ← Deployment configs
│   ├── nginx/                            ← Docker + local dev configs
│   ├── mysql/init.sql                    ← DB + user creation
│   ├── redis/redis.conf                  ← AOF, LRU, maxmemory
│   ├── minio/                            ← Placeholder
│   └── verify.sh                         ← Deployment verification
├── scripts/                              ← 6 dev/ops shell scripts
├── agents/                               ← Placeholder (future Agent defs)
└── cad-worker/                           ← Placeholder (future Windows C# worker)
```

---

## 16. Extension Guide -- What Touches What

### Adding a new API endpoint

1. Define Pydantic schemas in `app/schemas/` (request + response)
2. Implement business logic in `app/services/` (if new domain, create new service)
3. Create route module in `app/api/v1/` (if new resource, create new file)
4. Register in `app/api/v1/router.py`
5. Write tests in `backend/tests/`

### Adding a new database table

1. Define SQLAlchemy model in `app/models/` (use `TimestampMixin`)
2. Generate Alembic migration: `cd backend && uv run alembic revision --autogenerate -m "description"`
3. Apply migration: `uv run alembic upgrade head`
4. Add Pydantic schemas for the new resource
5. Update `app/models/__init__.py` to export the new model

### Enabling the Agent (Stage 2)

1. Implement `app/agents/agent_factory.py` (LangGraph `create_react_agent`)
2. Implement `app/agents/prompts.py` (system prompt)
3. Implement `app/agents/tool_registry.py` (MCP-to-LangChain adapter)
4. Implement `app/mcp_client/cad_mcp_client.py` and `mcp_tool_adapter.py`
5. Add Agent task implementation on top of the existing Celery app
6. Start Redis and the relevant Celery worker queues
7. Set `AGENT_ENABLED=true` in `.env`

### Switching storage to MinIO

1. Set `STORAGE_BACKEND=minio`, configure MinIO endpoint + credentials
3. Start MinIO container: `docker compose up minio -d`
4. Run migration to backfill storage keys if migrating existing files

---

*Document version: 2.0 -- last updated 2026-07-03*
*Corresponds to codebase at Stage 1 completion (350 tests, 64 endpoints, 17 tables)*
