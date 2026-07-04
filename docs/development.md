# DWG-Agent Development Guide

> **Audience:** Lead developers, new engineers, anyone who needs to understand how this
> codebase is put together and how to contribute to it effectively.
>
> **Status:** Stage 1 complete — platform skeleton with RESTful API, RBAC, file management,
> and job lifecycle. Stage 2 (Agent subsystem), Stage 3 (DXF pipeline), and Stage 4 (ZWCAD
> Worker) are planned but not yet implemented.
>
> **Authority:** Every design decision traces back to `DWG-Agent企业平台技术规范.md` (the
> spec) in the repository root. When in doubt, read the spec first.

---

## 1. Repository Structure Walkthrough

Every directory has a specific reason for existing. Here is what each one does and what
kind of code belongs there.

```
complete_framework/
├── DWG-Agent企业平台技术规范.md   ← Ground truth for all design decisions (v2.0, 1317 lines)
├── CLAUDE.md                      ← Agent instructions — conventions, don'ts, file map
├── README.md                      ← Human-facing project overview
├── compose.yaml                   ← Docker Compose for all services
├── .env.example                   ← Template for local dev environment
├── .env.docker.example            ← Template for Docker Compose environment
├── Makefile                       ← (Planned) convenience targets for common tasks
│
├── backend/                       ← Python 3.12, uv, FastAPI — the main codebase
│   ├── pyproject.toml             ← Dependencies, ruff config, build settings
│   ├── uv.lock                    ← COMMITTED — exact dependency versions
│   ├── .python-version            ← 3.12 (tells uv which Python to use)
│   ├── alembic.ini                ← Alembic migration config (points at app.core.config)
│   ├── Dockerfile                 ← Multi-stage, non-root, HEALTHCHECK
│   ├── .dockerignore
│   ├── app/                       ← All application code
│   │   ├── main.py                ← FastAPI app creation, lifespan, CORS, exception handlers
│   │   ├── api/v1/                ← Route handlers (thin — no business logic)
│   │   │   ├── router.py          ← Central router: mounts all sub-routers under /api/v1
│   │   │   ├── auth_api.py        ← POST /sessions, DELETE /sessions/current, POST /tokens/refresh, GET /me, PATCH /password
│   │   │   ├── users_api.py       ← CRUD users, role assignment, password reset
│   │   │   ├── roles_api.py       ← CRUD roles, permission assignment
│   │   │   ├── projects_api.py    ← CRUD projects, member management
│   │   │   ├── files_api.py       ← Upload, list, download-url, delete
│   │   │   ├── drawings_api.py    ← CRUD drawings, version management
│   │   │   ├── jobs_api.py        ← Create, list, cancel, retry, results
│   │   │   ├── results_api.py     ← Result detail, download-url, review submission, review history
│   │   │   ├── reviews_api.py     ← Pending reviews list
│   │   │   ├── audit_logs_api.py  ← Audit log listing (super_admin/auditor only)
│   │   │   └── agent_runs_api.py  ← (Stage 2 — currently returns 503)
│   │   ├── core/                  ← Cross-cutting infrastructure
│   │   │   ├── config.py          ← pydantic-settings, all env vars, MySQL/Redis/Celery URLs
│   │   │   ├── security.py        ← JWT creation/verification, password hashing (argon2)
│   │   │   ├── permissions.py     ← RBAC permission checking, dependency callables
│   │   │   ├── exceptions.py      ← AppHTTPException (use this, not bare HTTPException)
│   │   │   ├── redis_client.py    ← Lazy-init sync Redis client (safe when unavailable)
│   │   │   ├── logger.py          ← Structured logging
│   │   │   ├── validators.py      ← Field-level validators (phone, password, etc.)
│   │   │   └── constants.py       ← Enums, string constants
│   │   ├── db/                    ← Database setup
│   │   │   ├── base.py            ← SQLAlchemy declarative Base
│   │   │   ├── session.py         ← Engine creation (MySQL/SQLite), WAL pragmas, get_db generator
│   │   │   └── init_db.py         ← Seed data: default roles, permissions, admin user
│   │   ├── models/                ← SQLAlchemy ORM models (10 files)
│   │   │   ├── mixins.py          ← TimestampMixin (created_at, updated_at, deleted_at)
│   │   │   ├── user.py            ← User model with status, password fields
│   │   │   ├── role.py            ← Role + Permission + association tables
│   │   │   ├── project.py         ← Project + ProjectMember
│   │   │   ├── file.py            ← File metadata (bucket, storage_key, sha256, etc.)
│   │   │   ├── drawing.py         ← Drawing + DrawingVersion
│   │   │   ├── job.py             ← Job + JobStep
│   │   │   ├── agent_run.py       ← AgentRun + AgentRunStep (Stage 2 schema ready)
│   │   │   ├── result.py          ← AnalysisResult + ReviewRecord
│   │   │   └── audit_log.py       ← AuditLog
│   │   ├── schemas/               ← Pydantic v2 request/response models
│   │   │   ├── common.py          ← Shared: pagination params, wrapped response helpers
│   │   │   ├── auth_schema.py     ← Login, Token, PasswordChange
│   │   │   ├── user_schema.py     ← UserCreate, UserUpdate, UserResponse
│   │   │   ├── project_schema.py  ← ProjectCreate, ProjectUpdate, ProjectResponse
│   │   │   ├── file_schema.py     ← FileUpload, FileResponse
│   │   │   ├── drawing_schema.py  ← DrawingCreate, DrawingResponse
│   │   │   ├── job_schema.py      ← JobCreate, JobResponse, JobStepResponse
│   │   │   ├── result_schema.py   ← ResultResponse, ReviewSubmit
│   │   │   ├── audit_schema.py    ← AuditLogResponse
│   │   │   └── agent_schema.py    ← AgentRunCreate, AgentRunResponse (Stage 2)
│   │   ├── services/              ← Business logic — all state-changing operations
│   │   │   ├── auth_service.py    ← Login, logout, token refresh, password change
│   │   │   ├── user_service.py    ← User CRUD, role assignment, enable/disable
│   │   │   ├── project_service.py ← Project CRUD, member management
│   │   │   ├── file_service.py    ← File upload validation, metadata, download URL signing
│   │   │   ├── drawing_service.py ← Drawing/version CRUD, version increment
│   │   │   ├── job_service.py     ← Job lifecycle, status transitions
│   │   │   ├── review_service.py  ← Review submission, pending reviews
│   │   │   ├── agent_service.py   ← Agent execution orchestration (Stage 2)
│   │   │   ├── storage_service.py ← File save, retrieve, delete (local + MinIO)
│   │   │   ├── audit_service.py   ← write_audit_log(), list audit logs
│   │   │   ├── redis_memory.py    ← Agent session memory (Stage 2 infra)
│   │   │   └── cache_service.py   ← Generic cache layer (Stage 2 infra)
│   │   ├── repositories/          ← PLACEHOLDER — empty __init__.py
│   │   │                           (DB access to be extracted from services in Stage 2+)
│   │   ├── agents/                ← PLACEHOLDER — agent_factory, prompts, tool_registry stubs
│   │   ├── mcp_client/            ← PLACEHOLDER — MCP client + adapter stubs
│   │   ├── workers/               ← celery_app + report task active; agent/dxf/cad task stubs
│   │   ├── storage/               ← Storage abstraction layer
│   │   │   ├── base.py            ← Abstract StorageBackend
│   │   │   ├── local_storage.py   ← Local filesystem (active in Stage 1)
│   │   │   └── minio_storage.py   ← MinIO/S3 backend for Docker deployment
│   │   ├── integrations/zwcad/    ← PLACEHOLDER — ZWCAD Worker client + schemas (Stage 4)
│   │   └── utils/                 ← Utility functions
│   │       ├── path_utils.py      ← ensure_within_root() — all file paths MUST pass through this
│   │       ├── file_hash.py       ← SHA-256 computation
│   │       └── time_utils.py      ← Timestamp formatting
│   ├── tests/                     ← 432 tests, 24 test files (pytest)
│   │   ├── conftest.py            ← Autouse fixtures: FakeRedis + in-memory SQLite isolation
│   │   ├── test_health.py         ← Health endpoint
│   │   ├── test_config.py         ← Settings validation (MySQL, Redis, Celery URL computation)
│   │   ├── test_db_session.py     ← Engine creation, session, pragmas
│   │   ├── test_smoke_flow.py     ← Full happy-path: login → create project → upload → job
│   │   ├── test_security_boundaries.py  ← Unauthenticated/unauthorized access tests
│   │   ├── test_api_regressions.py      ← Endpoint contract tests
│   │   ├── test_new_features.py         ← Tests for recently added features
│   │   ├── test_token_lifecycle.py      ← Access/refresh token flow
│   │   ├── test_rigorous.py             ← Edge cases and error handling
│   │   ├── test_deep_verify.py          ← Deeper validation tests
│   │   ├── test_edge_cases.py           ← Boundary condition tests
│   │   ├── test_stage1_boundaries.py    ← Stage 1 scope boundary tests
│   │   ├── test_cache_service.py        ← Cache layer tests (FakeRedis)
│   │   ├── test_redis_client.py         ← Redis client connectivity tests
│   │   ├── test_redis_memory.py         ← Agent memory service tests
│   │   ├── test_redis_real.py           ← Real Redis integration (auto-skipped)
│   │   ├── test_compose.py              ← Docker Compose config validation
│   │   ├── test_celery_minio_deployment.py ← Celery/MinIO deployment config validation
│   │   ├── test_cross_audit_fixes.py     ← Cross-cutting audit fix validation
│   │   ├── test_migrations.py           ← Alembic migration tests
│   │   └── test_scripts.py              ← Shell script validation
│   ├── migrations/               ← Alembic
│   │   ├── env.py                 ← Migration environment (imports Base + all models)
│   │   ├── script.py.mako         ← Template for new migrations
│   │   └── versions/              ← 2 migration scripts
│   └── var/                       ← Runtime data — uploaded files, SQLite DB (gitignored)
│
├── frontend/                      ← React 19 + TypeScript + Vite + Ant Design 6
│   ├── package.json               ← All versions locked — NO "latest"
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts             ← Vite config (no proxy; set VITE_API_BASE_URL for local dev)
│   ├── index.html
│   └── src/
│       ├── main.tsx               ← ReactDOM entry
│       ├── App.tsx                ← Root component
│       ├── api/                   ← All API calls go through here (11 modules)
│       │   ├── client.ts          ← Axios instance with interceptors (auth header, 401 refresh)
│       │   ├── auth.api.ts        ← login, logout, refresh, me, changePassword
│       │   ├── users.api.ts       ← User CRUD
│       │   ├── roles.api.ts       ← Role CRUD
│       │   ├── projects.api.ts    ← Project CRUD, members
│       │   ├── files.api.ts       ← File upload, list, download-url
│       │   ├── drawings.api.ts    ← Drawing CRUD, versions
│       │   ├── jobs.api.ts        ← Job CRUD, cancel, retry
│       │   ├── results.api.ts     ← Result detail, review submission
│       │   ├── reviews.api.ts     ← Pending reviews
│       │   ├── agent-runs.api.ts  ← Agent execution (Stage 2)
│       │   └── audit-logs.api.ts  ← Audit log listing
│       ├── app/                   ← Application shell
│       │   ├── router.tsx         ← Route definitions with permission guards
│       │   ├── providers.tsx      ← TanStack Query, Ant Design ConfigProvider
│       │   └── layout.tsx         ← Main layout: sidebar, header, content area
│       ├── features/              ← Page modules (10 directories)
│       │   ├── auth/              ← Login page
│       │   ├── dashboard/         ← Dashboard/workbench
│       │   ├── users/             ← User management (admin)
│       │   ├── projects/          ← Project list + detail
│       │   ├── files/             ← File list + upload
│       │   ├── drawings/          ← Drawing list + detail
│       │   ├── jobs/              ← Job list + detail
│       │   ├── reviews/           ← Pending reviews + review form
│       │   ├── profile/           ← User profile page
│       │   └── admin/             ← Roles, audit logs (super_admin)
│       ├── components/            ← Shared UI components (2 real + 6 stubs)
│       │   ├── FileUpload.tsx        [REAL]
│       │   ├── PermissionGuard.tsx   [REAL]
│       │   ├── TaskInput.tsx         [STUB — placeholder]
│       │   ├── AgentSteps.tsx        [STUB — placeholder]
│       │   ├── ResultPanel.tsx       [STUB — placeholder]
│       │   ├── DrawingPreview.tsx    [STUB — placeholder]
│       │   ├── JobTimeline.tsx       [STUB — placeholder]
│       │   └── ReviewPanel.tsx       [STUB — placeholder]
│       ├── stores/                ← Zustand stores
│       │   └── auth.store.ts      ← Current user, roles, token
│       └── types/                 ← TypeScript type definitions
│           ├── auth.ts
│           ├── user.ts
│           ├── project.ts
│           ├── file.ts
│           ├── drawing.ts
│           ├── job.ts
│           ├── result.ts
│           ├── agent.ts
│           └── audit.ts
│
├── docs/                          ← Handover documentation (7 docs including this one)
│   ├── architecture.md            ← System architecture overview
│   ├── api.md                     ← API reference
│   ├── database.md                ← Database schema reference
│   ├── deployment.md              ← Deployment & operations guide
│   ├── development.md             ← This document
│   ├── roadmap.md                 ← 6-stage delivery roadmap
│   └── security.md                ← Security architecture & pentest findings
│
├── infra/                         ← Deployment infrastructure configs
│   ├── nginx/
│   │   ├── nginx.conf             ← Docker deployment nginx config (container paths)
│   │   └── nginx.local.conf       ← Local dev nginx config (absolute paths, sed-templated)
│   ├── mysql/
│   │   └── init.sql               ← Initial schema + seed data for Docker
│   ├── redis/
│   │   └── redis.conf             ← AOF, LRU, maxmemory 256mb for Docker
│   ├── minio/                     ← MinIO config placeholder
│   └── verify.sh                  ← Infrastructure validation script
│
├── scripts/                       ← Dev/ops shell scripts
│   ├── lib.sh                     ← Shared functions (logging, env loading)
│   ├── start-dev.sh               ← Start backend + frontend dev servers
│   ├── start-all.sh               ← Start all services (including nginx, redis, mysql)
│   ├── stop-all.sh                ← Stop all services
│   ├── status.sh                  ← Check service status
│   └── db.sh                      ← Database helpers (migrate, seed, reset)
│
├── agents/                        ← PLACEHOLDER — future Agent definition modules
├── cad-worker/                    ← PLACEHOLDER — Windows C# CAD Worker
└── tests/                         ← PLACEHOLDER — future E2E / integration tests
```

