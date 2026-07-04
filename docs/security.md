# DWG-Agent Platform -- Security Architecture

> **Audience:** Security auditors, platform operators, on-premise deployment engineers
> **Last updated:** 2026-07-03
> **Scope:** Authentication, RBAC, API security, file security, pentest remediation, deployment checklist, audit log coverage

---

## 1. Authentication Flow

### 1.1 Login

```
POST /api/v1/auth/sessions
{
  "username": "10001",
  "password": "********"
}
```

The authentication path is:

1. **Username lookup** -- query `sys_users` by `username` column.
2. **Constant-time verification** -- `authenticate_user()` in `auth_service.py` always runs one full Argon2id verification:
   - If the user exists and is `active`, verify against the stored `password_hash`.
   - If the user does not exist or is `disabled`/`deleted`, verify against a hardcoded dummy Argon2id hash.
   - This eliminates the timing side-channel (previously 40x faster to reject non-existent users). See pentest finding H1.
3. **Token issuance on success:**
   - **Access token:** JWT HS256, `sub` = user ID, `jti` = random UUID4, `type` = `"access"`, expiry = 30 minutes. Returned in the JSON response body.
   - **Refresh token:** JWT HS256, `sub` = user ID, `jti` = random UUID4, `type` = `"refresh"`, expiry = 14 days. Set as `HttpOnly; SameSite=Lax` cookie on the `/api/v1/auth` path. The `Secure` flag is set only when `APP_ENV=production` (not in development mode).
4. **Login response** includes `access_token`, `token_type` ("Bearer"), `expires_in` (1800 seconds), and a summary user object.

### 1.2 Token structure

Both token types share the same payload shape:

```json
{
  "sub": "1",
  "username": "admin",
  "jti": "a1b2c3d4-...",
  "iat": 1751500000,
  "exp": 1751501800,
  "type": "access"
}
```

- **`sub`:** User ID (stringified int).
- **`jti`:** Unique token identifier for blacklisting. A token without `jti` is accepted but logged as a warning (pre-rollout compatibility).
- **`type`:** `"access"` or `"refresh"` -- the `get_current_user` dependency rejects refresh tokens.
- **Algorithm:** HS256 with `JWT_SECRET_KEY` from environment.

### 1.3 Token refresh

```
POST /api/v1/auth/tokens/refresh
```

- The refresh token is read from the `refresh_token` cookie.
- A new access token is issued. The refresh token itself is **not rotated** (see Section 5.3, remaining gaps).

### 1.4 Logout

```
DELETE /api/v1/auth/sessions/current
```

- The access token's `jti` is extracted and stored in Redis with TTL = remaining token lifetime (`exp - now`).
- The refresh token's `jti` is similarly blacklisted.
- Redis keys follow the pattern `blacklist:jti:{jti}` -- they self-expire after TTL, no cleanup job needed.
- If Redis is unavailable, blacklisting is silently skipped (degraded mode, logged as warning).

### 1.5 Per-request token validation

Every authenticated request flows through `get_current_user()` in `app/api/deps.py`:

1. Decode and verify the JWT signature.
2. Reject if `type` != `"access"`.
3. Check `jti` against the Redis blacklist -- return 401 `TOKEN_REVOKED` if blacklisted.
4. Look up the user by `sub` in the database.
5. Reject if the user does not exist or `status` != `"active"`.
6. Check whether the token was issued before the last password change -- return 401 `TOKEN_REVOKED` (password changed) if stale. This invalidates all tokens across all devices when the user changes their password.

### 1.6 Password management

- **Hashing:** Argon2id via `pwdlib.PasswordHash.recommended()` (m=65536, t=3, p=4).
- **Algorithm stored:** `password_algo = "argon2id"` in `sys_users`.
- **Minimum length:** 12 characters (enforced in Pydantic schema).
- **Complexity:** Must contain at least one uppercase letter, one lowercase letter, and one digit.
- **Common password blacklist:** Rejects passwords from a built-in list of common/breached passwords.
- **Password change:** `PATCH /api/v1/auth/password` -- requires old password verification, writes audit log.
- **Admin reset:** `POST /api/v1/users/{user_id}/password-reset-requests` -- admin-only, generates audit record.

---

## 2. RBAC Model

