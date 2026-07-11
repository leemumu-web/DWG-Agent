# Configuration

> Chinese mirror: [zh/configuration.md](zh/configuration.md)

## Loading and Precedence

`backend/app/core/config.py` uses Pydantic Settings with `.env` resolved from the process working directory. Repository scripts start Python from `backend/`, so the API and workers normally read `backend/.env`; root database and shell tooling reads the root `.env`. Keep both files' `MYSQL_*` values aligned. Compose reads `.env.docker` through `env_file`.

Environment variables override `.env`. `DATABASE_URL`, when set, overrides the component fields for SQLAlchemy. If it is a MySQL URL it also becomes the source for Celery; otherwise Celery still derives a MySQL URL from `MYSQL_*`. A non-MySQL runtime override is unsupported outside tests.

## Application and Network

| Variable | Default | Meaning |
|---|---|---|
| `APP_NAME` | `DWG-Agent Platform` | OpenAPI/application display name |
| `APP_ENV` | `development` | Controls production cookie and documentation behavior |
| `DEBUG` | `true` | Enables development docs and detailed unhandled errors; must be false in production |
| `API_V1_PREFIX` | `/api/v1` | Router prefix; Nginx and frontend assume this value |
| `BACKEND_CORS_ORIGINS` | local Vite origins | Comma-separated exact origins; credentials are enabled |
| `VITE_API_BASE_URL` | empty | Empty means same-origin Nginx; direct Vite uses `http://127.0.0.1:8010` |

FastAPI local port `8010`, container port `8000`, Vite `5173`, and local Nginx `8080` are script/configuration constants rather than Pydantic fields.

## Database and Pools

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | unset | Optional full SQLAlchemy DSN override; avoid duplicating `MYSQL_*` in normal deployments |
| `MYSQL_HOST` | `127.0.0.1` | Compose overrides to `mysql` |
| `MYSQL_PORT` | `3306` | MySQL service port |
| `MYSQL_DATABASE` | `dwg_agent` | Application schema |
| `MYSQL_USER` | `dwg_user` | Application user |
| `MYSQL_PASSWORD` | empty | Required outside disposable development |
| `MYSQL_ROOT_PASSWORD` | template-only | Consumed by database scripts/Compose, not by `Settings` |
| `DB_POOL_SIZE` | 2 | Persistent application connections per process |
| `DB_POOL_MAX_OVERFLOW` | 2 | Burst connections per process |
| `DB_POOL_TIMEOUT_SECONDS` | 30 | Checkout wait timeout |
| `DB_POOL_RECYCLE_SECONDS` | 3600 | Connection recycle age |

Celery broker and result URLs are computed as `sqla+<effective-mysql-dsn>` and `db+<effective-mysql-dsn>`. There is deliberately no independent broker URL in the supported configuration contract.

## Storage and Uploads

| Variable | Default | Meaning |
|---|---|---|
| `STORAGE_BACKEND` | `local` | Exactly `local` or `minio` |
| `LOCAL_STORAGE_ROOT` | `./var/storage` | Relative to the backend process working directory |
| `MAX_UPLOAD_SIZE_MB` | 512 | Per-upload streaming limit |
| `MAX_ZIP_EXTRACT_MB` | 2048 | Total uncompressed ZIP limit |
| `MAX_ZIP_ENTRY_COUNT` | 1000 | ZIP entry-count limit |
| `MINIO_ENDPOINT` | `http://localhost:9000` | API endpoint, not console endpoint |
| `MINIO_ACCESS_KEY` | empty | MinIO client identity |
| `MINIO_SECRET_KEY` | empty | MinIO client secret |
| `MINIO_ROOT_USER` | template-only | Compose server setting, not a backend `Settings` field |
| `MINIO_ROOT_PASSWORD` | template-only | Compose server setting, not a backend `Settings` field |

Bucket defaults are `MINIO_BUCKET_ORIGINAL=dwg-original`, `MINIO_BUCKET_DERIVED=dwg-derived`, `MINIO_BUCKET_REPORTS=dwg-reports`, `MINIO_BUCKET_TEMP=dwg-temp`, `MINIO_BUCKET_DXF_ORIGINAL=dxf-original`, and `MINIO_BUCKET_DXF_DERIVED=dxf-derived`.

The two ZIP limits and the DXF bucket overrides exist in code but are not currently listed as active lines in both environment templates. Defaults therefore apply unless operators add them explicitly.

## Authentication and Bootstrap