---

## 2. Backend Development Workflow

This section walks through adding a complete new feature to the backend, from database
to API to tests. We will use "adding a new `notifications` resource" as a concrete example.

### 2.1 Step-by-step: Adding a New Endpoint

**Step 1: Define the SQLAlchemy model** (`app/models/notification.py`)

```python
from __future__ import annotations

from sqlalchemy import BigInteger, Column, ForeignKey, String, Text, Boolean
from app.db.base import Base
from app.models.mixins import TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("sys_users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
```

Then register the model in `app/db/base.py` (or ensure it is imported in
`app/models/__init__.py` so Alembic's `env.py` discovers it).

**Step 2: Create the Alembic migration**

```bash
cd backend
uv run alembic revision --autogenerate -m "add notifications table"
uv run alembic upgrade head
```

Always review the generated migration. Alembic autogenerate handles most column types
correctly, but check for things like `ondelete` cascades, default values, and enum types
that may need manual adjustment.

**Step 3: Define Pydantic schemas** (`app/schemas/notification_schema.py`)

```python
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class NotificationCreate(BaseModel):
    title: str
    message: str


class NotificationUpdate(BaseModel):
    is_read: bool | None = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    message: str
    is_read: bool
    created_at: datetime
```

Use `ConfigDict(from_attributes=True)` on all response schemas so they can be constructed
from ORM model instances.

**Step 4: Write service logic** (`app/services/notification_service.py`)

Services contain the business logic. They receive a SQLAlchemy `Session` and Pydantic
schemas, and return ORM models or Pydantic responses. Services MUST NOT depend on
FastAPI `Request` objects.

```python
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.notification import Notification
from app.schemas.notification_schema import NotificationCreate


def create_notification(db: Session, user_id: int, data: NotificationCreate) -> Notification:
    notification = Notification(user_id=user_id, **data.model_dump())
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def list_notifications(db: Session, user_id: int, is_read: bool | None = None):
    stmt = select(Notification).where(Notification.user_id == user_id)
    if is_read is not None:
        stmt = stmt.where(Notification.is_read == is_read)
    return db.scalars(stmt.order_by(Notification.created_at.desc())).all()
```

**Step 5: Add route handler** (`app/api/v1/notifications_api.py`)

API route handlers are thin — they parse parameters, call services, and return wrapped
responses. No business logic lives here.

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from app.api.deps import CurrentUser
from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.notification_schema import NotificationCreate, NotificationResponse
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post("", status_code=201)
def create_notification(
    payload: NotificationCreate,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
):
    notification = notification_service.create_notification(db, current_user.id, payload)
    return ok(NotificationResponse.model_validate(notification).model_dump(), request.state.request_id)


@router.get("")
def list_notifications(
    is_read: bool | None = Query(None),
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
):
    notifications = notification_service.list_notifications(db, current_user.id, is_read)
    return ok(
        [NotificationResponse.model_validate(n).model_dump() for n in notifications],
        request.state.request_id,
    )
```

**Step 6: Register in the central router** (`app/api/v1/router.py`)

```python
from app.api.v1.notifications_api import router as notifications_router

api_router.include_router(notifications_router)
```

**Step 7: Write tests** (`tests/test_notifications_api.py`)

See [Section 4](#4-testing-strategy-and-how-to-write-tests) for the full testing pattern.

**Step 8: Write audit log** (if the endpoint changes state)

For any state-changing operation (create, update, delete), call `write_audit_log()`:

```python
from app.services.audit_service import write_audit_log

write_audit_log(
    db=db,
    actor_user_id=current_user.id,
    action="notification.create",
    resource_type="notification",
    resource_id=notification.id,
    after_json=NotificationResponse.model_validate(notification).model_dump(),
)
```

### 2.2 Architecture Rules (from Spec Section 6.2)

These rules are non-negotiable:

| Layer | Directory | Allowed to | NOT allowed to |
|-------|-----------|------------|----------------|
| API | `app/api/v1/` | Routes, param parsing, DI, response wrapping | Business logic, direct DB queries |
| Service | `app/services/` | Business logic orchestration | Depend on FastAPI `Request` |
| Repository | `app/repositories/` | DB read/write encapsulation | Business rules |
| Worker | `app/workers/` | Call Services, execute async tasks | Duplicate business logic |
| Agent | `app/agents/` | Tool orchestration, LLM interaction | Direct DB/filesystem access |
| Model | `app/models/` | ORM table definitions | Business logic |

**Additional hard rules:**

1. **All file paths must pass through `app/utils/path_utils.py`** (`ensure_within_root()`).
   Never construct storage paths directly from user input.
2. **All business endpoints require authentication.** The `current_user: CurrentUser`
   dependency must never have a `= None` default — if it did, unauthenticated requests
   would reach business logic.
3. **Use `AppHTTPException`** (from `app.core.exceptions`) for business errors. Do not
   raise bare `fastapi.HTTPException` — `AppHTTPException` ensures consistent error
   response formatting.
4. **Status codes** follow spec Section 7.2: 200 for queries/updates, 201 for resource
   creation, 202 for async acceptance, 204 for deletion. Never return `200 + code: 0`
   for errors.

### 2.3 API Response Format

Every endpoint returns one of these shapes:

**Single resource / mutation:**
```json
{
  "data": { ... },
  "meta": { "request_id": "req_...", "timestamp": "2026-07-03T10:00:00+08:00" }
}
```

**List with pagination:**
```json
{
  "data": [ ... ],
  "pagination": { "page": 1, "page_size": 20, "total": 120 },
  "meta": { "request_id": "req_...", "timestamp": "..." }
}
```

**Error:**
```json
{
  "error": { "code": "RESOURCE_NOT_FOUND", "message": "...", "details": {} },
  "meta": { "request_id": "req_...", "timestamp": "..." }
}
```

### 2.4 Dependency Parameter Order

FastAPI resolves parameters positionally. The correct order in a route function signature
must respect **Python's syntax rule**: parameters without defaults MUST come before
parameters with defaults.

Since `CurrentUser` (an `Annotated[..., Depends(...)]` type) has no `= default` in the
function signature, it must appear **before** `Depends()` and `Query()` parameters,
which do have defaults:

```python
@router.patch("/{user_id}")
def update_user(
    user_id: int,                         # 1. Path parameters (no default)
    payload: UserUpdate,                  # 2. Request body (no default)
    current_user: CurrentUser,            # 3. Annotated Depends (no default — MUST come before defaults)
    page: int = Query(1),                 # 4. Query parameters (have defaults)
    db: Session = Depends(get_db),        # 5. Explicit Depends() (have defaults)
    file: UploadFile | None = None,       # 6. UploadFile — always LAST
):
```

Getting this order wrong causes Python `SyntaxError`, not just FastAPI 422 responses.
The key rule: **all parameters without defaults first**, then parameters with defaults,
with `UploadFile` always last.

---

## 3. Frontend Development Workflow

### 3.1 Adding a New Page

**Step 1: Add API client module** (`src/api/notifications.api.ts`)

All HTTP calls go through `src/api/client.ts`, which is an Axios instance with:
- Automatic `Authorization: Bearer <token>` header injection (reads token from `sessionStorage` via Zustand store)
- Base URL from `VITE_API_BASE_URL` env var (defaults to empty string)

```typescript
import client from './client';
import type { NotificationResponse } from '@/types/notification';

export const listNotifications = (isRead?: boolean) =>
  client.get<{ data: NotificationResponse[] }>('/api/v1/notifications', { params: { is_read: isRead } });

export const markAsRead = (id: number) =>
  client.patch(`/api/v1/notifications/${id}`, { is_read: true });
```

**Never write `fetch()` or raw `axios.get()` in components.** All API calls go through
`src/api/` modules.

**Step 2: Add TypeScript types** (`src/types/notification.ts`)

Types should mirror the corresponding Pydantic response schemas:

```typescript
export interface NotificationResponse {
  id: number;
  user_id: number;
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
}
```

**Step 3: Create the feature page** (`src/features/notifications/`)

Use TanStack Query for server state, Zustand for client-only state (auth, UI).

```typescript
import { useQuery } from '@tanstack/react-query';
import { listNotifications } from '@/api/notifications.api';

export default function NotificationsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications(),
  });
  // ...
}
```

**Step 4: Add route** (`src/app/router.tsx`)

Wrap routes that require specific roles with the `PermissionGuard` component:

```tsx
{
  path: 'notifications',
  element: <PermissionGuard requiredRoles={['engineer', 'admin']}><NotificationsPage /></PermissionGuard>,
}
```

**Step 5: Add menu item** (in `src/app/layout.tsx` sidebar configuration)

### 3.2 Frontend Conventions

**API base URL:** Always use `VITE_API_BASE_URL` env var. For local development against
a running backend, set it to `http://127.0.0.1:8000` (the Vite dev server has no built-in
proxy). In Docker, nginx serves the built frontend and proxies `/api/v1/` to the backend,
so the env var may be empty.

