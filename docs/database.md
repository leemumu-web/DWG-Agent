# DWG-Agent Platform -- Database Design & Operations

> **Audience:** DBAs, platform operators, backend developers
> **Last updated:** 2026-07-03
> **Scope:** Engine configuration, table catalog, entity relationships, migration management, seed data, backup strategy

---

## 1. Engine Configuration

### 1.1 Runtime: MySQL 8.x

The production/runtime database engine is MySQL 8.x, accessed via `mysql+pymysql://`.

**Configuration** (`backend/app/db/session.py`):

```python
engine = create_engine(settings.database_url, pool_pre_ping=True,
                        pool_recycle=3600, pool_size=10, max_overflow=20)
# pool_args are only applied when DATABASE_URL starts with "mysql"
```

| Parameter | Value | Rationale |
|---|---|---|
| `pool_size` | 10 | Base number of persistent connections. Suitable for 4 gunicorn workers with headroom. |
| `max_overflow` | 20 | Peak connections = pool_size + max_overflow = 30. Provides burst capacity without overwhelming MySQL's `max_connections`. |
| `pool_recycle` | 3600s (1 hour) | Recycles connections before MySQL's default `wait_timeout` (28800s). Prevents stale connections from causing errors after long idle periods. |
| `pool_pre_ping` | True | Tests connection liveness before use. Adds one extra query per checkout but eliminates `MySQL server has gone away` errors. |

**Connections per deployment profile:**

| Profile | Workers | Min connections | Max connections |
|---|---|---|---|
| Local dev (uvicorn --reload) | 1 | 10 | 30 |
| Docker (gunicorn -w 4) | 4 | 40 | 120 |
| Docker (gunicorn -w 8) | 8 | 80 | 240 |

Ensure MySQL `max_connections` is at least 150 for 4-worker deployment, accounting for Celery workers, Alembic migrations, and admin connections.

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

### 2.1 Identity & Access Management (IAM) -- 5 tables

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
| `storage_key` | VARCHAR(512) | NOT NULL, INDEXED | Path within bucket (`local/{uuid}.dwg`) |
| `original_name` | VARCHAR(255) | NOT NULL | User-provided filename (display only) |
| `file_ext` | VARCHAR(32) | NOT NULL | Lowercase extension including dot (`.dwg`) |
| `content_type` | VARCHAR(128) | NULLABLE | MIME type from upload or detection |
| `size_bytes` | BIGINT | NOT NULL | File size in bytes |
| `sha256` | VARCHAR(64) | NOT NULL, INDEXED | SHA-256 hex digest (64 chars) |
| `md5` | VARCHAR(32) | NULLABLE | MD5 hex digest (32 chars) |
| `uploaded_by` | BIGINT | FK → `sys_users.id` | Uploader user ID |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'available' | `available` / `deleted` / `processing` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_files_sha256`, `ix_files_storage_key`

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

Immutable version records. Each upload of a new DWG revision for a drawing creates a new version row.

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
| `task_type` | VARCHAR(64) | NOT NULL | Task code (e.g. `extract_layers`, `count_blocks`) |
| `precision_level` | VARCHAR(32) | NOT NULL | `normal` / `high` (determines pipeline routing) |
| `pipeline` | VARCHAR(64) | NULLABLE | Assigned pipeline: `dxf_open_source` / `zwcad_worker` / `local_stub` |
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'queued', INDEXED | `pending` → `queued` → `running` → `succeeded`/`failed`/`cancelled` |
| `priority` | INT | NOT NULL, DEFAULT 0 | Higher = more urgent |
| `progress` | INT | NOT NULL, DEFAULT 0 | 0-100 percentage |
| `params_json` | JSON | NULLABLE | Task-specific parameters |
| `error_code` | VARCHAR(64) | NULLABLE | Machine-readable error code on failure |
| `error_message` | TEXT | NULLABLE | Human-readable error description |
| `created_at` | DATETIME | NOT NULL | |
| `started_at` | DATETIME | NULLABLE | When worker picked up the job |
| `finished_at` | DATETIME | NULLABLE | When job reached terminal state |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_jobs_project_id`, `ix_jobs_drawing_id`, `ix_jobs_status`

**Job lifecycle states:** `pending` (created, not yet queued) → `queued` (in Redis/Celery queue) → `running` (worker executing) → `succeeded` / `failed` / `cancelled`. Intermediate states: `waiting_cad_worker`, `validating`, `need_review`.

#### `job_steps`

Granular execution steps within a job.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `job_id` | BIGINT | NOT NULL, FK → `jobs.id`, INDEXED | Parent job |
| `step_name` | VARCHAR(128) | NOT NULL | Human-readable step label |
| `worker_name` | VARCHAR(128) | NULLABLE | Celery worker hostname that executed this step |
| `status` | VARCHAR(32) | NOT NULL | `pending` / `running` / `succeeded` / `failed` |
| `input_json` | JSON | NULLABLE | Step input parameters |
| `output_json` | JSON | NULLABLE | Step output data |
| `error_message` | TEXT | NULLABLE | Error details if step failed |
| `started_at` | DATETIME | NULLABLE | |
| `finished_at` | DATETIME | NULLABLE | |

**Indexes:** `ix_job_steps_job_id`

