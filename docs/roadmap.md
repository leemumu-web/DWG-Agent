# DWG-Agent Platform Roadmap

> Product owner's and integration engineer's view of the 6-stage delivery plan.
> Current phase: **Stage 1 complete, Stage 2 next.**
> Spec authority: `DWG-Agent企业平台技术规范.md` sections 11-15, 23.

---

## 1. Stage Overview

| Stage | Name | Status | Key Deliverables | Dependencies | Est. Effort |
|-------|------|--------|------------------|--------------|-------------|
| **1** | Platform Skeleton | **DONE** | Auth, RBAC, projects, file upload, job lifecycle, audit, 64 API endpoints, 432 tests, React frontend (10 pages), MinIO/Celery deployment base | None | Completed |
| **2** | Agent Subsystem | **NEXT** | LangGraph `create_react_agent`, DeepSeek LLM, MCP client, Redis session memory, Agent Celery task body, `/api/v1/agent-runs` live, AgentSteps UI | Stage 1 | 2-3 weeks |
| **3** | DXF Pipeline | Planned | DWG Converter abstraction, ezdxf parsing Worker, entities.json extraction, structured result display, low-confidence review | Stage 2 (for Agent tool integration) | 2-3 weeks |
| **4** | Windows CAD Worker | Planned | ASP.NET Core Worker Service, ZWCAD API integration, pull-based task dispatch, cad_result.json export, CAD crash recovery | Stage 1 (internal API), Stage 2 (for dispatch tool) | 3-4 weeks |
| **5** | Business Algorithms | Planned | LaR left-right entry, component list comparison, material table extraction, Excel/PDF/ZIP reports, batch tasks, review closed loop | Stage 3, Stage 4 | 4-6 weeks |
| **6** | Production Hardening | Planned | RabbitMQ (optional), Prometheus/Grafana, Loki, backup/restore, CI/CD, multi-CAD-Worker scaling, rate limiting, token blacklist middleware | Stage 5 | Ongoing |

---

## 2. Stage 1: Platform Skeleton -- COMPLETION REPORT

### 2.1 Infrastructure

| Component | Status | Details |
|-----------|--------|---------|
| Docker Compose | Config ready, not production-tested | 9 services (nginx, backend-api, worker-agent, worker-dxf, worker-report, mysql, redis, minio, flower); worker-report default, profiles for Agent/DXF and monitoring; `.env.docker.example` template |
| MySQL 8.x | Runtime database | `DATABASE_URL=mysql+pymysql://...`; pool: `pool_size=10, max_overflow=20, pool_recycle=3600`; WAL pragmas; `init.sql` seed script |
| Redis (Valkey) | Deployed and validated | Systemd-managed; `redis_client` (lazy init, no-crash on unavailable), `redis_memory`, `cache_service` all tested; FakeRedis (419 non-real-Redis tests via conftest autouse) + real Redis integration (13 tests) dual-layer validation |
| MinIO | Docker storage backend ready | Three-layer abstraction: `base.py` / `local_storage.py` / `minio_storage.py`; local dev uses local storage, Docker uses MinIO |
| Celery | Stage 1 fake task ready | Real Celery app with Redis broker/result backend; `worker-report` runs `run_stub_job` for queued→running→succeeded flow |
| Nginx | Production + local dev dual config | `infra/nginx/nginx.conf` (Docker), `infra/nginx/nginx.local.conf` (local dev); reverse proxy `/api/v1/*` to backend; SPA static serving |
| Alembic | 3 migration versions | `40452ddd24e7_initial.py` (17 tables) + `b8f9e7d6c5a4_add_missing_timestamp_columns.py` (TimestampMixin fix) + `c3d2e1f0a9b8_fix_audit_logs_resource_id_type.py` (resource_id type fix); `scripts/db.sh migration-test` validates end-to-end |

### 2.2 Backend -- 64 API Endpoints across 11 Route Modules