### 2.1 Five permission tables

```
sys_users  ──< sys_user_roles  >── sys_roles  ──< sys_role_permissions  >── sys_permissions

                                    ┌─────────────────────────────┐
                                    │ sys_users                   │
                                    │  id, username, status       │
                                    │  active / disabled / deleted│
                                    └──────────┬──────────────────┘
                                               │
                                    ┌──────────▼──────────────────┐
                                    │ sys_user_roles              │
                                    │  user_id FK, role_id FK      │
                                    │  PK: (user_id, role_id)      │
                                    └──────────┬──────────────────┘
                                               │
                 ┌─────────────────────────────▼──────┐
                 │ sys_roles                          │
                 │  code, is_system                   │
                 │  super_admin, admin, engineer, ...  │
                 └──────────┬─────────────────────────┘
                            │
                 ┌──────────▼─────────────────────────┐
                 │ sys_role_permissions               │
                 │  role_id FK, permission_id FK       │
                 │  PK: (role_id, permission_id)       │
                 └──────────┬─────────────────────────┘
                            │
                 ┌──────────▼─────────────────────────┐
                 │ sys_permissions                     │
                 │  code, resource, action              │
                 │  e.g. "users:read", "jobs:write"     │
                 └────────────────────────────────────┘
```

### 2.2 Seven global roles

| Role code | Display name | Typical capabilities |
|---|---|---|
| `super_admin` | Super Admin | Bypasses **all** permission checks. Full system access. |
| `admin` | System Admin | User management, project management, job management. Has `is_admin()` privilege (equivalent to `has_global_project_access`). |
| `engineer` | Engineer | Upload files, create tasks, view project results within their projects. |
| `reviewer` | Reviewer | Review analysis results, submit approval/rejection decisions. |
| `operator` | Operator | Execute assigned tasks within their projects. |
| `viewer` | Viewer | Read-only access to assigned projects. |
| `auditor` | Auditor | Read-only access to audit logs and system configuration. |

### 2.3 Four project-level roles

| Project role | Access level within a project |
|---|---|
| `project_owner` | Full control over the project, its members, files, drawings, jobs, and results. |
| `project_engineer` | Can upload files, create drawings, submit jobs, view results. |
| `project_reviewer` | Can review analysis results submitted for the project. |
| `project_viewer` | Read-only access to the project and its resources. |

### 2.4 Permission decision tree

```
                    ┌─────────────────────────────────────────┐
                    │        Incoming API request              │
                    │   (all business endpoints require auth)   │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │  1. Is the access token valid?           │
                    │     - JWT signature verified?            │
                    │     - type == "access"?                  │
                    │     - jti not blacklisted?               │
                    └────────────────────┬────────────────────┘
                              ┌─────────┴──────────┐
                              │ YES                  │ NO → 401 (INVALID_TOKEN / TOKEN_REVOKED)
                              ▼                      │
                    ┌─────────────────────────────────────────┐
                    │  2. Is the user active?                  │
                    │     - user exists in DB?                 │
                    │     - user.status == "active"?           │
                    └────────────────────┬────────────────────┘
                              ┌─────────┴──────────┐
                              │ YES                  │ NO → 401 (USER_NOT_ACTIVE)
                              ▼                      │
                    ┌─────────────────────────────────────────┐
                    │  3. Does the user have a global role     │
                    │     that grants access?                  │
                    │     - super_admin → bypass ALL checks    │
                    │     - admin → global project access      │
                    │     - role_codes ∩ required_roles != ∅   │
                    └────────────────────┬────────────────────┘
                              ┌─────────┴──────────┐
                              │ YES                  │ NO → Continue to step 4
                              ▼                      ▼
                    ┌──────────────┐   ┌─────────────────────────────────────────┐
                    │  ACCESS      │   │  4. Is the resource scoped to a project? │
                    │  GRANTED     │   │     (project_id present in path/body)    │
                    └──────────────┘   └────────────────────┬────────────────────┘
                                               ┌───────────┴──────────┐
                                               │ YES                   │ NO → 403
                                               ▼                       │
                                    ┌─────────────────────────────────────────┐
                                    │  5. Is the user a member of this project?│
                                    │     - check project_members table        │
                                    │     - project must be active (not soft-  │
                                    │       deleted)                            │
                                    └────────────────────┬────────────────────┘
                                              ┌──────────┴──────────┐
                                              │ YES                   │ NO → 403
                                              ▼                       │
                                    ┌─────────────────────────────────────────┐
                                    │  6. Does the project role allow this     │
                                    │     specific action?                     │
                                    │     - e.g. project_viewer cannot POST    │
                                    │     - e.g. project_engineer can upload   │
                                    └────────────────────┬────────────────────┘
                                              ┌──────────┴──────────┐
                                              │ YES                   │ NO → 403
                                              ▼                       │
                                    ┌──────────────┐                │
                                    │  ACCESS      │                │
                                    │  GRANTED     │                │
                                    └──────────────┘                │
```

