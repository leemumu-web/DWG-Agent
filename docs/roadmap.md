# DWG-Agent Platform Roadmap

> Product owner's and integration engineer's view of the 6-stage delivery plan.
> Current phase: **Stage 1 complete. Stage 3 conversion pipelines (DWG→DXF, DXF→DWG, DXF→Excel) are implemented but flag-gated OFF by default; the Stage 2 Agent subsystem is still a flag-gated stub and is the next activation target.**
> Spec authority: `DWG-Agent企业平台技术规范.md` sections 11-15, 23.

---

## 1. Stage Overview

| Stage | Name | Status | Key Deliverables | Dependencies | Est. Effort |
|-------|------|--------|------------------|--------------|-------------|
| **1** | Platform Skeleton | **DONE** | Auth, RBAC, projects, file upload/batch/zip, job lifecycle, audit, 73 API endpoints across 12 modules (+ root `GET /health`), 432 tests, React frontend (10 pages), MinIO/Celery deployment base | None | Completed |
| **2** | Agent Subsystem | **NEXT** (stub) | LangGraph `create_react_agent`, DeepSeek LLM, MCP client, Redis session memory, Agent Celery task body, `/api/v1/agent-runs` live, AgentSteps UI | Stage 1 | 2-3 weeks |
| **3** | DXF Pipeline | **IMPLEMENTED** (flag-gated off) | DWG→DXF and DXF→DWG conversion via ODA File Converter, batch DXF→Excel material-table extraction; real `dxf`/`dxf2dwg`/`dxf2excel` Celery workers + `job_steps` + `AnalysisResult`; `GET /system/health/oda` | None (independent of Agent) | Done (disabled by default) |
| **4** | Windows CAD Worker | Planned (stub) | ASP.NET Core Worker Service, ZWCAD API integration, pull-based task dispatch, cad_result.json export, CAD crash recovery | Stage 1 (internal API), Stage 2 (for dispatch tool) | 3-4 weeks |
| **5** | Business Algorithms | **PARTIAL** | Material-table extraction (DXF→Excel) shipped early in Stage 3; review closed-loop primitives live (`review_service`, `/reviews`, `AnalysisResult`); LaR entry, component-list comparison, Excel/PDF/ZIP reports, batch orchestration still to build | Stage 3, Stage 4 | 4-6 weeks |
| **6** | Production Hardening | **PARTIAL** | Token JTI blacklist + password-change staleness already shipped; Celery queue-hygiene config present. Remaining: RabbitMQ (optional), Prometheus/Grafana, Loki, backup/restore, CI/CD, multi-CAD-Worker scaling, rate limiting | Stage 5 | Ongoing |

---

## 2. Stage 1: Platform Skeleton -- COMPLETION REPORT

### 2.1 Infrastructure

| Component | Status | Details |
|-----------|--------|---------|
| Docker Compose | Config ready, not production-tested | 9 services (nginx, backend-api, worker-agent, worker-dxf, worker-report, mysql, redis, minio, flower); worker-report default, profiles for Agent/DXF and monitoring; `.env.docker.example` template |
| MySQL 8.x | Runtime database | `DATABASE_URL=mysql+pymysql://...`; pool (MySQL only): `pool_size=10, max_overflow=20, pool_recycle=3600` plus `pool_pre_ping=True`; `init.sql` seed script. No WAL/session pragmas are set on MySQL — the SQLite `foreign_keys=ON` pragma lives only in `tests/conftest.py` |
| Redis (Valkey) | Deployed and validated | Systemd-managed; `redis_client` (lazy init, no-crash on unavailable), `redis_memory`, `cache_service` all tested; FakeRedis (419 non-real-Redis tests via conftest autouse) + real Redis integration (13 tests) dual-layer validation |
| MinIO | Docker storage backend ready | Three-layer abstraction: `base.py` / `local_storage.py` / `minio_storage.py`; local dev uses local storage, Docker uses MinIO |
| Celery | Stub job + real conversion tasks | Real Celery app with Redis broker/result backend; `worker-report` runs `run_stub_job` (queued→running→succeeded); `tasks_dxf`/`tasks_dxf2dwg`/`tasks_dxf2excel` are real conversion task bodies delegating to their services (flag-gated off); `tasks_agent`/`tasks_cad` register nothing (stubs) |
| Nginx | Production + local dev dual config | `infra/nginx/nginx.conf` (Docker), `infra/nginx/nginx.local.conf` (local dev); reverse proxy `/api/v1/*` to backend; SPA static serving |
| Alembic | 4 migration versions | `40452ddd24e7_initial.py` (17 tables) → `b8f9e7d6c5a4_add_missing_timestamp_columns.py` (TimestampMixin fix) → `c3d2e1f0a9b8_fix_audit_logs_resource_id_type.py` (resource_id type fix) → `53cd59adf848_add_batch_name_to_files.py` (`files.batch_name` + index for DXF/Excel batch uploads, current head); `scripts/db.sh migration-test` validates end-to-end |

### 2.2 Backend -- 73 API Endpoints across 12 Route Modules (+ root `GET /health`)