### 2.6 Agent Execution -- 2 tables

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
| `status` | VARCHAR(32) | NOT NULL, DEFAULT 'succeeded' | `succeeded` (initial) / `pending_review` / `approved` / `rejected` |
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

Immutable audit trail for all significant actions.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | |
| `actor_user_id` | BIGINT | FK → `sys_users.id` | Who performed the action (NULL for system actions) |
| `action` | VARCHAR(128) | NOT NULL, INDEXED | Action code (e.g. `user.create`) |
| `resource_type` | VARCHAR(64) | NOT NULL | Resource namespace |
| `resource_id` | BIGINT | NULLABLE, INDEXED | Affected resource ID |
| `ip_address` | VARCHAR(64) | NULLABLE | Client IP |
| `user_agent` | VARCHAR(512) | NULLABLE | Client User-Agent |
| `before_json` | JSON | NULLABLE | Resource state before the action |
| `after_json` | JSON | NULLABLE | Resource state after the action |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

**Indexes:** `ix_audit_logs_action`, `ix_audit_logs_resource_id`

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

**All FKs use the default `NO ACTION` (RESTRICT in MySQL).** There are no `ON DELETE CASCADE` or `ON UPDATE CASCADE` clauses defined in the migration.

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

### 4.2 How to create a new migration

```bash
# 1. Modify SQLAlchemy models in app/models/

# 2. Generate the migration script
cd backend
uv run alembic revision --autogenerate -m "description_of_change"

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
1. Creates a temporary MySQL database (or uses SQLite for CI without MySQL).
2. Runs `alembic upgrade head`.
3. Runs `alembic downgrade base` (rolls back all migrations).
4. Runs `alembic upgrade head` again (verifies idempotency).
5. Drops the temporary database.

This verifies that both upgrade and downgrade paths are valid and that the schema can be rebuilt from scratch.

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

The seed user is created only if no user with that username exists. To re-seed after changing the password:

```bash
# 1. Delete the existing super admin (MySQL)
mysql -u dwg_user -p dwg_agent -e "DELETE FROM sys_user_roles WHERE user_id IN (SELECT id FROM sys_users WHERE username='admin');"
mysql -u dwg_user -p dwg_agent -e "DELETE FROM sys_users WHERE username='admin';"

# 2. Update .env with new SUPER_ADMIN_PASSWORD

# 3. Restart the application (init_db runs on startup)
```

### 5.3 Manual seed via script

```
bash scripts/db.sh init
```

This runs the complete initialization: create database if needed, run all migrations, seed roles/permissions/super-admin.

---

## 6. Backup Strategy Recommendations

### 6.1 What to back up

| Component | Priority | Method |
|---|---|---|
| MySQL database (`dwg_agent`) | **Critical** | `mysqldump` --single-transaction |
| File storage (MinIO / local `var/storage/`) | **Critical** | `mc mirror` (MinIO) or `rsync` (local) |
| Redis data | Low | AOF/RDB persistence, not typically backed up separately |
| Configuration (`.env.docker`, `compose.yaml`) | High | Git + encrypted backup |
| Nginx config (`infra/nginx/`) | Medium | Git |

### 6.2 MySQL backup command (recommended)

```bash
# Full logical backup (consistent snapshot via --single-transaction)
mysqldump -h 127.0.0.1 -u dwg_user -p \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --set-gtid-purged=OFF \
  dwg_agent | gzip > dwg_agent_$(date +%Y%m%d_%H%M%S).sql.gz
```

### 6.3 Recommended backup schedule

| Frequency | Type | Retention |
|---|---|---|
| Daily | Full `mysqldump` | 7 days (rolling) |
| Weekly | Full `mysqldump` | 4 weeks (rolling) |
| Monthly | Full `mysqldump` | 12 months |
| Before migrations | Manual full backup | Until migration verified |

### 6.4 MinIO / file storage backup

```bash
# MinIO mirror to a backup location
mc mirror minio/dwg-original backup/dwg-original --watch
mc mirror minio/dwg-derived backup/dwg-derived --watch

# Local storage rsync
rsync -avz --delete var/storage/ backup@backup-server:/backups/dwg-agent/storage/
```

### 6.5 Restore procedure

```bash
# 1. Stop the application (to prevent writes during restore)
docker compose stop backend-api worker-*

# 2. Restore MySQL
gunzip < dwg_agent_20260703_120000.sql.gz | mysql -h 127.0.0.1 -u dwg_user -p dwg_agent

# 3. Restore files (MinIO example)
mc mirror backup/dwg-original/ minio/dwg-original/ --overwrite

# 4. Verify data integrity
mysql -u dwg_user -p dwg_agent -e "SELECT COUNT(*) FROM audit_logs; SELECT COUNT(*) FROM files;"

# 5. Restart the application
docker compose up -d
```

### 6.6 Point-in-time recovery (advanced)

For production deployments requiring PITR:
- Enable MySQL binary logging (`log_bin = ON` in `my.cnf`).
- Back up binlogs alongside daily dumps.
- Recovery: restore the latest full dump, then replay binlogs to the desired point.

### 6.7 Backup verification

- **Automated:** Run a weekly restore test to a staging database, verify table row counts, and check FK integrity.
- **Manual:** Spot-check file downloads after storage restore -- verify SHA-256 matches between the restored file and the `files.sha256` database record.
