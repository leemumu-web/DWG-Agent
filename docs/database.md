# DWG-Agent Platform -- Database Design & Operations

> **Audience:** DBAs, platform operators, backend developers
> **Last updated:** 2026-07-11
> **Scope:** Engine configuration, table catalog, entity relationships, migration management, seed data, backup strategy

---

## 1. Engine Configuration

### 1.1 Runtime: MySQL 8.x

The production/runtime database engine is MySQL 8.x, accessed via `mysql+pymysql://`.

**Configuration** (`backend/app/db/session.py`):

```python
pool_args = {
    "pool_recycle": settings.db_pool_recycle_seconds,
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_pool_max_overflow,
    "pool_timeout": settings.db_pool_timeout_seconds,
    "pool_use_lifo": True,
}
engine_kwargs = {"pool_pre_ping": True}
if settings.sqlalchemy_database_url.startswith("mysql"):
    engine_kwargs.update(pool_args)
engine = create_engine(settings.sqlalchemy_database_url, **engine_kwargs)
```

| Parameter | Value | Rationale |
|---|---|---|
| `DB_POOL_SIZE` | 2 | Per-process persistent pool capacity. Connections are opened lazily. |
| `DB_POOL_MAX_OVERFLOW` | 2 | Per-process burst capacity; default ceiling is 4 checked-out connections. |
| `DB_POOL_TIMEOUT_SECONDS` | 30s | Maximum wait for a pooled application connection. |
| `DB_POOL_RECYCLE_SECONDS` | 3600s | Recycles connections before common MySQL idle timeouts. |
| `pool_pre_ping` | True | Tests connection liveness before use. Adds one extra query per checkout but eliminates `MySQL server has gone away` errors. |

**Connections per deployment profile:**

| Profile | Processes | Default application-engine ceiling |
|---|---|---|
| Local API | 1 | 4 |
| Compose API (gunicorn `-w 4`) | 4 | 16 |
| Each Celery execution process | 1 | 4 |

These are ceilings, not eagerly opened minimums. Capacity planning must also count every Celery parent/child process, Kombu/result-backend connections, migrations, and operator sessions; tune the four `DB_POOL_*` settings instead of hard-coding a larger pool.

### 1.2 Test isolation: SQLite in-memory

Pytest uses SQLite with `StaticPool` for complete test isolation:

```python
# Per conftest.py
engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
```

**SQLite pragma set on each test connection:**

| Pragma | Value | Purpose |
|---|---|---|
| `foreign_keys` | ON | Enforce FK constraints (SQLite disables them by default) |

This pragma is applied via a `@event.listens_for(engine, "connect")` handler in the test conftest. It does NOT affect the runtime MySQL engine.

**Why only `foreign_keys`?** The test database uses in-memory SQLite behind `StaticPool`, which provides exactly one connection. In this configuration:
- `journal_mode=WAL` is meaningless -- WAL enables concurrent reads during writes, but a single connection has no concurrency.
- `busy_timeout` is meaningless -- with one connection there is never lock contention.

These two pragmas would only matter for file-backed SQLite with a connection pool. They are intentionally omitted.

### 1.3 MySQL vs SQLite type mapping

The migration's `_pk_type()` helper smooths over the type difference:

```python
def _pk_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")
```

- **MySQL:** Primary keys are `BIGINT` (8 bytes, signed).
- **SQLite:** `.with_variant(sa.Integer(), "sqlite")` tells SQLAlchemy to use `INTEGER` when speaking SQLite. This is necessary because SQLite's `INTEGER` affinity handles autoincrement differently from MySQL's `BIGINT AUTO_INCREMENT`.
- **DECIMAL columns:** The `confidence DECIMAL(5,4)` in `analysis_results` is handled correctly by both engines.

### 1.4 Database URL assembly

The `settings.mysql_url` property assembles the DSN from component fields (spec Section 18):

```python
mysql_url = f"mysql+pymysql://{user_part}@{host}:{port}/{database}"
```

This allows Docker Compose to override individual components (e.g. `MYSQL_HOST=mysql`) without reconstructing the full URL.

---

## 2. Complete Table Catalog

