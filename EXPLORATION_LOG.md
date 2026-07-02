# DWG-Agent Platform — System Exploration Log

> **Operator:** Claude Code automated exploration
> **Date:** 2026-07-02
> **Scope:** Full system exploration — API + Frontend + Database + Infrastructure
> **Method:** Non-destructive black-box testing via HTTP and browser analysis

---

## 1. System Overview (Initial State)

| Component | Status | Port | Notes |
|-----------|--------|------|-------|
| MySQL | Running | 3306 | Production data; 200+ audit log entries |
| Redis (Valkey 9.1) | Running | 6379 | No password local dev |
| FastAPI Backend | Running | 8000 | uvicorn with --reload |
| Nginx | Running | 8080 | Reverse proxy + SPA static hosting |
| Frontend (Vite) | Built | :8080 via Nginx | React 19 + Ant Design 6 |

### Existing Data at Start
- 1 user: `admin` (super_admin)
- 18 projects from previous smoke tests
- 44 files from previous tests
- 2 drawings
- 22+ jobs
- 200+ audit log entries

---

## 2. API Exploration

### 2.1 Health Check (`GET /api/v1/health`, `GET /health`)
- **Status:** ✅ Working
- Response format correct: `{"data": {"status": "ok", "components": {...}}, "meta": {...}}`
- All components (api, database, redis) report ok

### 2.2 Authentication (`/api/v1/auth/*`)
- **Status:** ✅ Working
- `POST /api/v1/auth/sessions` — Login works, returns access_token + user info
- `GET /api/v1/auth/me` — Returns current user with roles
- Login with wrong password → 401 `INVALID_CREDENTIALS`
- Login with non-existent user → 401 `INVALID_CREDENTIALS` (doesn't leak user existence — good!)
- Token type: JWT Bearer, 30min expiry
- **BUG:** `updated_at` field changes on every `/auth/me` call (even when no data changes)

### 2.3 Users & RBAC (`/api/v1/users`, `/api/v1/roles`, `/api/v1/permissions`)
- **Status:** ✅ Working
- 8 seeded permissions (users:read/write, roles:write, projects:write, files:write, jobs:write, reviews:write, audit_logs:read)
- 7 seeded roles (super_admin, admin, engineer, reviewer, operator, viewer, auditor)
- User creation with duplicate username → 409 `USERNAME_EXISTS`
- New users have no roles by default → cannot access protected endpoints
- RBAC enforced: unauthenticated requests → 401; users without permission → 403

### 2.4 Projects (`/api/v1/projects`)
- **Status:** ✅ Working
- CRUD operations all functional
- Duplicate project code → 409 `PROJECT_CODE_EXISTS`
- Project members management works
- Project member roles: project_owner, project_engineer, project_reviewer, project_viewer

### 2.5 Files (`/api/v1/files`)
- **Status:** ✅ Working
- Upload via multipart form field name: `upload` (not `file`)
- Returns full metadata: sha256, md5, size, storage_key
- Download URL returns short-lived URL (300s expiry)
- `GET /api/v1/files/{id}/download` — actual file download works