**Token storage:** `sessionStorage` only — never `localStorage`. This mitigates XSS-based
token theft. The Axios interceptor in `client.ts` reads from `sessionStorage` on every
request.

**State management:**
- **TanStack Query** for server state (lists, detail views, mutations that invalidate
  queries).
- **Zustand** for client-only state (current user, auth status, UI toggles).
- Do not store server data in Zustand — that is what TanStack Query is for.

**Permission enforcement:** Three layers:
1. **Route-level:** `PermissionGuard` component wraps protected routes.
2. **Menu-level:** Sidebar items are conditionally rendered based on user roles.
3. **Component/button-level:** Individual action buttons check permissions before
   rendering.

**Important:** Frontend permission checks are UX optimization only. The backend is the
enforcement point. Never assume the frontend has correctly gated a user.

**Dependency versions:** All `package.json` dependencies are pinned to exact versions.
`"latest"` is forbidden. Use `npm install <package>@<version>` to add new dependencies.

---

## 4. Testing Strategy and How to Write Tests

### 4.1 Test Architecture

The test suite is designed for speed and isolation:

- **Database:** Every test gets an isolated in-memory SQLite database via `StaticPool`.
  The `conftest.py` autouse fixture creates a fresh engine, builds all tables, and
  overrides FastAPI's `Depends(get_db)` to use the test session. No test touches the
  real MySQL database.
