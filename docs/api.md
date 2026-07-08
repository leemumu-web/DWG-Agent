# API Reference -- DWG-Agent Platform

> Version: v1.0 | Base path: `/api/v1` | Stage 1 (production-ready skeleton)
> 73 endpoints under `/api/v1` across 12 route modules, plus 1 root health endpoint at `/health` (74 total).
> Spec authority: `DWG-Agent企业平台技术规范.md` (v2.0) section 7.

---

## 1. Quick Reference -- All Endpoints

### 1.1 Auth -- `/api/v1/auth`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 1 | POST | `/api/v1/auth/sessions` | Login, returns access token + sets refresh cookie | Public |
| 2 | DELETE | `/api/v1/auth/sessions/current` | Logout (blacklists tokens) | Authenticated |
| 3 | POST | `/api/v1/auth/tokens/refresh` | Refresh access token (reads HttpOnly cookie) | Public (reads cookie) |
| 4 | GET | `/api/v1/auth/me` | Current user profile | Authenticated |
| 5 | PATCH | `/api/v1/auth/password` | Change own password (requires current password) | Authenticated |

### 1.2 Users -- `/api/v1/users`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 6 | GET | `/api/v1/users` | List users (paginated) | admin / super_admin |
| 7 | POST | `/api/v1/users` | Create user | admin / super_admin |
| 8 | GET | `/api/v1/users/{user_id}` | User detail | admin / super_admin |
| 9 | PATCH | `/api/v1/users/me` | Update own profile | Authenticated |
| 10 | PATCH | `/api/v1/users/{user_id}` | Update user (admin action) | admin (cannot modify super_admin) |
| 11 | DELETE | `/api/v1/users/{user_id}` | Soft-delete user | admin (cannot delete super_admin / self) |
| 12 | POST | `/api/v1/users/{user_id}/roles` | Assign role to user | admin (only super_admin can assign super_admin role) |
| 13 | DELETE | `/api/v1/users/{user_id}/roles/{role_id}` | Remove role from user | admin (cannot remove own role) |
| 14 | POST | `/api/v1/users/{user_id}/password-reset-requests` | Admin-initiated password reset | admin |
| 15 | POST | `/api/v1/users/{user_id}/disable-requests` | Disable user account | admin (cannot disable super_admin / self) |
| 16 | POST | `/api/v1/users/{user_id}/enable-requests` | Re-enable user account | admin (cannot enable super_admin) |

### 1.3 Roles & Permissions -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 17 | GET | `/api/v1/roles` | List roles | super_admin / admin |
| 18 | POST | `/api/v1/roles` | Create role | super_admin |
| 19 | GET | `/api/v1/permissions` | List available permissions | super_admin / admin |
| 20 | PUT | `/api/v1/roles/{role_id}/permissions` | Replace role's permission set | super_admin |

### 1.4 Projects -- `/api/v1/projects`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 21 | GET | `/api/v1/projects` | List projects (admin sees all, others see own) | Authenticated |
| 22 | POST | `/api/v1/projects` | Create project (creator becomes project_owner) | Authenticated |
| 23 | GET | `/api/v1/projects/{project_id}` | Project detail | Project member |
| 24 | PATCH | `/api/v1/projects/{project_id}` | Update project | project_owner / project_engineer |
| 25 | DELETE | `/api/v1/projects/{project_id}` | Soft-delete project | project_owner |
| 26 | GET | `/api/v1/projects/{project_id}/members` | List project members | Project member |
| 27 | POST | `/api/v1/projects/{project_id}/members` | Add member to project | project_owner |
| 28 | PATCH | `/api/v1/projects/{project_id}/members/{member_id}` | Change member's project role | project_owner |
| 29 | DELETE | `/api/v1/projects/{project_id}/members/{member_id}` | Remove member (hard delete) | project_owner |

### 1.5 Files -- `/api/v1/files`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 30 | POST | `/api/v1/files` | Upload file (multipart, DWG validated) | Authenticated |
| 31 | GET | `/api/v1/files` | List files (admin sees all, others see own) | Authenticated |
| 32 | GET | `/api/v1/files/{file_id}` | File metadata | Uploader / admin / project member |
| 33 | DELETE | `/api/v1/files/{file_id}` | Soft-delete file | Uploader / admin |
| 34 | GET | `/api/v1/files/{file_id}/download-url` | Get short-term signed download URL (HMAC, TTL=300s) | Uploader / admin / project member |
| 35 | GET | `/api/v1/files/{file_id}/download` | Direct download (requires signature params) | Uploader / admin / project member |
| 36 | POST | `/api/v1/files/upload-zip` | Upload a `.zip`, extract matching `.dwg`/`.dxf` files into a batch | Authenticated |
| 37 | GET | `/api/v1/files/batches` | List distinct batch names (file count + latest created_at) | Authenticated |
| 38 | DELETE | `/api/v1/files/batches/{batch_name}` | Soft-delete all files in a batch | Uploader / admin (per file) |
| 39 | GET | `/api/v1/files/batches/{batch_name}/download-zip` | Download all files in a batch as a ZIP (streamed) | Uploader / admin / project member |
| 40 | GET | `/api/v1/files/{file_id}/excel-preview` | Preview an `.xlsx`/`.xls` file's content as JSON | Uploader / admin / project member |
| 41 | POST | `/api/v1/files/bulk-delete` | Soft-delete multiple files by ID | Uploader / admin (per file) |
| 42 | POST | `/api/v1/files/download-zip` | Download selected files' DWG/DXF versions as a ZIP (streamed) | Uploader / admin / project member |

### 1.6 Drawings -- `/api/v1/drawings`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 43 | GET | `/api/v1/drawings` | List drawings (admin sees all, others see own projects) | Authenticated |
| 44 | POST | `/api/v1/drawings` | Create drawing (optional initial file_id) | project_owner / project_engineer |
| 45 | GET | `/api/v1/drawings/{drawing_id}` | Drawing detail (cascaded project active check) | Project member |
| 46 | PATCH | `/api/v1/drawings/{drawing_id}` | Update drawing metadata | project_owner / project_engineer |
| 47 | DELETE | `/api/v1/drawings/{drawing_id}` | Archive drawing (soft-delete) | project_owner / project_engineer |
| 48 | GET | `/api/v1/drawings/{drawing_id}/versions` | List drawing versions | Project member |
| 49 | POST | `/api/v1/drawings/{drawing_id}/versions` | Upload new version (auto-increment version_no) | project_owner / project_engineer |
| 50 | GET | `/api/v1/drawings/{drawing_id}/preview` | Get drawing preview (Stage 1 placeholder) | Project member |

