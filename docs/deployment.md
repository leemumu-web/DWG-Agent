# Deployment

> Chinese mirror: [zh/deployment.md](zh/deployment.md)

## Supported Modes

| Mode | API | Storage | Entry |
|---|---|---|---|
| Local development | `127.0.0.1:8010` | `local` by default | Vite `:5173` or Nginx `:8080` |
| Docker Compose | `backend-api:8000` internal | MinIO | Nginx `:80/:443` |

MySQL is mandatory in both modes. SQLite is a pytest-only test double.

## Local Setup

```bash
cp .env.example .env
cp .env.example backend/.env
# Replace secrets and keep database fields identical in both files.

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

`start-dev.sh` starts all five implemented workers, FastAPI on `8010`, and Vite on `5173`. If Vite selects another port, use the printed URL. `start-all.sh` also starts all five implemented workers, builds the frontend when needed, starts FastAPI `8010`, and exposes it through local Nginx `8080`.

```bash
bash scripts/status.sh
bash scripts/stop-all.sh
```

Worker scripts discover processes by Celery app, queue, and managed node name. A missing pidfile is repaired instead of starting a duplicate. Shutdown waits for warm exit and does not kill an unrelated process merely because it owns a port.

## Database

```bash
bash scripts/db.sh start
bash scripts/db.sh check
bash scripts/db.sh migrate
bash scripts/db.sh migration-test
bash scripts/db.sh backup
```

`migration-test` creates a temporary empty MySQL schema, upgrades to head, validates tables/columns/types, and drops the schema. Current head is `a74c2e9f1d30`.

The Celery broker URL is derived from the effective MySQL DSN as `sqla+mysql+pymysql://...`. The result backend is derived as `db+mysql+pymysql://...`. Do not configure independent broker credentials that can drift from the application database.

## Local MinIO

Set:

```dotenv
STORAGE_BACKEND=minio
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
```

`/health/ready` returns 503 when configured MinIO is unavailable and recovers without an API restart. Object bytes must live on a persistent volume.

## Docker Compose

```bash
cp .env.docker.example .env.docker
# Replace every CHANGE_ME_* value.

npm --prefix frontend ci
npm --prefix frontend run build

docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

Core services are `nginx`, `backend-api`, `mysql`, `minio`, and `worker-report`. The `workers` profile adds `worker-agent`, `worker-dxf`, `worker-dxf2dwg`, `worker-dxf2excel`, and `worker-excel-final`.

`worker-agent` is operational infrastructure for a feature-gated subsystem, not proof that Agent task logic is enabled.

## Compose Initialization Order

1. MySQL creates `dwg_agent` and `hardware_handbook`.
2. `01-platform.sql` grants application access and read-only handbook access.
3. `02-hardware-handbook.sql` imports the handbook data.
4. MinIO reports live.
5. Backend runs `alembic upgrade head`, seeds roles/admin, and starts Gunicorn.
6. Workers wait for backend readiness, prepare Kombu SQL tables/indexes, then write the ready marker.
7. Nginx waits for backend readiness.

## Images and Build Context

- Backend uses a multi-stage uv image and runs as UID/GID 1000 `appuser`.
- MinIO is pinned by digest to the verified release, not `latest`.
- `.dockerignore` excludes virtual environments, Stage samples, local storage, test output, and separately deployed third-party applications.
- The validated backend build context is about 89 MB and includes one ODA binary.

## Health Checks

| Service | Check |
|---|---|
| Backend | local: `GET http://127.0.0.1:8010/health/ready`; container: `GET http://localhost:8000/health/ready` inside the container |
| MySQL | `mysqladmin ping` using container root credentials |
| MinIO | `GET /minio/health/live` |
| Worker | `/tmp/dwg-celery-ready` exists and PID 1 is Celery |
| Nginx | depends on healthy backend |

`/health` is liveness only. `/health/ready` reports database and storage independently; a storage outage must not label the database unavailable.

## Nginx

Local configuration proxies `/api/v1`, `/health`, `/docs`, `/redoc`, and `/openapi.json` to `127.0.0.1:8010`. Compose proxies to `backend-api:8000`. SSE locations disable proxy buffering and use a long read timeout.

## Celery SQL Transport

The SQLAlchemy transport is suitable for the bounded worker topology in this repository, not arbitrary horizontal scale. It uses:

- `READ COMMITTED` isolation.
- Bounded pool size and pre-ping.
- `(queue_id, timestamp, id, visible)` on `kombu_message`.
- No remote-control fanout or `inspect` health check.
- `worker_prefetch_multiplier=1`, late acknowledgement, and lost-worker rejection.

If throughput requires many worker replicas, evaluate RabbitMQ as a broker while retaining MySQL as the business truth source.

## Production Secrets

Never commit `.env`, `backend/.env`, or `.env.docker`. Replace JWT, admin, MySQL, and MinIO credentials. Public deployments require TLS and secure refresh cookies. An HTTP-only private VPN deployment may explicitly set `REFRESH_COOKIE_SECURE=false`; do not use that on a public network.

## Verification

```bash
bash infra/verify.sh
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 backend-api worker-report mysql minio
```

Production acceptance must submit a job through the API, observe Celery consumption, verify a MinIO object, download through a signed URL, and compare SHA-256. Stop and restart MinIO to verify 503 degradation and persistent-object recovery.