- **Redis:** Every test gets a `FakeRedis` instance via `conftest.py` autouse fixture
  that monkeypatches the `app.core.redis_client` module-level singleton. Keys are
  flushed between tests. A separate `test_redis_real.py` file tests against the real
  Redis server and is auto-skipped when Redis is unavailable.
- **HTTP:** All tests use `fastapi.testclient.TestClient` — in-process, no real HTTP
  server. No network dependency.

### 4.2 Running Tests

```bash
cd backend

# Run all tests (quiet mode)
uv run pytest -q

# Run a specific test file
uv run pytest tests/test_auth.py -q

# Run a specific test function
uv run pytest tests/test_auth.py::test_login_success -q

# Run with verbose output (see test names)
uv run pytest -v

# Run and stop on first failure
uv run pytest -x

# Run only tests matching a keyword expression
uv run pytest -k "login"
```

Expected: 432 passed, 0 failed when Redis is available. If Redis is unavailable, the 13 tests in `test_redis_real.py` are skipped.

### 4.3 Linting Before Tests

```bash
cd backend
uv run ruff check app tests    # Must pass with zero errors
```

### 4.4 Test File Naming

- Files: `test_<topic>.py`
- Functions: `test_<what>_<expected_behavior>`
- Descriptive names are encouraged — the function name is documentation.