| Module | Endpoints | Key Features |
|--------|-----------|--------------|
| **Auth** (5) | POST sessions, DELETE sessions/current, POST tokens/refresh, GET me, PATCH password | Login/logout with JWT access token + HttpOnly refresh cookie; token blacklist on logout; password change with old-password verification |
| **Users** (11) | Full CRUD + role management + password reset + disable/enable | Admin-only; soft-delete; `super_admin` protection (can't delete/disable); self-update via `PATCH /users/me`; username pattern `^[a-zA-Z0-9_.@-]+$`; password min 12 chars with complexity |
| **Roles** (4) | GET roles, POST roles, GET permissions, PUT roles/{id}/permissions | 7 global roles + 4 project roles; 5 RBAC tables; super_admin bypasses all checks; roles/permissions mounted with no extra prefix |
| **Projects** (9) | CRUD + member management (4 project roles) | Cascade active-status check (`require_active_project`); deleted projects → 404 for all members; creator auto-assigned `project_owner` |
| **Files** (13) | Upload (single), upload-zip, list, batches, batch delete, batch download-zip, excel-preview, detail, delete, download-url, download, bulk-delete, download-zip | DWG validation: header (AC1012-AC1032), min 1024 bytes, extension whitelist `{.dwg,.dxf,.zip}`, SHA-256/MD5 hash; zip-bomb guards (entry-count + uncompressed-size caps); HMAC-signed download URLs (TTL=300s); batch grouping via `batch_name` (Redis-cached 30s); Excel preview via openpyxl (cached 5min); ownership + project-member access control |
| **Drawings** (8) | CRUD + version management + preview | Auto-increment `version_no`; project-scoped; `GET /drawings/{id}/preview` still returns a Stage-1 placeholder stub |
| **Jobs** (10) | List, create, get, cancel, retry, steps, logs, events (SSE), results, cancel-all-active | State machine: pending→queued→running→succeeded/failed/cancelled; state guards on cancel/retry; `POST /jobs` is feature-gated per `task_type` (503 `DXF_PIPELINE_DISABLED`/`DXF2DWG_PIPELINE_DISABLED`/`DXF2EXCEL_PIPELINE_DISABLED`); `GET /jobs/{id}/events` is a live SSE stream (Redis pub/sub, `?token=` auth); `GET /jobs/{id}/logs` still a stub; `POST /jobs/cancel-all-active` admin-only |
| **Results** (4) | Detail, download-url, review submit, review history | `approved`/`rejected` decisions; confidence scoring; review-history list returned un-paginated |
| **Reviews** (1) | Pending list | `status=need_review` results, filtered by project membership |
| **Audit** (2) | List (last 200), detail | super_admin + auditor only; logs logins, user mgmt, role changes, file ops, job ops, reviews, agent-run creation |
| **Agent** (4) | POST agent-runs, GET agent-runs/{id}, GET steps, GET tools | All return 503 when `AGENT_ENABLED=false` (default); resource model established, no frontend changes needed when enabled |
| **System** (2) | GET system/health, GET system/health/oda | `GET /system/health` reports `{redis, features{agent,dxf_pipeline,cad_worker}, storage_backend}`; `GET /system/health/oda` reports ODA File Converter environment health (`oda_found`, `oda_executable`, `ezdxf_available`) |

### 2.3 Frontend -- React 19 + TypeScript + Vite

- **~14 page components**, organized feature-first under `src/features/*Page.tsx` (no `src/pages/` dir): Login, Dashboard, Projects, Drawings, Files, Jobs, Reviews, Admin, Profile — where **Files** splits into a `FilesLayout` plus `dwg2dxf`/`dxf2dwg`/`dxf2excel` sub-pages and **Admin** splits into Users/Roles/AuditLogs (see `src/app/router.tsx`)
- **13 API client files** under `src/api/` (12 modules + client.ts)
- **7 shared components** under `src/components/`, all implemented: ConversionPage, ExcelPreview, FileUpload, JobTimeline, PermissionGuard, ui, ZipDownloadModal (ConversionPage/ExcelPreview/ZipDownloadModal drive the Stage-3 conversion UI). The Stage-2+ components (TaskInput, AgentSteps, ResultPanel, DrawingPreview, ReviewPanel) do not exist yet and are still to be built -- see §3.3.7.
- **Route-level auth guards** with role-based access
- **SessionStorage** token storage (not localStorage)
- **npm ci + npm run build** pass clean

### 2.4 Test Coverage -- 432 Tests

```text
ruff check app tests    →  All checks passed (0 errors)
pytest -q               →  432 passed, 0 failed
```

Test domains covered:
- Authentication flow (login, logout, refresh, password change)
- RBAC enforcement (role-based access, super_admin bypass, cross-project isolation)
- File upload validation (DWG header, size, extension, hash)
- Job lifecycle (create, state transitions, cancel/retry guards)
- Audit log write-through
- Redis client, memory, and cache service (dual FakeRedis + real Redis)
- Security boundaries (timing attack defense, path traversal, HTML injection, SQL integrity)
- API regression (cross-project reads, downloads, review authorization, file list leakage)
- Configuration (MySQL component fields, Redis URL assembly, Celery URL assembly)

### 2.5 Known Limitations (Stage 1)

| Limitation | Resolution Stage |
|------------|-----------------|
| Docker Compose not production-tested | Stage 2 (incremental hardening) |
| Agent + Windows-CAD worker task bodies are stubs (`tasks_agent`/`tasks_cad` register nothing) | Stage 2 / Stage 4 |
| Agent returns 503 for all requests (`AGENT_ENABLED=false`) | Stage 2 |
| DWG↔DXF & DXF→Excel conversion implemented but disabled by default (`DXF_PIPELINE_ENABLED`/`DXF2DWG_PIPELINE_ENABLED`/`DXF2EXCEL_PIPELINE_ENABLED`) | Enable per env (needs ODA binary) |
| No ZWCAD high-precision integration | Stage 4 |
| SSE job-events stream is live (`GET /jobs/{id}/events`); `GET /jobs/{id}/logs` and `GET /drawings/{id}/preview` still return placeholder stubs | Stage 2 / ongoing |
| No chunked upload | Stage 6 |
| Frontend detail pages are basic | Ongoing |
| No admin token introspection/revocation endpoint (jti blacklist + password-change staleness already implemented) | Stage 6 |

---

## 3. Stage 2: Agent Subsystem -- IMPLEMENTATION GUIDE

### 3.1 Target Architecture

```
User → POST /api/v1/agent-runs → FastAPI → Celery agent queue
                                              ↓
                                    LangGraph create_react_agent
                                              ↓
                              ┌───────┼───────┐
                              ↓       ↓       ↓
                          MCP Client  LLM    Redis Memory
                         (CAD tools) (DeepSeek) (session history)
```

### 3.2 What Already Exists (No New Code Needed for These)

| Component | File | Status |
|-----------|------|--------|
| Agent API endpoints (4) | `backend/app/api/v1/agent_runs_api.py` | Defined, return 503; no changes needed for resource model |
| Agent tools endpoint | `backend/app/api/v1/agent_runs_api.py` | Defined, returns 503 |
| Agent factory stub | `backend/app/agents/agent_factory.py` | Placeholder -- replace with real LangGraph agent |
| System prompt | `backend/app/agents/prompts.py` | Placeholder -- define CAD-specific prompt |
| Tool registry stub | `backend/app/agents/tool_registry.py` | Placeholder -- register MCP→LangChain tool adapters |
| MCP client stub | `backend/app/mcp_client/cad_mcp_client.py` | Placeholder -- implement connect/list_tools/call_tool |
| MCP tool adapter stub | `backend/app/mcp_client/mcp_tool_adapter.py` | Placeholder -- wrap MCP tools as LangChain tools |
| Redis memory service | `backend/app/services/redis_memory.py` | **Fully implemented and tested** -- calls `save_session_history()`/`get_session_history()` on `agent:memory:{session_id}` key, TTL=7200s, max 20 messages |
| Redis client | `backend/app/core/redis_client.py` | **Fully implemented** -- lazy-init, no-crash on unavailable, sync redis-py |
| Cache service | `backend/app/services/cache_service.py` | **Fully implemented** -- generic `cache:{namespace}:{key}` pattern |
| Celery app | `backend/app/workers/celery_app.py` | Implemented -- Redis broker/result backend, queue routing, eager mode for tests |
| Agent task stub | `backend/app/workers/tasks_agent.py` | Placeholder -- needs real Agent execution task |
| Celery broker URL | `backend/app/core/config.py` | `celery_broker_url` property auto-assembled from Redis component fields |
| LLM config | `backend/app/core/config.py` | `model_name`, `model_api_key`, `model_base_url` fields ready |
| MCP config | `backend/app/core/config.py` | `mcp_cad_command`, `mcp_cad_args` fields ready |
| Feature flag | `backend/app/core/config.py` | `agent_enabled: bool = False` -- set to `true` to activate |
| Docker Compose worker profile | `compose.yaml` | `worker-agent` service defined under `profiles: [workers]` |

### 3.3 What Needs to Be Built

#### 3.3.1 Celery Agent Task Integration

**Files:** `backend/app/workers/tasks_agent.py` (still a stub). Note: `tasks_dxf`/`tasks_dxf2dwg`/`tasks_dxf2excel` are already real Stage-3 conversion tasks; only `tasks_agent` (Stage 2) and `tasks_cad` (Stage 4) remain stubs.

The Celery app itself already exists. Stage 2 should add real Agent task bodies on top of the existing Redis-backed app and keep platform safety checks in FastAPI services:

```python
@celery_app.task(name="app.workers.tasks_agent.run_agent")
def run_agent_task(agent_run_id: int) -> dict:
    ...
```

#### 3.3.2 LangGraph Agent Implementation

**File:** `backend/app/agents/agent_factory.py`

Implement the agent creation function:

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


def create_cad_agent(mcp_client, settings):
    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        temperature=0,
    )
    tools = build_langchain_tools(mcp_client)
    agent = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)
    return agent