The runtime schema has **31 tables**: 22 business tables (17 initial tables, `token_blacklist`, `agent_memory`, and three Excel Final tables), `alembic_version`, and eight Celery-owned tables. The latter are `kombu_queue`, `kombu_message`, `celery_taskmeta`, `celery_tasksetmeta`, plus `message_id_sequence`, `queue_id_sequence`, `task_id_sequence`, and `taskset_id_sequence`. Alembic autogenerate excludes all eight runtime-owned tables.

### 2.1 Identity & Access Management (IAM) -- 6 tables

#### `sys_users`

Core user identity table. Supports soft-delete via `deleted_at` timestamp.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | Surrogate primary key |
| `username` | VARCHAR(64) | UNIQUE, NOT NULL, INDEXED | Login identifier (employee number or username) |
| `employee_no` | VARCHAR(64) | NULLABLE | Company employee number (metadata only) |
| `real_name` | VARCHAR(64) | NOT NULL | Display name |
| `email` | VARCHAR(128) | NULLABLE | Contact email |
| `password_hash` | VARCHAR(255) | NOT NULL | Argon2id hash of the password |
| `password_algo` | VARCHAR(32) | NOT NULL, DEFAULT 'argon2id' | Algorithm tag for future migration |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'active', INDEXED | `active` / `disabled` / `deleted` |
| `last_login_at` | DATETIME | NULLABLE | Timestamp of last successful login |
| `password_changed_at` | DATETIME | NULLABLE | Tokens issued at or before this instant are rejected |
| `deleted_at` | DATETIME | NULLABLE, INDEXED | Soft-delete timestamp (NULL = not deleted) |
| `created_at` | DATETIME | NOT NULL | Record creation timestamp |
| `updated_at` | DATETIME | NOT NULL | Record last modification timestamp |

**Indexes:** `ix_sys_users_username` (UNIQUE), `ix_sys_users_status`, `ix_sys_users_deleted_at`

**Important:** A user with `status = 'deleted'` is treated as if they do not exist by `get_user_or_404()`. Their `deleted_at` timestamp is set but the row is retained for referential integrity (audit logs, file ownership, job history).

#### `sys_roles`

Global role definitions. System roles are seeded and protected.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | Surrogate key |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL, INDEXED | Machine-readable role code (e.g. `super_admin`) |
| `name` | VARCHAR(64) | NOT NULL | Human-readable display name |
| `description` | VARCHAR(255) | NULLABLE | Role purpose description |
| `is_system` | BOOLEAN | NOT NULL, DEFAULT FALSE | If TRUE, role is seeded and should not be deleted |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Seeded roles:** `super_admin`, `admin`, `engineer`, `reviewer`, `operator`, `viewer`, `auditor`

#### `sys_permissions`

Atomic permission definitions as resource+action pairs.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | Surrogate key |
| `code` | VARCHAR(128) | UNIQUE, NOT NULL, INDEXED | Machine-readable code (e.g. `users:read`) |
| `resource` | VARCHAR(64) | NOT NULL | Resource namespace (`users`, `files`, `jobs`, etc.) |
| `action` | VARCHAR(64) | NOT NULL | Action type (`read`, `write`) |
| `name` | VARCHAR(128) | NOT NULL | Human-readable description |

**8 seeded permissions** (see security.md Section 2.6 for full list).

#### `sys_user_roles`

Many-to-many join between users and their global roles.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `user_id` | BIGINT | PK (composite), FK → `sys_users.id` | |
| `role_id` | BIGINT | PK (composite), FK → `sys_roles.id` | |
| `created_at` | DATETIME | NOT NULL, DEFAULT NOW() | Assignment timestamp |

**Primary key:** `(user_id, role_id)` -- prevents duplicate role assignments.

#### `sys_role_permissions`

Many-to-many join between roles and their permissions.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `role_id` | BIGINT | PK (composite), FK → `sys_roles.id` | |
| `permission_id` | BIGINT | PK (composite), FK → `sys_permissions.id` | |

**Primary key:** `(role_id, permission_id)` -- prevents duplicate permission grants.

#### `token_blacklist`

Durable JWT revocation records. Expired rows are removed during subsequent logout operations.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `jti` | VARCHAR(36) | PK | JWT identifier |
| `expires_at` | DATETIME | NOT NULL, INDEXED | Token expiry; rows past this time no longer revoke |