| Variable | Default | Meaning |
|---|---|---|
| `JWT_SECRET_KEY` | insecure development string | Replace with a high-entropy secret before shared use |
| `JWT_ALGORITHM` | `HS256` | Token signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 30 | Access and SSE cookie lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | 14 | Refresh cookie lifetime |
| `REFRESH_COOKIE_SECURE` | automatic | Secure when `APP_ENV=production`; explicit false is private-HTTP risk acceptance |
| `SUPER_ADMIN_USERNAME` | `admin` | Seed username |
| `SUPER_ADMIN_PASSWORD` | insecure development value | Used only when the seed user does not exist |
| `SUPER_ADMIN_REAL_NAME` | `系统管理员` | Seed display name |

Changing `SUPER_ADMIN_PASSWORD` does not rotate an existing account. Use the authenticated password-change or admin reset API; do not delete a referenced super-admin row to force re-seeding.

## Feature Flags and Processing

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_ENABLED` | `false` | Must remain false while Agent tasks are placeholders |
| `DXF_PIPELINE_ENABLED` | `false` | Enables DWG -> DXF job creation |
| `DXF2DWG_PIPELINE_ENABLED` | `false` | Enables DXF -> DWG job creation |
| `DXF2EXCEL_PIPELINE_ENABLED` | `false` | Enables DXF batch -> Excel job creation |
| `EXCEL_FINAL_PIPELINE_ENABLED` | `false` | Enables Excel Final endpoints/jobs |
| `CAD_WORKER_ENABLED` | `false` | Must remain false while the Windows worker is absent |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Test/development escape hatch; not a production worker topology |
| `CELERY_STALE_JOB_TIMEOUT_SECONDS` | 7200 | Running-job inactivity threshold |
| `EXCEL_FINAL_STAGE_ROOT` | auto-detected | Optional Stage path override; code field exists but templates omit it |
| `EXCEL_FINAL_TIMEOUT_SECONDS` | 1800 | Child-process timeout, constrained to 30-7200 seconds |

ODA fields are `ODA_CONVERTER_VERSION=ACAD2018`, `ODA_CONVERTER_AUDIT=true`, `ODA_CONVERTER_TIMEOUT=300`, `ODA_CONVERTER_RETRIES=1`, `ODA_XVFB_RUN=true`, `DXF2DWG_CONVERTER_VERSION=ACAD2018`, `DXF2DWG_CONVERTER_AUDIT=true`, `DXF2DWG_CONVERTER_TIMEOUT=300`, `DXF2DWG_CONVERTER_RETRIES=1`, and `ODA_HOME` empty by default.

## Handbook, Agent, and CAD Placeholders

The Excel Final handbook defaults to the platform MySQL host/user/password with database `hardware_handbook`. Use `HANDBOOK_MYSQL_HOST`, `HANDBOOK_MYSQL_PORT`, `HANDBOOK_MYSQL_DATABASE`, `HANDBOOK_MYSQL_USER`, and `HANDBOOK_MYSQL_PASSWORD` for a separate read-only account.

Agent placeholder fields are `MODEL_NAME=deepseek-chat`, `MODEL_API_KEY`, `MODEL_BASE_URL=https://api.deepseek.com`, `MCP_CAD_COMMAND=uvx`, `MCP_CAD_ARGS=cad-mcp-server,stdio`, `AGENT_MEMORY_TTL=7200`, and `AGENT_MAX_MESSAGES=20`. Windows placeholder fields are `CAD_WORKER_API_BASE=http://cad-worker.internal:8080` and `CAD_WORKER_API_KEY`. Setting these values does not implement or enable the missing tasks.

## Secret Classification

| Class | Examples | Rule |
|---|---|---|
| Secret | `JWT_SECRET_KEY`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `MINIO_SECRET_KEY`, `MINIO_ROOT_PASSWORD`, `SUPER_ADMIN_PASSWORD`, `MODEL_API_KEY`, `CAD_WORKER_API_KEY`, `HANDBOOK_MYSQL_PASSWORD` | Never commit, print, place in client bundles, or store in job errors |
| Deployment-sensitive | hosts, origins, bucket names, `REFRESH_COOKIE_SECURE` | Review per environment and record intentional exceptions |
| Safe defaults | pool sizes, timeouts, feature flags | Still verify against workload and dependencies |

`.env`, `backend/.env`, and `.env.docker` are ignored local secret files. The repository does not provide a secret manager, automated rotation, or encrypted configuration backup.

## Validation

```bash
# Parse templates and Compose contracts
bash infra/verify.sh
docker compose config --quiet

# Verify Settings and database behavior
cd backend
uv run pytest -q tests/test_config.py tests/test_mysql_runtime.py tests/test_compose.py
```

Before enabling a pipeline, validate both its flag and its dependency readiness. A healthy generic worker with a disabled flag or missing Stage does not prove the pipeline is usable.