### 2.5 Key permission implementation details

- **`require_roles(*allowed_roles)`:** FastAPI dependency. If `super_admin` is in the user's roles, access is granted immediately. Otherwise checks intersection with `allowed_roles`.
- **`is_admin(user)`:** True for `super_admin` or `admin`. Used as the gate for `has_global_project_access`.
- **`has_global_project_access(user)`:** `super_admin` and `admin` see all projects, bypassing project membership checks.
- **`require_project_member(db, user, project_id)`:** Checks `project_members` table. Skips if user has global access. Also validates that the project is active (not soft-deleted) -- this closes the BUG-7 soft-delete cascade.
- **`require_project_role(db, user, project_id, allowed_roles)`:** Checks project membership AND that the member's `project_role` is in the allowed set.
- **Self-action guards:**
  - Cannot delete or disable your own account.
  - Non-`super_admin` users cannot manage `super_admin` accounts.
- **`transition_user_status()`:** Uses `UPDATE ... WHERE id = :id AND status != 'deleted'` with `rowcount` check. Also supports `FOR UPDATE` via `get_user_or_404(for_update=True)`. Effectively eliminates the SELECT→UPDATE TOCTOU window.

### 2.6 Seeded permissions

| Permission code | Resource | Action | Description |
|---|---|---|---|
| `users:read` | users | read | View users |
| `users:write` | users | write | Manage users |
| `roles:write` | roles | write | Manage roles |
| `projects:write` | projects | write | Manage projects |
| `files:write` | files | write | Upload/delete files |
| `jobs:write` | jobs | write | Create/manage jobs |
| `reviews:write` | reviews | write | Submit reviews |
| `audit_logs:read` | audit_logs | read | View audit logs |

All 8 permissions are granted to `super_admin` at seed time.

---

## 3. API Security Measures

### 3.1 Authentication enforcement

- **All business endpoints require `current_user: CurrentUser`** -- no endpoint accepts `= None` as a default.
- The only unauthenticated endpoints are `POST /auth/sessions` (login), `POST /auth/tokens/refresh`, and `GET /health`.
- `OAuth2PasswordBearer` extracts the `Authorization: Bearer <token>` header automatically.
- WebSocket and SSE endpoints (for job events) also validate the token on connect.

### 3.2 CORS policy

```python
allow_origins = settings.cors_origins          # from BACKEND_CORS_ORIGINS env
allow_credentials = True                        # required for HttpOnly cookies
allow_methods = ["GET", "POST", "PATCH", "PUT", "DELETE"]  # OPTIONS auto-added
allow_headers = ["Authorization", "Content-Type"]
```

Notable: `allow_methods` is explicitly enumerated (not `["*"]`). `OPTIONS`, `HEAD`, `TRACE`, `CONNECT` are not exposed. `allow_headers` is also explicitly listed -- arbitrary headers are rejected by the CORS middleware.

### 3.3 Input validation

- **All inputs pass through Pydantic v2 models** with `model_config = ConfigDict(from_attributes=True)`.
- `RequestValidationError` is caught by a global handler and returns 422 with structured error details (never raw Pydantic tracebacks).
- Specific field-level constraints:
  - **Username:** `^[a-zA-Z0-9_.@-]+$` (closes H6: username injection via spaces/unicode).
  - **Real name:** HTML tag rejection (closes BUG-3: HTML injection).
  - **Password:** min_length=12, upper+lower+digit required, common password blacklist (closes BUG-2).
  - **task_type:** `^[a-z][a-z0-9_]+$` pattern (closes BUG-8).
  - **email:** valid `EmailStr` format.