### 2.2 Project & Membership -- 2 tables

#### `projects`

Project container for organizing drawings, files, and jobs.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL, INDEXED | Short project code (e.g. `PRJ-2026-001`) |
| `name` | VARCHAR(128) | NOT NULL | Project display name |
| `description` | TEXT | NULLABLE | Project details |
| `owner_id` | BIGINT | FK → `sys_users.id` | Project owner (typically the creator) |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'active' | `active` / `archived` / `deleted` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_projects_code` (UNIQUE)

#### `project_members`

Project-level membership with role-based access within the project scope.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `project_id` | BIGINT | NOT NULL, FK → `projects.id` | |
| `user_id` | BIGINT | NOT NULL, FK → `sys_users.id` | |
| `project_role` | VARCHAR(64) | NOT NULL | `project_owner` / `project_engineer` / `project_reviewer` / `project_viewer` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Unique constraint:** `uq_project_member` on `(project_id, user_id)` -- a user can only have one role per project.

### 2.3 File Management -- 1 table

#### `files`

File metadata store. The actual file bytes live in storage (local filesystem or MinIO), not in the database.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `bucket` | VARCHAR(128) | NOT NULL | Storage bucket name (`dwg-original`, etc.) |
| `storage_key` | VARCHAR(512) | NOT NULL, INDEXED | Path within bucket (`uploads/{uuid}.dwg`) |
| `original_name` | VARCHAR(255) | NOT NULL | User-provided filename (display only) |
| `file_ext` | VARCHAR(32) | NOT NULL | Lowercase extension including dot (`.dwg`) |
| `content_type` | VARCHAR(128) | NULLABLE | MIME type from upload or detection |
| `size_bytes` | BIGINT | NOT NULL | File size in bytes |
| `sha256` | VARCHAR(64) | NOT NULL, INDEXED | SHA-256 hex digest (64 chars) |
| `md5` | VARCHAR(32) | NULLABLE | MD5 hex digest (32 chars) |
| `batch_name` | VARCHAR(128) | NULLABLE, INDEXED | Batch grouping label for multi-file DXF/Excel uploads (e.g. ZIP stem). Added by migration `53cd59adf848` |
| `uploaded_by` | BIGINT | FK → `sys_users.id` | Uploader user ID |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'available' | `available` / `deleted` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_files_sha256`, `ix_files_storage_key`, `ix_files_batch_name`

**Batch uploads:** `batch_name` groups files uploaded together (single-file upload with a `batch_name` query param, or a `.zip` extracted under its stem) so the DXF→Excel pipeline and batch download/delete endpoints can operate on a whole set at once. It is `NULL` for ungrouped uploads.

### 2.4 Drawing & Versioning -- 2 tables

#### `drawings`