```

Requirements per spec section 11.3:
- Use `create_react_agent` (not custom graph)
- `temperature=0` for deterministic behavior
- API key from env, never hardcoded
- Hard platform rules (auth, paths, pipeline routing) must NOT be delegated to the LLM

#### 3.3.3 MCP Client Implementation

**File:** `backend/app/mcp_client/cad_mcp_client.py`

Must implement (per spec section 11.4):
- `connect()` -- MCP stdio connection
- `disconnect()` -- clean shutdown
- `list_tools() -> list[dict]` -- tool inventory
- `call_tool(tool_name, arguments) -> str` -- synchronous tool invocation

Critical MCP behavior:
- Connection failure must NOT crash the service
- MCP unavailable → `POST /api/v1/agent-runs` returns 503
- Stdio stdout must contain only valid JSON

#### 3.3.4 MCP-to-LangChain Tool Adapter

**File:** `backend/app/mcp_client/mcp_tool_adapter.py`

Wrap each MCP tool as a LangChain `BaseTool`:
- Tool name and description from MCP `list_tools()` output
- Argument schema derived from MCP tool parameter definitions
- `_run()` delegates to `mcp_client.call_tool()`

#### 3.3.5 Agent Worker Task

**File:** `backend/app/workers/tasks_agent.py`

Replace the stub with a real Celery task:

```python
@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def execute_agent_run(self, agent_run_id: int):
    """
    1. Load agent_run from DB
    2. Get/create MCP client connection
    3. Create agent via agent_factory
    4. Load session history from Redis (redis_memory.get_session_history)
    5. Call agent.ainvoke() or agent.invoke()
    6. Extract final answer and tool-call steps
    7. Save steps to agent_run_steps table
    8. Update agent_run status + answer
    9. Save updated history to Redis (redis_memory.save_session_history)
    10. On failure: retry or mark failed with error
    """
```

#### 3.3.6 Agent Service

**File:** `backend/app/services/agent_service.py`

Implement the service that:
- Validates the agent-run request
- Creates the `agent_run` DB record (status=queued)
- Dispatches the Celery task
- Returns 202 Accepted with the agent_run ID
- Provides query methods for agent-run detail and steps

#### 3.3.7 Agent Steps Frontend Component

**File:** `frontend/src/components/AgentSteps.tsx`

Build the UI to display:
- Tool calls (tool name, arguments, status)
- LLM reasoning steps
- Final answer display
- Error states (MCP unavailable, tool failure, timeout)

Additional Stage-2+ shared components to create alongside it (none exist yet under `src/components/`): `TaskInput.tsx` (natural-language task entry), `ResultPanel.tsx` (structured result display), `DrawingPreview.tsx` (drawing preview), `ReviewPanel.tsx` (review decision UI).

### 3.4 Config Checklist

When ready to enable Stage 2, set these in `.env`:

```bash
# Enable the Agent subsystem
AGENT_ENABLED=true

# LLM (DeepSeek -- OpenAI-compatible)
MODEL_NAME=deepseek-chat
MODEL_API_KEY=sk-your-key-here
MODEL_BASE_URL=https://api.deepseek.com

# MCP CAD tool server
MCP_CAD_COMMAND=uvx
MCP_CAD_ARGS=cad-mcp-server,stdio

# Redis (already running, memory TTL config)
REDIS_MEMORY_TTL=7200
REDIS_MAX_MESSAGES=20