### 1.7 Jobs -- `/api/v1/jobs`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 51 | GET | `/api/v1/jobs` | List jobs (admin sees all, others see own projects) | Authenticated |
| 52 | POST | `/api/v1/jobs` | Create processing job | project_owner / project_engineer |
| 53 | GET | `/api/v1/jobs/{job_id}` | Job detail (status, progress, error info) | Project member |
| 54 | POST | `/api/v1/jobs/{job_id}/cancellation-requests` | Request cancellation (only queued/running) | project_owner / project_engineer |
| 55 | POST | `/api/v1/jobs/{job_id}/retry-requests` | Request retry (only failed/cancelled) | project_owner / project_engineer |
| 56 | POST | `/api/v1/jobs/cancel-all-active` | Bulk-cancel ALL queued/running/pending jobs system-wide (best-effort Celery revoke + queue purge) | admin / super_admin |
| 57 | GET | `/api/v1/jobs/{job_id}/steps` | List job execution steps | Project member |
| 58 | GET | `/api/v1/jobs/{job_id}/logs` | Get job logs (Stage 1 placeholder) | Project member |
| 59 | GET | `/api/v1/jobs/{job_id}/events` | SSE event stream (Stage 1 placeholder) | Project member |
| 60 | GET | `/api/v1/jobs/{job_id}/results` | List analysis results for this job | Project member |

### 1.8 Results & Reviews -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 61 | GET | `/api/v1/results/{result_id}` | Result detail | Project member (via job → drawing → project) |
| 62 | GET | `/api/v1/results/{result_id}/download-url` | Result file download URL | Project member |
| 63 | POST | `/api/v1/results/{result_id}/reviews` | Submit review (approved / rejected / needs_revision) | project_owner / project_reviewer |
| 64 | GET | `/api/v1/results/{result_id}/reviews` | Review history | Project member |

### 1.9 Reviews -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 65 | GET | `/api/v1/reviews/pending` | List results needing review (need_review status) | Authenticated (admin sees all; others filtered by project) |

### 1.10 Audit Logs -- `/api/v1/audit-logs`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 66 | GET | `/api/v1/audit-logs` | List audit logs (last 200, paginated) | super_admin / auditor |
| 67 | GET | `/api/v1/audit-logs/{audit_log_id}` | Audit log detail | super_admin / auditor |

### 1.11 Agent -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 68 | POST | `/api/v1/agent-runs` | Create Agent execution (Stage 1: returns 503) | Authenticated |
| 69 | GET | `/api/v1/agent-runs/{agent_run_id}` | Agent execution detail (Stage 1: returns 503) | Authenticated |
| 70 | GET | `/api/v1/agent-runs/{agent_run_id}/steps` | Agent execution steps (Stage 1: returns 503) | Authenticated |
| 71 | GET | `/api/v1/agent-tools` | Available Agent tools (Stage 1: returns 503) | Authenticated |

### 1.12 Health

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| -- | GET | `/health` | Service health check | Public |

### 1.13 System -- `/api/v1/system`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 72 | GET | `/api/v1/system/health` | Deep health: Redis reachability, feature flags, storage backend | Authenticated |
| 73 | GET | `/api/v1/system/health/oda` | ODA File Converter availability probe (never 503; state in body) | Authenticated |

---

## 2. Authentication

### 2.1 How to Obtain a Token

Send credentials to the login endpoint:

```
POST /api/v1/auth/sessions
Content-Type: application/json

{
  "username": "10001",
  "password": "your-password-here"
}
```

On success, you receive an `access_token` in the response body and a `dwg_refresh_token` is set as an HttpOnly cookie.

```
HTTP/1.1 201 Created
Set-Cookie: dwg_refresh_token=<jwt>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth

{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "Zhang San",
      "employee_no": "10001",
      "email": "zhangsan@company.local",
      "status": "active",
      "roles": [
        {"id": 3, "code": "engineer", "name": "Engineer"}
      ]
    }
  },
  "meta": {
    "request_id": "req_20260703_000001",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 2.2 How to Pass the Token

Include the access token in the `Authorization` header for every authenticated request:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

All business endpoints require authentication. Public endpoints are:
- `POST /api/v1/auth/sessions` (login)
- `POST /api/v1/auth/tokens/refresh` (token refresh)
- `GET /health` (health check)

### 2.3 Token Lifecycle

| Token | Lifetime | Storage | Revocation |
|-------|----------|---------|------------|
| `access_token` | 30 minutes (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`) | Client memory (`sessionStorage` in frontend) | Blacklisted on logout via Redis |
| `refresh_token` | 14 days (`JWT_REFRESH_TOKEN_EXPIRE_DAYS`) | HttpOnly Secure SameSite Cookie | Blacklisted on logout |

### 2.4 Refreshing the Access Token

When the access token expires, use the refresh cookie to obtain a new one:

```
POST /api/v1/auth/tokens/refresh
Cookie: dwg_refresh_token=<jwt>
```

The endpoint reads the HttpOnly cookie automatically. No request body is needed.

```
HTTP/1.1 200 OK

{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "Zhang San",
      "employee_no": "10001",
      "email": "zhangsan@company.local",
      "status": "active",
      "roles": [
        {"id": 3, "code": "engineer", "name": "Engineer"}
      ]
    }
  },
  "meta": {
    "request_id": "req_20260703_000002",
    "timestamp": "2026-07-03T10:25:00+00:00"
  }
}
```

### 2.5 Logout

```
DELETE /api/v1/auth/sessions/current
Authorization: Bearer <access_token>
Cookie: dwg_refresh_token=<jwt>
```

Both tokens are added to a Redis blacklist. Subsequent use of either token returns 401.

```
HTTP/1.1 204 No Content
```

### 2.6 Password Requirements

- Minimum 12 characters
- Must contain at least one uppercase letter, one lowercase letter, and one digit
- Common passwords (e.g., `password123`, `admin123456`) are rejected
- Passwords are hashed with Argon2id before storage

---

## 3. Detailed Endpoint Reference

### 3.1 Auth Endpoints

#### POST /api/v1/auth/sessions -- Login

Create a new session. Returns an access token and sets a refresh token cookie.