Logical drawing record with version tracking.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `project_id` | BIGINT | NOT NULL, FK → `projects.id`, INDEXED | Owning project |
| `drawing_no` | VARCHAR(128) | NULLABLE | Drawing number (e.g. `A-001`) |
| `title` | VARCHAR(255) | NULLABLE | Drawing title |
| `discipline` | VARCHAR(64) | NULLABLE | Engineering discipline code |
| `current_version_id` | BIGINT | FK → `drawing_versions.id` | Points to the latest version |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'active' | `active` / `archived` / `deleted` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_drawings_project_id`

**Circular FK note:** `current_version_id` references `drawing_versions.id`, but `drawing_versions.drawing_id` references `drawings.id`. The migration handles this by creating `drawings` without the FK first, then `drawing_versions`, then adding the circular FK via `op.create_foreign_key()` after both tables exist. Down migrations reverse this order.

#### `drawing_versions`

Application-append-only version records. Each upload of a new DWG revision creates a new row; the API has no mutation route for an existing version. The database does not use a trigger or cryptographic seal to make rows physically immutable.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `drawing_id` | BIGINT | NOT NULL, FK → `drawings.id` | Parent drawing |
| `file_id` | BIGINT | NOT NULL, FK → `files.id` | The actual file for this version |
| `version_no` | INT | NOT NULL | Monotonically increasing version number |
| `source` | VARCHAR(64) | NULLABLE | Upload source tag |
| `created_by` | BIGINT | FK → `sys_users.id` | Who uploaded this version |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

### 2.5 Job Processing -- 2 tables

#### `jobs`

Asynchronous processing job for a DWG drawing.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `project_id` | BIGINT | NULLABLE, FK → `projects.id`, INDEXED | Project scope |
| `drawing_id` | BIGINT | NULLABLE, FK → `drawings.id`, INDEXED | Target drawing |
| `created_by` | BIGINT | NULLABLE, FK → `sys_users.id` | Job submitter |
| `task_type` | VARCHAR(64) | NOT NULL | Task code: `convert_dwg_to_dxf` / `convert_dxf_to_dwg` / `extract_dxf_to_excel` |
| `precision_level` | VARCHAR(32) | NOT NULL | `normal` / `high` (determines pipeline routing) |
| `pipeline` | VARCHAR(64) | NULLABLE | Assigned pipeline: `local_stub` / `dxf_open_source` / `dxf2dwg_open_source` / `dxf2excel` / `zwcad_worker` |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'queued', INDEXED | `pending` → `queued` → `running` → `succeeded`/`failed`/`cancelled` |
| `attempt` | INT | NOT NULL, DEFAULT 1 | Current execution generation; incremented atomically on accepted retry |
| `priority` | INT | NOT NULL, DEFAULT 0 | Higher = more urgent |
| `progress` | INT | NOT NULL, DEFAULT 0 | 0-100 percentage |
| `params_json` | JSON | NULLABLE | Task-specific parameters |
| `error_code` | VARCHAR(64) | NULLABLE | Machine-readable error code on failure |
| `error_message` | TEXT | NULLABLE | Human-readable error description |
| `progress_data` | JSON | NULLABLE | Latest durable SSE payload (message, step, result metadata) |
| `created_at` | DATETIME | NOT NULL | |
| `started_at` | DATETIME | NULLABLE | When worker picked up the job |
| `finished_at` | DATETIME | NULLABLE | When job reached terminal state |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_jobs_project_id`, `ix_jobs_drawing_id`, `ix_jobs_status`

**Job lifecycle states:** `pending` (created, not yet queued) → `queued` (in the MySQL-backed Celery queue) → `running` (worker executing) → `succeeded` / `failed` / `cancelled`. Intermediate states: `waiting_cad_worker`, `validating`, `need_review`.

#### `job_steps`

Granular execution steps within a job.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `job_id` | BIGINT | NOT NULL, FK → `jobs.id`, INDEXED | Parent job |
| `attempt` | INT | NOT NULL, DEFAULT 1 | Execution generation that produced this step |
| `step_name` | VARCHAR(128) | NOT NULL | Human-readable step label |
| `worker_name` | VARCHAR(128) | NULLABLE | Celery worker hostname that executed this step |
| `status` | VARCHAR(32) | NOT NULL | `pending` / `running` / `succeeded` / `failed` |
| `input_json` | JSON | NULLABLE | Step input parameters |
| `output_json` | JSON | NULLABLE | Step output data |
| `error_message` | TEXT | NULLABLE | Error details if step failed |
| `started_at` | DATETIME | NULLABLE | |
| `finished_at` | DATETIME | NULLABLE | |

**Indexes:** `ix_job_steps_job_id`, `ix_job_steps_job_id_attempt`

### 2.6 Agent Execution -- 3 tables

#### `agent_runs`

