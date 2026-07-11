# Deployment

> Chinese mirror: [zh/deployment.md](zh/deployment.md)

## Supported Modes

| Mode | Entry | API | Storage | Readiness |
|---|---|---|---|---|
| Local development | Vite `5173` or Nginx `8080` | `127.0.0.1:8010` | local by default; MinIO optional | MySQL + selected storage |
| Docker Compose | HTTP host `80` -> Nginx `8080` | internal `backend-api:8000` | internal MinIO | MySQL + MinIO |

MySQL is mandatory in both modes. SQLite is a pytest-only test double. Current Compose has no functional HTTPS: host `443` is mapped to container `8443`, but Nginx does not listen there and no certificates are mounted.

## Repository Prerequisite

Before a clean-clone deployment, repair `Stages/dxf2excel`. The parent repository stores only gitlink `86e99dce5ebce992273c7df78ca13d58036f7472`, has no `.gitmodules`, and does not contain that object. Backend `uv sync` and Docker `COPY Stages/dxf2excel` work only because the present working tree is populated outside the parent index.

The acceptable repair is either a normal tracked directory or a valid submodule URL plus reachable pinned commit. Validate with a new clone, not the current checkout.

## Local Setup

```bash
cp .env.example .env
cp .env.example backend/.env
# Replace secrets and keep MYSQL_* identical in both files.

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

`start-dev.sh` starts report, dxf, dxf2dwg, dxf2excel, and excel_final workers, FastAPI `8010`, and Vite `5173`. `start-all.sh` builds the frontend and uses local Nginx `8080` instead of Vite. Agent and CAD workers are not started locally.

A worker may be alive while its feature flag is false. Keep all conversion flags false until dependencies and real samples pass their pipeline checklist.

```bash
bash scripts/status.sh
bash scripts/stop-all.sh
bash scripts/db.sh status
```

Scripts discover managed workers by Celery app, queue, and node name, repair missing pidfile tracking, and avoid killing unrelated port owners.

## Database Initialization

```bash
bash scripts/db.sh check
bash scripts/db.sh migrate
bash scripts/db.sh migration-test
```

`migration-test` creates a temporary empty MySQL schema, upgrades it to `a74c2e9f1d30`, validates 22 business tables and critical constraints, then removes it. It does not execute downgrade.

In local Uvicorn, FastAPI lifespan calls idempotent `init_db`; startup logs and continues if it fails, so check `/health/ready`. In the image, Docker CMD runs `alembic upgrade head` and `app.db.init_db` before Gunicorn; failure prevents the API process from starting.

## Local MinIO

```dotenv
STORAGE_BACKEND=minio
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
```

When selected MinIO is unavailable, `/health/ready` returns 503 and must recover without an API restart. Persistence and bucket creation/credentials remain operator responsibilities in local mode.

## Compose Preparation

```bash
cp .env.docker.example .env.docker
# Replace every CHANGE_ME_* value; do not commit the file.

npm --prefix frontend ci
npm --prefix frontend run build
docker compose config --quiet
```

`frontend/dist` is mounted read-only into Nginx; it is not built inside Compose. Build it again after frontend changes.

## Compose Services

| Service | Default / profile | Purpose | Boundary |
|---|---|---|---|
| `mysql` | default | business DB, broker/result, handbook schemas | private network; 8.4 tag, not digest-pinned |
| `minio` | default | object bytes | private network; digest-pinned image and named volume |
| `backend-api` | default | four Gunicorn workers on internal `8000` | production mode disables runtime API docs |
| `worker-report` | default | framework report/stub queue | not an Agent |
| `nginx` | default | HTTP SPA/API gateway on host `80` | no TLS listener despite `443` mapping |
| conversion workers | `workers` | four processing queues | pipeline flags/dependencies remain separate |
| `worker-agent` | `workers` | placeholder queue process | task module is empty; do not enable Agent |

There is no `worker-cad` Compose service.

```bash
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

## Initialization Order

1. MySQL creates `dwg_agent` and imports `hardware_handbook` from init scripts on a fresh volume.
2. The platform SQL grants application schema access and handbook `SELECT`.
3. MinIO reports its process live.
4. Backend runs migrations and idempotent seeds, then Gunicorn.
5. Workers wait for backend readiness, create Celery runtime tables/index, run startup maintenance, and emit ready marker.
6. Nginx waits for healthy backend.

MySQL init scripts run only when the data directory is first initialized. Changing them does not mutate an existing named volume.

## Images and Build Context

- Backend uses Python 3.12/uv multi-stage images and UID/GID 1000 `appuser`.
- Backend and every worker share one image, so ODA/Stage code exists even when a process does not use it.
- `Stages/dwg2dxf`, `Stages/dxf2dwg`, and `Stages/dxf2excel` are editable path dependencies during build.
- Excel Final is copied as a standalone script tree and launched in a child process.
- `.dockerignore` removes local virtualenvs, samples, browser traces, storage, and unrelated third-party preview applications.
- Do not document a fixed context size; it changes with tracked Stage binaries and source ownership.

## Health Checks

| Service | Implementation | Interpretation |
|---|---|---|
| Backend | `curl /health/ready` on internal `8000` | MySQL and MinIO are reachable |
| MySQL | root `mysqladmin ping` | server accepts connections, not schema correctness |
| MinIO | `/minio/health/live` | server process is live, not object integrity |
| Worker | ready marker + Celery PID 1 | connected startup, not specific pipeline readiness |
| Nginx | dependency only | no independent Compose healthcheck |

## Nginx and TLS

Local and Compose configurations proxy API, health, and development-document paths and disable buffering for Job SSE. In `APP_ENV=production` with `DEBUG=false`, FastAPI returns 404 for `/docs`, `/redoc`, and `/openapi.json` even though Nginx can route those paths.

To add TLS, create a separate reviewed server listening on container `8443 ssl`, mount certificates read-only, redirect HTTP, add HSTS only after HTTPS is verified, and test expiry/renewal. Until then remove or clearly ignore the dead host 443 mapping at deployment time.

## Celery SQL Transport

The broker and result backend are derived from the effective MySQL DSN as `sqla+mysql+pymysql://...` and `db+mysql+pymysql://...`; operators do not configure a second credential set.

The SQLAlchemy broker is intended for the repository's bounded queue layout. It uses `READ COMMITTED`, one-message prefetch, late acknowledgement, lost-worker rejection, bounded engine pools, and the composite queue claim index. It does not support fanout remote control and does not guarantee high-throughput multi-replica scheduling.

Evaluate RabbitMQ when measured throughput or routing requires it. That is a broker change only; MySQL remains the Job/progress/authorization truth source.

## Production Gaps

- Functional TLS, certificate lifecycle, and public-network hardening.
- Clean-clone `dxf2excel` source ownership.
- Secret manager and rotation workflow.
- Coordinated MySQL/MinIO backup, retention, restore automation, and RPO/RTO evidence.
- Central logs, metrics, tracing, alerting, SLOs, and capacity tests.
- Rolling deployment/schema compatibility and multi-replica coordination.
- Completed Agent and Windows CAD worker.

## Verification

```bash
bash infra/verify.sh
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 backend-api worker-report mysql minio nginx
```

Static/config checks do not prove a deployed workflow. Acceptance must submit an authenticated Job through Nginx, observe Celery, verify MySQL state and MinIO bytes, download through a fresh signed path, compare SHA-256, and exercise storage interruption/recovery.