# Celery (already configured via redis component fields)
# CELERY_BROKER_URL and CELERY_RESULT_BACKEND are auto-computed
```

### 3.5 Interface Contracts: Stage 1 to Stage 2

These API contracts are already defined in the Stage 1 codebase. No changes to the resource model are needed; only the behavior changes from 503 to real execution.

#### POST /api/v1/agent-runs

**Request:**
```json
{
  "session_id": "sess_abc123",
  "task": "Extract all layers and text from this DWG file",
  "file_id": 1001,
  "context": {
    "project_id": 1,
    "drawing_id": 123
  }
}
```

**Response (202 Accepted):**
```json
{
  "data": {
    "id": 9001,
    "session_id": "sess_abc123",
    "status": "queued",
    "answer": null,
    "history_count": 4,
    "created_at": "2026-07-03T10:00:00+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

#### GET /api/v1/agent-runs/{agent_run_id}

**Response (200 OK, after completion):**
```json
{
  "data": {
    "id": 9001,
    "session_id": "sess_abc123",
    "status": "succeeded",
    "answer": "Extraction complete. Found 18 layers and 326 text objects. Results saved.",
    "steps": [
      {
        "type": "tool_call",
        "title": "Parse DXF entities",
        "tool_name": "parse_dxf_entities",
        "arguments": {"file_id": 1001},
        "status": "success"
      }
    ],
    "output_file_id": 2001,
    "history_count": 6,
    "created_at": "2026-07-03T10:00:00+08:00",
    "started_at": "2026-07-03T10:00:01+08:00",
    "finished_at": "2026-07-03T10:00:15+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:20+08:00"
  }
}
```

#### GET /api/v1/agent-tools

**Response (200 OK):**
```json
{
  "data": [
    {
      "name": "list_project_files",
      "description": "List all files in a given project.",
      "parameters": {
        "project_id": {"type": "integer", "description": "Project ID"}
      }
    },
    {
      "name": "convert_dwg_to_dxf",
      "description": "Convert a DWG file to DXF format.",
      "parameters": {
        "file_id": {"type": "integer", "description": "Source DWG file ID"}
      }
    },
    {
      "name": "parse_dxf_entities",
      "description": "Parse entities (layers, texts, lines) from a DXF file and return structured JSON.",
      "parameters": {
        "file_id": {"type": "integer", "description": "DXF file ID"}
      }
    },
    {
      "name": "dispatch_to_zwcad_worker",
      "description": "Dispatch a high-precision task to the ZWCAD Windows worker.",
      "parameters": {
        "file_id": {"type": "integer", "description": "Source DWG file ID"},
        "task_type": {"type": "string", "description": "Type of CAD task"}
      }
    },
    {
      "name": "generate_report",
      "description": "Generate an Excel or PDF report from analysis results.",
      "parameters": {
        "result_id": {"type": "integer", "description": "Analysis result ID"},
        "format": {"type": "string", "enum": ["xlsx", "pdf"], "description": "Report format"}
      }
    }
  ],
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

### 3.6 Migration Steps: Stage 1 → Stage 2

**Step-by-step checklist for the integration engineer:**

1. **Enable feature flag**
   - Set `AGENT_ENABLED=true` in `.env`
   - Verify: `GET /api/v1/agent-runs/1` no longer returns 503 (may return 404 if no runs)

2. **Configure LLM credentials**
   - Set `MODEL_API_KEY` with a valid DeepSeek API key
   - Verify connectivity: curl DeepSeek API directly

3. **Start Celery workers**
   ```bash
   # Option A: Docker Compose with workers profile
   docker compose --profile workers up -d

   # Option B: Local dev
   celery -A app.workers.celery_app worker -Q agent -n agent@%h --concurrency=2
   ```

4. **Start MCP server**
   - Ensure the CAD MCP server is running and accessible
   - Verify: MCP client `list_tools()` returns expected tool list

5. **Verify Redis memory**
   - `redis_memory.py` is already tested; verify with live Redis:
   ```bash
   redis-cli KEYS "agent:memory:*"
   ```

6. **Run integration smoke test**
   ```bash
   # Create an agent run
   curl -X POST http://localhost:8000/api/v1/agent-runs \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"session_id":"test","task":"list available tools"}'

   # Poll status (replace 9001 with returned ID)
   curl http://localhost:8000/api/v1/agent-runs/9001 \
     -H "Authorization: Bearer $TOKEN"
   ```

7. **Frontend verification**
   - Navigate to a drawing detail page
   - Enter a natural language task in the Agent input component
   - Verify AgentSteps component renders tool calls and final answer

8. **Rollback plan**
   - Set `AGENT_ENABLED=false` -- all 4 agent endpoints return 503 again
   - Stop Celery workers -- jobs remain in DB, no data loss
   - Agent runs created during Stage 2 remain queryable (historical data)

---

## 4. Stage 3: DXF Pipeline -- IMPLEMENTED (flag-gated off by default)

> **Status update:** all three conversion pipelines below are fully wired to real engines and enabled only via feature flags (all default `False`). Earlier drafts of this roadmap described Stage 3 as "planned / no DWG→DXF conversion"; the code now contradicts that and the pipelines are live.

### 4.1 Scope

Stage 3 delivers three open-source conversion pipelines. Each shares the same state-machine shape: `queued→running`, one `job_steps` row per stage, `publish_job_event` progress on Redis channel `job:events:{job_id}`, persist output via `save_bytes_as_file`, register an `AnalysisResult`; failures set `job.status`/`error_code` without raising (except on environment errors such as a missing ODA binary).

| Pipeline | `task_type` | `pipeline` | Engine | Enable flag | Output bucket |
|----------|-------------|-----------|--------|-------------|---------------|
| DWG → DXF (forward) | `convert_dwg_to_dxf` | `dxf_open_source` | ODA File Converter subprocess (`Stages/dwg2dxf`, `xvfb-run` headless) | `DXF_PIPELINE_ENABLED` | `dxf-derived` |
| DXF → DWG (reverse) | `convert_dxf_to_dwg` | `dxf2dwg_open_source` | ODA File Converter subprocess (`Stages/dxf2dwg`) | `DXF2DWG_PIPELINE_ENABLED` | `dwg-derived` |
| DXF → Excel (batch material-table) | `extract_dxf_to_excel` | `dxf2excel` | Pure-Python grid/table recovery (`Stages/dxf2excel`) | `DXF2EXCEL_PIPELINE_ENABLED` | `dwg-reports` |

**Engine note:** the DWG↔DXF conversion path is an **ODA File Converter** subprocess, *not* ezdxf. ezdxf is only an optional parse-time dependency (used by `dxf_stats` for entity-count summaries). The DXF→Excel path is pure Python (blocks→grid→cells→classify→normalize), no ODA.

**Out of scope (→ Stage 4, ZWCAD):**
- High-precision measurement
- Complex dynamic blocks
- 3D solids
- Proxy objects

### 4.2 Pipeline Flow (all three)

```
Source file (MinIO / local storage)
  ↓ stage to worker sandbox (download_source_* step)
ODA File Converter subprocess  |  dxf2excel pure-Python pipeline
  ↓ convert / extract (run_oda_convert* | run_dxf2excel_pipeline step)
converted artifact (.dxf / .dwg / .xlsx)
  ↓ save_bytes_as_file → output bucket (persist_* step)
AnalysisResult row (MySQL index) + job_steps + SSE events
  ↓
job.status = succeeded   (failure → job.status=failed + error_code, no raise)
```

Job-step names per pipeline (from `app/core/constants.py`):
- **DWG→DXF:** `download_source_dwg` → `run_oda_convert` → `persist_dxf_result`
- **DXF→DWG:** `download_source_dxf` → `run_oda_convert_dxf` → `persist_dwg_result`
- **DXF→Excel:** `download_dxf_batch` → `run_dxf2excel_pipeline` → `persist_excel_result`

### 4.3 What Is Implemented

| Component | File | Status |
|-----------|------|--------|
| DWG→DXF service | `backend/app/services/dxf_service.py` | Real -- stages source, calls `dwg_converter.convert_file` (ODA), persists DXF, DWG-header→version auto-map |
| DXF→DWG service | `backend/app/services/dxf2dwg_service.py` | Real -- reverse conversion; `$ACADVER` detection + reverse-lookup of original DWG version via `AnalysisResult.tool_version` |
| DXF→Excel service | `backend/app/services/dxf2excel_service.py` | Real -- batch (N DXF → 1 `.xlsx`); Redis progress caching + per-file SSE progress |
| DXF stats helper | `backend/app/services/dxf_stats.py` | Real -- stdlib DXF entity/section counter (no ezdxf dependency) |
| DWG→DXF worker task | `backend/app/workers/tasks_dxf.py` | Real -- `convert_dwg_to_dxf` on queue `dxf`, delegates to `dxf_service` |
| DXF→DWG worker task | `backend/app/workers/tasks_dxf2dwg.py` | Real -- `convert_dxf_to_dwg` on queue `dxf2dwg` |
| DXF→Excel worker task | `backend/app/workers/tasks_dxf2excel.py` | Real -- `extract_dxf_to_excel` on queue `dxf2excel` |
| SSE progress channel | `backend/app/services/job_events.py` | Real -- Redis pub/sub `job:events:{job_id}`, keepalive, terminal fallback |
| Feature flags | `backend/app/core/config.py` | `dxf_pipeline_enabled`, `dxf2dwg_pipeline_enabled`, `dxf2excel_pipeline_enabled` (all default `False`) |
| Job gating | `backend/app/api/v1/jobs_api.py` | `POST /jobs` returns 503 `DXF_PIPELINE_DISABLED`/`DXF2DWG_PIPELINE_DISABLED`/`DXF2EXCEL_PIPELINE_DISABLED` per `task_type` when the flag is off |
| ODA health endpoint | `backend/app/api/v1/system_api.py` | `GET /api/v1/system/health/oda` → `dwg_converter.framework.health_check` (`oda_found`, `oda_executable`, `ezdxf_available`) |
| Conversion engines | `Stages/{dwg2dxf,dxf2dwg,dxf2excel}` | Real editable path-dep packages; ODA AppImage baked into Docker image at `/app/oda` |
| Docker Compose worker | `compose.yaml` | `worker-dxf` (queue `dxf`) under `profiles: [workers]`; `dxf2dwg`/`dxf2excel` queues routed in `celery_app.task_routes` and launched by `scripts/lib.sh` locally |

### 4.4 Remaining Work (Stage 3 → Stage 5 enrichment)

The conversion + persistence backbone is complete. Not yet built:

1. Rich structured entity extraction (`entities.json` with per-entity geometry) beyond the current `dxf_stats` entity-count summary -- a Stage-5 extraction item.
2. Confidence scoring + automatic `< 0.85 → need_review` routing (constants `JOB_VALIDATING`/`JOB_NEED_REVIEW` exist; auto-flagging not yet wired).
3. Frontend structured result panel (`frontend/src/components/ResultPanel.tsx` is still a stub).

### 4.5 Interface Contract: DXF Conversion Result

On success each conversion registers an `AnalysisResult` row (bucket per the table in §4.1) plus one `job_steps` row per stage. `AnalysisResult` carries `result_type` (= `task_type`), `result_file_id` (the converted artifact), `tool_version` (source CAD version, used for round-trip fidelity), and a `result_json` summary. The `dxf_stats` helper contributes an entity/section count summary, e.g.:

```json
{
  "source": "dxf",
  "converter": "oda_file_converter",
  "tool_version": "ACAD2018",
  "stats": {
    "total_entities": 1247,
    "text_count": 326,
    "line_count": 580,
    "circle_count": 45,
    "arc_count": 89,
    "insert_count": 207
  },
  "layers": ["0", "DIM", "TEXT", "STEEL", "CONCRETE"]
}
```

> The richer per-entity `entities.json` schema (individual TEXT/LINE/CIRCLE/INSERT records with coordinates) is a **Stage-5 extraction enhancement**, not produced by the current Stage-3 conversion path.

### 4.6 Interface Contract: DXF Job Creation

**Request (`POST /api/v1/jobs`)** -- `JobCreate` fields: `drawing_id`, `project_id`, `task_type` (lowercase snake_case, `^[a-z][a-z0-9_]+$`), `precision_level` (default `normal`), `params` (free-form dict; `$`/`__`/`constructor` keys rejected):

```json
{
  "drawing_id": 123,
  "project_id": 1,
  "task_type": "convert_dwg_to_dxf",
  "precision_level": "normal",
  "params": {}
}
```

For DXF→Excel, `params` carries the source grouping, e.g. `{"batch_name": "shop-drawings-2026-07"}`, and N DXF files in that batch produce one `.xlsx`.

**Response (202 Accepted)** -- the server maps `task_type`→`pipeline` (`convert_dwg_to_dxf`→`dxf_open_source`, `convert_dxf_to_dwg`→`dxf2dwg_open_source`, `extract_dxf_to_excel`→`dxf2excel`, anything else→`local_stub`):

```json
{
  "data": {
    "id": 456,
    "status": "queued",
    "pipeline": "dxf_open_source",
    "task_type": "convert_dwg_to_dxf",
    "progress": 0,
    "created_at": "2026-07-03T10:00:00+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

If the matching pipeline flag is off, `POST /jobs` returns **503** with code `DXF_PIPELINE_DISABLED` / `DXF2DWG_PIPELINE_DISABLED` / `DXF2EXCEL_PIPELINE_DISABLED`.

---

## 5. Stage 4: Windows CAD Worker -- TECHNICAL SPECIFICATION

### 5.1 Scope

Stage 4 implements the high-precision CAD processing pipeline using ZWCAD on a separate Windows node.

**In scope:**
- ASP.NET Core Worker Service (C#)
- ZWCAD .NET API integration
- Pull-based task dispatch: `GET /api/v1/internal/cad-worker/jobs/next`
- DWG download to local sandbox
- Open in ZWCAD → load C# plugin → execute task
- Export `cad_result.json` → upload to MinIO
- PATCH job status back to FastAPI
- CAD crash recovery and license-unavailable error codes

**Out of scope:**
- Running ZWCAD inside Linux Docker
- Managing business users/permissions/projects (FastAPI owns this)

### 5.2 Pull-Based Task Model

```
CAD Worker (Windows)              FastAPI Backend (Linux)
      │                                  │
      ├── POST /heartbeats ────────────→ │  (register, periodic)
      │                                  │
      ├── GET /jobs/next ──────────────→ │  (poll for work)
      │←─ 200 {job_id, file_url, ...} ──┤
      │                                  │
      │  [download DWG to sandbox]       │
      │  [open ZWCAD, execute task]     │
      │  [export cad_result.json]        │
      │  [upload result to MinIO]        │
      │                                  │
      ├── PATCH /jobs/{job_id} ────────→ │  (update status + result)
      │←─ 200 OK ───────────────────────┤
      │                                  │
      ├── GET /jobs/next ──────────────→ │  (poll next)
```

### 5.3 What Already Exists

| Component | File | Status |
|-----------|------|--------|
| ZWCAD client stub | `backend/app/integrations/zwcad/client.py` | Placeholder -- needs real HTTP client |
| ZWCAD schemas stub | `backend/app/integrations/zwcad/schemas.py` | Placeholder -- needs Pydantic models |
| CAD worker task stub | `backend/app/workers/tasks_cad.py` | Placeholder -- needs dispatch task |
| Config fields | `backend/app/core/config.py` | `cad_worker_api_base`, `cad_worker_api_key` ready |
| Feature flag | `backend/app/core/config.py` | `cad_worker_enabled: bool = False` |
| Docker Compose worker | `compose.yaml` | Comment-only reference — `worker-cad-dispatch` reserved for Stage 4 implementation |

### 5.4 What Needs to Be Built

#### Backend (Linux side)

1. **Internal CAD Worker API** (`backend/app/api/v1/internal/`)
   - `GET /api/v1/internal/cad-worker/jobs/next` -- return next pending CAD job
   - `PATCH /api/v1/internal/cad-worker/jobs/{job_id}` -- update job status/results
   - `POST /api/v1/internal/cad-worker/heartbeats` -- worker health reporting
   - All secured with `X-API-Key` header validation

2. **ZWCAD integration client** (`backend/app/integrations/zwcad/client.py`)
   - `dispatch_job(job_id, task_type, params)` -- create CAD dispatch record
   - `handle_callback(job_id, status, result_json)` -- process worker callback
   - `get_worker_status()` -- query worker health

3. **CAD dispatch worker task** (`backend/app/workers/tasks_cad.py`)
   - Poll for CAD worker availability
   - Dispatch job to Windows worker via internal API
   - Monitor timeout and retry logic

#### Windows Worker (C# side)

```
cad-worker/
├── ZwCadWorker.Api/              ASP.NET Core Worker Service
│   ├── Program.cs                Service entry point
│   ├── Controllers/
│   │   └── InternalController.cs  (if using API pattern)
│   └── appsettings.json
├── ZwCadWorker.Core/             Core models and protocols
│   ├── Models/
│   │   ├── CadJob.cs
│   │   ├── CadResult.cs
│   │   └── Heartbeat.cs
│   └── Services/
│       ├── JobPoller.cs          Poll GET /jobs/next
│       ├── JobExecutor.cs        Orchestrate CAD execution
│       └── ResultUploader.cs     Upload to MinIO
├── ZwCadWorker.Plugin/           ZWCAD .NET plugin
│   ├── CadCommands.cs            CAD API commands
│   ├── LayerExtractor.cs
│   ├── TextExtractor.cs
│   ├── DimensionExtractor.cs
│   └── GeometryExtractor.cs
├── ZwCadWorker.Infrastructure/
│   ├── BackendClient.cs          HTTP client to FastAPI
│   ├── MinioClient.cs            Upload results
│   ├── SandboxManager.cs         Per-job sandbox
│   └── LicenseChecker.cs         ZWCAD license status
└── tests/
```

### 5.5 Interface Contract: CAD Worker Internal API

All internal endpoints require `X-API-Key` header matching `CAD_WORKER_API_KEY`.

#### GET /api/v1/internal/cad-worker/jobs/next

**Response (200 OK -- job available):**
```json
{
  "data": {
    "job_id": 789,
    "task_type": "extract_dimensions",
    "drawing_id": 123,
    "params": {
      "precision": "high",
      "units": "mm"
    },
    "file_download_url": "http://minio:9000/dwg-original/project/1/drawing/123/v2/source.dwg?signature=...",
    "file_sha256": "abc123def456..."
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

**Response (200 OK -- no jobs):**
```json
{
  "data": null,
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

#### PATCH /api/v1/internal/cad-worker/jobs/{job_id}

**Request:**
```json
{
  "status": "succeeded",
  "progress": 100,
  "result": {
    "cad_result_file_key": "dwg-derived/project/1/drawing/123/job/789/cad_result.json",
    "preview_file_key": "dwg-derived/project/1/drawing/123/job/789/cad_preview.png",
    "summary": {
      "layers_extracted": 18,
      "dimensions_extracted": 245,
      "texts_extracted": 326,
      "blocks_identified": 42
    }
  }
}
```

**Response (200 OK):**
```json
{
  "data": {
    "job_id": 789,
    "status": "succeeded",
    "updated_at": "2026-07-03T10:05:00+08:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

#### POST /api/v1/internal/cad-worker/heartbeats

**Request:**
```json
{
  "worker_id": "cad-worker-01",
  "status": "idle",
  "zwcad_version": "2025",
  "license_available": true,
  "active_jobs": 0,
  "cpu_percent": 12.5,
  "memory_mb": 2048
}
```

**Response (200 OK):**
```json
{
  "data": {
    "acknowledged": true,
    "server_time": "2026-07-03T10:00:00+08:00"
  }
}
```

### 5.6 Interface Contract: cad_result.json Output Schema

```json
{
  "source": "zwcad",
  "cad_version": "ZWCAD 2025",
  "plugin_version": "1.0.0",
  "drawing_units": "mm",
  "layers": [
    {"name": "0", "color": 7, "line_type": "Continuous", "entity_count": 0},
    {"name": "DIM", "color": 3, "line_type": "Continuous", "entity_count": 245},
    {"name": "TEXT", "color": 2, "line_type": "Continuous", "entity_count": 326},
    {"name": "STEEL", "color": 1, "line_type": "Continuous", "entity_count": 580}
  ],
  "texts": [
    {
      "layer": "TEXT",
      "content": "BH650*300*14*24",
      "position": {"x": 120.5, "y": 88.0, "z": 0.0},
      "rotation": 0.0,
      "height": 3.5,
      "style": "STANDARD",
      "alignment": "Left"
    }
  ],
  "dimensions": [
    {
      "layer": "DIM",
      "type": "AlignedDimension",
      "value": 6000.0,
      "unit": "mm",
      "tolerance": null,
      "definition_points": [
        {"x": 0.0, "y": 0.0},
        {"x": 6000.0, "y": 0.0}
      ]
    }
  ],
  "blocks": [
    {
      "name": "BEAM_SECTION",
      "layer": "STEEL",
      "insertions": [
        {"position": {"x": 200.0, "y": 150.0}, "scale": {"x": 1.0, "y": 1.0, "z": 1.0}}
      ],
      "attributes": {
        "SECTION_ID": "B-001",
        "STEEL_GRADE": "Q345B"
      }
    }
  ],
  "geometry_summary": {
    "lines": 580,
    "polylines": 124,
    "circles": 45,
    "arcs": 89,
    "splines": 12
  },
  "execution": {
    "started_at": "2026-07-03T10:00:05+08:00",
    "finished_at": "2026-07-03T10:04:58+08:00",
    "duration_seconds": 293
  }
}
```

---

## 6. Stage 5: Business Algorithms

### 6.1 Scope

Stage 5 layers specific business algorithms on top of the raw extraction pipelines from Stages 3 and 4.

| Algorithm | Input | Output | Pipeline |
|-----------|-------|--------|----------|
| **LaR left-right entry recognition** | entities.json or cad_result.json | Direction-labeled entities | DXF or CAD |
| **Component list comparison** | Two drawing versions | Diff report (added/removed/changed) | DXF or CAD |
| **Material table extraction** | Batch of DXF files (`batch_name`) | Structured material list → `.xlsx` | DXF→Excel (**already shipped in Stage 3**, `extract_dxf_to_excel`) |
| **Report generation** | Analysis results | Excel, PDF, or ZIP | Report worker |
| **Batch task orchestration** | Multiple drawings | Batch result summary | All workers |
| **Human review closed loop** | need_review results | Approved/rejected with feedback | Review API |

### 6.2 Dependencies

- Stage 3 (DXF Pipeline) for low-precision extraction
- Stage 4 (CAD Worker) for high-precision extraction
- Stage 2 (Agent) for natural language task decomposition (optional, but recommended)
- Report worker queue (defined in `compose.yaml`, needs real implementation)

---

## 7. Stage 6: Production Hardening

### 7.1 Scope (Prioritized)

| Priority | Item | Description |
|----------|------|-------------|
| P0 | **Backup/restore strategy** | MySQL dumps, MinIO bucket sync, disaster recovery runbook |
| P0 | **CI/CD pipeline** | Automated test → build → deploy pipeline for both backend and frontend |
| P0 | **Rate limiting** | Per-user and per-IP rate limiting on auth and upload endpoints |
| P1 | **Monitoring** | Prometheus metrics (API latency, worker queue depth, error rates), Grafana dashboards |
| P1 | **Log aggregation** | Loki for centralized log querying across all containers |
| P1 | **Admin token management** | Admin endpoint for token introspection and manual revocation (basic jti-blacklist and password-change invalidation already implemented) |
| P2 | **RabbitMQ broker** | Replace Redis as Celery broker for production reliability (optional) |
| P2 | **Multi CAD Worker scaling** | Load-balanced CAD Worker nodes with health-aware dispatch |
| P3 | **Chunked upload** | Resumable large file uploads with progress tracking |

---

## 8. Interface Contracts Appendix

### 8.1 Pipeline Selection Logic (Stage 2+)

The system must deterministically route tasks to the correct pipeline. This logic lives in the job service, not in the LLM Agent.

> **Current implementation** (`job_service._pipeline_for`) maps `task_type` directly to a pipeline (see §4.6: `convert_dwg_to_dxf`→`dxf_open_source`, `convert_dxf_to_dwg`→`dxf2dwg_open_source`, `extract_dxf_to_excel`→`dxf2excel`, else `local_stub`). The confidence/precision-based fallback routing below is the **Stage-4 target** once the ZWCAD worker exists (`waiting_cad_worker` state + `zwcad_worker` pipeline constants are defined but not yet routed).

```
if user specifies precision_level == "high":
    → CAD Worker (Stage 4)
elif task_type in ("precise_measurement", "dimension_extraction"):
    → CAD Worker (Stage 4)
elif DXF conversion fails:
    → CAD Worker (Stage 4) as fallback
elif DXF parsing confidence < 0.85:
    → CAD Worker (Stage 4) as fallback
elif drawing contains complex_blocks, proxy_objects, 3d_solids:
    → CAD Worker (Stage 4)
else:
    → DXF Pipeline (Stage 3)
```

### 8.2 Job Status State Machine (All Stages)

```
                    ┌─────────┐
                    │ pending │
                    └────┬────┘
                         ↓
                    ┌─────────┐
              ┌─────│ queued  │
              │     └────┬────┘
              │          ↓
              │     ┌─────────┐
              │     │ running │──────────┐
              │     └────┬────┘          │
              │          ↓               ↓
              │     ┌──────────────────────┐
              │     │ waiting_cad_worker   │ (Stage 4 only)
              │     └─────────┬────────────┘
              │               ↓
              │          ┌──────────┐
              │          │validating│
              │          └────┬─────┘
              │               ↓
              │     ┌─────────┴─────────┐
              │     ↓                   ↓
              │ ┌──────────┐    ┌────────────┐
              │ │need_review│    │ succeeded  │
              │ └─────┬────┘    └────────────┘
              │       ↓ (after review)
              │  ┌──────────┐
              │  │ succeeded│
              │  └──────────┘
              │
              ↓
         ┌─────────┐
    ┌───→│ failed  │←──── retry (only from failed/cancelled)
    │    └─────────┘
    │
    │    ┌───────────┐
    └───→│ cancelled │←──── cancel (only from queued/running)
         └───────────┘
```

### 8.3 Agent-MCP Tool Boundary (Stage 2 → Stage 3/4)

The Agent (Stage 2) sees tools as a flat list. Tool implementations route to the correct backend:

| Tool Name | Backend | Stage |
|-----------|---------|-------|
| `list_project_files` | FastAPI service | Stage 2 |
| `get_file_metadata` | FastAPI service | Stage 2 |
| `create_processing_job` | FastAPI service | Stage 2 |
| `get_job_status` | FastAPI service | Stage 2 |
| `convert_dwg_to_dxf` | Celery DXF worker | Stage 3 |
| `convert_dxf_to_dwg` | Celery DXF2DWG worker | Stage 3 |
| `extract_dxf_to_excel` | Celery DXF2Excel worker | Stage 3 |
| `parse_dxf_entities` | Celery DXF worker | Stage 3 |
| `extract_layers` | Celery DXF worker | Stage 3 |
| `extract_texts` | Celery DXF worker | Stage 3 |
| `extract_blocks` | Celery DXF worker | Stage 3 |
| `dispatch_to_zwcad_worker` | Celery CAD dispatch → Windows Worker | Stage 4 |
| `validate_analysis_result` | FastAPI service | Stage 3+ |
| `generate_report` | Celery report worker | Stage 5 |
| `create_review_record` | FastAPI service | Stage 2+ |

### 8.4 Error Code Convention for Worker Communication

All workers (DXF, CAD, Report) must report errors using these standard codes:

| Code | Meaning | Retryable |
|------|---------|-----------|
| `DWG_CONVERSION_FAILED` | DWG→DXF conversion error | No |
| `DXF_PARSE_ERROR` | ezdxf parsing failure | No |
| `CAD_OPEN_FAILED` | ZWCAD could not open file | Yes |
| `CAD_CRASH` | ZWCAD process crashed | Yes |
| `CAD_LICENSE_UNAVAILABLE` | ZWCAD license not available | Yes (wait and retry) |
| `CAD_TIMEOUT` | Task exceeded max execution time | Yes |
| `SANDBOX_ERROR` | Could not create/clean sandbox | No |
| `UPLOAD_FAILED` | Could not upload result to MinIO | Yes |
| `SCHEMA_VALIDATION_FAILED` | Result JSON does not match schema | No |
| `UNKNOWN_ERROR` | Unclassified failure | No |

**Live Stage-3 codes** (actually emitted today by the DXF services): `DXF_CONVERSION_FAILED` / `DXF_SOURCE_MISSING` (DWG→DXF, `dxf_service`), `DWG_CONVERSION_FAILED` / `DXF_SOURCE_FILE_MISSING` (DXF→DWG, `dxf2dwg_service`), and `DXF2EXCEL_EMPTY_BATCH` / `DXF2EXCEL_PIPELINE_FAILED` / `DXF2EXCEL_NO_OUTPUT` / `DXF2EXCEL_UNAVAILABLE` / `DXF2EXCEL_STORAGE_FAILED` (DXF→Excel, `dxf2excel_service`). At the API layer, `POST /jobs` gates disabled pipelines with 503 `DXF_PIPELINE_DISABLED` / `DXF2DWG_PIPELINE_DISABLED` / `DXF2EXCEL_PIPELINE_DISABLED`.

---

## 9. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| MCP CAD server not available / unstable | Medium | Stage 2 blocked | MCP client must handle disconnect gracefully; 503 not 500 |
| DeepSeek API rate limit or outage | Medium | Agent unavailable | Queue agent runs; timeout + retry in worker |
| DWG Converter not producing valid DXF | Medium | Stage 3 incomplete | Fallback to CAD Worker; flag file for manual processing |
| ZWCAD license server unreachable | Low | Stage 4 blocked | Queue jobs; clear error code; alerting |
| CAD Worker node hardware failure | Low | Stage 4 downtime | Multi-node deployment (Stage 6); job queue persists in MySQL |
| Schema drift between backend and CAD Worker | Medium | Integration failures | Versioned API; JSON schema validation on both sides |
| Redis memory loss (no persistence configured) | Low | Lost session history | AOF enabled in Docker config; TTL provides auto-cleanup |

---

## 10. Success Metrics (per Stage)

### Stage 1 (Baseline)
- [x] 73 API endpoints (+ root `GET /health`) operational
- [x] 432 tests passing
- [x] RBAC with 7 global + 4 project roles
- [x] DWG upload with header validation
- [x] Job lifecycle from queued to succeeded
- [x] Audit log write-through

### Stage 2 (Target)
- [ ] Agent responds to natural language tasks within 30 seconds
- [ ] MCP tool calls succeed at > 95% rate
- [ ] Redis session memory preserves context across 20+ messages
- [ ] Agent unavailable → 503 (not 500, not crash)
- [ ] AgentSteps frontend component renders tool calls and answers

### Stage 3 (Target)

_(Pipelines implemented and flag-gated off; the rates below are runtime targets pending enablement with a real ODA binary.)_
- [ ] DWG→DXF conversion success rate > 90% for standard DWG files
- [ ] entities.json extraction completes within 60 seconds for < 50MB files
- [ ] Low-confidence detection correctly flags > 80% of problematic files

### Stage 4 (Target)
- [ ] CAD Worker processes 1 job per 5 minutes (sustained)
- [ ] CAD crash recovery within 60 seconds
- [ ] License unavailable → clear error code within 10 seconds of detection

### Stage 5 (Target)
- [ ] LaR recognition accuracy > 95%
- [ ] Component list comparison identifies > 98% of changes
- [ ] Report generation completes within 30 seconds

### Stage 6 (Target)
- [ ] 99.5% API uptime
- [ ] P95 API latency < 500ms
- [ ] Backup RPO < 1 hour
- [ ] Multi-worker scaling to 3+ CAD Worker nodes