### 4.5 Helper Pattern (Shared Across Tests)

Most test files use these helpers:

```python
from fastapi.testclient import TestClient
from app.db.init_db import init_db
from app.main import app


def _client() -> TestClient:
    """Create a TestClient with a fresh-seeded database."""
    init_db()
    return TestClient(app)


def _admin(client: TestClient) -> dict[str, str]:
    """Log in as admin and return the Authorization header dict."""
    resp = client.post("/api/v1/auth/sessions", json={
        "username": "admin",
        "password": "SuperAdminPass1"
    })
    assert resp.status_code == 201
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _engineer(client: TestClient) -> dict[str, str]:
    """Log in as engineer and return the Authorization header dict."""
    # Create an engineer user first if needed, then log in
    ...
```

### 4.6 Test Patterns for Common Scenarios

**Testing an endpoint with auth:**
```python
def test_list_users_requires_auth():
    client = _client()
    resp = client.get("/api/v1/users")
    assert resp.status_code == 401
```

**Testing a protected admin endpoint:**
```python
def test_delete_user_as_admin():
    client = _client()
    headers = _admin(client)
    resp = client.delete("/api/v1/users/2", headers=headers)
    assert resp.status_code == 204
```

**Testing RBAC — engineer cannot access admin endpoints:**
```python
def test_engineer_cannot_create_user():
    client = _client()
    headers = _engineer(client)
    resp = client.post("/api/v1/users", json={...}, headers=headers)
    assert resp.status_code == 403
```

**Testing error response shape:**
```python
def test_not_found_returns_proper_error_format():
    client = _client()
    headers = _admin(client)
    resp = client.get("/api/v1/users/99999", headers=headers)
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "USER_NOT_FOUND"
    assert "meta" in body
    assert "request_id" in body["meta"]
```

### 4.7 Critical Test Rule

**Never use `assert False`.** Use `raise AssertionError("message")` instead. Ruff rule
B011 rejects bare `assert False` because it catches `AssertionError` ambiguously.

```python
# WRONG
if some_condition:
    assert False

# RIGHT
if some_condition:
    raise AssertionError("Expected X but got Y")
```

### 4.8 Key Test Files and What They Cover

| File | Purpose |
|------|---------|
| `test_smoke_flow.py` | End-to-end happy path: login → project → upload → job → result |
| `test_security_boundaries.py` | Unauthenticated/unauthorized access for every protected endpoint |
| `test_api_regressions.py` | Contract tests: every endpoint returns correct status codes and shapes |
| `test_token_lifecycle.py` | Access token creation, refresh, expiry, logout |
| `test_new_features.py` | Tests for the most recently implemented features |
| `test_rigorous.py` | Exhaustive edge case and error handling tests |
| `test_deep_verify.py` | Deeper validation of business rules and data integrity |
| `test_edge_cases.py` | Boundary conditions: empty inputs, max-length strings, etc. |
| `test_job_lifecycle.py` | Job status transitions, cancel, retry lifecycle |
| `test_rbac_deep.py` | Deep RBAC permission checking across roles and resources |
| `test_service_layer.py` | Service-layer unit tests (business logic isolated from HTTP) |
| `test_stage1_boundaries.py` | Verifies that Stage 2+ features return 503 (not 500) |
| `test_health.py` | Health endpoint |
| `test_config.py` | Settings validation, MySQL/Redis URL computation |
| `test_db_session.py` | Engine creation, WAL pragmas, connection pooling |
| `test_redis_client.py` | Redis client initialization and failure modes |
| `test_redis_memory.py` | Agent memory service (store/retrieve/trim/TTL) |
| `test_redis_real.py` | Real Redis integration tests (auto-skipped if unavailable) |
| `test_cache_service.py` | Cache layer get/set/delete/namespace operations |
| `test_migrations.py` | Alembic migration up/down/roundtrip |
| `test_compose.py` | Docker Compose config validation |
| `test_celery_minio_deployment.py` | Celery/MinIO deployment config validation |
| `test_cross_audit_fixes.py` | Cross-cutting audit fix validation |
| `test_scripts.py` | Shell script validation |