Records of LLM Agent execution sessions.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `session_id` | VARCHAR(128) | NOT NULL, INDEXED | Client-provided session identifier |
| `user_id` | BIGINT | NOT NULL, FK → `sys_users.id` | Who initiated the agent run |
| `project_id` | BIGINT | FK → `projects.id` | Project context |
| `drawing_id` | BIGINT | FK → `drawings.id` | Drawing context |
| `file_id` | BIGINT | FK → `files.id` | Input file context |
| `task` | TEXT | NOT NULL | Natural language task description |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'queued' | `queued` / `running` / `succeeded` / `failed` |
| `answer` | TEXT | NULLABLE | Final LLM response text |
| `output_file_id` | BIGINT | FK → `files.id` | Result file if the agent produced one |
| `history_count` | INT | NOT NULL, DEFAULT 0 | Number of conversation turns in this session |
| `created_at` | DATETIME | NOT NULL | |
| `started_at` | DATETIME | NULLABLE | |
| `finished_at` | DATETIME | NULLABLE | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_agent_runs_session_id`

#### `agent_run_steps`

Individual tool calls and reasoning steps within an agent run.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `agent_run_id` | BIGINT | NOT NULL, FK → `agent_runs.id`, INDEXED | |
| `step_type` | VARCHAR(64) | NOT NULL | `tool_call` / `thought` / `observation` / `answer` |
| `title` | VARCHAR(255) | NULLABLE | Human-readable step summary |
| `tool_name` | VARCHAR(128) | NULLABLE | Name of the MCP tool invoked |
| `arguments_json` | JSON | NULLABLE | Tool call arguments |
| `content` | TEXT | NULLABLE | Step output or reasoning content |
| `status` | VARCHAR(32) | NOT NULL | `success` / `error` / `skipped` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_agent_run_steps_agent_run_id`

#### `agent_memory`

One row per Agent session. `messages` stores the bounded JSON conversation history; `updated_at` is compared with `AGENT_MEMORY_TTL` on read and expired rows are deleted in the caller's transaction.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `session_id` | VARCHAR(128) | PK | Stable Agent session identifier |
| `messages` | JSON | NOT NULL | At most `AGENT_MAX_MESSAGES` recent messages |
| `created_at` | DATETIME | NOT NULL | Creation time |
| `updated_at` | DATETIME | NOT NULL | Last write, used for TTL enforcement |

### 2.7 Results & Review -- 2 tables

#### `analysis_results`

Output of a processing job -- the structured analysis data.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `job_id` | BIGINT | NOT NULL, FK → `jobs.id`, INDEXED | Source job |
| `drawing_id` | BIGINT | FK → `drawings.id`, INDEXED | Source drawing |
| `result_type` | VARCHAR(64) | NOT NULL | Type of result (e.g. `layer_list`, `entity_count`) |
| `result_json` | JSON | NULLABLE | Structured result data |
| `confidence` | DECIMAL(5,4) | NULLABLE | Algorithm confidence 0.0000-1.0000 |
| `result_file_id` | BIGINT | FK → `files.id` | Output file (Excel, PDF, etc.) |
| `algorithm_version` | VARCHAR(64) | NULLABLE | Version of the processing algorithm |
| `tool_version` | VARCHAR(64) | NULLABLE | Version of the processing tool |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'succeeded' | `succeeded` (initial) / `need_review` / `approved` / `rejected` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_analysis_results_job_id`, `ix_analysis_results_drawing_id`

#### `review_records`

Human review decisions on analysis results.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `result_id` | BIGINT | NOT NULL, FK → `analysis_results.id`, INDEXED | |
| `reviewer_id` | BIGINT | FK → `sys_users.id` | Who performed the review |
| `decision` | VARCHAR(32) | NOT NULL | `approved` / `rejected` / `needs_revision` |
| `comment` | TEXT | NULLABLE | Reviewer's notes |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_review_records_result_id`

### 2.8 Audit -- 1 table

#### `audit_logs`

Application audit record for security-relevant actions. The service appends rows and exposes no update/delete API, but the database does not enforce physical or cryptographic immutability.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `actor_user_id` | BIGINT | FK → `sys_users.id` | Who performed the action (NULL for system actions) |
| `action` | VARCHAR(128) | NOT NULL, INDEXED | Action code (e.g. `users.create`) |
| `resource_type` | VARCHAR(64) | NOT NULL | Resource namespace |
| `resource_id` | BIGINT | NULLABLE, INDEXED | Affected resource ID |
| `ip_address` | VARCHAR(64) | NULLABLE | Client IP |
| `user_agent` | VARCHAR(512) | NULLABLE | Client User-Agent |
| `before_json` | JSON | NULLABLE | Resource state before the action |
| `after_json` | JSON | NULLABLE | Resource state after the action |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_audit_logs_action`, `ix_audit_logs_resource_id`

**Note:** `resource_id` is a **polymorphic pointer**, not a real foreign key — it stores the affected resource's ID regardless of its type, so no FK constraint exists. Migration `c3d2e1f0a9b8` corrected its type from `Integer` to `BIGINT` for consistency with every other ID column.

### 2.9 Excel Final -- 3 tables

- `excel_final_batches` is the one-to-one structured import for a job. `job_id` is unique and cascades on job deletion; optional `file_id` points to the source file and becomes NULL if that file is deleted.
- `excel_final_components` stores component-level quantity and weight summaries. `batch_id` cascades on batch deletion.
- `excel_final_parts` stores normalized part rows, dimensions, material, quantities, theoretical/net/gross weights, and surface areas. It is indexed by `batch_id`, `material`, and `part_no`.

The tables contain parsed business data only. Source and generated workbook bytes remain in the storage layer and are referenced through `files`.

---

## 3. Entity Relationship Overview

### 3.1 Core relationship diagram

```
sys_users ──< sys_user_roles >── sys_roles ──< sys_role_permissions >── sys_permissions