### 3.4 Exception handling and information leakage

Four exception handlers cover the full error surface:

| Handler | Status | Behavior |
|---|---|---|
| `AppHTTPException` | Variable | Formats the business error code/message/details into the standard error envelope. |
| `StarletteHTTPException` | Variable | Catches framework-level HTTP errors (e.g. 405 Method Not Allowed). |
| `RequestValidationError` | 422 | Returns structured Pydantic error details. |
| `Exception` (catch-all) | 500 | Logs full traceback internally. Returns `"Internal server error."` when `debug=False`. Returns `str(exc)` only when `debug=True`. **Never leaks traceback.** |

The health endpoint returns `{"data": {"status": "ok"}, "meta": {"request_id": "...", "timestamp": "..."}}` -- no database status, version, uptime, or dependency info (closes BUG-4).

### 3.5 Resource isolation

- **Admin users** (`super_admin`, `admin`): Can list and access all projects, files, drawings, jobs, results.
- **Regular users:** Can only see projects they are members of. Files, drawings, jobs, and results are filtered by project membership.
- **File downloads:** Require either global project access OR project membership on the file's associated project. Cross-project file access is denied at the API layer before the storage layer is touched.

### 3.6 Race condition protection

- **User creation:** `IntegrityError` on duplicate username is caught and converted to a 409 `USERNAME_EXISTS` (closes BUG-6).
- **Status transitions:** `transition_user_status()` uses `UPDATE ... WHERE` with rowcount check -- no SELECT-then-UPDATE gap.
- **`FOR UPDATE`:** Available via `get_user_or_404(for_update=True)` for pessimistic locking when needed.

---

## 4. File Security Measures

### 4.1 Upload validation chain

Every file upload passes through this pipeline (in order):

```
1. Extension whitelist    → .dwg only (ALLOWED_UPLOAD_EXTENSIONS = {".dwg"})
2. MIME type check        → 8 accepted DWG-related MIME types (application/acad, application/dwg, etc.)
3. DWG header validation  → First 6 bytes must match AC1012-AC1032 (AutoCAD R13 through 2018+)
4. Size enforcement       → Max: max_upload_size_mb (512 MiB default), Min: 1024 bytes
5. Streaming hash         → SHA-256 + MD5 computed during chunked read
6. Temp buffer cleanup      → SpooledTemporaryFile automatically cleans the in-memory/os-buffer after use. However, if the storage backend write (`put_fileobj`) fails mid-write, a partial file may remain in the storage backend (e.g. MinIO or local filesystem) — the application does not attempt to unlink partially-written files from the backend.
```

### 4.2 Supported DWG versions

| Magic bytes | AutoCAD version |
|---|---|
| `AC1012` | R13 |
| `AC1014` | R14 |
| `AC1015` | 2000 / 2000i / 2002 |
| `AC1018` | 2004 / 2005 / 2006 |
| `AC1021` | 2007 / 2008 / 2009 |
| `AC1024` | 2010 / 2011 / 2012 |
| `AC1027` | 2013-2017 |
| `AC1032` | 2018+ |

Files with headers outside this set are rejected with 415 `FILE_NOT_DWG`.

### 4.3 Storage path security

- **Storage paths never use user-provided filenames.** The `storage_key` is `local/{uuid4().hex}{ext}`.
- **`original_name`** is stored as metadata only and never interpolated into file paths.
- **Path traversal guard:** `ensure_within_root(root, candidate)` resolves both paths and checks that the candidate's resolved path starts with the root's resolved path. Any escape attempt raises 400 `INVALID_STORAGE_PATH`.
- **Original files are never overwritten.** Each upload creates a new storage key.

### 4.4 Download security

- **HMAC-signed download URLs** (`GET /files/{file_id}/download-url`): URLs include `expires` (TTL=300s) and `signature` parameters. The signature is HMAC-SHA256 over `file_id:expires`.
- **Permission check before URL generation:** The caller must have access to the file's project (or global access). Cross-project download requests are rejected before any URL is generated.
- **Note:** The signed URL TTL is enforced by the backend at download time, but the URL itself is not a cryptographically self-contained capability token -- the download endpoint also requires authentication (see Section 5.3, remaining gaps).