---

## 5. Code Conventions

### 5.1 Python (Backend)

**File header:** Every functional `.py` file begins with:
```python
from __future__ import annotations
```
This enables PEP 604 syntax (`X | None`, `list[dict]`) in all files, even ones that
define models with forward references. **Exceptions:** `__init__.py` files (which contain
no type annotations) and placeholder/stub files for future stages are exempt.

**Type hints:**
- Use `X | None`, not `Optional[X]` (enforced by ruff UP007).
- Use `list[X]`, `dict[K, V]`, `tuple[X, Y]`, not `List`, `Dict`, `Tuple` (ruff UP006).
- Import from `collections.abc`: `from collections.abc import Callable, Sequence`
  (ruff UP035).
- All public function signatures must have type annotations.

**Imports:**
- Sorted with ruff's `I` (isort) rules, which means: standard library first, then
  third-party, then local (`app.*`).
- No unused imports (ruff F401).
- No wildcard imports.

**Line length:** 100 characters (configured in `pyproject.toml` `[tool.ruff]`).

**Whitespace:**
- No trailing whitespace (ruff W291).
- End-of-file newline required.
- 4 spaces for indentation (no tabs).

**Pydantic models:**
```python
from pydantic import BaseModel, ConfigDict

class MySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # Enables ORM → schema conversion
    name: str
    count: int = 0
```

**Database models:**
```python
from app.db.base import Base
from app.models.mixins import TimestampMixin

class MyModel(Base, TimestampMixin):
    __tablename__ = "my_table"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
```

Use `TimestampMixin` for any table that needs `created_at` / `updated_at` / `deleted_at`.

### 5.2 TypeScript (Frontend)

- All API responses must have TypeScript interfaces defined in `src/types/`.
- Do not use `any` — if the type is unknown, use `unknown` and narrow it.
- Prefer `interface` for object shapes, `type` for unions/intersections.
- React components use function declarations with `React.FC` or explicit prop types.
- Use `const` assertions (`as const`) for literal types where applicable.

### 5.3 API Naming Conventions

- **Resource names are plural nouns:** `/api/v1/users`, `/api/v1/projects`.
- **Sub-resources nest under parents:** `/api/v1/projects/{id}/members`.
- **Kebab-case for compound names:** `/api/v1/agent-runs`, `/api/v1/audit-logs`.
- **File and module names:** snake_case for Python, kebab-case for frontend files.
- **No verb-based endpoints:** Never `/getUser` or `/createJob`. Use HTTP methods:
  `GET /api/v1/users/{id}`, `POST /api/v1/jobs`.
- **State-changing actions as sub-resources:** `POST /api/v1/jobs/{id}/cancellation-requests`,
  `POST /api/v1/jobs/{id}/retry-requests`.

---

## 6. Dependency Management

### 6.1 Backend: uv

**Adding a runtime dependency:**
```bash
cd backend
uv add <package-name>
```

**Adding a dev dependency:**
```bash
cd backend
uv add --dev <package-name>
```

**Removing a dependency:**
```bash
cd backend
uv remove <package-name>
```

**Syncing (install everything from lock file):**
```bash
cd backend
uv sync
```

**Critical rule:** Always use `uv add` / `uv remove` to modify dependencies. Never edit
`pyproject.toml` dependency lists by hand — `uv.lock` will get out of sync. The
`uv.lock` file is committed to version control, so everyone gets identical dependency
trees.

**Python version:** Locked to `>=3.12,<3.13` in `pyproject.toml`. The `.python-version`
file tells `uv` to use Python 3.12 specifically.

### 6.2 Frontend: npm

**Adding a dependency:**
```bash
cd frontend
npm install <package>@<exact-version>
```

**Removing a dependency:**
```bash
cd frontend
npm uninstall <package>
```

**Installing all dependencies:**
```bash
cd frontend
npm ci    # Uses package-lock.json — preferred for CI/reproducible builds
# or
npm install    # Updates package-lock.json if package.json changed
```

**Critical rule:** NEVER use `"latest"` as a version specifier in `package.json`.
Every dependency is pinned to an exact version or a safe semver range. Using `"latest"`
means different developers and CI builds get different versions with no warning.

---

## 7. Common Pitfalls and Gotchas

### 7.1 FastAPI Dependency Order

Getting parameter order wrong in a FastAPI route function is the #1 source of
confusing 422 errors. **The fundamental constraint is Python syntax**: parameters
without defaults MUST come before parameters with defaults.

Since `CurrentUser` (an `Annotated[..., Depends(...)]` type) has no `= default` in
the signature, it must appear **before** `Depends()` and `Query()` parameters:

1. Path parameters (e.g., `user_id: int`) -- no default
2. Request body (e.g., `payload: UserUpdate`) -- no default
3. Annotated Depends with no default (e.g., `current_user: CurrentUser`)
4. Query parameters (e.g., `page: int = Query(1)`) -- has default
5. `Depends()` dependencies (e.g., `db: Session = Depends(get_db)`) -- has default
6. `UploadFile` -- always last

Example of a **correct** signature:
```python
@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: CurrentUser,       # No default — must precede Depends()
    db: Session = Depends(get_db),   # Has default — must follow non-default params
):
```