**Request body:**
```json
{
  "username": "10001",
  "password": "********"
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `username` | string | Yes | 1-64 characters |
| `password` | string | Yes | 1+ characters |

**Success response (201 Created):**
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "Zhang San",
      "employee_no": "10001",
      "email": "zhangsan@company.local",
      "status": "active",
      "roles": [
        {"id": 3, "code": "engineer", "name": "Engineer"}
      ]
    }
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 401 | `INVALID_CREDENTIALS` | Wrong username or password |
| 401 | `INVALID_CREDENTIALS` | Account is disabled or deleted (timing-safe: same response as wrong password) |
| 422 | (Pydantic) | Validation failure on username/password format |

---

#### PATCH /api/v1/auth/password -- Change Own Password

**Request body:**
```json
{
  "current_password": "old-password",
  "new_password": "NewSecurePass123"
}
```

**Success response (200 OK):**
```json
{
  "data": {
    "changed": true
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 400 | `INVALID_CURRENT_PASSWORD` | Current password is wrong |
| 422 | (Pydantic) | New password fails complexity check |

---

### 3.2 File Upload

#### POST /api/v1/files -- Upload a DWG File

**Request:** `multipart/form-data`

| Field | Type | In | Required | Description |
|-------|------|----|----------|-------------|
| `upload` | file (binary) | form | Yes | The DWG file to upload |
| `batch_name` | string | query | No | Optional batch label to group this upload with others |

**Validation performed:**
1. Extension whitelist: `.dwg` only
2. Minimum size: 1024 bytes
3. DWG file header: must match one of AC1012 through AC1032
4. SHA-256 hash computed and stored
5. MD5 hash computed and stored

**cURL example:**
```bash
curl -X POST http://localhost:8000/api/v1/files \
  -H "Authorization: Bearer $TOKEN" \
  -F "upload=@building-A.dwg"
```

**Success response (201 Created):**
```json
{
  "data": {
    "id": 1001,
    "bucket": "dwg-original",
    "storage_key": "uploads/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.dwg",
    "original_name": "building-A.dwg",
    "file_ext": ".dwg",
    "content_type": "application/x-dwg",
    "size_bytes": 12345678,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "md5": "d41d8cd98f00b204e9800998ecf8427e",
    "uploaded_by": 1,
    "status": "available",
    "created_at": "2026-07-03T10:05:00+00:00",
    "updated_at": "2026-07-03T10:05:00+00:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 415 | `FILE_TYPE_NOT_ALLOWED` | Extension is not `.dwg` |
| 415 | `FILE_NOT_DWG` | File does not start with a valid DWG header, or is too small (< 1024 bytes) |
| 415 | `FILE_MIME_NOT_ALLOWED` | MIME type not in DWG allowlist |
| 413 | `FILE_TOO_LARGE` | File exceeds `MAX_UPLOAD_SIZE_MB` (default 512 MB) |

---

#### GET /api/v1/files/{file_id}/download-url -- Get Signed Download URL

Returns an HMAC-signed temporary URL for downloading the file. The URL is valid for 300 seconds (5 minutes).

**Success response (200 OK):**
```json
{
  "data": {
    "url": "/api/v1/files/1001/download?expires=1751523900&signature=abc123...",
    "expires_in": 300
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

**Authorization:** Uploader, admin, or member of a project that uses this file.

---

#### POST /api/v1/files/upload-zip -- Upload a ZIP Archive (Batch Import)

Upload a `.zip` archive, extract every file whose extension matches `file_ext`, and auto-create a `StoredFile` record for each. The ZIP filename (minus `.zip`) becomes the `batch_name` for all extracted files; if it sanitizes to empty/`unnamed`, a fallback `导入_YYYYMMDD_HHMMSS` name is generated. Files that do not match `file_ext` (and `.dwg` entries that fail the DWG header check) are counted as skipped.

**Authorization:** Any authenticated user. Extracted files are owned by the caller (`uploaded_by`).

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `upload` | file (binary) | Yes | The `.zip` archive to import |

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_ext` | string | No | `.dwg` | Extension to extract; must be `.dwg` or `.dxf` |

**Extraction limits & validation:**
1. Upload filename must end in `.zip` and be in the upload allowlist (`.dwg`, `.dxf`, `.zip`).
2. Total ZIP size must not exceed `MAX_UPLOAD_SIZE_MB` (default 512 MB).
3. Archive must be a valid, non-corrupt ZIP (`testzip` passes).
4. Entry count must not exceed `max_zip_entry_count` (default 1000).
5. Total uncompressed extracted size must not exceed `max_zip_extract_mb` (default 2048 MB).
6. `.dwg` entries must pass the DWG header check (AC1012–AC1032); failures are skipped, not fatal.
7. Path-traversal defence: entry names are sanitized and directory components stripped.

**cURL example:**
```bash
curl -X POST "http://localhost:8000/api/v1/files/upload-zip?file_ext=.dxf" \
  -H "Authorization: Bearer $TOKEN" \
  -F "upload=@楼层图纸.zip"
```

**Success response (201 Created):**
```json
{
  "data": {
    "batch_name": "楼层图纸",
    "files": [
      {
        "id": 2101,
        "bucket": "dxf-original",
        "storage_key": "uploads/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.dxf",
        "original_name": "F1-plan.dxf",
        "file_ext": ".dxf",
        "content_type": "image/vnd.dxf",
        "size_bytes": 245760,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "md5": "d41d8cd98f00b204e9800998ecf8427e",
        "uploaded_by": 1,
        "status": "available",
        "batch_name": "楼层图纸",
        "created_at": "2026-07-03T10:05:00+00:00",
        "updated_at": "2026-07-03T10:05:00+00:00"
      }
    ],
    "success_count": 1,
    "skipped_count": 2
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 415 | `FILE_TYPE_NOT_ALLOWED` | Uploaded file is not a `.zip` archive |
| 422 | `INVALID_PARAMS` | `file_ext` is empty, is not `.dwg`/`.dxf`, or is not in the upload allowlist |
| 413 | `FILE_TOO_LARGE` | ZIP archive exceeds `MAX_UPLOAD_SIZE_MB` |
| 415 | `FILE_NOT_ZIP` | Uploaded file is not a valid ZIP archive |
| 415 | `ZIP_CORRUPTED` | ZIP contains a corrupted entry (`testzip` failed) |
| 413 | `ZIP_TOO_MANY_FILES` | ZIP contains more than `max_zip_entry_count` files |
| 413 | `ZIP_TOO_LARGE` | Extracted content exceeds `max_zip_extract_mb` |
| 422 | `ZIP_EMPTY` | Archive contains no files at all (nothing extracted and nothing skipped) |

---

#### GET /api/v1/files/batches -- List Batches

Return all distinct batch names among non-deleted files, each with a file count and the most recent `created_at` within the batch. Results are cached in Redis for 30 seconds to reduce database load during rapid polling.

**Authorization:** Any authenticated user. Note: this endpoint returns batch aggregates across all non-deleted files and does **not** apply per-file access filtering.

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_ext` | string | No | `""` (all) | Filter batches by file extension, e.g. `.dwg` or `.dxf` |

**Success response (200 OK):**
```json
{
  "data": [
    {
      "name": "楼层图纸",
      "file_count": 12,
      "latest_created_at": "2026-07-03T10:05:00+00:00"
    },
    {
      "name": "导入_20260702_143000",
      "file_count": 3,
      "latest_created_at": "2026-07-02T14:30:00+00:00"
    }
  ],
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

The `data` field is a plain array (not paginated). Batches are ordered by `latest_created_at` descending.

**Error responses:** none beyond standard authentication (`401`).

---

#### DELETE /api/v1/files/batches/{batch_name} -- Delete a Batch

Soft-delete every non-deleted file whose `batch_name` matches. Access control is enforced per file: if **any** file in the batch is not deletable by the caller, the entire operation is rejected and no files are deleted.

**Authorization:** For every file in the batch, the caller must be the uploader or an administrator (global project access).

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `batch_name` | string | The batch name to delete |

**Request body:** none.

**Success response (204 No Content):**
```
HTTP/1.1 204 No Content
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 404 | `NOT_FOUND` | No non-deleted files exist with this batch name |
| 403 | `FORBIDDEN` | At least one file in the batch is not deletable by the caller |

---

#### GET /api/v1/files/batches/{batch_name}/download-zip -- Download a Batch as ZIP

Stream all non-deleted files in a batch as a single ZIP archive. Only the original file format is included (conversion results are not). The archive format is derived from the first file's extension (`dwg` or `dxf`, defaulting to `dxf`). The response is streamed from a temporary file that is deleted after the transfer completes.

**Authorization:** For every file in the batch, the caller must be the uploader, an administrator, or a member of a project that uses the file.

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `batch_name` | string | The batch name to download |

**Request body:** none. No query parameters.

**Success response (200 OK):** binary ZIP stream (not JSON).
```
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename*=UTF-8''%E6%A5%BC%E5%B1%82%E5%9B%BE%E7%BA%B8.zip
Content-Length: 184320

<binary ZIP stream>
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 404 | `NOT_FOUND` | No non-deleted files exist with this batch name |
| 403 | `FORBIDDEN` | At least one file in the batch is not readable by the caller |

---

#### GET /api/v1/files/{file_id}/excel-preview -- Preview an Excel File

Return an Excel file's content as JSON for browser preview. Only `.xlsx` / `.xls` files are supported. The first row is treated as the header; empty header cells become `Col A`, `Col B`, … and duplicate headers get a numeric suffix (`Name`, `Name_2`). Results are cached in Redis for 5 minutes, keyed by file and sheet.

**Authorization:** Uploader, administrator, or member of a project that uses the file.

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_id` | int | The stored file's ID |

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sheet` | string | No | `""` (first sheet) | Sheet name to preview |

**Success response (200 OK):**
```json
{
  "data": {
    "file": "楼层图纸.xlsx",
    "file_id": 2050,
    "sheets": ["Sheet1", "材料表"],
    "sheet": "Sheet1",
    "headers": ["序号", "名称", "规格", "数量"],
    "rows": [
      {"序号": 1, "名称": "角钢", "规格": "L50x5", "数量": 4},
      {"序号": 2, "名称": "钢板", "规格": "δ10", "数量": 2}
    ],
    "total_rows": 128
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 404 | `NOT_FOUND` | File does not exist or is soft-deleted |
| 403 | `FORBIDDEN` | Caller lacks read access to the file |
| 415 | `NOT_EXCEL` | File extension is not `.xlsx` / `.xls` |
| 404 | `NOT_FOUND` | Stored object is missing from the storage backend |
| 503 | `STORAGE_READ_FAILED` | Storage backend failed to read the file object |
| 503 | `OPENPYXL_UNAVAILABLE` | `openpyxl` is not installed |
| 415 | `EXCEL_PARSE_ERROR` | `openpyxl` could not parse the workbook |
| 415 | `EXCEL_EMPTY` | Workbook contains no sheets |
| 422 | `SHEET_NOT_FOUND` | Requested `sheet` name is not in the workbook |

---

#### POST /api/v1/files/bulk-delete -- Bulk Soft-Delete Files

Soft-delete multiple files by ID. Only files that currently exist and are not already deleted are affected; unknown or already-deleted IDs are silently ignored. Access is checked per file before deletion.

**Authorization:** For every matched file, the caller must be the uploader or an administrator.

**Request body:**
```json
{
  "file_ids": [1001, 1002, 1003]
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `file_ids` | array of int | Yes | Must not be empty |

**Success response (204 No Content):**
```
HTTP/1.1 204 No Content
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 422 | `INVALID_PARAMS` | `file_ids` is empty |
| 403 | `FORBIDDEN` | At least one matched file is not deletable by the caller |

---

#### POST /api/v1/files/download-zip -- Download Selected Files as ZIP

Build a ZIP archive containing the requested format versions (DWG and/or DXF) of the selected source files and stream it directly. For each source file, the DWG/DXF version is taken from the source itself when its extension matches, otherwise from a succeeded conversion result. Filenames colliding on stem are disambiguated with `(1)`, `(2)`, … The response is streamed from a temporary file that is deleted after the transfer.

**Authorization:** Every requested file must be readable by the caller (uploader, administrator, or project member).

**Request body:**
```json
{
  "file_ids": [1001, 1002],
  "formats": ["dwg", "dxf"],
  "folder_name": "图纸导出"
}
```

| Field | Type | Required | Default | Constraints |
|-------|------|----------|---------|-------------|
| `file_ids` | array of int | Yes | -- | Must not be empty; every ID must reference an existing, non-deleted file |
| `formats` | array of string | Yes | -- | Must not be empty; each element is `"dwg"` or `"dxf"` |
| `folder_name` | string | No | `"图纸导出"` | Top-level folder name inside the archive (sanitized) |

**Success response (200 OK):** binary ZIP stream (not JSON).
```
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename*=UTF-8''%E5%9B%BE%E7%BA%B8%E5%AF%BC%E5%87%BA.zip
Content-Length: 262144

<binary ZIP stream>
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 422 | `INVALID_PARAMS` | `file_ids` is empty |
| 422 | `INVALID_PARAMS` | `formats` is empty |
| 404 | `NOT_FOUND` | One or more requested file IDs do not exist or are soft-deleted |
| 403 | `FORBIDDEN` | At least one requested file is not readable by the caller |

---

### 3.3 Job Lifecycle

#### POST /api/v1/jobs -- Create a Processing Job

**Request body:**
```json
{
  "drawing_id": 123,
  "project_id": 1,
  "task_type": "extract_layers",
  "precision_level": "normal",
  "params": {
    "include_hidden_layers": false,
    "export_preview": true
  }
}
```

| Field | Type | Required | Default | Constraints |
|-------|------|----------|---------|-------------|
| `drawing_id` | int | No | null | Must belong to an active project |
| `project_id` | int | No | null | Must be active |
| `task_type` | string | No | `"framework_smoke_test"` | 1-64 chars; pattern `^[a-z][a-z0-9_]+$` |
| `precision_level` | string | No | `"normal"` | 1-32 chars |
| `params` | object | No | `{}` | Arbitrary key-value pairs passed to worker |

**Success response (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "project_id": 1,
    "drawing_id": 123,
    "created_by": 1,
    "task_type": "extract_layers",
    "precision_level": "normal",
    "pipeline": "local_stub",
    "status": "queued",
    "priority": 0,
    "progress": 0,
    "params_json": {
      "include_hidden_layers": false,
      "export_preview": true
    },
    "error_code": null,
    "error_message": null,
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": null,
    "finished_at": null
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:10:00+00:00"
  }
}
```

**Stage 1 behavior:** A Celery `worker-report` stub task transitions the job from `queued` to `running` to `succeeded`, writes job steps, and stores a JSON result file. Tests run this task in Celery eager mode; Docker/local scripts run a real worker process.

---

#### GET /api/v1/jobs/{job_id} -- Job Status

Poll this endpoint to track a job's progress.

**Success response (200 OK -- job in progress):**
```json
{
  "data": {
    "id": 456,
    "project_id": 1,
    "drawing_id": 123,
    "created_by": 1,
    "task_type": "extract_layers",
    "precision_level": "normal",
    "pipeline": "dxf_open_source",
    "status": "running",
    "priority": 0,
    "progress": 45,
    "params_json": {
      "include_hidden_layers": false
    },
    "error_code": null,
    "error_message": null,
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": "2026-07-03T10:10:01+00:00",
    "finished_at": null
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:12:00+00:00"
  }
}
```

**Success response (200 OK -- job completed):**
```json
{
  "data": {
    "id": 456,
    "project_id": 1,
    "drawing_id": 123,
    "created_by": 1,
    "task_type": "extract_layers",
    "precision_level": "normal",
    "pipeline": "dxf_open_source",
    "status": "succeeded",
    "priority": 0,
    "progress": 100,
    "params_json": {
      "include_hidden_layers": false
    },
    "error_code": null,
    "error_message": null,
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": "2026-07-03T10:10:01+00:00",
    "finished_at": "2026-07-03T10:13:30+00:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:14:00+00:00"
  }
}
```

**Success response (200 OK -- job failed):**
```json
{
  "data": {
    "id": 456,
    "status": "failed",
    "progress": 72,
    "error_code": "DXF_PARSE_ERROR",
    "error_message": "ezdxf failed to parse entity at line 2541: invalid group code.",
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": "2026-07-03T10:10:01+00:00",
    "finished_at": "2026-07-03T10:11:45+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

---

#### POST /api/v1/jobs/{job_id}/cancellation-requests -- Cancel a Job

Only jobs in `queued` or `running` status can be cancelled. Jobs already in a terminal state (`succeeded`, `failed`, `cancelled`) will be rejected.

**Request body:** none required (empty body).

**Success response (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "status": "cancelled",
    "finished_at": "2026-07-03T10:15:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 409 | `JOB_NOT_CANCELLABLE` | Job is already succeeded / failed / cancelled |

---

#### POST /api/v1/jobs/{job_id}/retry-requests -- Retry a Job

Only jobs in `failed` or `cancelled` status can be retried.

**Request body:** none required (empty body).

**Success response (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "status": "queued",
    "progress": 0,
    "created_at": "2026-07-03T10:16:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

The job is retried in-place: its status is reset to `queued` and progress is reset to 0, then re-enqueued.

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 409 | `JOB_NOT_RETRYABLE` | Job is not in failed or cancelled status |

---

#### POST /api/v1/jobs/cancel-all-active -- Cancel All Active Jobs

Administrative kill-switch. Bulk-cancels **every** job currently in `queued`, `running`, or `pending` status **across the entire system** (not scoped to the caller's own jobs or projects). Executed as a single bulk SQL `UPDATE` (deliberately bypassing the ORM to avoid optimistic-locking / MySQL error 1020 conflicts with a Celery worker touching the same rows), then makes a best-effort attempt to revoke active/reserved Celery tasks (`terminate=True`, `SIGTERM`) and purge the `dxf`, `report`, `agent`, and `cad` queues. Every cancelled row is stamped with `error_code = "CANCELLED_BY_ADMIN"` and `error_message = "Cancelled by administrator via bulk cancel."`. A single batch audit-log entry (`action = "jobs.cancel_all"`) records `cancelled_count` and `cancelled_ids`.

**Authorization:** Caller must have global project access (`super_admin` / `admin`). All other authenticated users receive 403.

**Request body:** none required (empty body).

**Success response (200 OK):**
```json
{
  "data": {
    "cancelled_count": 7,
    "celery_revoked": 3
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `cancelled_count` | int | Number of DB rows transitioned to `cancelled` (bulk `UPDATE` rowcount for jobs previously in `queued`/`running`/`pending`) |
| `celery_revoked` | int | Count of live Celery tasks revoked (best-effort; `0` if Celery is unreachable — the DB cancellation still succeeds) |

Note: unlike the other job actions (`cancellation-requests` / `retry-requests`, which return **202 Accepted**), this endpoint returns **200 OK** (no explicit status code on the route).

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 403 | `FORBIDDEN` | Caller is not `admin` / `super_admin` (message: "Only administrators can cancel all jobs.") |
| 401 | `INVALID_TOKEN` | Missing, expired, or blacklisted token |

---

### 3.4 Project Membership

#### POST /api/v1/projects/{project_id}/members -- Add Member

**Request body:**
```json
{
  "user_id": 5,
  "project_role": "project_engineer"
}
```

**Success response (201 Created):**
```json
{
  "data": {
    "id": 42,
    "project_id": 1,
    "user_id": 5,
    "project_role": "project_engineer",
    "created_at": "2026-07-03T10:20:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

**Valid `project_role` values:** `project_owner`, `project_engineer`, `project_reviewer`, `project_viewer`

---

### 3.5 Result Review

#### POST /api/v1/results/{result_id}/reviews -- Submit Review

**Request body:**
```json
{
  "decision": "approved",
  "comment": "All extracted layers match the drawing. Dimensions verified."
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `decision` | string | Yes | `"approved"`, `"rejected"`, or `"needs_revision"` |
| `comment` | string | No | Review notes |

**Success response (201 Created):**
```json
{
  "data": {
    "id": 12,
    "result_id": 2001,
    "reviewer_id": 1,
    "decision": "approved",
    "comment": "All extracted layers match the drawing.",
    "created_at": "2026-07-03T10:30:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

---

### 3.6 System & Health

#### GET /api/v1/system/health -- Deep Health Check

Authenticated operational overview. Reports Redis client availability, the three feature flags, and the active storage backend. `status` is a **static** `"ok"` literal (not a computed aggregate). The endpoint does **not** perform a database probe, and despite its docstring does **not** include an ODA check (use `/system/health/oda` for that). Only three of the pipeline flags are surfaced here — `dxf2dwg_pipeline_enabled` and `dxf2excel_pipeline_enabled` are not reported.

**Request body:** none.

**Success response (200 OK):**
```json
{
  "data": {
    "status": "ok",
    "redis": true,
    "features": {
      "agent": false,
      "dxf_pipeline": false,
      "cad_worker": false
    },
    "storage_backend": "local"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always the literal `"ok"` (static; does not reflect subsystem failures) |
| `redis` | bool | `true` when a Redis client can be obtained (`get_redis()` is not `None`), else `false` |
| `features.agent` | bool | Value of `AGENT_ENABLED` |
| `features.dxf_pipeline` | bool | Value of `DXF_PIPELINE_ENABLED` |
| `features.cad_worker` | bool | Value of `CAD_WORKER_ENABLED` |
| `storage_backend` | string | Value of `STORAGE_BACKEND` -- `"local"` or `"minio"` |

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 401 | `INVALID_TOKEN` | Missing, expired, or blacklisted token |

---

#### GET /api/v1/system/health/oda -- ODA File Converter Probe

Authenticated environment probe for the ODA File Converter binary used by the DXF pipelines. Delegates to `dwg_converter.framework.health_check()`, which inspects the environment (locates the ODA executable, checks `ezdxf` importability) and **never raises** — an unhealthy environment is reported in the body, so the endpoint always returns **200 OK** (never 503). The response body is the verbatim `HealthStatus.to_dict()`.

**Request body:** none.

**Success response (200 OK -- ODA available):**
```json
{
  "data": {
    "healthy": true,
    "oda_found": true,
    "oda_executable": "/opt/oda/ODAFileConverter",
    "ezdxf_available": true,
    "messages": ["ODA File Converter located", "ezdxf import OK"],
    "error_code": null
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

**Success response (200 OK -- ODA missing):**
```json
{
  "data": {
    "healthy": false,
    "oda_found": false,
    "oda_executable": null,
    "ezdxf_available": true,
    "messages": ["ODA File Converter 未找到 ..."],
    "error_code": "ODA_NOT_FOUND"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `healthy` | bool | `true` only when the ODA executable was located |
| `oda_found` | bool | Whether the ODA File Converter binary was found |
| `oda_executable` | string \| null | Absolute path to the located binary; `null` when not found |
| `ezdxf_available` | bool | Whether `ezdxf` (parsing-stage dependency) is importable |
| `messages` | string[] | Detailed environment-check log lines |
| `error_code` | string \| null | `"ODA_NOT_FOUND"` when unhealthy; `null` when healthy |

**Error responses:**
| Status | Code | Condition |
|--------|------|-----------|
| 401 | `INVALID_TOKEN` | Missing, expired, or blacklisted token |

(No 503 is emitted by this endpoint: an unavailable ODA environment yields 200 with `healthy: false` and `error_code: "ODA_NOT_FOUND"` in the body.)

---

## 4. Unified Response Format

### 4.1 Success (Single Resource)

```json
{
  "data": { ... },
  "meta": {
    "request_id": "req_20260703_000001",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 4.2 Success (Paginated List)

```json
{
  "data": [ ... ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 120,
    "total_pages": 6
  },
  "meta": {
    "request_id": "req_20260703_000001",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 4.3 Error

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description of what went wrong.",
    "details": {
      "field": "optional per-field detail"
    }
  },
  "meta": {
    "request_id": "req_20260703_000003",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 4.4 Empty Success (204 No Content)

For DELETE operations and logout, the response has no body:

```
HTTP/1.1 204 No Content
```

---

## 5. Pagination and Filtering

### 5.1 Pagination

All list endpoints (`GET /api/v1/users`, `GET /api/v1/projects`, `GET /api/v1/files`, etc.) support pagination via query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number (1-indexed) |
| `page_size` | int | 20 | Items per page (max 200) |
| `sort_by` | string | `"created_at"` | Column to sort by (varies per endpoint) |
| `sort_dir` | string | `"desc"` | Sort direction: `asc` or `desc` |

**Example:**
```
GET /api/v1/projects?page=2&page_size=50&sort_by=name&sort_dir=asc
```

The response includes a `pagination` object with `page`, `page_size`, `total`, and `total_pages`.

### 5.2 Filtering

List endpoints accept optional query filters. Common filters include:

| Parameter | Type | Example | Description |
|-----------|------|---------|-------------|
| `status` | string | `?status=active` | Filter by resource status |
| `project_id` | int | `?project_id=1` | Filter by parent project |

Specific filter parameters vary per endpoint. Check the OpenAPI schema at `/docs` for the full list.

### 5.3 Filtering Behavior by Role

- **super_admin / admin:** See all resources by default. Filters narrow the result set.
- **Regular users:** Results are automatically scoped to the user's accessible resources (owned files, member projects, etc.). Filters further narrow within that scope.
- **Cross-project isolation:** Regular users cannot see resources from projects they are not members of (the server filters before pagination, so leaked counts are impossible).

---

## 6. HTTP Status Codes

| Code | Meaning | Typical Usage |
|------|---------|---------------|
| 200 | OK | Successful GET, PATCH, PUT operations returning data |
| 201 | Created | Resource created (POST user, project, file, member) |
| 202 | Accepted | Async operation accepted (POST job, agent-run, cancel, retry) |
| 204 | No Content | Successful DELETE, logout (no response body) |
| 400 | Bad Request | Invalid request parameters or combination |
| 401 | Unauthorized | Missing, expired, or blacklisted token |
| 403 | Forbidden | Authenticated but insufficient permissions |
| 404 | Not Found | Resource does not exist (or user lacks access -- indistinguishable) |
| 409 | Conflict | State conflict (e.g., retry non-retryable job, duplicate username) |
| 413 | Payload Too Large | Upload exceeds `MAX_UPLOAD_SIZE_MB` |
| 415 | Unsupported Media Type | File type not allowed |
| 422 | Unprocessable Entity | Pydantic validation failure on request body |
| 429 | Too Many Requests | Rate limit exceeded (login attempts) |
| 500 | Internal Server Error | Unhandled server exception |
| 503 | Service Unavailable | Agent disabled (`AGENT_ENABLED=false`), MCP unavailable, CAD Worker unreachable |

---

## 7. Error Codes Reference

### 7.1 Authentication Errors

| Code | HTTP | Message |
|------|------|---------|
| `INVALID_CREDENTIALS` | 401 | Incorrect username or password. |
| `INVALID_TOKEN` | 401 | Access token or refresh token is invalid or expired. |
| `TOKEN_REVOKED` | 401 | Token has been revoked (logged out or password changed). |
| `USER_NOT_ACTIVE` | 401 | Account is disabled or deleted. |
| `INVALID_CURRENT_PASSWORD` | 400 | Current password is incorrect. |

### 7.2 Authorization Errors

| Code | HTTP | Message |
|------|------|---------|
| `FORBIDDEN` | 403 | Insufficient permissions for this action (generic). |
| `CANNOT_MANAGE_SUPER_ADMIN` | 400 | Only super_admin can manage super_admin accounts. |
| `CANNOT_DISABLE_SELF` | 400 | Admin cannot disable their own account. |
| `CANNOT_DELETE_SELF` | 400 | Admin cannot delete their own account. |
| `CANNOT_REMOVE_OWN_ROLE` | 400 | Admin cannot remove roles from their own account. |

### 7.3 Resource Errors

| Code | HTTP | Message |
|------|------|---------|
| `NOT_FOUND` | 404 | The requested resource does not exist. |
| `USERNAME_EXISTS` | 409 | A user with this username already exists. |
| `ROLE_EXISTS` | 409 | A role with this code already exists. |
| `PROJECT_CODE_EXISTS` | 409 | A project with this code already exists. |
| `USER_DELETED` | 400 | User has already been deleted. |
| `SELF_RESET_NOT_IMPLEMENTED` | 400 | Self-service password reset is not yet implemented. |
| `PROJECT_MEMBER_EXISTS` | 409 | User is already a member of this project. |

### 7.4 File Errors

| Code | HTTP | Message |
|------|------|---------|
| `FILE_TYPE_NOT_ALLOWED` | 415 | Only `.dwg` files are accepted. |
| `FILE_MIME_NOT_ALLOWED` | 415 | MIME type is not in the DWG allowlist. |
| `FILE_NOT_DWG` | 415 | File does not have a valid DWG header, or is too small (< 1024 bytes). |
| `FILE_TOO_LARGE` | 413 | File exceeds the maximum upload size. |
| `FILE_NOT_ZIP` | 415 | Uploaded file is not a valid ZIP archive. |
| `ZIP_CORRUPTED` | 415 | ZIP archive is corrupted or cannot be opened. |
| `ZIP_TOO_MANY_FILES` | 413 | ZIP contains more entries than `MAX_ZIP_ENTRY_COUNT`. |
| `ZIP_TOO_LARGE` | 413 | ZIP uncompressed size exceeds `MAX_ZIP_EXTRACT_MB`. |
| `ZIP_EMPTY` | 422 | ZIP archive contains no usable files. |
| `NOT_EXCEL` | 415 | Only `.xlsx` / `.xls` files can be previewed. |
| `EXCEL_PARSE_ERROR` | 415 | Failed to parse Excel file. |
| `EXCEL_EMPTY` | 415 | Excel file has no sheets. |
| `SHEET_NOT_FOUND` | 422 | Requested sheet not found in the workbook. |

### 7.5 Download Errors

| Code | HTTP | Message |
|------|------|---------|
| `INVALID_DOWNLOAD_SIGNATURE` | 403 | Download URL signature is missing or invalid. |
| `DOWNLOAD_URL_EXPIRED` | 403 | Download URL has expired (TTL=300s). |

### 7.6 Storage Errors

| Code | HTTP | Message |
|------|------|---------|
| `STORAGE_WRITE_FAILED` | 503 | Failed to persist file to storage backend. |
| `STORAGE_READ_FAILED` | 503 | Failed to read stored file object. |
| `STORAGE_BACKEND_MISCONFIGURED` | 500 | Configured storage backend is not ready. |
| `STORAGE_BACKEND_UNSUPPORTED` | 500 | Unsupported storage backend. |
| `INVALID_STORAGE_PATH` | 400 | Path escapes the configured storage root. |

### 7.7 Job Errors

| Code | HTTP | Message |
|------|------|---------|
| `JOB_NOT_CANCELLABLE` | 409 | Job cannot be cancelled because it is already in a terminal state. |
| `JOB_NOT_RETRYABLE` | 409 | Job cannot be retried in its current state (only failed/cancelled). |
| `JOB_ENQUEUE_FAILED` | 503 | Job was created but could not be dispatched to Celery. |
| `DXF_PIPELINE_DISABLED` | 503 | DWG→DXF pipeline is disabled. Set `DXF_PIPELINE_ENABLED=true` to enable. |
| `DXF2DWG_PIPELINE_DISABLED` | 503 | DXF→DWG pipeline is disabled. Set `DXF2DWG_PIPELINE_ENABLED=true` to enable. |
| `DXF2EXCEL_PIPELINE_DISABLED` | 503 | DXF→Excel pipeline is disabled. Set `DXF2EXCEL_PIPELINE_ENABLED=true` to enable. |
| `STUB_WORKER_FAILED` | — (async) | Stage-1 stub worker raised an exception. |
| `CANCELLED_BY_ADMIN` | — (async) | Job was cancelled by an administrator. |
| `DXF_CONVERSION_FAILED` | — (async) | DWG→DXF ODA conversion failed. |
| `DXF_SOURCE_MISSING` | — (async) | Source DWG file missing for DWG→DXF job. |
| `DWG_CONVERSION_FAILED` | — (async) | DXF→DWG ODA conversion failed. |
| `DXF_SOURCE_FILE_MISSING` | — (async) | Source DXF file missing for DXF→DWG job. |
| `DXF2EXCEL_EMPTY_BATCH` | — (async) | No DXF files in the batch for the DXF→Excel job. |
| `DXF2EXCEL_UNAVAILABLE` | — (async) | DXF→Excel pipeline dependency unavailable. |
| `DXF2EXCEL_PIPELINE_FAILED` | — (async) | DXF→Excel extraction failed. |
| `DXF2EXCEL_NO_OUTPUT` | — (async) | DXF→Excel produced no output file. |
| `DXF2EXCEL_STORAGE_FAILED` | — (async) | Failed to persist DXF→Excel result to storage. |

### 7.8 Service Errors

| Code | HTTP | Message |
|------|------|---------|
| `AGENT_DISABLED` | 503 | Agent subsystem is intentionally disabled in Stage 1. |
| `INTERNAL_ERROR` | 500 | An unexpected server error occurred. |
| `VALIDATION_ERROR` | 422 | Request validation failed (Pydantic). |
| `INVALID_SORT_COLUMN` | 422 | The provided sort column is not in the allowed list for this resource. |
| `INVALID_SORT_RESOURCE` | 422 | Sorting is not supported for this resource type. |
| `INVALID_PARAMS` | 422 | Required query/body parameter is missing or invalid. |
| `INVALID_STATUS_FILTER` | 422 | Status filter must be `active`, `deleted`, or omitted. |
| `OPENPYXL_UNAVAILABLE` | 503 | openpyxl is not installed — cannot preview Excel files. |
| `HTTP_ERROR` | (matches raised status) | Generic fallback code emitted by the global exception handler (`main.py`) for plain `HTTPException`s lacking a structured `{code,message}` detail. |

---

## 8. Project Roles

### 8.1 Global Roles (System-Level)

Controlled via the `sys_roles` and `sys_user_roles` tables. Managed by super_admin/admin through `/api/v1/users/{user_id}/roles`.

| Role | Constant | Code | Scope |
|------|----------|------|-------|
| Super Admin | `ROLE_SUPER_ADMIN` | `super_admin` | Full system access; bypasses all permission checks |
| Admin | `ROLE_ADMIN` | `admin` | User management, project oversight, job monitoring |
| Engineer | `ROLE_ENGINEER` | `engineer` | Upload files, create tasks, view own projects |
| Reviewer | `ROLE_REVIEWER` | `reviewer` | Review analysis results |
| Operator | `ROLE_OPERATOR` | `operator` | Execute assigned tasks |
| Viewer | `ROLE_VIEWER` | `viewer` | Read-only access to assigned projects |
| Auditor | `ROLE_AUDITOR` | `auditor` | View audit logs |

### 8.2 Project Roles (Project-Level)

Controlled via the `project_members` table. Managed by `project_owner` through `/api/v1/projects/{project_id}/members`.

| Role | Constant Set | Can Read | Can Write Drawings | Can Create Jobs | Can Review | Can Manage Members |
|------|-------------|----------|-------------------|-----------------|------------|-------------------|
| `project_owner` | `PROJECT_OWNER_ROLES` | Yes | Yes | Yes | Yes | Yes |
| `project_engineer` | `PROJECT_WRITE_ROLES` | Yes | Yes | Yes | No | No |
| `project_reviewer` | -- | Yes | No | No | Yes | No |
| `project_viewer` | -- | Yes | No | No | No | No |

### 8.3 Role Combination Rules

- A user can hold multiple global roles.
- A user can hold one project role per project.
- `super_admin` bypasses all project-level permission checks -- they are effectively members of every project with full access.
- `admin` also bypasses project membership checks for read and oversight operations.
- Project role checks cascade through active project status: if a project is soft-deleted, all members get 404 regardless of their role.

---

## 9. Job Status State Machine

```
pending ──→ queued ──→ running ──→ validating ──→ need_review ──→ succeeded
  │                     │   │                        (after review)
  │                     │   └──→ waiting_cad_worker ──→ validating ──→ ...
  │                     │
  └── (auto)            ├──→ cancelled (from queued/running only)
                        └──→ failed    (from running/validating)
                                         │
                                         └──→ retry ──→ queued (from failed/cancelled only)
```

| Status | Meaning | Terminal? |
|--------|---------|-----------|
| `pending` | Job created but not yet dispatched | No |
| `queued` | Dispatched to Celery queue | No |
| `running` | Worker is actively processing | No |
| `waiting_cad_worker` | Waiting for Windows CAD Worker (Stage 4) | No |
| `validating` | Result validation in progress | No |
| `need_review` | Low confidence -- requires human review | No |
| `succeeded` | Task completed successfully | **Yes** |
| `failed` | Task failed with error | **Yes** |
| `cancelled` | User cancelled the task | **Yes** |

---

## 10. Pipeline Constants

| Constant | Value | Description | Stage |
|----------|-------|-------------|-------|
| `PIPELINE_STUB` | `local_stub` | Celery report-queue fake worker (default fallback in `_pipeline_for`) | Stage 1 |
| `PIPELINE_DXF` | `dxf_open_source` | DWG→DXF conversion via ODA File Converter, then ezdxf parsing (`convert_dwg_to_dxf`) | Stage 3 |
| `PIPELINE_DXF2DWG` | `dxf2dwg_open_source` | DXF→DWG reverse conversion via ODA File Converter (`convert_dxf_to_dwg`) | Stage 3 |
| `PIPELINE_DXF2EXCEL` | `dxf2excel` | DXF→Excel material-table extraction (`extract_dxf_to_excel`) | Stage 3 |
| `PIPELINE_CAD` | `zwcad_worker` | Windows ZWCAD high-precision pipeline (defined only; not yet selected by `_pipeline_for`) | Stage 4 |

---

## 11. Feature Flags

All future-stage capabilities are gated behind boolean feature flags in `backend/app/core/config.py` (all default `false`). When a flag is disabled the request is rejected with HTTP 503 — either at the endpoint itself (`AGENT_ENABLED`, enforced in `agent_runs_api.py`) or during job pipeline selection in `POST /api/v1/jobs` (the three DXF flags, enforced in `jobs_api.py`). `CAD_WORKER_ENABLED` is defined and reported by the system flags endpoint but is **not yet enforced** anywhere (Stage 4 dispatch not wired). To enable a feature, set the flag to `true` in `.env` and restart the backend.

| Flag | Default | Controls | Stage |
|------|---------|----------|-------|
| `AGENT_ENABLED` | `false` | All 4 agent endpoints (3 `/api/v1/agent-runs/*` + `/api/v1/agent-tools`); each returns 503 `AGENT_DISABLED` when off (enforced in `agent_runs_api.py`) | Stage 2 |
| `DXF_PIPELINE_ENABLED` | `false` | DWG→DXF selection (`task_type=convert_dwg_to_dxf`) in `POST /api/v1/jobs`; returns 503 `DXF_PIPELINE_DISABLED` when off (enforced in `jobs_api.py`) | Stage 3 |
| `DXF2DWG_PIPELINE_ENABLED` | `false` | DXF→DWG reverse selection (`task_type=convert_dxf_to_dwg`) in `POST /api/v1/jobs`; returns 503 `DXF2DWG_PIPELINE_DISABLED` when off (enforced in `jobs_api.py`) | Stage 3 |
| `DXF2EXCEL_PIPELINE_ENABLED` | `false` | DXF→Excel selection (`task_type=extract_dxf_to_excel`) in `POST /api/v1/jobs`; returns 503 `DXF2EXCEL_PIPELINE_DISABLED` when off (enforced in `jobs_api.py`) | Stage 3 |
| `CAD_WORKER_ENABLED` | `false` | Reserved for CAD Worker dispatch selection; currently only surfaced by the system flags endpoint (`system_api.py`) — no 503 gate wired in `jobs_api.py` / `job_service.py` yet | Stage 4 |

---

## 12. OpenAPI Schema

Interactive API documentation is available at:

```
http://localhost:8000/docs     (Swagger UI)
http://localhost:8000/redoc    (ReDoc)
```

All request/response schemas are generated from Pydantic v2 models and reflect the live API surface.