### 4.5 File hashing

- **SHA-256:** Primary integrity hash, stored in `files.sha256`, indexed for deduplication queries.
- **MD5:** Secondary hash for legacy compatibility, stored in `files.md5`.
- Both are computed during the streaming upload (single pass over the file data).

---

## 5. Pentest Findings Resolution

### 5.1 Fixed (12 out of 18)

| ID | Finding | Severity | Fix | File |
|---|---|---|---|---|
| H1 | Timing oracle -- 40x time difference for user enumeration via login | **Critical** | Dummy Argon2id hash when user doesn't exist/is inactive. Both code paths perform one full argon2id verification. | `app/services/auth_service.py` |
| H6 | Username injection via spaces and Unicode characters | **High** | Pattern constraint `^[a-zA-Z0-9_.@-]+$` on `username` field in Pydantic schema. | `app/schemas/user_schema.py` |
| BUG-1 | Mass assignment of `role_codes` via `UserCreate` | **High** | `role_codes` field removed from `UserCreate` schema. Role assignment is now a separate `POST /users/{id}/roles` endpoint gated by RBAC. | `app/schemas/user_schema.py` |
| BUG-2 | Weak password policy -- no minimum length or complexity | **High** | `min_length=12`, upper+lower+digit required, common password blacklist. | `app/schemas/user_schema.py` |
| BUG-3 | HTML injection in `real_name` field | **Medium** | HTML tag pattern rejection in Pydantic validator. | `app/schemas/user_schema.py` |
| BUG-4 | Health endpoint infoleak -- database status, version | **Low** | Simplified to `{"data": {"status": "ok"}}`. | `app/main.py` |
| BUG-5 | DWG size validation too small -- accepted < 1024 bytes | **Medium** | `MIN_DWG_SIZE_BYTES = 1024` enforced after upload, combined with header validation. | `app/services/storage_service.py` |
| BUG-6 | Race condition causing 500 with traceback leak | **Medium** | `IntegrityError` caught and converted to 409. Catch-all `Exception` handler returns `"Internal server error."` in production. | `app/services/user_service.py`, `app/main.py` |
| BUG-7 | Soft-delete cascade -- deleted projects still visible in file listings | **Medium** | `require_active_project()` check added to `require_project_member()`. File listing filtered by project membership. | `app/api/deps.py` |
| BUG-8 | `task_type` field unvalidated -- accepted arbitrary strings | **Low** | Pattern constraint `^[a-z][a-z0-9_]+$`. | `app/schemas/job_schema.py` |
| BUG-9 | Retry without state guard -- any job could be retried | **Medium** | Only `failed` or `cancelled` jobs are retryable. | `app/services/job_service.py` |
| BUG-12 | No self-update endpoint for users | **Low** | `PATCH /users/me` added with `UserSelfUpdate` schema (no status changes allowed). | `app/api/v1/users_api.py` |

### 5.2 Not fixed by design (6 out of 18)

| ID | Finding | Rationale |
|---|---|---|
| BUG-10 | Nanosecond-level TOCTOU window | Risk is negligible in practice -- the window is too small to exploit reliably in a web application context. Not worth the complexity of application-level serializable transactions. |
| BUG-11 | Unclear root cause, cannot reproduce | Unable to reproduce after multiple attempts. No telemetry to diagnose. Filed for monitoring in production. |
| BUG-13 | Parameter not present in current API | The parameter referenced in the finding does not exist in any deployed API endpoint. The finding may have been against a stale/staging version. |
| BUG-14 | Parameter not present in current API | Same as BUG-13. |
| C1 | JWT secret key strength | Deployment concern, not a code issue. Production deployment must use a cryptographically random key (see checklist 6.1). |
| C2 | Port 8000 exposed | Infrastructure concern. Docker Compose places backend-api on the `internal` network only. Nginx is the public-facing service on ports 80/443. If deploying without Docker, follow the checklist (Section 6.5). |

### 5.3 Remaining gaps (acknowledged, not yet resolved)

