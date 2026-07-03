# DWG-Agent Platform -- Deployment & Operations Guide

> **Audience:** Operations engineers deploying, running, and troubleshooting this system.
> **Spec authority:** `DWG-Agent企业平台技术规范.md` (repo root) -- every design decision flows from this document.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick Start (5 Minutes)](#2-quick-start-5-minutes)
3. [Local Development Setup](#3-local-development-setup)
4. [Docker Compose Deployment](#4-docker-compose-deployment)
5. [Configuration Reference](#5-configuration-reference)
6. [Scripts Reference](#6-scripts-reference)
7. [Health Checks and Monitoring](#7-health-checks-and-monitoring)
8. [Troubleshooting](#8-troubleshooting)
9. [Backup and Restore](#9-backup-and-restore)
10. [Stage 1 Limitations](#10-stage-1-limitations)

---

## 1. Prerequisites

### Local Development

| Dependency | Minimum Version | Purpose |
|---|---|---|
| Python | 3.12 (exact -- `<3.13`) | Backend runtime |
| uv | Latest (via `ghcr.io/astral-sh/uv`) | Python package manager |
| MySQL or MariaDB | 8.x | Runtime database |
| Redis or Valkey | 7.x / 9.x | Session cache, message broker (Celery) |
| Node.js | 18+ (LTS) | Frontend build |
| npm | 9+ | Frontend package manager |

### Docker Deployment

| Dependency | Minimum Version | Purpose |
|---|---|---|
| Docker Engine | 24+ | Container runtime |
| Docker Compose | v2 (plugin or standalone) | Orchestration |
| Node.js | 18+ (LTS) | Frontend build (must run `npm run build` before `docker compose up`) |

### Arch Linux Quick Install

```bash
# Python 3.12
sudo pacman -S python python-pip
# Then install uv: curl -LsSf https://astral.sh/uv/install.sh | sh

# MySQL
sudo pacman -S mysql
sudo systemctl enable --now mysqld

# Redis (Valkey)
sudo pacman -S redis
sudo systemctl enable --now redis

# Node.js + npm
sudo pacman -S nodejs npm
```

---

## 2. Quick Start (5 Minutes)

This gets the platform running in local development mode from a fresh clone.
All commands run from the **repository root**.

```bash
# 0. Enter the repo
cd /path/to/complete_framework

# 1. Configure environment
cp .env.example .env
# Edit .env: set MYSQL_PASSWORD, MYSQL_ROOT_PASSWORD
# All other defaults (127.0.0.1:3306, no Redis password) work out of the box

# 2. Install backend dependencies
cd backend
uv python install 3.12   # first time only
uv sync --locked
cd ..

# 3. Initialize MySQL
bash scripts/db.sh start        # ensure MySQL is running
bash scripts/db.sh setup-user   # create dwg_user + grant privileges
bash scripts/db.sh init         # create schema + alembic upgrade + seed super_admin

# 4. Start the platform
bash scripts/start-dev.sh       # backend (uvicorn --reload) + frontend (Vite HMR)

# 5. Verify
curl http://127.0.0.1:8000/health
# => {"data": {"status": "ok"}, "meta": {...}}
```

**Access:**
- Frontend: `http://127.0.0.1:5173` (Vite dev server with HMR)
- API docs: `http://127.0.0.1:8000/docs`
- Default login: `admin` / `SuperAdminPass1` (**change immediately in production**)

**Stop:**
```bash
bash scripts/stop-all.sh
```

---

## 3. Local Development Setup

### 3.1 Architecture Overview (Local)

```
┌──────────┐     ┌──────────────┐     ┌─────────┐
│  Browser  │────▶│  Vite :5173  │────▶│ Backend  │
│           │     │  (HMR proxy) │     │ :8000   │
└──────────┘     └──────────────┘     └────┬────┘
                                           │
                          ┌─────────────────┼─────────────────┐
                          ▼                 ▼                  ▼
                     ┌─────────┐     ┌──────────┐     ┌──────────┐
                     │ MySQL   │     │ Redis    │     │ Local FS │
                     │ :3306   │     │ :6379    │     │ ./var/   │
                     └─────────┘     └──────────┘     └──────────┘
```

Optionally, Nginx can unify frontend and backend behind `http://localhost:8080` (see section 3.5).

### 3.2 Environment Files

Two `.env` files must be kept in sync:

| File | Purpose |
|---|---|
| `.env` (repo root) | Master config; used by `scripts/db.sh` and entry-point scripts |
| `backend/.env` | Backend runtime config; read by `pydantic-settings` at startup |

Both must contain the same values for `DATABASE_URL`, `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`. The `db.sh check` command validates this automatically.

### 3.3 Backend

```bash
cd backend
uv sync --locked
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Key facts:
- Uses `--reload` for hot-reload during development.
- Binds to `127.0.0.1` only (not `0.0.0.0`).
- Health endpoint: `GET /health` returns `{"data": {"status": "ok"}, "meta": {...}}`.
- The old `/api/v1/health` endpoint has been removed -- only `/health` exists.

### 3.4 Frontend

```bash
cd frontend
npm ci              # clean install from lockfile
npm run dev         # Vite dev server with HMR on :5173
```

The Vite dev server proxies `/api/*` and `/health` to `http://127.0.0.1:8000`. The proxy is defined in `frontend/vite.config.ts` -- `VITE_API_BASE_URL` should be empty in `.env` for dev mode.

### 3.5 Nginx (Optional Local Gateway)

Nginx can be started locally to serve the built frontend and proxy API calls from a single port:

```bash
# Prerequisites: backend running on :8000, frontend built (frontend/dist/ exists)
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf
```

This provides:

| Path | Behaviour |
|---|---|
| `http://localhost:8080` | React SPA with BrowserRouter fallback |
| `http://localhost:8080/api/v1/*` | Reverse proxy to FastAPI `:8000` |
| `http://localhost:8080/health` | Health check proxy |

Nginx management:
```bash
# Reload config without downtime
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s reload

# Graceful shutdown
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit

# Syntax check
sudo nginx -t -c $(pwd)/infra/nginx/nginx.local.conf
```

### 3.6 Database Management

All MySQL operations go through `scripts/db.sh`:

```bash
bash scripts/db.sh start          # ensure MySQL running, validate credentials
bash scripts/db.sh setup-user     # first-time: create dwg_user + grants
bash scripts/db.sh init           # create schema + alembic + seed super_admin
bash scripts/db.sh migrate        # alembic upgrade head (fix schema drift)
bash scripts/db.sh status         # config, credentials, schema, connection status
bash scripts/db.sh check          # non-destructive CI/verification check
bash scripts/db.sh shell          # open MySQL shell with app credentials
bash scripts/db.sh logs           # tail MySQL/MariaDB systemd journal
bash scripts/db.sh migration-test # create temp schema, run full migration, verify, cleanup
```

`db.sh` enforces MySQL-only URLs at the shell level -- `sqlite://` URLs are rejected for runtime operations. SQLite is reserved exclusively for pytest isolation.

### 3.7 Redis (Valkey 9.1)

```bash
# Check status
redis-cli ping   # => PONG

# Service management
sudo systemctl status redis
sudo systemctl restart redis
```

Key facts:
- Local dev: no password, listens on `127.0.0.1:6379`.
- Backend connects lazily -- Redis being down does **not** crash the API server.
- Memory service: keys at `agent:memory:{session_id}`, TTL 7200s, max 20 messages.
- Cache service: keys at `cache:{namespace}:{key}`, all methods safe when Redis is down.

### 3.8 Testing

```bash
cd backend
uv run ruff check app tests    # lint (must pass)
uv run pytest -q               # 307 tests (must pass)
```

Tests use SQLite in-memory databases (`StaticPool`) and FakeRedis -- no external services required. Real Redis integration tests in `test_redis_real.py` auto-skip when Redis is unavailable.

---

## 4. Docker Compose Deployment

### 4.1 Architecture Overview (Docker)

```
                    ┌──────────────────────────────────────┐
                    │          public network              │
                    │                                      │
                    │  ┌──────────┐                        │
                    │  │  Nginx   │ :80 :443               │
                    │  │  1.27    │                        │
                    │  └────┬─────┘                        │
                    │       │                               │
                    └───────┼───────────────────────────────┘
                            │
            ┌───────────────┼─────────────── internal network ────┐
            │               │                                     │
            │     ┌─────────▼──────────┐                          │
            │     │   backend-api      │                          │
            │     │   gunicorn :8000   │                          │
            │     └──┬──────┬──────┬───┘                          │
            │        │      │      │                              │
            │   ┌────▼──┐ ┌─▼──┐ ┌─▼────┐                         │
            │   │ mysql │ │redis│ │minio │                         │
            │   │ 8.4   │ │7.4  │ │latest│                         │
            │   └───────┘ └─────┘ └──────┘                         │
            │                                                      │
            │   ┌──────────┐ ┌──────────┐ ┌───────────┐           │
            │   │ worker-  │ │ worker-  │ │ worker-   │           │
            │   │ agent    │ │ dxf      │ │ report    │           │
            │   │ (profile)│ │ (profile)│ │ default   │           │
            │   └──────────┘ └──────────┘ └───────────┘           │
            │                                                      │
            │   ┌──────────┐                                       │
            │   │ flower   │  (monitoring profile)                 │
            │   │ :5555    │                                       │
            │   └──────────┘                                       │
            └──────────────────────────────────────────────────────┘
```

### 4.2 Service Summary

| Service | Image | Ports | Profile | Health Check |
|---|---|---|---|---|
| `nginx` | `nginx:1.27-alpine` | 80, 443 | -- | depends_on backend-api healthy |
| `backend-api` | Self-built | 8000 (internal) | -- | `curl /health` every 10s |
| `mysql` | `mysql:8.4` | 3306 (internal) | -- | `mysqladmin ping` every 10s |
| `redis` | `redis:7.4-alpine` | 6379 (internal) | -- | `redis-cli ping` every 10s |
| `minio` | `minio/minio:latest` | 9000, 9001 (internal) | -- | `curl /minio/health/live` |
| `worker-agent` | Self-built | -- | `workers` | -- |
| `worker-dxf` | Self-built | -- | `workers` | -- |
| `worker-report` | Self-built | -- | -- | depends_on mysql/redis/minio |
| `flower` | Self-built | 5555 (internal) | `monitoring` | -- |

### 4.3 Step-by-Step Deployment

**Step 1: Create Docker environment file**

```bash
cp .env.docker.example .env.docker
```

Edit `.env.docker` and replace ALL `CHANGE_ME_*` placeholders:

| Placeholder | Description |
|---|---|
| `CHANGE_ME_MYSQL_PASSWORD` | MySQL application user password |
| `CHANGE_ME_MYSQL_ROOT_PASSWORD` | MySQL root password |
| `CHANGE_ME_REDIS_PASSWORD` | Redis requirepass password |
| `CHANGE_ME_MINIO_ROOT_USER` | MinIO admin username |
| `CHANGE_ME_MINIO_ROOT_PASSWORD` | MinIO admin password |
| `CHANGE_ME_256_BIT_JWT_SECRET` | JWT signing secret (at least 32 chars) |
| `CHANGE_ME_SUPER_ADMIN_PASSWORD` | Bootstrap super admin password |

**Step 2: Build frontend**

```bash
cd frontend
npm ci
npm run build
cd ..
```

**Step 3: Start core services**

```bash
docker compose up -d
```

This starts: nginx, backend-api, mysql, redis, minio, and `worker-report` for the Stage 1 Celery fake job.

**Step 4: Verify deployment**

```bash
# Check all containers
docker compose ps

# View logs
docker compose logs -f nginx backend-api

# Health check via nginx
curl http://localhost/health
# => {"data": {"status": "ok"}, "meta": {...}}
```

**Access:** `http://localhost`

Login: `admin` / your configured `SUPER_ADMIN_PASSWORD`

**Step 5 (Optional): Start Agent/DXF workers and monitoring**

```bash
# Agent/DXF placeholder workers
docker compose --profile workers up -d

# Agent/DXF workers + Flower monitoring dashboard
docker compose --profile workers --profile monitoring up -d
```

Flower dashboard: `http://localhost:5555` (internal network, configure port mapping if external access needed).

### 4.4 Dockerfile Details

Located at `backend/Dockerfile` (multi-stage build):

**Stage 1 (builder):**
- Base: `python:3.12-slim`
- Installs `uv` from `ghcr.io/astral-sh/uv:latest`
- Runs `uv sync --frozen --no-dev` to create venv

**Stage 2 (runtime):**
- Base: `python:3.12-slim`
- Creates non-root user `appuser` (uid 1000)
- Installs `curl` + `ca-certificates` for healthcheck
- Copies `.venv` from builder, then `app/`, `alembic.ini`, `migrations/`
- Creates writable `/app/var/` owned by `appuser`
- HEALTHCHECK: `curl -f http://localhost:8000/health` every 15s, 5 retries
- CMD: `alembic upgrade head && exec gunicorn app.main:app --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --access-logfile - --error-logfile -`

### 4.5 Volumes

| Volume | Mount Point | Purpose | Persistence |
|---|---|---|---|
| `mysql_data` | `/var/lib/mysql` | MySQL data files | Survives `docker compose down` |
| `redis_data` | `/data` | Redis AOF + data | Survives `docker compose down` |
| `minio_data` | `/data` | Object storage data | Survives `docker compose down` |

To fully reset: `docker compose down -v`

### 4.6 Networks

| Network | Type | Purpose |
|---|---|---|
| `public` | External-facing | Nginx ingress (ports 80/443 exposed) |
| `internal` | `internal: true` | All backend services (no external access) |

### 4.7 Stopping

```bash
# Stop all services (preserves volumes)
docker compose --profile workers --profile monitoring down

# Stop and remove volumes (full reset)
docker compose --profile workers --profile monitoring down -v
```

---

## 5. Configuration Reference

All configuration is driven by environment variables. The canonical definitions live in `backend/app/core/config.py` (pydantic-settings, 44 fields + 5 computed properties).

### 5.1 Application

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `DWG-Agent Platform` | Display name |
| `APP_ENV` | `development` | Environment: `development` / `production` |
| `DEBUG` | `true` | Debug mode (disable in production) |
| `API_V1_PREFIX` | `/api/v1` | API URL prefix |
| `BACKEND_CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated CORS origins |

**Computed: `settings.cors_origins`** -- splits `BACKEND_CORS_ORIGINS` into a list.

### 5.2 Database (MySQL)

| Variable | Local Default | Docker Default | Description |
|---|---|---|---|
| `DATABASE_URL` | `mysql+pymysql://dwg_user:...@127.0.0.1:3306/dwg_agent` | `...@mysql:3306/dwg_agent` | Full connection URL |
| `MYSQL_HOST` | `127.0.0.1` | `mysql` | Hostname |
| `MYSQL_PORT` | `3306` | `3306` | Port |
| `MYSQL_DATABASE` | `dwg_agent` | `dwg_agent` | Database name |
| `MYSQL_USER` | `dwg_user` | `dwg_user` | Application user |
| `MYSQL_PASSWORD` | (required) | (required) | Application user password |
| `MYSQL_ROOT_PASSWORD` | (required) | (required) | MySQL root password |

**Computed: `settings.mysql_url`** -- assembled from component fields with URL-encoded password.

Connection pool settings (hardcoded in `app/db/session.py`, MySQL only):
- `pool_recycle=3600`
- `pool_size=10`
- `max_overflow=20`

### 5.3 Redis (Valkey-compatible)

| Variable | Local Default | Docker Default | Description |
|---|---|---|---|
| `REDIS_HOST` | `localhost` | `redis` | Hostname |
| `REDIS_PORT` | `6379` | `6379` | Port |
| `REDIS_DB` | `0` | `0` | Database number |
| `REDIS_PASSWORD` | (empty) | (required) | Auth password |
| `REDIS_MEMORY_TTL` | `7200` | `7200` | Agent memory TTL (seconds) |
| `REDIS_MAX_MESSAGES` | `20` | `20` | Max messages per session |

**Computed: `settings.redis_url`** -- assembled from component fields.
**Computed: `settings.celery_broker_url`** -- `redis://.../{host}:{port}/0`
**Computed: `settings.celery_result_backend`** -- `redis://.../{host}:{port}/1`

The `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` env vars in `.env` are also set for Celery's direct `os.environ` reads.

### 5.4 Storage

| Variable | Default | Description |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` or `minio` |
| `LOCAL_STORAGE_ROOT` | `./var/storage` | Local filesystem path (relative to CWD) |
| `MAX_UPLOAD_SIZE_MB` | `512` | Maximum file upload size |

### 5.5 MinIO (Object Storage)

| Variable | Default | Description |
|---|---|---|
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO server endpoint |
| `MINIO_ACCESS_KEY` / `MINIO_ROOT_USER` | (required) | MinIO access key |
| `MINIO_SECRET_KEY` / `MINIO_ROOT_PASSWORD` | (required) | MinIO secret key |
| `MINIO_BUCKET_ORIGINAL` | `dwg-original` | Uploaded DWG files |
| `MINIO_BUCKET_DERIVED` | `dwg-derived` | Processed derivatives |
| `MINIO_BUCKET_REPORTS` | `dwg-reports` | Generated reports |
| `MINIO_BUCKET_TEMP` | `dwg-temp` | Temporary files |

Docker Compose passes `MINIO_ROOT_USER` to both `MINIO_ACCESS_KEY` and `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD` to both `MINIO_SECRET_KEY` and `MINIO_ROOT_PASSWORD`.

### 5.6 JWT Authentication

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | (placeholder) | **MUST CHANGE** -- at least 32 random characters |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token TTL |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `14` | Refresh token TTL |

### 5.7 Super Admin Bootstrap

| Variable | Default | Description |
|---|---|---|
| `SUPER_ADMIN_USERNAME` | `admin` | Bootstrap admin username |
| `SUPER_ADMIN_PASSWORD` | `SuperAdminPass1` | **MUST CHANGE in production** |
| `SUPER_ADMIN_REAL_NAME` | `系统管理员` | Display name |

This user is seeded by `app/db/init_db.py` on first run.

### 5.8 Feature Flags

| Variable | Default | Effect |
|---|---|---|
| `AGENT_ENABLED` | `false` | When false, `/api/v1/agent-runs/*` returns 503 |
| `DXF_PIPELINE_ENABLED` | `false` | When false, DXF parsing endpoints return 503 |
| `CAD_WORKER_ENABLED` | `false` | When false, CAD worker endpoints return 503 |

All three are `false` in Stage 1. These are parsed as Python booleans (`true`/`false`, case-insensitive).

### 5.9 LLM (Stage 2)

| Variable | Default | Description |
|---|---|---|
| `MODEL_NAME` | `deepseek-chat` | LLM model identifier |
| `MODEL_API_KEY` | (empty) | API key for LLM provider |
| `MODEL_BASE_URL` | `https://api.deepseek.com` | LLM API base URL |

### 5.10 MCP (Stage 2)

| Variable | Default | Description |
|---|---|---|
| `MCP_CAD_COMMAND` | `uvx` | MCP client command |
| `MCP_CAD_ARGS` | `cad-mcp-server,stdio` | MCP client arguments |

### 5.11 CAD Worker (Stage 4)

| Variable | Default | Description |
|---|---|---|
| `CAD_WORKER_API_BASE` | `http://cad-worker.internal:8080` | Windows CAD Worker endpoint |
| `CAD_WORKER_API_KEY` | (empty) | Auth key for CAD Worker |

### 5.12 Frontend

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE_URL` | (empty) | API base URL for frontend; empty = relative path (use Nginx proxy) |

---

## 6. Scripts Reference

All scripts live in `scripts/` and are sourced from the repository root. They share common functions from `scripts/lib.sh`.

### `lib.sh` -- Shared Functions

Sourced by all other scripts. Provides:

| Function | Purpose |
|---|---|
| `port_free <port>` | Returns true if port is not in use |
| `check_port <port> <label>` | Reports port status (health check aggregation) |
| `kill_by_pidfile <pidfile> <label>` | Kills process by PID file |
| `pidfile_running <pidfile>` | Checks whether a PID file points to a live process |
| `start_report_worker` | Starts local Celery `worker-report` and writes `/tmp/dwg-agent-worker-report.pid` |
| `wait_port <host> <port> <timeout> <label>` | Blocks until port is accepting connections |
| `ensure_service <port> <name...>` | Starts systemd service if port not listening |
| `ok` / `warn` / `err` / `info` / `step` | Coloured console output |

Sets `PROJECT_ROOT` env var to the repo root directory.

### `db.sh` -- MySQL Management

```
Usage: bash scripts/db.sh <command>

Commands:
  start           Start MySQL/MariaDB, validate .env credentials can connect
  setup-user      Create dwg_agent database + dwg_user + grants (first-time setup)
  init            Full init: create schema + alembic upgrade head + seed super_admin
  migrate         Run alembic upgrade head (fix existing schema drift)
  migration-test  Create temp MySQL schema, run full migration from scratch, verify, cleanup
  check           Non-destructive validation: config consistency, credentials, schema, SQLite fd check
  status          Print database configuration and diagnostic summary
  shell           Open MySQL shell using application credentials from backend/.env
  logs            Tail MySQL/MariaDB systemd journal
```

Key behaviours:
- Requires `DATABASE_URL` to be `mysql+pymysql://` (rejects `sqlite://` at shell level).
- Validates `.env` and `backend/.env` have consistent database config.
- Detects MariaDB vs MySQL systemd service naming.
- `migration-test` creates a temporary `dwg_agent_migration_test_<pid>` database, runs the full Alembic chain from an empty schema, verifies all 17 expected tables and TimestampMixin columns, then drops the temp database.

### `start-dev.sh` -- Development Mode

```
Usage: bash scripts/start-dev.sh
```

- Starts MySQL + Redis, initializes database, starts local Celery `worker-report`, backend (`uvicorn --reload` on `:8000`) and frontend (Vite HMR on `:5173`).
- Writes PID files to `/tmp/dwg-agent-worker-report.pid`, `/tmp/dwg-agent-backend.pid`, and `/tmp/dwg-agent-frontend.pid`.
- Automatically detects if `VITE_API_BASE_URL` is empty (Nginx mode) and temporarily sets it to `http://127.0.0.1:8000` for direct backend access.
- Blocks until Ctrl+C (uses `wait`), then prints stop instructions.

### `start-all.sh` -- Full Stack (Nginx Gateway)

```
Usage: bash scripts/start-all.sh [--rebuild]
```

- Starts MySQL, Redis, local Celery `worker-report`, backend (`uvicorn --reload` on `:8000`), builds frontend if needed, starts Nginx on `:8080`.
- `--rebuild` flag forces frontend rebuild even if `dist/` exists.
- Unified access: `http://localhost:8080` (SPA + API + health check through Nginx).
- Stops if port 8080 is occupied by an unknown process.

### `stop-all.sh` -- Graceful Shutdown

```
Usage: bash scripts/stop-all.sh
```

- Stops Nginx (via `nginx -s quit`).
- Kills backend via PID file at `/tmp/dwg-agent-backend.pid`.
- Kills local Celery `worker-report` via PID file at `/tmp/dwg-agent-worker-report.pid`.
- Verifies port 8000 is released.
- Does **not** stop MySQL/Redis (they are shared infrastructure).

### `status.sh` -- Health Check Aggregation

```
Usage: bash scripts/status.sh
```

Checks:
1. MySQL status (via `db.sh status`)
2. Redis port 6379
3. Celery worker-report PID
3. Backend port 8000 + `GET /health` endpoint
4. Nginx port 8080 + API reverse proxy + SPA static serving

Prints a colour-coded summary with "all ok" or "partial failure" and recovery hints.

---

## 7. Health Checks and Monitoring

### 7.1 Backend Health Endpoint

```
GET /health
```

Response (200 OK):
```json
{
  "data": {"status": "ok"},
  "meta": {"request_id": "...", "timestamp": "..."}
}
```

Failure responses:
- 503 if database is unreachable (via `db_health()`)
- 500 on unexpected errors

The endpoint does **not** expose internal component details (database URL, Redis status, etc.) for security.

### 7.2 Docker Health Checks

| Service | Check | Interval | Timeout | Retries |
|---|---|---|---|---|
| `backend-api` | `curl -f http://localhost:8000/health` | 10s | 3s | 5 |
| `mysql` | `mysqladmin ping -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}"` | 10s | 3s | 5 |
| `redis` | `redis-cli -a "${REDIS_PASSWORD}" ping` | 10s | 3s | 5 |
| `minio` | `curl -f http://localhost:9000/minio/health/live` | 10s | 3s | 5 |

Nginx `depends_on` `backend-api` with `condition: service_healthy`, so Nginx will not start (and therefore not accept traffic) until the backend is healthy.

### 7.3 Dockerfile Health Check

The backend Dockerfile has a built-in HEALTHCHECK instruction for container runtimes:

```dockerfile
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1
```

This runs inside the container, independent of Docker Compose health checks.

### 7.4 Nginx Monitoring Points

- **Access log format:** `extended` format includes `$request_id`, `$request_time`, `$upstream_connect_time`, `$upstream_header_time`, `$upstream_response_time`.
- **Auth log:** Login endpoint (`/api/v1/auth/sessions`) logs separately to `/var/log/nginx/auth-access.log` for audit purposes.
- **Rate limiting:** Login endpoint rate-limited to 2 req/s (burst 3), general API to 100 req/s (burst 20) -- both return HTTP 429 when exceeded.
- **Health endpoint:** `access_log off` for `/health` to reduce log noise.

### 7.5 Flower (Celery Monitoring)

Start with the `monitoring` profile:

```bash
docker compose --profile monitoring up -d flower
```

Flower dashboard at `http://localhost:5555` provides:
- Worker status (online/offline)
- Task queue lengths
- Task success/failure rates
- Task execution time distribution

### 7.6 Infrastructure Verification

The `infra/verify.sh` script performs a comprehensive static + runtime verification:

```bash
bash infra/verify.sh
```

Checks (6 sections):
1. **Nginx config** -- syntax validation, key directives (upstream, rate limiting, security headers, SPA fallback)
2. **Docker Compose** -- service count (9), image versions, volume mounts, environment variable blanking, health checks, profiles
3. **Dockerfile** -- multi-stage build, non-root user, HEALTHCHECK, STOPSIGNAL, gunicorn CMD
4. **MySQL integration** -- database accessibility, all 17 tables, TimestampMixin columns, role seeds, admin user, dwg_user grants, application credentials
5. **File integrity** -- all required config files present, env template key consistency
6. **Dead code check** -- confirms removed directories (`conf.d/`, `snippets/`) are not referenced

---

## 8. Troubleshooting

### 8.1 Backend Won't Start

**Symptom:** `bash scripts/start-dev.sh` hangs or backend port 8000 not listening.

**Check:**
```bash
# Verify MySQL is running
bash scripts/db.sh status

# Verify Redis is running
redis-cli ping

# Check backend .env exists and has correct values
cat backend/.env | grep DATABASE_URL

# Try starting backend manually to see errors
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Common causes:**
- MySQL not running (`sudo systemctl start mariadb` or `mysqld`)
- Wrong `MYSQL_PASSWORD` in `.env` / `backend/.env`
- Database not initialized (`bash scripts/db.sh init`)
- Redis not running (`sudo systemctl start redis`)

### 8.2 Database Connection Refused

**Symptom:** Backend logs show `Can't connect to MySQL server on '127.0.0.1' (111)`.

**Fix:**
```bash
# Check MySQL is running
sudo systemctl status mariadb   # or mysqld

# If not running
sudo systemctl start mariadb    # or mysqld

# Verify port
bash scripts/db.sh status

# Re-initialize if needed
bash scripts/db.sh init
```

### 8.3 "DATABASE_URL is not mysql+pymysql://"

**Symptom:** `db.sh` commands fail with error about scheme.

**Fix:** Both `.env` and `backend/.env` must use:
```
DATABASE_URL=mysql+pymysql://dwg_user:YOUR_PASSWORD@127.0.0.1:3306/dwg_agent
```

Not `sqlite:///...`. SQLite is only for pytest isolation.

### 8.4 Frontend Shows "Cannot connect to API"

**Symptom:** Browser console shows failed requests to `/api/v1/...`.

**Fix:**
- In dev mode with Vite (`http://localhost:5173`): the Vite proxy forwards `/api/` to the backend. Ensure `VITE_API_BASE_URL` is empty in `frontend/.env`.
- In Nginx mode (`http://localhost:8080`): ensure backend is running on `:8000` and Nginx is started correctly.
- In Docker mode (`http://localhost`): check `docker compose ps` -- all services should be `Up (healthy)`.

### 8.5 Docker Containers Crash-Looping

**Symptom:** `docker compose ps` shows containers restarting.

**Check:**
```bash
# View specific service logs
docker compose logs backend-api --tail 50
docker compose logs mysql --tail 50

# Check if .env.docker exists and has real passwords
grep CHANGE_ME .env.docker
# If any CHANGE_ME values remain, the container will fail to connect
```

**Common causes:**
- `.env.docker` still has `CHANGE_ME_*` placeholder values
- MySQL data volume corrupted (`docker compose down -v && docker compose up -d` for full reset)
- Port 80 already in use on host (`sudo ss -tlnp 'sport = :80'`)

### 8.6 Port 8080 Already in Use

**Symptom:** `bash scripts/start-all.sh` fails with "端口 8080 已被占用".

**Fix:**
```bash
# Check what's using port 8080
sudo ss -tlnp 'sport = :8080'

# If it's a stale nginx instance
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit

# If it's another process
sudo kill <PID>
```

### 8.7 .env / backend/.env Inconsistency

**Symptom:** `db.sh check` reports mismatched database config values.

**Fix:** Copy the database section from one file to the other to make them identical:
```bash
# Option 1: Copy root .env to backend/.env (then edit backend-specific values)
cp .env backend/.env

# Option 2: Manually edit to match
vim -d .env backend/.env
```

### 8.8 Alembic Migration Errors

**Symptom:** `alembic upgrade head` fails with duplicate column or table errors.

**Fix:**
```bash
# Check current alembic head
cd backend && uv run alembic current

# If database is too far ahead of migrations, you may need to:
# 1. Drop and recreate (destructive)
bash scripts/db.sh init

# Or test migration from scratch without affecting production data:
bash scripts/db.sh migration-test
```

### 8.9 Agent Endpoints Return 503

This is **expected behaviour in Stage 1**. The feature flag `AGENT_ENABLED=false` causes all `/api/v1/agent-runs/*` endpoints to return 503 with message "Agent subsystem not yet available." The same applies to DXF pipeline and CAD Worker endpoints with their respective flags.

### 8.10 Redis Unavailable (Local Dev)

**Symptom:** `redis-cli ping` fails or backend logs show Redis connection errors.

**Fix:**
```bash
# Check service
sudo systemctl status redis

# Start if needed
sudo systemctl start redis

# Verify
redis-cli ping   # => PONG
```

Note: The backend is designed to survive Redis unavailability -- it connects lazily and all Redis-using code paths have fallbacks for when Redis is down.

---

## 9. Backup and Restore

### 9.1 MySQL Database

**Backup (local):**
```bash
# Dump with application credentials
mysqldump -h 127.0.0.1 -u dwg_user -p dwg_agent \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --add-drop-database \
  > dwg_agent_backup_$(date +%Y%m%d_%H%M%S).sql
```

**Backup (Docker):**
```bash
docker compose exec mysql mysqldump \
  -u dwg_user -p"${MYSQL_PASSWORD}" dwg_agent \
  --single-transaction \
  > dwg_agent_backup_$(date +%Y%m%d_%H%M%S).sql
```

**Restore:**
```bash
# Local
mysql -h 127.0.0.1 -u dwg_user -p dwg_agent < dwg_agent_backup_YYYYMMDD_HHMMSS.sql

# Docker
docker compose exec -T mysql mysql \
  -u dwg_user -p"${MYSQL_PASSWORD}" dwg_agent \
  < dwg_agent_backup_YYYYMMDD_HHMMSS.sql
```

Then run migrations to ensure schema is current:
```bash
bash scripts/db.sh migrate
```

### 9.2 Docker Volumes

**Backup volumes:**
```bash
# MySQL
docker run --rm -v complete_framework_mysql_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/mysql_data_backup.tar.gz -C /data .

# Redis
docker run --rm -v complete_framework_redis_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/redis_data_backup.tar.gz -C /data .

# MinIO
docker run --rm -v complete_framework_minio_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/minio_data_backup.tar.gz -C /data .
```

**Restore volumes:**
```bash
# Stop services first
docker compose down

# Restore
docker run --rm -v complete_framework_mysql_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/mysql_data_backup.tar.gz -C /data

docker compose up -d
```

### 9.3 Local File Storage

When `STORAGE_BACKEND=local`, files are stored at `backend/var/storage/` (relative to CWD). Back up this directory:

```bash
tar czf storage_backup_$(date +%Y%m%d_%H%M%S).tar.gz -C backend var/storage
```

### 9.4 Recommended Backup Schedule

| Resource | Frequency | Retention |
|---|---|---|
| MySQL dump (logical) | Daily | 30 days |
| File storage (local/minio) | Daily | 30 days |
| Docker volumes | Weekly | 4 weeks |
| Before schema migration | Manual | Keep until migration verified |

---

## 10. Stage 1 Limitations

The following components are **configured but not operational** in Stage 1:

| Component | Status | Behaviour | Stage Planned |
|---|---|---|---|
| **Agent subsystem** | Feature-flagged off | `/api/v1/agent-runs/*` returns 503 | Stage 2 |
| **DXF pipeline** | Feature-flagged off | DXF parsing endpoints return 503 | Stage 2 |
| **CAD Worker** | Feature-flagged off | CAD worker endpoints return 503 | Stage 4 |
| **Agent/DXF Celery Workers** | Compose profile only | `worker-agent` and `worker-dxf` start but concrete task bodies are deferred | Stage 2/3 |
| **MinIO (object storage)** | Docker default | Backend uses MinIO when `STORAGE_BACKEND=minio`; local dev still defaults to local FS | Done for deployment |
| **Flower monitoring** | Compose profile only | Dashboard can monitor `worker-report`; external port mapping is intentionally not exposed by default | Stage 2 hardening |
| **SSL/TLS (HTTPS)** | Not configured | Nginx listens on port 443 but no SSL cert | Stage C |
| **MCP CAD integration** | Stub code only | `app/mcp_client/` contains placeholder modules | Stage 2 |
| **ZWCAD integration** | Stub code only | `app/integrations/zwcad/` contains placeholder modules | Stage 4 |
| **Agent tool registry** | Empty registry | `app/agents/tool_registry.py` is an empty registry | Stage 2 |
| **Repository layer** | Not extracted | Business logic reads DB directly in services | Ongoing |

### What IS running and verified in Stage 1:

- Full RESTful API with 11 route modules under `/api/v1`
- RBAC with 7 roles, permissions, and user-role mapping
- JWT authentication (access + refresh tokens)
- File upload/download via storage backend (local in dev, MinIO in Docker)
- Celery `worker-report` fake task for queued → running → succeeded job flow
- Project, drawing, file, and job CRUD operations
- Audit logging (all mutations recorded)
- Database migrations (Alembic, 2 versions, 17 tables)
- Bootstrap super admin seeding
- 307 passing tests (pytest + FakeRedis)
- Docker Compose deployment with 9 services
- Nginx gateway with rate limiting, security headers, SPA fallback

### Environment flag reference for Stage 1:

```bash
AGENT_ENABLED=false          # Agent returns 503
DXF_PIPELINE_ENABLED=false   # DXF pipeline returns 503
CAD_WORKER_ENABLED=false     # CAD Worker returns 503
STORAGE_BACKEND=local        # Local dev filesystem; Docker .env.docker uses minio
CELERY_TASK_ALWAYS_EAGER=false  # Tests override to true; runtime uses real worker
```

When upgrading to Stage 2/3, change `AGENT_ENABLED` / `DXF_PIPELINE_ENABLED` only after implementing the corresponding task bodies, then start the relevant profile workers.

---

## Appendix A: Useful Commands Cheat Sheet

```bash
# ---- Local Dev ----
bash scripts/start-dev.sh                           # Start backend + frontend in dev mode
bash scripts/stop-all.sh                            # Stop backend + nginx
bash scripts/status.sh                              # Check all services
bash scripts/db.sh status                           # MySQL detailed status
bash scripts/db.sh shell                            # MySQL shell
bash scripts/db.sh init                             # Full DB initialization
bash scripts/db.sh migration-test                   # Test migrations from scratch

# ---- Backend ----
cd backend && uv run uvicorn app.main:app --reload  # Manual backend start
cd backend && uv run pytest -q                      # Run all tests
cd backend && uv run ruff check app tests           # Lint
cd backend && uv run alembic current                # Check migration state
cd backend && uv run alembic upgrade head           # Run pending migrations
cd backend && uv run python -m app.db.init_db       # Seed database manually

# ---- Frontend ----
cd frontend && npm ci                               # Install dependencies
cd frontend && npm run dev                          # Vite dev server
cd frontend && npm run build                        # Production build
cd frontend && npm run lint                         # ESLint

# ---- Docker ----
docker compose up -d                                # Core services
docker compose --profile workers --profile monitoring up -d  # Full stack
docker compose ps                                   # Service status
docker compose logs -f <service>                    # Follow logs
docker compose down                                 # Stop all (preserve volumes)
docker compose down -v                              # Stop and remove volumes
docker compose exec backend-api sh                  # Shell into backend container
docker compose exec mysql mysql -u dwg_user -p      # MySQL shell in container

# ---- Health Checks ----
curl http://127.0.0.1:8000/health                   # Backend (local)
curl http://localhost:8080/health                   # Via Nginx (local)
curl http://localhost/health                        # Via Nginx (Docker)
redis-cli ping                                      # Redis
bash infra/verify.sh                                # Full infrastructure verification
```

## Appendix B: Port Map

| Port | Service | Mode | Protocol |
|---|---|---|---|
| 80 | Nginx | Docker only | HTTP |
| 443 | Nginx (SSL placeholder) | Docker only | HTTPS |
| 3306 | MySQL | Both | MySQL wire |
| 5173 | Vite HMR | Local dev only | HTTP |
| 6379 | Redis/Valkey | Both | Redis wire |
| 8000 | Backend (FastAPI/Gunicorn) | Both | HTTP |
| 8080 | Nginx (optional local) | Local dev only | HTTP |
| 9000 | MinIO API | Docker only | HTTP |
| 9001 | MinIO Console | Docker only | HTTP |
| 5555 | Flower | Docker (profile) | HTTP |
