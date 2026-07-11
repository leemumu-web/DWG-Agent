# Infrastructure

Deployment configuration for Nginx, FastAPI, MySQL, MinIO, and Celery.

## Topology

| Component | Compose behavior |
|---|---|
| Nginx | only published application entry (`80`, optional `443`) |
| FastAPI | internal `backend-api:8000`; readiness checks MySQL and storage |
| MySQL 8.4 | application state, Celery SQL broker/results, hardware handbook schema |
| MinIO | digest-pinned image and persistent object volume |
| worker-report | core report/smoke queue |
| worker-dxf | DWG to DXF via ODA, `workers` profile |
| worker-dxf2dwg | DXF to DWG, `workers` profile |
| worker-dxf2excel | DXF extraction, `workers` profile |
| worker-excel-final | Excel Final processing, `workers` profile |
| worker-agent | reserved queue; Agent feature remains disabled, `workers` profile |

Redis/Valkey and Flower are intentionally absent. MySQL and MinIO are not published to the host.

## Compose

```bash
cp .env.docker.example .env.docker
# replace every secret placeholder
cd frontend && npm ci && npm run build && cd ..

docker compose up -d
docker compose --profile workers up -d
docker compose ps
docker compose logs -f nginx backend-api worker-report
```

Worker health requires both Celery PID 1 and `/tmp/dwg-celery-ready`, written after broker schema
preparation and startup maintenance. MinIO data and MySQL data use named volumes.

## Local Mode

Local FastAPI listens on `127.0.0.1:8010`; local Nginx listens on `8080`. Use:

```bash
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/stop-all.sh
```

## Verification

```bash
bash infra/verify.sh
docker compose config --quiet
```

`infra/verify.sh` validates both Nginx configurations, Compose secrets/health structure, Dockerfile
requirements, live application MySQL access, schema columns, and environment-template parity.