| Gap | Impact | Mitigation |
|---|---|---|
| **Token blacklist middleware** | Access tokens blacklisted at logout are checked on every request (via `is_token_blacklisted(jti)` in `get_current_user`), which is correct. However, there is no periodic cleanup of the blacklist beyond Redis TTL expiry. | Acceptable -- Redis TTL auto-cleans keys. |
| **No login rate limiting** | Brute-force login attempts are not throttled. The timing oracle fix (H1) prevents user enumeration, but password guessing at scale remains possible. | **Production must add rate limiting** (e.g. slowapi or nginx `limit_req_zone`). Recommended: 5 attempts per IP per minute, escalating lockout. |
| **No refresh token rotation** | If a refresh token is stolen, the attacker can continue generating new access tokens for up to 14 days. | **Consider implementing rotation** -- issue a new refresh token on each use, invalidate the old one. This is the standard OAuth 2.0 best practice. |
| **Signed download URLs** | The HMAC-signed URL includes an `expires` parameter with TTL=300s, but the download endpoint also checks authentication. The URL is not a standalone capability token. | This is actually a defense-in-depth choice, but it means the signature does not provide the intended time-limited anonymous access. Evaluate whether truly expiring capability URLs are needed. |
| **No audit log retention policy** | Audit logs grow unbounded in the database. | Add a retention policy (e.g. archive logs older than N months). |

---

## 6. Production Deployment Security Checklist

### 6.1 Secrets management

- [ ] **`JWT_SECRET_KEY`**: Generate with `openssl rand -hex 32`. Must be at least 256 bits of entropy.
- [ ] **`SUPER_ADMIN_PASSWORD`**: Change from the seed default before any users are created.
- [ ] **`MYSQL_PASSWORD`**, **`MYSQL_ROOT_PASSWORD`**: Strong, unique passwords.
- [ ] **`REDIS_PASSWORD`**: Set in production (Redis AUTH).
- [ ] **`MINIO_ROOT_USER`**, **`MINIO_ROOT_PASSWORD`**: Strong, unique.
- [ ] **`.env` and `.env.docker`**: Never committed to Git. Verify `.gitignore` covers them.
- [ ] **`MODEL_API_KEY`**: LLM API key must be set if Agent features are enabled.

### 6.2 Network security

- [ ] **Database port (3306)**: Not exposed to the public network. Docker: on `internal` network only.
- [ ] **Redis port (6379)**: Not exposed. Docker: on `internal` network only.
- [ ] **MinIO ports (9000, 9001)**: Not exposed. Docker: on `internal` network only.
- [ ] **Backend port (8000)**: Not exposed directly. All traffic goes through Nginx.
- [ ] **Nginx**: Only ports 80 and 443 exposed. Redirect HTTP to HTTPS in production.
- [ ] **CAD Worker node**: Isolated network, API Key authentication required (Section 19.4 of spec).

### 6.3 TLS/HTTPS