| Module | Endpoints | Key Features |
|--------|-----------|--------------|
| **Auth** (5) | POST sessions, DELETE sessions/current, POST tokens/refresh, GET me, PATCH password | Login/logout with JWT access token + HttpOnly refresh cookie; token blacklist on logout; password change with old-password verification |
| **Users** (11) | Full CRUD + role management + password reset + disable/enable | Admin-only; soft-delete; `super_admin` protection (can't delete/disable); self-update via `PATCH /users/me`; username pattern `^[a-zA-Z0-9_.@-]+$`; password min 12 chars with complexity |
| **Roles** (4) | GET roles, POST roles, GET permissions, PUT permissions | 7 global roles + 4 project roles; 5 RBAC tables; super_admin bypasses all checks |
| **Projects** (9) | CRUD + member management (4 project roles) | Cascade active-status check (`require_active_project`); deleted projects → 404 for all members; creator auto-assigned `project_owner` |
| **Files** (6) | Upload, list, detail, delete, download-url, download | DWG validation: header (AC1012-AC1032), min 1024 bytes, extension whitelist, SHA-256/MD5 hash; HMAC-signed download URLs (TTL=300s); ownership + project-member access control |
| **Drawings** (8) | CRUD + version management + preview | Auto-increment `version_no`; project-scoped; preview endpoint returns placeholder in Stage 1 |
| **Jobs** (9) | Create, cancel, retry, steps, logs, events, results | State machine: pending→queued→running→succeeded/failed/cancelled; state guards on cancel/retry; Stage 1 stub worker uses Celery worker-report to auto-progress |
| **Results** (4) | Detail, download-url, review submit, review history | `approved`/`rejected` decisions; confidence scoring |
| **Reviews** (1) | Pending list | Filtered by project membership |
| **Audit** (2) | List (last 200), detail | super_admin + auditor only; logs logins, user mgmt, role changes, file ops, job ops, reviews |
| **Agent** (4) | POST agent-runs, GET agent-runs/{id}, GET steps, GET tools | All return 503 when `AGENT_ENABLED=false`; resource model established, no frontend changes needed when enabled |

### 2.3 Frontend -- React 19 + TypeScript + Vite

- **10 pages:** Login, Dashboard, Projects, Drawings, Files, Jobs, Reviews, Admin (Users/Roles/Audit), Profile
- **12 API client files** under `src/api/` (11 modules + client.ts)
- **8 shared components:** FileUpload, TaskInput, AgentSteps, ResultPanel, DrawingPreview, JobTimeline, PermissionGuard, ReviewPanel (6 of 8 are Stage 2+ stubs; only FileUpload and PermissionGuard have full implementations)
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
| Agent/DXF/CAD worker task bodies are stubs | Stage 2-4 |
| Agent returns 503 for all requests | Stage 2 |
| No DWG→DXF conversion | Stage 3 |
| No ZWCAD integration | Stage 4 |
| No SSE event streaming (endpoint defined, returns placeholder) | Stage 2 |
| No chunked upload | Stage 6 |
| Frontend detail pages are basic | Ongoing |
| No admin token introspection/revocation endpoint | Stage 6 |

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

**Files:** `backend/app/workers/tasks_agent.py`, `backend/app/workers/tasks_dxf.py`, `backend/app/workers/tasks_cad.py`

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

## 4. Stage 3: DXF Pipeline -- TECHNICAL SPECIFICATION

### 4.1 Scope

Stage 3 implements the open-source DWG processing pipeline for low/medium precision tasks.

**In scope:**
- DWG Converter abstraction layer (pluggable backend)
- DWG → DXF conversion (via ODA File Converter, LibreDWG, or commercial SDK)
- ezdxf-based entity extraction: layers, texts, blocks, lines, polylines, circles, arcs
- Structured `entities.json` output
- Low-confidence auto-flagging → `need_review` status
- Frontend structured result display

**Out of scope:**
- High-precision measurement (→ Stage 4, ZWCAD)
- Complex dynamic blocks (→ Stage 4)
- 3D solids (→ Stage 4)
- Proxy objects (→ Stage 4)

### 4.2 Pipeline Flow

```
DWG File (MinIO / local storage)
  ↓ download to worker sandbox
DWG Converter (abstraction layer)
  ↓ convert
converted.dxf (temp file)
  ↓ ezdxf read
entities.json (structured output)
  ↓ rule processing
result.json (final analysis)
  ↓ upload to MinIO
analysis_results table (MySQL index)
  ↓ confidence check
≥ 0.85 → succeeded
< 0.85 → CAD Worker (fallback to high-precision pipeline per spec §16.3)
```

### 4.3 What Already Exists

| Component | File | Status |
|-----------|------|--------|
| DXF task stub | `backend/app/workers/tasks_dxf.py` | Placeholder -- needs real DXF processing task |
| Feature flag | `backend/app/core/config.py` | `dxf_pipeline_enabled: bool = False` |
| Docker Compose worker | `compose.yaml` | `worker-dxf` service under `profiles: [workers]` |
| Storage abstraction | `backend/app/storage/` | Base + local + MinIO adapters ready |

### 4.4 What Needs to Be Built

1. **DWG Converter abstraction** (`backend/app/integrations/converter/`)
   - `base.py` -- abstract interface: `convert(dwg_path) -> dxf_path`
   - `oda_converter.py` -- ODA File Converter implementation
   - `libredwg_converter.py` -- LibreDWG/dwg2dxf implementation
   - Config-driven backend selection

2. **DXF parsing service** (`backend/app/services/dxf_service.py`)
   - `extract_layers(dxf_path) -> list[str]`
   - `extract_texts(dxf_path) -> list[dict]`
   - `extract_blocks(dxf_path) -> list[dict]`
   - `extract_geometry(dxf_path) -> list[dict]` (lines, polylines, circles, arcs)
   - `extract_all(dxf_path) -> entities.json`

3. **DXF worker task** (`backend/app/workers/tasks_dxf.py`)
   - Download DWG from storage to sandbox
   - Call DWG Converter
   - Call ezdxf parser
   - Compute confidence score
   - Upload derived files (converted.dxf, entities.json, preview.png)
   - Write `analysis_results` row
   - Transition job status

4. **Structured result frontend** (`frontend/src/components/ResultPanel.tsx`)
   - Layer tree view
   - Entity table with type/layer/coordinates
   - Text content search
   - Confidence indicator

### 4.5 Interface Contract: entities.json Output Schema

```json
{
  "source": "dxf",
  "converter": "oda_file_converter",
  "converter_version": "25.6.0",
  "parser": "ezdxf",
  "parser_version": "1.4.0",
  "confidence": 0.92,
  "layers": ["0", "DIM", "TEXT", "STEEL", "CONCRETE"],
  "entities": [
    {
      "type": "TEXT",
      "layer": "TEXT",
      "text": "BH650*300*14*24",
      "position": [120.5, 88.0],
      "rotation": 0.0,
      "height": 3.5,
      "style": "STANDARD"
    },
    {
      "type": "LINE",
      "layer": "STEEL",
      "start": [0.0, 0.0],
      "end": [1000.0, 0.0]
    },
    {
      "type": "CIRCLE",
      "layer": "DIM",
      "center": [500.0, 300.0],
      "radius": 25.0
    },
    {
      "type": "INSERT",
      "layer": "STEEL",
      "block_name": "BEAM_SECTION",
      "position": [200.0, 150.0],
      "scale": [1.0, 1.0, 1.0],
      "rotation": 0.0
    }
  ],
  "stats": {
    "total_entities": 1247,
    "text_count": 326,
    "line_count": 580,
    "circle_count": 45,
    "arc_count": 89,
    "insert_count": 207
  }
}
```

### 4.6 Interface Contract: DXF Job Creation

**Request (POST /api/v1/jobs):**
```json
{
  "drawing_id": 123,
  "project_id": 1,
  "task_type": "extract_all_dxf",
  "precision_level": "normal",
  "params": {
    "include_hidden_layers": false,
    "export_preview": true,
    "force_dxf_pipeline": true
  }
}
```

**Response (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "status": "queued",
    "pipeline": "dxf_open_source",
    "task_type": "extract_all_dxf",
    "created_at": "2026-07-03T10:00:00+08:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+08:00"
  }
}
```

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
| **Material table extraction** | Drawing with BOM/schedule | Structured material list | DXF or CAD |
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
- [x] 64 API endpoints operational
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
