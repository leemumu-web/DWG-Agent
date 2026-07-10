# DWG-Agent Platform -- Architecture Document

> **Target audience:** Senior engineer taking over maintenance or extension of this codebase.
> **Stage:** 1 complete (platform skeleton). Stage 3 pipelines (DWG→DXF, DXF→DWG, DXF→Excel) are fully implemented in code but disabled by default behind feature flags. Stages 2 (Agent) and 4 (Windows CAD Worker) remain stubs; Stages 5/6 are partial.
> **Spec authority:** `DWG-Agent企业平台技术规范.md` (repo root, v2.0, 25 sections, 1296 lines).

---

## 1. System Overview

DWG-Agent is an enterprise CAD intelligent processing platform for internal company use. It accepts DWG/DXF drawing uploads, manages projects/drawings/files with full RBAC, and routes conversion/extraction jobs to three real Linux pipelines — DWG→DXF, DXF→DWG (both ODA File Converter subprocesses), and batch DXF→Excel material-table extraction (pure-Python) — with a high-precision Windows CAD Worker (C#/ZWCAD API) planned for Stage 4 and an LLM Agent for natural-language tasks planned for Stage 2. (Note: the DXF conversion path uses the ODA File Converter, not ezdxf; ezdxf is only an optional parsing dependency.)

At Stage 1, the platform provides a complete RESTful API, authentication/RBAC, project/file/drawing/job lifecycle management, file upload with DWG validation, and audit logging -- everything a user needs to upload and manage DWG files before the actual CAD processing pipelines come online.

```
DWG-Agent Platform (Stage 1)
═══════════════════════════════════════════════════
  User → React SPA → Nginx → FastAPI → MySQL (metadata + runtime state)
                                    → Local FS / MinIO (files)

  FastAPI / Celery workers → MySQL (task queue/results, token revocation,
                                    Agent memory, durable job progress)
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
│          │                           │                      │
│          ▼                           ▼                      │
│  ┌─────────────────────┐   ┌───────────────────────────┐   │
│  │ MySQL :3306          │   │ MinIO :9000               │   │
│  │ metadata + auth      │   │ DWG/DXF/result files      │   │
│  │ queue/results/events │   │                           │   │
│  └──────────┬──────────┘   └─────────────┬─────────────┘   │
│             │                            │                 │
│  ┌──────────┴────────────────────────────┘                 │
│  │  Celery Workers    │                                     │
│  │  - worker-report (default, always on)                     │
│  │  - worker-dxf      (workers profile)                      │
│  │  - worker-dxf2dwg  (workers profile)                      │
│  │  - worker-dxf2excel(workers profile)                      │
│  │  - worker-agent    (workers profile)                      │
│  │  (worker-cad-dispatch reserved for Stage 4, not in compose)│
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
  ├── Application records + durable auth/job/Agent state
  └── Celery SQL queue and result tables
      │
      ▼
Celery worker-report (report queue, local pidfile)
  │
Local FS (backend/var/storage/)
```

**Key difference from spec:** Local development uses local FS by default and has no Windows CAD node. Docker deployment uses MinIO and starts `worker-report` by default; Agent and all three DXF workers are behind the `workers` profile. MySQL is the only runtime database and also backs Celery transport/results.

---

## 3. Logical Layered Architecture

The codebase follows the six-layer architecture defined in Spec Section 6. Below is every layer, its directory, what it does, what it explicitly does NOT do, and its implementation status.

### 3.1 Layer Map

```
┌──────────────────────────────────────────────────────────────┐
│ 1. API Layer              app/api/v1/         12 modules     │
│    Routes, param parsing, auth deps, response wrapping       │
│    DOES NOT: contain business logic, DB queries, file I/O    │
├──────────────────────────────────────────────────────────────┤
│ 2. Schema Layer           app/schemas/         10 modules    │
│    Pydantic v2 request/response validation                   │
│    DOES NOT: contain business rules, DB access               │
├──────────────────────────────────────────────────────────────┤
│ 3. Service Layer          app/services/        17 modules    │
│    Business logic orchestration, cross-cutting workflows     │
│    DOES NOT: depend on FastAPI Request, do raw SQL           │
├──────────────────────────────────────────────────────────────┤
│ 4. Repository Layer       app/repositories/    placeholder   │
│    DB read/write encapsulation (future extraction)           │
│    DOES NOT: handle business rules (currently n/a)           │
├──────────────────────────────────────────────────────────────┤
│ 5. Model Layer            app/models/          12 modules    │
│    SQLAlchemy 2.x ORM models (19 business tables)            │
│    DOES NOT: contain biz logic, validation (that's schemas)  │
├──────────────────────────────────────────────────────────────┤
│ 6. Core / Infrastructure  app/core/            7 modules     │
│    Config, security, permissions, exceptions, logging        │
│    DOES NOT: contain domain logic                            │
└──────────────────────────────────────────────────────────────┘

Horizontal (cross-cutting):
┌──────────────────────────────────────────────────────────────┐
│ Agent Layer     app/agents/          3 stubs    (Stage 2)    │
│ MCP Layer       app/mcp_client/      2 stubs    (Stage 2)    │
│ Worker Layer    app/workers/         celery_app + report/dxf/ │
│                                      dxf2dwg/dxf2excel (real)  │
│                                      + agent/cad stubs         │
│ Storage Layer   app/storage/          local dev + MinIO       │
│                                      Docker backend           │
│ Integration     app/integrations/zwcad/ 2 stubs (Stage 4)   │
│ Engines         Stages/{dwg2dxf,dxf2dwg,dxf2excel} (real)    │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Layer Details

#### API Layer -- `app/api/v1/` (12 route modules, 73 under /api/v1 + 1 root health = 74 total endpoints)

| Module | Endpoints | Spec Section | Status |
|--------|-----------|-------------|--------|
| `auth_api.py` | POST sessions, DELETE sessions, POST refresh, GET me, PATCH password | 7.5 | Done |
| `users_api.py` | GET/POST users, GET/PATCH/DELETE user, PATCH me (self profile), POST/DELETE user roles, password-reset/enable/disable requests | 7.6 | Done |
| `roles_api.py` | GET/POST roles, GET permissions, PUT role permissions | 7.6 | Done |
| `projects_api.py` | GET/POST projects, GET/PATCH/DELETE project, GET/POST members, PATCH/DELETE member | 7.7 | Done |
| `files_api.py` | POST files, POST upload-zip, GET files, GET/DELETE batches, GET batch download-zip, GET excel-preview, GET/DELETE file, GET download-url, GET download, POST bulk-delete, POST download-zip | 7.8 | Done |
| `drawings_api.py` | GET/POST drawings, GET/PATCH/DELETE drawing, GET/POST versions, GET preview | 7.9 | Done |
| `jobs_api.py` | GET/POST jobs, GET job, POST cancellation-requests/retry-requests, GET steps/logs/results, GET events (SSE), POST cancel-all-active | 7.10 | Done |
| `agent_runs_api.py` | POST agent-runs, GET agent-run, GET agent-run steps, GET agent-tools | 7.11 | Done (503 gated) |
| `results_api.py` | GET result, GET download-url, POST/GET reviews | 7.12 | Done |
| `reviews_api.py` | GET pending reviews | 7.12 | Done |
| `audit_logs_api.py` | GET audit-logs, GET audit-log | 7.13 | Done |
| `system_api.py` | GET system/health, GET system/health/oda | 18.2 | Done |

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

**Router assembly** (`router.py`): All 12 route modules are assembled into a single `api_router` mounted at `/api/v1` in `main.py`. Most sub-routers carry a prefix (`/auth`, `/users`, `/projects`, `/files`, `/drawings`, `/jobs`, `/results`, `/reviews`, `/audit-logs`, `/system`); `roles_api` and `agent_runs_api` are mounted WITHOUT an extra prefix (their in-file decorator paths are already full sub-paths, e.g. `/roles`, `/permissions`, `/agent-runs`, `/agent-tools`). The root `GET /health` lives on the app itself, NOT under `/api/v1`.

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

#### Service Layer -- `app/services/` (16 modules)

| Service | Responsibility | Key Dependencies |
|---------|---------------|-----------------|
| `project_service.py` | Project CRUD, member management, role assignment | `Project`/`ProjectMember` models, `audit_service` |
| `file_service.py` | File metadata management, permission checks | `StoredFile` model |
| `drawing_service.py` | Drawing CRUD, version management (auto-increment version_no) | `Drawing`/`DrawingVersion` models |
| `review_service.py` | Review submission, approval/rejection decisions | `ReviewRecord` model |
| `agent_service.py` | Agent run orchestration (Stage 2 stub) | `AgentRun` model |
| `auth_service.py` | Login with timing-safe user lookup, JWT issuance, durable token revocation and password-change invalidation | `security.py`, `TokenBlacklist`, `User` models |
| `user_service.py` | User CRUD, profile, atomic status transitions, soft delete | `User` model, `audit_service` |
| `job_service.py` | Job creation, Celery stub dispatch, status lifecycle updates | `Job`/`JobStep` models |
| `storage_service.py` | File save/retrieve/delete, DWG header validation, SHA-256 hashing, download URL signing | `path_utils.py`, `file_hash.py`, `StoredFile` model |
| `audit_service.py` | Structured audit trail writes (who, what, resource, before/after, IP, UA) | `AuditLog` model |
| `agent_memory.py` | MySQL Agent session history with bounded messages and read-time TTL expiry (Stage 2 infrastructure) | `AgentMemory` model |
| `dxf_service.py` | **DWG→DXF** orchestration: stage source → `dwg_converter.convert_file` (ODA subprocess) → persist DXF to `dxf-derived` → `AnalysisResult` + `job_steps` + SSE events (Real, flag-gated) | `Stages/dwg2dxf`, `storage_service`, `job_events` |
| `dxf2dwg_service.py` | **DXF→DWG** reverse orchestration: `dxf_converter.convert_file` (ODA) → persist DWG to `dwg-derived`; `$ACADVER`/reverse-lookup version detection (Real, flag-gated) | `Stages/dxf2dwg`, `storage_service` |
| `dxf2excel_service.py` | **Batch DXF→Excel** material-table extraction: files queried by `batch_name` → `dxf2excel.pipeline.process_file` → `write_excel` → persist `.xlsx` to `dwg-reports`; progress committed with job state (Real, flag-gated) | `Stages/dxf2excel`, `job_events` |
| `dxf_stats.py` | Stdlib DXF entity/section counter for fidelity metrics (no ezdxf dependency) | -- |
| `job_events.py` | Stores the latest progress event in `jobs.progress_data`; SSE polls with a fresh short-lived MySQL session, keepalive and 600s cap | `Job` model, session factory |

**DOES:**
- Orchestrate business workflows across models, schemas, and external services
- Enforce business rules (status transitions, permission checks at data level)
- Coordinate transactional boundaries

**DOES NOT:**
- Depend on `fastapi.Request` or `fastapi.Response` (acceptable: `UploadFile` and other Starlette data types; `Request` in `TYPE_CHECKING` blocks for type hints only)
- Contain route-level logic (param extraction, HTTP response construction)
- Issue raw SQL (uses SQLAlchemy ORM)

#### Model Layer -- `app/models/` (12 files, 19 tables, ~419 lines)

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

#### Core Layer -- `app/core/` (8 modules, ~500 lines)

| Module | Responsibility |
|--------|---------------|
| `config.py` | pydantic-settings from `.env`, computed properties for URLs, feature flags |
| `security.py` | Password hashing (Argon2id via `pwdlib`), JWT create/decode (HS256, jti claim) |
| `permissions.py` | Canonical import surface for `app/api/deps` (permission check functions) |
| `exceptions.py` | `AppHTTPException` base + factory functions (`not_found`, `forbidden`, `service_unavailable`) |
| `constants.py` | File size limits, allowed extensions, user status constants |
| `logger.py` | Logging configuration helpers |
| `validators.py` | Sort column whitelist validation per resource (prevents SQL injection via sort_by params) |

**DOES:**
- Provide infrastructure that every other layer depends on
- Centralize configuration, security primitives, error types

**DOES NOT:**
- Contain domain logic
- Know about request/response shapes

#### Agent Layer -- `app/agents/` (3 files, all stubs)

All three files are stubs/placeholders: `agent_factory.py` and `tool_registry.py` are single-line docstring stubs; `prompts.py` contains a `SYSTEM_PROMPT` constant placeholder. Target: Stage 2.

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

#### Worker Layer -- `app/workers/` (celery_app + 6 task modules)

`celery_app.py` defines the real Celery application (`"dwg_agent"`) using Kombu's SQLAlchemy MySQL transport and Celery's database result backend, both derived from the effective application MySQL DSN. It enables `task_acks_late`, `task_reject_on_worker_lost`, `worker_prefetch_multiplier=1`, 24-hour result cleanup, and a `task_always_eager` toggle for tests. Because the SQL transport has no fanout/remote-control support, task event broadcasts and inspect-based health checks are disabled. Queue routing: `agent→agent`, `dxf→dxf`, `dxf2dwg→dxf2dwg`, `dxf2excel→dxf2excel`, `cad→cad`, `report→report`, default `default`.

Task modules:
- `tasks_report.py` → `run_stub_job` (queue **report**) → `job_service.run_local_stub_job` — the Stage 1 framework fake task (Real).
- `tasks_dxf.py` → `convert_dwg_to_dxf` (queue **dxf**) → `dxf_service.run_dxf_conversion` (Real, flag-gated).
- `tasks_dxf2dwg.py` → `convert_dxf_to_dwg` (queue **dxf2dwg**) → `dxf2dwg_service.run_dxf_to_dwg_conversion` (Real, flag-gated).
- `tasks_dxf2excel.py` → `extract_dxf_to_excel` (queue **dxf2excel**) → `dxf2excel_service.run_dxf2excel_extraction` (Real, flag-gated).
- `tasks_agent.py` (queue agent) and `tasks_cad.py` (queue cad) — docstring-only stubs that register no task (Stage 2 / Stage 4).

Spec reference: Section 13 (Celery design). Real DXF pipelines share the same task state machine as the Stage 1 fake task (queued→running→succeeded/failed, `job_steps` per stage, `AnalysisResult`, audit/review plumbing).

#### Storage Layer -- `app/storage/` (3 files)

`base.py` defines `AbstractStorageBackend`; `local_storage.py` implements filesystem storage for local development; `minio_storage.py` implements the S3-compatible MinIO backend used by Docker deployment. `storage_service.py` performs DWG validation, hashing, and metadata creation, then writes bytes through the selected backend.

Spec reference: Section 10 (file storage). Six buckets defined: `dwg-original`, `dwg-derived`, `dwg-reports`, `dwg-temp`, plus direction-specific `dxf-original` (DXF uploads) and `dxf-derived` (DWG→DXF output).

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
                    │  (12 route mods)  │
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
                     │ 19 tables      │ │  security, │
                     └───────┬────────┘ │  exceptions│
                             │          └────────────┘
                     ┌───────▼──────────────────────┐
                     │ MySQL 8.x                    │
                     │ app data + durable state +   │
                     │ Celery SQL queue/results     │
                     └──────────────────────────────┘

Future (Stage 2/4) and flag-gated (Stage 3) additions below this line:
- - - - - - - - - - - - - - - - - - - - - - - -
┌──────────┐  ┌──────────┐  ┌─────────────────┐
│ agents/  │  │mcp_client│  │ workers/        │
│ (LangGr) │──│/ (MCP)   │  │ (Celery real;   │
│ Stage 2  │  │ Stage 2  │  │ dxf/dxf2dwg/    │
│          │  │          │  │ dxf2excel real, │
│          │  │          │  │ agent/cad stub) │
└──────────┘  └──────────┘  └─────────────────┘
                                   │
┌──────────────────────────────────┼──────────┐
│ storage/ (local dev,             │          │
│   MinIO Docker backend)          │          │
│ Stages/{dwg2dxf,dxf2dwg,dxf2excel}│ (engines)│
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
Response body: {access_token, token_type, expires_in, user}
  (refresh token set separately as httponly cookie dwg_refresh_token; path /api/v1/auth; Secure per refresh_cookie_secure_enabled)
```

### 5.3 Logout / Token Blacklisting

```
DELETE /api/v1/auth/sessions/current
  │
  ▼
Extract jti from current access token (decode without verification)
  │
  ▼
INSERT/UPDATE token_blacklist(jti, expires_at) in the request transaction
  ├── Expired rows are ignored and cleaned during later logout operations
  ├── Database errors fail closed instead of silently re-enabling revoked tokens
  ▼
auth dependency checks is_token_blacklisted(jti) on every authenticated request
  ├── Active MySQL row → 401
  └── Missing/expired row → continue
```

### 5.4 File Upload Flow

```
POST /api/v1/files (multipart/form-data, file field)
  │
  ▼
Route handler
  ├── Auth: CurrentUser
  ├── Validate: file extension (.dwg/.dxf/.zip accepted)
  ├── Validate: file size (max_upload_size_mb setting)
  ▼
storage_service.save_uploaded_file(db, user, file)
  ├── Compute SHA-256 hash
  ├── Read first 6 bytes → validate DWG magic header (AC1012–AC1032) [.dwg only]
  ├── Validate minimum 1024 bytes [.dwg only]
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
file_service.build_signed_download_url(file_id)
  ├── HMAC-SHA256(file_id + exp_timestamp, secret) → signature
  ├── URL = /api/v1/files/{file_id}/download?expires={ts}&signature={sig}
  ├── TTL = 300 seconds
  ▼
GET /api/v1/files/{file_id}/download?expires={ts}&signature={sig}
  ├── Verify expires not expired
  ├── Recompute HMAC → compare with sig (constant-time)
  └── Stream file with Content-Disposition header
```

### 5.6 Job Lifecycle & Enqueue Routing

```
POST /api/v1/jobs {project_id|drawing_id, task_type, precision_level, params}  → 202
  │
  ▼
jobs_api.create_job
  ├── require_project_role write roles (via project_id or drawing's project)
  ├── Feature-flag gate per task_type:
  │     convert_dwg_to_dxf  → 503 DXF_PIPELINE_DISABLED      if !dxf_pipeline_enabled
  │     convert_dxf_to_dwg  → 503 DXF2DWG_PIPELINE_DISABLED  if !dxf2dwg_pipeline_enabled
  │     extract_dxf_to_excel→ 503 DXF2EXCEL_PIPELINE_DISABLED if !dxf2excel_pipeline_enabled
  ▼
job_service.create_job(db, user, data)
  ├── Map task_type → pipeline; INSERT INTO jobs (status="queued", ...)
  ├── Write audit log (jobs.create)
  ▼
job_service.enqueue_job(job_id)   # router → dxf / dxf2dwg / dxf2excel / report(stub) queue
  ├── 503 JOB_ENQUEUE_FAILED if Celery dispatch fails (job marked failed)
```

**Stage 1 framework stub path** (any unmapped task_type → `PIPELINE_STUB` / `local_stub`):

```
Celery worker-report consumes app.workers.tasks_report.run_stub_job
  ├── status → "running"; INSERT job_steps
  ├── Write JSON result file via StorageBackend (dwg-derived)
  ├── INSERT analysis_results; status → "succeeded"
```

The stub task body is intentionally fake; it exercises the full result/download/audit/review chain. The three real pipelines below share the same state-machine shape (queued→running, per-stage `job_steps`, `publish_job_event`, persist via `save_bytes_as_file`, register `AnalysisResult`).

### 5.7 DWG → DXF Pipeline (Stage 3 forward, flag-gated)

```
task_type="convert_dwg_to_dxf"  → enqueue_dxf_job → tasks_dxf.convert_dwg_to_dxf_task
  ▼
dxf_service.run_dxf_conversion
  ├── step download_source_dwg   (stage source from storage)
  ├── step run_oda_convert       (Stages/dwg2dxf dwg_converter.convert_file — ODA File
  │                               Converter AppImage via xvfb-run, headless; NOT ezdxf)
  ├── step persist_dxf_result    (write DXF → bucket dxf-derived)
  ├── AnalysisResult + SSE events; error_code DXF_CONVERSION_FAILED / DXF_SOURCE_MISSING
```

Health probe: `GET /api/v1/system/health/oda`.

### 5.8 DXF → DWG Pipeline (Stage 3 reverse, flag-gated)

```
task_type="convert_dxf_to_dwg"  → enqueue_dxf2dwg_job → tasks_dxf2dwg.convert_dxf_to_dwg_task
  ▼
dxf2dwg_service.run_dxf_to_dwg_conversion
  ├── step download_source_dxf
  ├── step run_oda_convert_dxf   (Stages/dxf2dwg dxf_converter.convert_file — ODA inverse;
  │                               version via reverse-lookup on AnalysisResult.tool_version
  │                               else $ACADVER scan)
  ├── step persist_dwg_result    (write DWG → bucket dwg-derived)
  ├── error_code DWG_CONVERSION_FAILED
```

Note: this is the ODA-based reverse conversion — NOT the high-precision ZWCAD path (Stage 4, still stub).

### 5.9 DXF → Excel Pipeline (batch material-table extraction, flag-gated)

```
task_type="extract_dxf_to_excel" (params carry batch_name)
  → enqueue_dxf2excel_job → tasks_dxf2excel.extract_dxf_to_excel_task
  ▼
dxf2excel_service.run_dxf2excel_extraction   (N DXF → 1 Job → 1 .xlsx)
  ├── step download_dxf_batch     (query files by batch_name)
  ├── step run_dxf2excel_pipeline (Stages/dxf2excel pipeline.process_file — pure-Python
  │                                grid/table recovery, no ODA; per-file SSE progress)
  ├── step persist_excel_result   (write .xlsx → bucket dwg-reports)
  └── Commit each progress event to jobs.progress_data with authoritative job state
```

### 5.10 SSE Job Progress

```
GET /api/v1/jobs/{job_id}/events   (media_type text/event-stream, ?token=<jwt> or Bearer)
  ├── Emit initial DB snapshot
  ├── Roll back the request read transaction and release its connection
  ├── Poll jobs with one fresh short-lived MySQL session per iteration
  ├── Emit only changed durable snapshots; send ": keepalive" while idle
  └── End on terminal state, missing job, database error, or the 600s cap
```

---

## 6. Key Architectural Decisions

### 6.1 Synchronous API + Celery Worker Boundary

**Decision:** FastAPI request handlers use SQLAlchemy 2.x synchronous sessions. Job execution crosses an explicit Celery boundary and runs in worker processes; both application state and Celery transport/result state are persisted in MySQL.

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
- 599 tests run with fast collection and execution

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

**Decision:** JWTs include a `jti` (JWT ID) claim (UUID4). On logout, the `jti` and its expiry are stored in MySQL. The auth dependency checks this durable blacklist on every request, and password changes atomically set `sys_users.password_changed_at` so older access and refresh tokens are rejected immediately.

**Why:** JWTs are stateless by design -- you cannot revoke them without durable server-side state. The MySQL-backed design gives us:
- Immediate logout (next request rejects the token)
- Immediate revocation of all pre-password-change tokens
- Consistent behavior across API workers and restarts
- Bounded growth through expiry checks and cleanup on logout

**Trade-off:** Every authenticated request may perform an indexed primary-key lookup. This adds database load, but removes split-brain/fail-open behavior and keeps authentication state in the same transactional system as users.

### 6.5 Atomic Status Transitions

**Decision:** User status changes (active ↔ disabled, soft-delete) use `UPDATE WHERE + rowcount` instead of read-modify-write:

```python
def transition_user_status(db, user_id, to_status, *, set_deleted_at=False):
    values = {"status": to_status}
    if set_deleted_at:
        values["deleted_at"] = datetime.now(UTC)
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.status != DELETED)
        .values(**values)
    )
    return result.rowcount > 0  # caller decides whether to raise
```

**Why:** Prevents TOCTOU race conditions where a SELECT followed by UPDATE leaves a window for another admin to concurrently toggle a user's status. The `WHERE status != DELETED` guard ensures soft-deleted users cannot be modified. Returning `bool` instead of raising lets callers decide the error semantics. This is a common pattern in optimistic concurrency control.

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

**Decision:** Five boolean feature flags in `Settings`, all default `False`: `agent_enabled`, `dxf_pipeline_enabled`, `dxf2dwg_pipeline_enabled`, `dxf2excel_pipeline_enabled`, `cad_worker_enabled`.

**Why:** The spec defines a 6-stage rollout. Feature flags let us merge code to main while keeping it dark, test individual subsystems independently, and do canary rollouts. At Stage 1, `agent_enabled=false` returns 503 `AGENT_DISABLED` from `/api/v1/agent-runs`; the three DXF pipeline flags each gate `POST /api/v1/jobs` per `task_type` with their own 503 code (`DXF_PIPELINE_DISABLED` / `DXF2DWG_PIPELINE_DISABLED` / `DXF2EXCEL_PIPELINE_DISABLED`). `cad_worker_enabled` is surfaced in `GET /api/v1/system/health` but does not directly gate an HTTP endpoint (enforced at the worker/pipeline layer).

### 6.10 Config from Component Fields, Not Monolithic URLs

**Decision:** Config uses MySQL component fields (`mysql_host`, `mysql_port`, `mysql_database`, `mysql_user`, `mysql_password`) with computed URLs. An optional `DATABASE_URL` remains as an authoritative compatibility override. Celery broker/result URLs are always derived from the same effective MySQL DSN.

**Why:** Spec Section 18 defines this pattern. It enables:
- Docker override per-component (e.g., `MYSQL_HOST=mysql` in Docker, `127.0.0.1` in dev)
- URL-encoding of special characters in passwords (via `urllib.parse.quote`)
- Clear separation of concerns in `.env` files
- One source of truth for application SQLAlchemy, Celery broker, and Celery result connections

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
    settings.sqlalchemy_database_url,
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

### 8.3 All 19 Business Tables

| # | Table | Primary Keys | Foreign Keys | Notes |
|---|-------|-------------|--------------|-------|
| 1 | `sys_users` | id | -- | username UNIQUE, password_hash, password_changed_at, status, soft-delete |
| 2 | `sys_roles` | id | -- | code UNIQUE, is_system flag |
| 3 | `sys_permissions` | id | -- | code UNIQUE, resource + action |
| 4 | `sys_user_roles` | (user_id, role_id) | users.id, roles.id | M2M join |
| 5 | `sys_role_permissions` | (role_id, permission_id) | roles.id, permissions.id | M2M join |
| 6 | `projects` | id | owner_id → users.id | code UNIQUE, status |
| 7 | `project_members` | id | project_id → projects.id, user_id → users.id | project_role enum |
| 8 | `files` | id | uploaded_by → users.id | storage_key, sha256, status, `batch_name` (indexed, DXF/Excel batch uploads) |
| 9 | `drawings` | id | project_id → projects.id | current_version_id self-ref |
| 10 | `drawing_versions` | id | drawing_id → drawings.id, file_id → files.id, created_by → users.id | version_no |
| 11 | `jobs` | id | project_id, drawing_id, created_by | task_type, pipeline, status, progress_data |
| 12 | `job_steps` | id | job_id → jobs.id | step_name, worker_name, status, input/output_json |
| 13 | `agent_runs` | id | user_id, project_id, drawing_id, file_id | session_id, task, status, answer, output_file_id |
| 14 | `agent_run_steps` | id | agent_run_id → agent_runs.id | step_type, tool_name, arguments_json, status |
| 15 | `analysis_results` | id | job_id, drawing_id | result_type, result_json, confidence, result_file_id |
| 16 | `review_records` | id | result_id → analysis_results.id, reviewer_id → users.id | decision (approved/rejected), comment |
| 17 | `audit_logs` | id | actor_user_id → users.id | action, resource_type, resource_id, before/after_json, ip_address |
| 18 | `token_blacklist` | jti | -- | durable JWT revocation with indexed expiry |
| 19 | `agent_memory` | session_id | -- | bounded JSON history with application-enforced TTL |

Tables 1-12 and 15-18 are active. Tables 13-14 are created and queryable but only written to in Stage 2. Table 19 is Stage 2 infrastructure with tested read/write/expiry behavior. Celery additionally owns four SQL transport/result tables, and Alembic owns `alembic_version`.

### 8.4 Migrations

Five Alembic versions in `backend/migrations/versions/` (linear chain):

1. `40452ddd24e7` -- **initial**: Creates all 17 tables with columns, constraints, indexes; adds the deferred `drawings.current_version_id` → `drawing_versions.id` named FK after both tables exist.
2. `b8f9e7d6c5a4` -- **add_missing_timestamp_columns**: Backfills `created_at`/`updated_at` for `project_members`, `drawing_versions`, `review_records`, `agent_run_steps` that missed the `TimestampMixin` in the initial migration.
3. `c3d2e1f0a9b8` -- **fix_audit_logs_resource_id_type**: Alters `audit_logs.resource_id` from `Integer` → BIGINT for consistency with other ID columns.
4. `53cd59adf848` -- **add_batch_name_to_files**: Adds `files.batch_name` VARCHAR(128) nullable + index `ix_files_batch_name` for DXF/Excel batch uploads.
5. `1d1696c7e854` -- **remove_redis_add_mysql_backend** (current head): Creates `token_blacklist` and `agent_memory`; adds `jobs.progress_data` and `sys_users.password_changed_at`.

Alembic reads the same `settings.sqlalchemy_database_url` used by the application.

---

## 9. MySQL-Backed Runtime State

### 9.1 Single Authoritative DSN

`Settings.sqlalchemy_database_url` selects the optional MySQL `DATABASE_URL` override or assembles a DSN from `MYSQL_*`. Celery derives `sqla+mysql+pymysql://...` and `db+mysql+pymysql://...` from that same effective DSN, preventing application and queue configuration drift.

### 9.2 Runtime State Ownership

| Capability | MySQL storage | Retention / consistency |
|------------|---------------|-------------------------|
| Token revocation | `token_blacklist` | Expiry indexed; cleanup on logout; fail closed on DB error |
| Password revocation | `sys_users.password_changed_at` | Written atomically with password update/reset |
| Agent memory | `agent_memory` | Bounded messages; TTL enforced on read |
| Job progress / SSE | `jobs.progress_data` + status fields | Same caller transaction as job state |
| Celery broker | `kombu_queue`, `kombu_message` | SQLAlchemy transport; no fanout/remote control |
| Celery results | `celery_taskmeta`, `celery_tasksetmeta` | 24-hour expiry cleanup on worker startup |

### 9.3 Connection and Polling Discipline

API requests use short transactions. SSE explicitly releases the request session before streaming and creates one session per poll so MySQL `REPEATABLE READ` cannot pin a stale snapshot or hold a pool connection for ten minutes. Workers write status, progress, error details and the latest event atomically.

### 9.4 Testing Strategy

Unit/API tests use SQLite isolation while exercising the same ORM paths. Migration tests validate the new tables and columns. Runtime acceptance tests additionally check that legacy modules, dependencies, compose services and environment keys are absent, and a real MySQL/Celery integration probe validates dispatch, execution and result persistence.

---

## 10. Storage Architecture

### 10.1 Current (Stage 1)

Files are stored through `AbstractStorageBackend`. Local development defaults to `LocalFileStorage` under `backend/var/storage/`; Docker deployment defaults to `MinioStorage` at `http://minio:9000`. The `storage_service.py` handles:
- File save (write bytes through selected backend)
- File retrieval (local `FileResponse` or MinIO streaming response)
- File deletion (remove file, soft-delete DB record)

The `file_service.py` handles:
- Download URL generation (HMAC-signed, 300s TTL, via `build_signed_download_url`)
- File read access checks (project membership, ownership, global admin)

### 10.2 Storage Backends

The `app/storage/` directory contains:

```
app/storage/
├── base.py           # AbstractStorageBackend ABC + storage exceptions
├── local_storage.py  # LocalFileStorage
└── minio_storage.py  # MinioStorage (S3-compatible)
```

The storage backend is selected via `STORAGE_BACKEND=local|minio`. MinIO uses six buckets per Spec Section 10.2:
- `dwg-original` -- raw DWG uploads (never overwritten)
- `dwg-derived` -- DXF→DWG output DWG + stub JSON results, and other DWG-derived artifacts
- `dwg-reports` -- Excel (DXF→Excel `.xlsx`), PDF, ZIP reports
- `dwg-temp` -- temporary worker sandbox files (reserved; no writer yet)
- `dxf-original` -- uploaded non-`.dwg` files (e.g. `.dxf`)
- `dxf-derived` -- DWG→DXF output DXF

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

Error codes are `UPPER_SNAKE_CASE` strings that are machine-parseable and stable. Examples: `NOT_FOUND`, `FORBIDDEN`, `FILE_TYPE_NOT_ALLOWED`, `AGENT_DISABLED`, `INVALID_STORAGE_PATH`. Frontend code can switch on `error.code` without parsing `error.message`.

---

## 12. Testing Architecture

### 12.1 Test Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Runner | pytest | Test discovery and execution |
| HTTP client | `fastapi.testclient.TestClient` | In-process API testing |
| DB isolation | SQLite `:memory:` + `StaticPool` | Per-test isolated database |
| MySQL migration integration | Local MariaDB/MySQL | Full Alembic chain and schema verification |
| Celery integration | MySQL SQL transport + result backend | Real dispatch/execution/result persistence probe |
| Fixtures | `conftest.py` | DB setup/teardown, auth headers, test data factories |

### 12.2 Test Categories (31 files, 599 tests)

| Category | Files | Focus |
|----------|-------|-------|
| API regression | `test_api_regressions.py` | All 74 endpoints return correct status codes and shapes |
| Adversarial inputs | `test_adversarial_auth.py`, `test_adversarial_files.py`, `test_adversarial_jobs.py` | Malformed/abusive payloads against auth, file, and job endpoints |
| Security boundaries | `test_security_boundaries.py`, `test_rbac_deep.py` | Auth required, RBAC enforcement, path traversal defense |
| Token lifecycle | `test_token_lifecycle.py` | Login, refresh, blacklist, expiry, jti validation |
| MySQL runtime migration | `test_mysql_runtime.py`, `test_job_events_mysql.py`, `test_agent_memory.py` | Removed-component guard, durable SSE polling, Agent memory TTL |
| Config | `test_config.py` | Effective MySQL URL assembly, Celery URL derivation, feature flags |
| DB session | `test_db_session.py` | Engine creation, health check, WAL pragmas |
| Edge cases | `test_edge_cases.py`, `test_rigorous.py`, `test_deep_verify.py` | Concurrent ops, large payloads, Unicode, null handling |
| Service layer | `test_service_layer.py` | Service function unit tests (user, file, project, auth) |
| File service | `test_file_service.py` | Signed download URLs, ZIP builder, project-scoped read/delete access checks |
| New features | `test_new_features.py` | Batch upload, excel-preview, bulk-delete, download-zip |
| Stage 1 boundaries | `test_stage1_boundaries.py` | Agent 503, Celery fake task, feature flag gates |
| DXF pipelines (Stage 3) | `test_dxf_pipeline.py`, `test_dxf2dwg_pipeline.py`, `test_dxf2excel_pipeline.py` | Real DWG→DXF, DXF→DWG, DXF→Excel conversion/extraction (flag-gated) |
| Flow tests | `test_smoke_flow.py`, `test_job_lifecycle.py` | End-to-end: register → login → upload → job → result |
| Celery/MinIO deploy | `test_celery_minio_deployment.py` | Celery worker health, MinIO bucket ops, E2E job pipeline |
| Cross-audit fixes | `test_cross_audit_fixes.py` | Pentest bug regression tests (31 test functions) |
| Scripts validation | `test_scripts.py` | Shell scripts syntax, lib.sh functions, db.sh operations |
| Migration tests | `test_migrations.py` | Alembic version count, table existence |
| Compose tests | `test_compose.py` | YAML parse, service count, required services present |
| Health | `test_health.py` | `/health` endpoint, DB health check function |

### 12.3 Test Isolation Mechanics

```python
# conftest.py (simplified)
@pytest.fixture(autouse=True)
def _isolate_test_db(monkeypatch):
    """Use one in-memory SQLite connection per test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    app.dependency_overrides[original_get_db] = _override_get_db
    # ... also monkeypatches SessionLocal/engine in init_db, job_service, db.session
    yield
    app.dependency_overrides.clear()
```

---

## 13. Implementation Status Matrix

### Stage 1 -- Done

| Component | Status | Lines | Tests | Notes |
|-----------|--------|-------|-------|-------|
| FastAPI app (main.py) | Done | 134 | Covered | Lifespan, CORS, X-Request-ID, 4 exception handlers, /health |
| API routes (12 modules) | Done | -- | Covered | All 74 endpoints (73 under /api/v1 + root /health) return proper envelopes |
| Pydantic schemas (10 modules) | Done | 513 | Covered | All use v2 `from_attributes=True` |
| Business services (16 modules) | Done | -- | Covered | auth, user, job, job_events, project, file, drawing, review, agent, agent_memory, storage, audit, dxf, dxf_stats, dxf2dwg, dxf2excel |
| SQLAlchemy models (19 business tables) | Done | -- | Covered | Includes durable token blacklist, Agent memory and job progress |
| Core infrastructure (7 modules) | Done | -- | Covered | Config, security, permissions, exceptions, logger, constants, validators |
| DB session + pool | Done | -- | Covered | MySQL pool config, SQLite WAL pragmas, health check |
| DB init + seed data | Done | -- | Covered | Super admin, 7 roles, 8 permissions |
| Alembic migrations | Done | 5 | Covered | Initial schema through MySQL runtime-state migration |
| Token blacklist | Done | -- | Covered | MySQL-backed jti expiry; database errors fail closed |
| File upload + validation | Done | -- | Covered | DWG header, SHA-256, path traversal guard, HMAC URLs |
| Audit logging | Done | 44 | Covered | Structured audit trail writes |
| Docker Compose (9 services) | Done | -- | Covered | MySQL/MinIO/backend/nginx plus five queue-specific workers |
| Dockerfile (backend) | Done | -- | Validated | Multi-stage, non-root, HEALTHCHECK, uv sync |
| Nginx config (Docker + local) | Done | -- | Validated | Rate limiting, proxy, static serving |
| Frontend (React 19 + TS + Vite) | Done | -- | Manual | 10 page features, 12 API client files (11 modules + client.ts), auth store, router |
| 599 tests | Done | -- | -- | 31 test files, all passing |

### Stage 2 -- Not Started (Agent, MCP)

| Component | Status | Lines | Notes |
|-----------|--------|-------|-------|
| LangGraph agent factory | Stub | 1 | `app/agents/agent_factory.py` |
| System prompts | Stub | 1 | `app/agents/prompts.py` |
| Tool registry | Stub | 1 | `app/agents/tool_registry.py` |
| MCP CAD client | Stub | 1 | `app/mcp_client/cad_mcp_client.py` |
| MCP tool adapter | Stub | 1 | `app/mcp_client/mcp_tool_adapter.py` |
| Celery app | Done | -- | MySQL SQLAlchemy broker/database result backend, queue routing |
| Agent tasks | Stub | 1 | `app/workers/tasks_agent.py` (registers no task) |
| Agent service | Stub | -- | `create_agent_run` raises `NotImplementedError` |
| Report tasks | Real (Stage 1 stub job) | -- | `run_stub_job` creates fake result files |
| Agent runs API | Real (503) | -- | Returns 503 `AGENT_DISABLED` when `AGENT_ENABLED=false` |
| Agent memory runtime | Infra only | -- | MySQL-backed and validated by tests; not called in request path yet |

### Stage 3 -- Implemented / real, flag-gated off (DXF Pipelines)

> The `docs/roadmap.md` still lists Stage 3 as "Planned" — that is **stale**. The backend contains fully-wired real conversion/extraction services, disabled by default via feature flags.

| Component | Status | Notes |
|-----------|--------|-------|
| DWG→DXF service | Real (flag-gated) | `dxf_service.run_dxf_conversion`; ODA File Converter via `Stages/dwg2dxf`; output → `dxf-derived`; flag `dxf_pipeline_enabled` |
| DXF→DWG service | Real (flag-gated) | `dxf2dwg_service.run_dxf_to_dwg_conversion`; ODA inverse via `Stages/dxf2dwg`; output → `dwg-derived`; flag `dxf2dwg_pipeline_enabled` |
| DXF→Excel service | Real (flag-gated) | `dxf2excel_service.run_dxf2excel_extraction`; pure-Python `Stages/dxf2excel`; batch by `batch_name`; output `.xlsx` → `dwg-reports`; flag `dxf2excel_pipeline_enabled` |
| DXF Celery tasks | Real | `tasks_dxf`, `tasks_dxf2dwg`, `tasks_dxf2excel` (queues dxf/dxf2dwg/dxf2excel) |
| dxf_stats helper | Real | Stdlib DXF entity/section counter for fidelity metrics |
| SSE progress | Real | Durable `jobs.progress_data` polling → `GET /api/v1/jobs/{id}/events` |
| ODA health endpoint | Real | `GET /api/v1/system/health/oda` |

### Stage 4 -- Not Started (Windows CAD Worker)

| Component | Status | Notes |
|-----------|--------|-------|
| ASP.NET Core Worker Service | Not started | Task polling, sandbox management |
| ZWCAD API plugin (C#) | Not started | Layer/text/dimension/block extraction |
| ZWCAD client | Stub | `app/integrations/zwcad/client.py` |
| ZWCAD schemas | Stub | `app/integrations/zwcad/schemas.py` |
| CAD dispatch tasks | Stub | `app/workers/tasks_cad.py` (registers no task) |
| CAD Worker safety | Not started | Process crash recovery, license check, sandbox per task |

Constants `PIPELINE_CAD="zwcad_worker"` and `JOB_WAITING_CAD_WORKER` are defined but never routed; `cad_worker_enabled` (default False) is surfaced only in `GET /api/v1/system/health`; `cad-worker/` is a placeholder directory.

### Stage 5-6 -- Partial

**Stage 5** (business algorithms): foundations only — review-loop primitives (`review_service`, `reviews_api`, `AnalysisResult`) are functional, and DXF→Excel material-table extraction (a Stage-5 item) shipped early inside Stage 3. Higher-level LaR entry, BOM comparison, and report-generation algorithms are not present.

**Stage 6** (production hardening): partial — durable token JTI blacklist + password-change staleness shipped early in `auth_service`; Celery queue hygiene and result cleanup are configured; `infra/` carries Docker/Nginx/MySQL/MinIO configs. No Prometheus/Loki/application rate-limiting/chunked upload in code yet.

---

## 14. Feature Flag Inventory

All flags are in `app/core/config.py` / `.env`:

| Flag | Default | Stage | Effect When False |
|------|---------|-------|--------------------|
| `AGENT_ENABLED` | `false` | 2 | All four agent endpoints (`POST /api/v1/agent-runs`, `GET /agent-runs/{id}`, `GET /agent-runs/{id}/steps`, `GET /agent-tools`) → 503 `AGENT_DISABLED` |
| `DXF_PIPELINE_ENABLED` | `false` | 3 | `POST /api/v1/jobs` with `task_type=convert_dwg_to_dxf` → 503 `DXF_PIPELINE_DISABLED` |
| `DXF2DWG_PIPELINE_ENABLED` | `false` | 3 | `POST /api/v1/jobs` with `task_type=convert_dxf_to_dwg` → 503 `DXF2DWG_PIPELINE_DISABLED` |
| `DXF2EXCEL_PIPELINE_ENABLED` | `false` | 3 | `POST /api/v1/jobs` with `task_type=extract_dxf_to_excel` → 503 `DXF2EXCEL_PIPELINE_DISABLED` |
| `CAD_WORKER_ENABLED` | `false` | 4 | Surfaced in `GET /api/v1/system/health` only; does not directly gate any HTTP endpoint (enforced at worker/pipeline layer) |
| `DEBUG` | `true` (dev) | All | Controls stack trace in 500 responses; must be `false` in production. Also gates `/docs`/`/redoc`/`/openapi.json` (mounted only when `APP_ENV=development` or `DEBUG=true`) |

Note: `system_api /system/health` reports only three flags (`agent`, `dxf_pipeline`, `cad_worker`); `dxf2dwg`/`dxf2excel` exist and gate jobs but are not in the health payload.

---

## 15. Directory Map (Complete)

```
complete_framework/
├── DWG-Agent企业平台技术规范.md          ← Spec v2.0 (1296 lines, 25 sections)
├── README.md
├── .env.example                          ← Local dev env template (tracked)
├── .env.docker.example                   ← Docker env template (tracked)
├── compose.yaml                          ← 9 services, 2 volumes, 2 networks
├── CLAUDE.md                             ← Agent instructions for this repo
├── Makefile                              ← Dev shortcuts (install, test, lint, run)
├── image.png                             ← Architecture diagram
├── EXPLORATION_REPORT.md                 ← Initial exploration report
├── FRONTEND_EXPLORATION.md               ← Frontend exploration report
├── REINVESTIGATION_REPORT.md             ← Reinvestigation report
├── var/                                  ← Runtime data (gitignored)
│
├── backend/                              ← Python 3.12, uv, FastAPI
│   ├── pyproject.toml                    ← Dependencies + ruff config
│   ├── uv.lock                           ← Locked deps (COMMITTED)
│   ├── .python-version                   ← 3.12
│   ├── Dockerfile                        ← Multi-stage, non-root
│   ├── .dockerignore
│   ├── alembic.ini                       ← Targets MySQL
│   ├── migrations/versions/              ← 5 Alembic versions
│   ├── tests/                            ← 30 test files
│   │   └── conftest.py                   ← SQLite isolation and app overrides
│   ├── var/storage/                      ← Runtime file storage (gitignored)
│   └── app/
│       ├── main.py                       ← FastAPI app, lifespan, middleware
│       ├── api/v1/                       ← 12 route modules
│       │   └── router.py                 ← Central router assembly
│       ├── schemas/                      ← 10 Pydantic v2 modules
│       ├── services/                     ← 16 business logic modules
│       ├── models/                       ← 12 ORM model files (19 business tables)
│       ├── core/                         ← 7 infrastructure modules
│       ├── db/                           ← session, base, init_db
│       ├── utils/                        ← path_utils, file_hash, time_utils
│       ├── agents/                       ← 3 stubs (Stage 2)
│       ├── mcp_client/                   ← 2 stubs (Stage 2)
│       ├── workers/                      ← celery_app (real) + 6 task modules (report/dxf/dxf2dwg/dxf2excel real, agent/cad stubs)
│       ├── storage/                      ← 3 files (base + local dev + MinIO deploy backend)
│       ├── integrations/zwcad/           ← 2 stubs (Stage 4)
│       └── repositories/                 ← Empty placeholder
│
├── Stages/                               ← Editable path-dep engine packages
│   ├── dwg2dxf/                          ← DWG→DXF (ODA File Converter + AppImage)
│   ├── dxf2dwg/                          ← DXF→DWG (ODA inverse)
│   └── dxf2excel/                        ← DXF→Excel (pure-Python grid/table recovery)
│
├── frontend/                             ← React 19 + TypeScript + Vite
│   ├── package.json                      ← All deps pinned
│   └── src/
│       ├── api/                          ← 12 API client files (11 modules + client.ts)
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
6. Apply the latest MySQL migration and start the relevant Celery worker queues
7. Set `AGENT_ENABLED=true` in `.env`

### Switching storage to MinIO

1. Set `STORAGE_BACKEND=minio`, configure MinIO endpoint + credentials
3. Start MinIO container: `docker compose up minio -d`
4. Run migration to backfill storage keys if migrating existing files

---

*Document version: 2.1 -- last updated 2026-07-08*
*Corresponds to codebase at Stage 1 complete + Stage 3 DXF pipelines implemented (flag-gated off) — 74 endpoints, 19 tables, 5 migrations*