- [ ] Obtain TLS certificate (Let's Encrypt or internal CA).
- [ ] Configure Nginx with `ssl_certificate` and `ssl_certificate_key`.
- [ ] Set `secure` flag on cookies (already in code for `refresh_token` cookie).
- [ ] Set HSTS header in Nginx.

### 6.4 Application hardening

- [ ] **`DEBUG=false`**: Must be set in production (prevents traceback leakage in the catch-all handler).
- [ ] **CORS origins**: Set `BACKEND_CORS_ORIGINS` to the production frontend domain(s) only -- not `*`.
- [ ] **Upload size limit**: Set `MAX_UPLOAD_SIZE_MB` appropriately (default 512 MiB).
- [ ] **Login rate limiting**: Deploy rate limiting middleware (e.g. slowapi) or configure Nginx `limit_req_zone` for `/api/v1/auth/sessions`.
- [ ] **Refresh token rotation**: Evaluate implementing per Section 5.3.

### 6.5 Database security

- [ ] MySQL user `dwg_user` has only the required privileges (SELECT, INSERT, UPDATE, DELETE on `dwg_agent.*`).
- [ ] MySQL root password is stored securely and not used by the application.
- [ ] Regular backups configured (see `docs/database.md`, Section 6).
- [ ] Connection uses `mysql+pymysql` with TLS if MySQL is on a separate host.

### 6.6 Docker security

- [ ] Backend container runs as non-root user (the production `Dockerfile` includes a non-root `USER` directive).
- [ ] Images are built with `--no-cache` for production deployments.
- [ ] Docker socket is not mounted into any container.
- [ ] Container resource limits are set (CPU, memory) to prevent resource exhaustion.

### 6.7 Logging and monitoring

- [ ] Audit logs are written for: user CRUD, role changes, login/logout, password changes, file uploads, job creation, review decisions, agent runs.
- [ ] Application logs include `request_id`, `user_id`, and resource IDs for traceability.
- [ ] Set up log aggregation (e.g. Docker logging driver → ELK/Loki).
- [ ] Configure alerts for: repeated 401/403 responses, high error rate, unusual file upload patterns.

---

## 7. Audit Log Coverage

### 7.1 Audit log schema

```text
audit_logs
├── id              BIGINT PK
├── actor_user_id   BIGINT FK → sys_users.id (nullable -- for system actions)
├── action          VARCHAR(128)    e.g. "user.create", "file.upload", "auth.logout"
├── resource_type   VARCHAR(64)     e.g. "user", "project", "file", "job", "result"
├── resource_id     BIGINT          ID of the affected resource
├── ip_address      VARCHAR(64)     Client IP from request
├── user_agent      VARCHAR(512)    User-Agent header
├── before_json     JSON            Resource state before the action (for updates/deletes)
├── after_json      JSON            Resource state after the action (for creates/updates)
├── created_at      DATETIME        Timestamp of the action
```

### 7.2 Actions that produce audit records

| Action code | Resource type | Trigger |
|---|---|---|
| `auth.login` | user | Successful user login |
| `auth.logout` | user | User logs out |
| `auth.password_change` | user | User changes their own password |
| `users.create` | user | Admin creates a new user |
| `users.update` | user | Admin modifies user details |
| `users.update_self` | user | User updates their own profile via /users/me |
| `users.delete` | user | Admin soft-deletes a user |
| `users.disable` | user | Admin disables a user account |
| `users.enable` | user | Admin re-enables a user account |
| `users.password_reset` | user | Admin resets a user's password |
| `users.roles.add` | user | Admin assigns a role to a user |
| `users.roles.remove` | user | Admin removes a role from a user |
| `roles.create` | role | Super admin creates a new role |
| `roles.permissions.replace` | role | Super admin updates a role's permissions |
| `projects.create` | project | User creates a project |
| `projects.update` | project | User modifies project details |
| `projects.delete` | project | User soft-deletes/archives a project |
| `project_members.create` | project | Owner adds a member to a project |
| `project_members.update` | project_member | Owner changes a member's project role |
| `project_members.delete` | project_member | Owner removes a member from a project |
| `files.upload` | file | User uploads a DWG file |
| `files.delete` | file | User deletes a file |
| `files.download_url` | file | User requests a signed download URL |
| `files.download` | file | User downloads a file via signed URL |
| `drawings.create` | drawing | User creates a drawing |
| `drawings.update` | drawing | User modifies drawing metadata |
| `drawings.delete` | drawing | User archives a drawing |
| `drawing_versions.create` | drawing | User uploads a new drawing version |
| `jobs.create` | job | User submits a processing job |
| `jobs.cancel` | job | User cancels a job |
| `jobs.retry` | job | User retries a failed/cancelled job |
| `agent_runs.create` | agent_run | User creates an agent run |
| `reviews.create` | result | Reviewer approves or rejects an analysis result |

### 7.3 Access control for audit logs

- **`GET /api/v1/audit-logs`**: Requires `super_admin` or `auditor` global role.
- **`GET /api/v1/audit-logs/{audit_log_id}`**: Same access control.
- Audit logs are **immutable** -- there is no API endpoint to modify or delete them. Deletion would require direct database access by a DBA.
- The `actor_user_id` can be `NULL` for system-initiated actions (e.g. seed data creation, automated cleanup).

### 7.4 Audit log query considerations

- The `action` and `resource_id` columns are indexed for efficient filtering.
- The `before_json` and `after_json` columns record full snapshots -- this is valuable for investigations but can grow large. Consider archiving strategy for production.
- For GDPR/privacy compliance, the `ip_address` column captures PII. Ensure your privacy policy and retention schedule account for this.