Example of a **broken** signature (Python `SyntaxError`):
```python
# WRONG: parameter without default follows parameter with default
@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),   # Has default
    current_user: CurrentUser,       # No default — SyntaxError!
):
```

### 7.2 Never Default `current_user` to None

```python
# WRONG — this endpoint would accept unauthenticated requests
def list_projects(current_user: CurrentUser = None):
    ...

# RIGHT — authentication is mandatory for business endpoints
def list_projects(current_user: CurrentUser):
    ...
```

The only endpoints that should accept unauthenticated requests are `/health` and
`POST /api/v1/auth/sessions` (login).

### 7.3 Use AppHTTPException, Not HTTPException

```python
# WRONG — bypasses the unified error response format
from fastapi import HTTPException
raise HTTPException(status_code=404, detail="User not found")

# RIGHT — produces consistent {"error": {"code": ..., "message": ...}} shape
from app.core.exceptions import AppHTTPException
raise AppHTTPException(status_code=404, code="USER_NOT_FOUND", message="User not found")
```

### 7.4 File Path Safety

Never construct file paths from user input directly:

```python
# WRONG — path traversal vulnerability
file_path = f"uploads/{user_provided_filename}"

# RIGHT — use path_utils
from app.utils.path_utils import ensure_within_root
safe_path = ensure_within_root(base_dir, user_provided_filename)
```

### 7.5 SQLite vs MySQL Differences

Tests use in-memory SQLite; production uses MySQL. Be aware of these differences:
- SQLite is case-insensitive for string comparison by default; MySQL depends on collation.
- SQLite does not enforce foreign key constraints unless `PRAGMA foreign_keys = ON` is set
  (the conftest fixture does this).
- SQLite's `AUTOINCREMENT` behaves slightly differently from MySQL's `AUTO_INCREMENT`.
- Some MySQL-specific SQL (e.g., `ON DUPLICATE KEY UPDATE`) will fail in tests.
- MySQL's `JSON` column type maps to SQLAlchemy's `JSON` which works on SQLite but
  stores as text.

If you need MySQL-specific behavior in tests, test it at the unit level (e.g., config
testing instantiates `Settings()` without a real MySQL server).

### 7.6 Auth Token in Frontend

```typescript
// WRONG — XSS-vulnerable
localStorage.setItem('token', accessToken);

// RIGHT — sessionStorage is cleared on tab close
sessionStorage.setItem('token', accessToken);
```

The Axios client in `src/api/client.ts` reads from `sessionStorage` automatically.

### 7.7 `assert False` in Tests

```python
# WRONG — ruff B011 rejects this
if condition:
    assert False

# RIGHT
if condition:
    raise AssertionError("Expected condition X but got Y")
```

### 7.8 Don't Write Business Logic in Route Handlers

```python
# WRONG — route handler contains business logic
@router.post("/{file_id}/process")
def process_file(
    file_id: int,
    current_user: CurrentUser,               # No default — must precede Depends()
    db: Session = Depends(get_db),
):
    file = db.scalar(select(File).where(File.id == file_id))
    if file.status != "available":
        raise AppHTTPException(...)
    # ... more logic inline ...

# RIGHT — route delegates to service
@router.post("/{file_id}/process")
def process_file(
    file_id: int,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
):
    result = file_service.process_file(db, file_id, current_user.id)
    return ok(FileResponse.model_validate(result).model_dump(), request.state.request_id)
```

### 7.9 Stage 2+ Features Must Return 503

Agent-run endpoints and other Stage 2+ features are currently disabled. They must
return `503 Service Unavailable`, not `500 Internal Server Error`. The
`test_stage1_boundaries.py` tests verify this. If enabling a feature, update these
tests.

### 7.10 Don't Hardcode API URLs in Frontend

```typescript
// WRONG
const resp = await axios.get('http://localhost:8000/api/v1/users');

// RIGHT
import client from '@/api/client';
const resp = await client.get('/api/v1/users');
```

The client's `baseURL` comes from `VITE_API_BASE_URL`, which should be set to the
backend URL for local dev (e.g., `http://127.0.0.1:8000`) and may be empty in
Docker/nginx deployments.

---

## 8. Database Migration Workflow

### 8.1 Creating a Migration

```bash
cd backend
uv run alembic revision --autogenerate -m "add notifications table"
```

This generates a new file in `backend/migrations/versions/` with an `upgrade()` and
`downgrade()` function.

### 8.2 Reviewing a Migration

Always review autogenerated migrations before committing. Common issues to check:
- `ondelete="CASCADE"` or `ondelete="SET NULL"` on foreign keys — Alembic may not infer
  these correctly.
- Enum types — Alembic may generate `sa.Enum()` without specifying `native_enum=False`
  for SQLite compatibility.
- Default values — strings and booleans are usually fine; server-side defaults may need
  manual `server_default=text("...")`.
- Indexes — auto-generated migrations may miss indexes you added manually on models.

### 8.3 Applying a Migration

```bash
# Apply all pending migrations
cd backend
uv run alembic upgrade head

# Apply one specific migration
uv run alembic upgrade <revision_id>

# Downgrade one step (development only — never in production without a backup)
uv run alembic downgrade -1

# Check current migration state
uv run alembic current
```

### 8.4 Testing Migrations

The `test_migrations.py` file tests that migrations can be applied, downgraded, and
re-applied (roundtrip) without errors. Always run:

```bash
cd backend
uv run pytest tests/test_migrations.py -v
```

after creating a new migration.

### 8.5 Model Registration

All ORM models must be imported in the Alembic `env.py` file's `target_metadata` chain.
Currently, `env.py` does `from app.db.base import Base` and models are discovered through
SQLAlchemy's metadata registry if they are imported somewhere in the application. If a
new model is not discovered by `--autogenerate`, check that it is imported in
`app/models/__init__.py`.

---

## 9. Linting and Quality Gates

### 9.1 Backend: ruff