sys_users ──< projects (owner_id)
sys_users ──< project_members ──> projects
sys_users ──< files (uploaded_by)
sys_users ──< drawing_versions (created_by)
sys_users ──< jobs (created_by)
sys_users ──< agent_runs (user_id)
sys_users ──< review_records (reviewer_id)
sys_users ──< audit_logs (actor_user_id)

projects ──< project_members
projects ──< drawings
projects ──< jobs
projects ──< agent_runs

drawings ──< drawing_versions ──> files
drawings ──< jobs
drawings ──< analysis_results
drawings ──< agent_runs

drawing_versions ──> drawings (current_version_id, circular FK)

files ──< drawing_versions
files ──< agent_runs (file_id, output_file_id)
files ──< analysis_results (result_file_id)

jobs ──< job_steps
jobs ──< analysis_results

analysis_results ──< review_records

agent_runs ──< agent_run_steps
```

### 3.2 Foreign key cascade behavior

Initial-schema FKs use the default `NO ACTION` (`RESTRICT` in MySQL). The Excel Final hardening migration intentionally uses `ON DELETE CASCADE` from batch children/job ownership and `ON DELETE SET NULL` for the optional source file. New migrations must document every non-default delete action.

This means:
- **You cannot delete a user who has uploaded files, created jobs, or owns projects** unless you first nullify or reassign those references.
- **You cannot delete a project that has drawings** unless you first archive or reassign the drawings.
- **You cannot delete a drawing that has versions** unless you first remove the versions.
- **Soft-delete** is the application-level strategy: rows are marked `status = 'deleted'` (and `deleted_at = NOW()` for users) rather than physically removed.

**Rationale:** In an enterprise CAD processing platform, referential integrity of audit trails and processing history is more important than ease of deletion. A deleted user's files are still historical artifacts; a deleted project's audit logs must remain traceable.

### 3.3 Circular FK: drawings <-> drawing_versions

`drawings.current_version_id` references `drawing_versions.id`, and `drawing_versions.drawing_id` references `drawings.id`. This is a circular dependency that requires careful DDL ordering:

**Create order (upgrade):**
1. Create `drawings` table **without** the `current_version_id` FK.
2. Create `drawing_versions` table with its FKs.
3. Add FK constraint `fk_drawings_current_version_id_drawing_versions` after both tables exist.

**Drop order (downgrade):**
1. Drop the circular FK constraint from `drawings` first.
2. Drop `drawing_versions`.
3. Drop `drawings`.

This is correctly handled in migration `40452ddd24e7`.

### 3.4 JSON columns

The following tables use MySQL's native JSON type (SQLite falls back to TEXT):

| Table | JSON column | Typical content |
|---|---|---|
| `jobs` | `params_json` | Task parameters (layer filters, precision options) |
| `job_steps` | `input_json`, `output_json` | Step I/O payloads |
| `analysis_results` | `result_json` | Structured analysis output (layer list, entity count, etc.) |
| `agent_run_steps` | `arguments_json` | MCP tool call arguments |
| `audit_logs` | `before_json`, `after_json` | Resource snapshots for audit trail |

**Querying JSON columns:** MySQL 8.x supports `JSON_EXTRACT()`, `->`, and `->>` operators. Use these rather than string matching for reliable queries.

---

## 4. Migration Management

### 4.1 Current migration versions

| Revision | Description | Date |
|---|---|---|
| `40452ddd24e7` | Initial -- creates all 17 business tables with explicit circular FK handling | 2026-07-03 |
| `b8f9e7d6c5a4` | TimestampMixin fix -- idempotent migration for old MySQL databases missing `created_at`/`updated_at` columns | 2026-07-03 |
| `c3d2e1f0a9b8` | Fix `audit_logs.resource_id` type -- `Integer` to `BigInteger` for consistency with all other ID columns | 2026-07-04 |
| `53cd59adf848` | Add `files.batch_name` VARCHAR(128) nullable + index `ix_files_batch_name` -- supports DXF/Excel batch uploads | 2026-07-06 |
| `1d1696c7e854` | Add `agent_memory`, `token_blacklist`, `jobs.progress_data`, and `sys_users.password_changed_at` | 2026-07-10 |
| `3480bd86ddc3` | Add the Excel Final batch/component/part tables | 2026-07-10 |
| `7f2a9c4e6b10` | Harden Excel Final foreign keys and uniqueness constraints | 2026-07-10 |
| `8c61f4d2a9e7` | Add `jobs.attempt` for retry generation isolation | 2026-07-11 |
| `a74c2e9f1d30` | Add `job_steps.attempt` and `(job_id, attempt)` index | 2026-07-11 |

The linear chain is `40452ddd24e7 → b8f9e7d6c5a4 → c3d2e1f0a9b8 → 53cd59adf848 → 1d1696c7e854 → 3480bd86ddc3 → 7f2a9c4e6b10 → 8c61f4d2a9e7 → a74c2e9f1d30`; **`a74c2e9f1d30` is the current head.**

### 4.2 How to create a new migration

```bash
# 1. Modify SQLAlchemy models in app/models/