**Configuration** (in `pyproject.toml`):

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "W"]
ignore = ["B008", "E501", "UP037"]
```

Rules breakdown:
- `E` — pycodestyle errors (syntax, indentation, whitespace)
- `F` — Pyflakes (unused imports, undefined names, redefinitions)
- `I` — isort (import ordering)
- `UP` — pyupgrade (modern Python syntax: `X | None`, `list[]`, `from __future__ import annotations`)
- `B` — flake8-bugbear (common bug patterns: mutable defaults, `assert False`, bare except)
- `W` — pycodestyle warnings (trailing whitespace, blank line issues)

Excluded rules:
- `B008` — Do not perform function calls in argument defaults (too noisy with FastAPI
  `Depends()` patterns)
- `E501` — Line too long (handled by formatter instead)
- `UP037` — Remove quotes from type annotation (conflicts with `from __future__ import annotations`)

**Running:**
```bash
cd backend
uv run ruff check app tests          # Check only
uv run ruff check --fix app tests    # Auto-fix safe issues
```

**Pre-commit gate:** Run `uv run ruff check app tests` before every commit. CI should
also run this as a required check.

### 9.2 Frontend: TypeScript + ESLint

The frontend uses TypeScript's strict mode and ESLint (if configured). At minimum:
- All TypeScript files must compile without errors: `npx tsc --noEmit`
- Build must succeed: `npm run build`

### 9.3 Pre-Commit Checklist

Before committing any change:

```bash
# Backend changes
cd backend
uv run ruff check app tests          # Must pass with 0 errors
uv run pytest -q                     # Must pass 432 tests

# Frontend changes
cd frontend
npx tsc --noEmit                     # Must pass with 0 errors
npm run build                        # Must produce dist/ without errors

# Git hygiene
git diff --cached                    # Review your staged changes
```

### 9.4 .gitignore Rules

The following MUST be gitignored (already configured):
- `backend/var/` — runtime data (uploads, temp files, SQLite databases)
- `.env` and `.env.docker` — real environment files with secrets
- `*.pyc`, `__pycache__/` — compiled Python
- `frontend/dist/` — build output
- IDE directories (`.vscode/`, `.idea/`)

The following MUST be committed:
- `uv.lock` — exact backend dependency versions
- `package-lock.json` — exact frontend dependency versions
- `.env.example` and `.env.docker.example` — templates (no secrets)
- `alembic.ini` and `migrations/` — database schema history

---

## 10. Git Workflow

### 10.1 Commit Messages

Follow the conventional commits format:

```
<type>(<scope>): <description>

[optional body]
```

Types:
- `feat` — A new feature
- `fix` — A bug fix
- `refactor` — Code restructuring (no behavior change)
- `test` — Adding or updating tests
- `docs` — Documentation changes
- `chore` — Maintenance tasks (deps, config, scripts)
- `style` — Formatting, whitespace (no logic change)

Scopes: `backend`, `frontend`, `infra`, `docs`, `scripts`, `tests`

Examples:
```
feat(backend): add notifications resource with CRUD endpoints
fix(frontend): correct token refresh interceptor 401 loop
refactor(backend): extract notification logic from routes into service
test(backend): add security boundary tests for notifications API
docs: add development guide covering full workflow
chore(backend): update ruff to 0.7.0
```

### 10.2 Branch Strategy

- `main` — Production-ready code. Protected. Only merge via PR.
- `develop` — Integration branch for active development.
- Feature branches: `feat/<description>` (e.g., `feat/add-notifications`)
- Fix branches: `fix/<description>` (e.g., `fix/token-refresh-loop`)
- Release branches: `release/<version>` (e.g., `release/v0.2.0`)

### 10.3 What Not to Commit

- `.env` and `.env.docker` — contain real secrets
- `backend/var/` — runtime data
- `frontend/dist/` — build artifacts
- IDE configuration files (`.vscode/`, `.idea/`)
- OS files (`.DS_Store`, `Thumbs.db`)
- Any file containing API keys, passwords, or tokens

### 10.4 Pull Request Checklist

- [ ] `uv run ruff check app tests` passes (0 errors)
- [ ] `uv run pytest -q` passes (all 432 tests)
- [ ] `npx tsc --noEmit` passes (frontend type check)
- [ ] New endpoints have corresponding tests (happy path + security boundaries)
- [ ] State-changing operations write audit logs
- [ ] New schemas use `ConfigDict(from_attributes=True)` for response models
- [ ] New dependencies were added with `uv add` / `npm install @version` (not hand-edited)
- [ ] Alembic migration is included (if schema changed)
- [ ] No hardcoded URLs, paths, or secrets
- [ ] `uv.lock` is updated (if `pyproject.toml` changed)
- [ ] `package-lock.json` is updated (if `package.json` changed)

---

## Quick Reference

### Start Developing

```bash
# Backend
cd backend
uv sync
cp .env.example .env   # Edit with your local settings
uv run uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
cp .env.example .env   # Edit VITE_API_BASE_URL if needed
npm install
npm run dev

# Or use the convenience scripts
cd /path/to/repo
./scripts/start-dev.sh
```

### Run Checks Before Commit

```bash
cd backend && uv run ruff check app tests && uv run pytest -q
cd frontend && npx tsc --noEmit && npm run build
```

### Key Files to Read When Onboarding

1. `DWG-Agent企业平台技术规范.md` — The spec (sections 5, 6, 7, 21 are most relevant for developers)
2. `CLAUDE.md` — Agent instruction file with all conventions and file map
3. `docs/architecture.md` — System architecture
4. `docs/api.md` — API reference
5. `docs/deployment.md` — Local development & deployment setup
6. `docs/database.md` — Database schema
7. `docs/security.md` — Security architecture
8. `docs/roadmap.md` — Stage delivery roadmap
9. `backend/app/core/config.py` — All configuration knobs
10. `backend/app/core/exceptions.py` — AppHTTPException definition
11. `backend/app/main.py` — How the FastAPI app is assembled
12. `backend/app/api/v1/router.py` — How routes are mounted