# 2. Generate the migration script
cd backend
uv run alembic revision --autogenerate -m "change_description"

# 3. Review the generated script in migrations/versions/
#    - Verify all table/column changes are intentional
#    - Check FK ordering for circular dependencies
#    - Ensure downgrade() reverses upgrade() correctly

# 4. Test the migration against a temporary schema
bash scripts/db.sh migration-test

# 5. Apply to your dev database
uv run alembic upgrade head
```

### 4.3 How to run migrations

```bash
# Apply all pending migrations
cd backend && uv run alembic upgrade head

# Apply to a specific revision
uv run alembic upgrade 40452ddd24e7

# Roll back one migration
uv run alembic downgrade -1

# Roll back to a specific revision
uv run alembic downgrade 40452ddd24e7

# Show current revision
uv run alembic current

# Show migration history
uv run alembic history
```

### 4.4 CI verification

The `scripts/db.sh migration-test` command:
1. Creates a **temporary** MySQL schema (utf8mb4) and grants the app user access to it.
2. Runs `alembic upgrade head` against that empty schema via a scoped `DATABASE_URL`.
3. Verifies the resulting schema: asserts all **22 expected business tables** are present, checks the current Alembic head, attempt columns/index-related types, Excel Final foreign keys/uniqueness, and the timestamp columns backfilled on `project_members`, `drawing_versions`, `review_records`, and `agent_run_steps`.
4. Drops the temporary schema (also on error, via an `EXIT` trap).

This verifies that the full migration chain rebuilds the schema from scratch and that the `TimestampMixin` columns are consistent. (It does not exercise a downgrade path.)

### 4.5 Writing safe migrations

**Do:**
- Always write a `downgrade()` that reverses the `upgrade()`.
- Use `op.create_index()` and `op.drop_index()` with explicit index names.
- Use `_pk_type()` for primary/foreign key columns to ensure SQLite compatibility.
- Test both upgrade and downgrade against a non-production database first.
- Handle circular FKs by deferring FK creation (see `40452ddd24e7` as reference).

**Don't:**
- Write data migrations that assume a specific row count or ID value.
- Use raw SQL without testing against both MySQL and SQLite syntax.
- Drop columns without a downgrade path (unless the column can be recreated with a default).
- Run migrations against production without a verified backup.

---

## 5. Seed Data

### 5.1 What gets seeded

The `init_db()` function in `backend/app/db/init_db.py` is called automatically at application startup (in the `lifespan` handler). It is idempotent -- if data already exists, it skips insertion.

**7 roles:**

> **Note:** The `name` column stores Chinese display names in the seed data (e.g. "超级管理员" for `super_admin`). The table below shows English translations for readability.

| Code | Name | `is_system` |
|---|---|---|
| `super_admin` | Super Admin | True |
| `admin` | System Admin | True |
| `engineer` | Engineer | True |
| `reviewer` | Reviewer | True |
| `operator` | Operator | True |
| `viewer` | Viewer | True |
| `auditor` | Auditor | True |

**8 permissions:**

| Code | Resource | Action |
|---|---|---|
| `users:read` | users | read |
| `users:write` | users | write |
| `roles:write` | roles | write |
| `projects:write` | projects | write |
| `files:write` | files | write |
| `jobs:write` | jobs | write |
| `reviews:write` | reviews | write |
| `audit_logs:read` | audit_logs | read |

All 8 permissions are assigned to the `super_admin` role at seed time.

**1 super admin user:**

| Field | Value |
|---|---|
| `username` | From `SUPER_ADMIN_USERNAME` env (default: `admin`) |
| `password_hash` | Argon2id hash of `SUPER_ADMIN_PASSWORD` env |
| `real_name` | From `SUPER_ADMIN_REAL_NAME` env (default: "系统管理员") |
| `status` | `active` |
| `roles` | `[super_admin]` |

### 5.2 Changing the seed super admin

The seed user is created only if no user with that username exists. Changing `SUPER_ADMIN_PASSWORD` later does not rotate an existing account. While authenticated, use `PATCH /api/v1/auth/password`; an authorized administrator may use the password-reset request endpoint for permitted target roles.

Do not delete the seeded row to force re-creation: users can be referenced by projects, files, Jobs, versions, reviews, and audit records. Lost-super-admin recovery needs a controlled, logged database procedure with a backup and independent approval; the repository does not automate it.

### 5.3 Manual seed via script

```
bash scripts/db.sh init
```

This runs the complete initialization: create database if needed, run all migrations, seed roles/permissions/super-admin.

---

## 6. Data Protection Boundary

### 6.1 Required recovery set

| Component | Required content | Consistency risk |
|---|---|---|
| MySQL `dwg_agent` | business tables, `alembic_version`, Celery runtime tables | DB-only restore can reference missing objects or replay broker rows |
| Object storage | every configured original/derived/report/temp/DXF bucket or local root | storage-only restore creates orphan bytes |
| `hardware_handbook` | schema/data or independently managed source | Excel Final weight lookup can change or fail |
| Configuration/secrets | Git-tracked config plus encrypted live values | `.env.docker` must not be stored in Git |
| Evidence | revision, migration head, timestamps, checksums, restore result | an untested dump is not a backup guarantee |

### 6.2 Local MySQL helper

```bash
bash scripts/db.sh backup /secure/path/dwg_agent_$(date +%Y%m%d_%H%M%S).sql.gz
```

This helper targets the local MySQL endpoint configured in root `.env`. It does not capture MinIO, `hardware_handbook`, live secrets, or a coordinated consistency point.

### 6.3 Compose backup boundary

Compose does not publish MySQL/MinIO host ports and provides no scheduled backup service. Stop all explicit API/worker writers for a simple quiesced backup, dump MySQL through `docker compose exec -T mysql`, and capture every MinIO bucket through a tested client on the internal network. Service-name wildcards such as `worker-*` do not work with Compose.

Exact Compose commands and stop/start ordering are maintained in [Operations](operations.md). Database and objects must be treated as one recovery set.

### 6.4 Restore requirements

1. Restore into an isolated or maintenance environment with writers stopped.
2. Restore MySQL and all object buckets from the same declared recovery point.
3. Verify `alembic current`, all required tables, and handbook queries.
4. Compare representative restored bytes with `files.sha256`.
5. Inspect queued/running Jobs and Celery broker rows before starting workers; restored messages may redeliver work.
6. Start the stack and run an authenticated upload/process/SSE/download workflow.

The repository currently has no automated retention, point-in-time recovery configuration, backup encryption, scheduled restore test, or RPO/RTO measurement. These are production gaps, not implicit platform features.
