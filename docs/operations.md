# Operations

> Chinese mirror: [zh/operations.md](zh/operations.md)

## Operating Boundary

This is a runbook for the repository's current local and Compose topology. It does not claim that monitoring, backup scheduling, TLS, log aggregation, rolling upgrades, or disaster recovery are automated. Operators must record environment-specific credentials, storage locations, retention, owners, and recovery objectives outside Git.

## Start, Status, and Stop

Local managed topology:

```bash
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/stop-all.sh
```

`start-all.sh` builds the frontend when needed, starts five implemented queue workers, FastAPI on `8010`, and local Nginx on `8080`. `start-dev.sh` replaces Nginx/static serving with Vite. The scripts identify workers by Celery app, queue, and node name; pidfiles are tracking aids, not the sole process identity.

Compose topology:

```bash
docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
docker compose logs --tail=200 nginx backend-api worker-report mysql minio
```

The second `up` is required only for conversion workers. It also starts the placeholder `worker-agent`; its health does not mean Agent tasks exist.

## Health Interpretation

| Signal | Proves | Does not prove |
|---|---|---|
| `GET /health` 200 | FastAPI process can answer | MySQL, storage, worker, or Stage availability |
| `GET /health/ready` 200 | MySQL and configured storage probes pass | A specific feature flag, queue worker, ODA, or handbook query works |
| worker healthy | Celery PID is alive and emitted `worker_ready` | Queue task implementation or Stage dependencies are usable |
| MySQL healthy | Server accepts root ping | Application grants, migration head, broker index, or query latency are correct |
| MinIO live | Server process responds | Buckets, credentials, object persistence, or download authorization work |
| Nginx reachable on 80 | HTTP gateway and static root respond | TLS on 443 is configured |

Use readiness plus one representative business transaction. Do not convert `/health` into a deep dependency check; liveness must remain useful during dependency outages.

## Logs and First Response

Local logs are under `/tmp`:

```bash
tail -n 200 /tmp/dwg-agent-backend.log
tail -n 200 /tmp/dwg-agent-worker-report.log
tail -n 200 /tmp/dwg-agent-nginx-error.log
```

Compose logs:

```bash
docker compose logs --since=15m backend-api worker-report mysql minio nginx
docker compose --profile workers logs --since=15m worker-dxf worker-dxf2dwg worker-dxf2excel worker-excel-final
```

Preserve the first exception, request ID, Job ID/attempt, worker node, dependency status, and timestamps before restarting. Current logs have no central retention or correlation backend; `/tmp` logs are lost on reboot and container logs depend on the Docker logging driver.

## Database Operations

Local MySQL commands:

```bash
bash scripts/db.sh status
bash scripts/db.sh check
bash scripts/db.sh tables
bash scripts/db.sh backup /secure/path/dwg_agent.sql.gz
bash scripts/db.sh migration-test
```

`migration-test` creates and removes a temporary schema. It proves upgrade-from-empty to `a74c2e9f1d30`; it does not test downgrade or production data migration time.

Before a migration:

1. Verify a recent database and object-store backup can be restored.
2. Stop application writers or place the deployment in a documented maintenance window.
3. Run `migration-test` against the exact revision.
4. Apply `alembic upgrade head` once, monitor duration and locks, then run readiness and schema checks.
5. Start workers/API and execute a representative upload/process/download.

The current `drawings`/`drawing_versions` circular FK emits an Alembic autogenerate sorting warning. `alembic check` should still report no new operations; do not ignore a new operation merely because the known warning is present.

## Backup and Restore

The repository has a local MySQL backup/restore helper but no Compose-wide coordinated backup service. A recoverable set must include:

| Data | Required content |
|---|---|
| Application MySQL | all business tables, `alembic_version`, and current Celery runtime tables |
| Object storage | every configured bucket, including original, derived, report, temporary-as-required, and DXF buckets |
| Handbook DB | `hardware_handbook` schema/data or an independently managed authoritative source |
| Secrets/configuration | encrypted copy of deployment values; never commit live `.env.docker` |
| Evidence | backup time, application revision, migration head, object snapshot marker, checksum, and restore-test result |

For a quiesced Compose database dump, stop every writer explicitly; Compose service names do not support a `worker-*` wildcard:

```bash
docker compose stop backend-api worker-report
docker compose --profile workers stop worker-agent worker-dxf worker-dxf2dwg worker-dxf2excel worker-excel-final

docker compose exec -T mysql sh -c \
  'exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction --routines --triggers --events "$MYSQL_DATABASE"' \
  | gzip > dwg_agent_$(date +%Y%m%d_%H%M%S).sql.gz
```

This database dump alone is insufficient. Capture MinIO data through a tested `mc mirror`/snapshot path on the internal network before restarting writers. The repository does not provide that one-off client or atomic database/object snapshot orchestration.

Restore only in an isolated or maintenance environment:

```bash
gunzip -c dwg_agent_TIMESTAMP.sql.gz | docker compose exec -T mysql sh -c \
  'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"'
```

Then restore all object buckets, run `alembic current`, verify representative `files.sha256` values against bytes, start services, and execute the full workflow. Restoring broker rows can reintroduce queued deliveries; inspect queued/running Jobs and broker tables before starting workers.

## Storage Incident

When storage is unavailable:

1. Confirm `/health` remains 200 and `/health/ready` reports database `ok`, storage `error`.
2. Stop submitting new file jobs; do not switch to an unplanned local fallback.
3. Check endpoint, credentials, network, bucket existence, and volume state without logging secrets.
4. Restore storage, verify readiness recovers without restarting FastAPI, then download a pre-incident object and compare SHA-256.
5. Review Jobs that failed during the interval and retry only from a supported terminal state.

Objects written before a rolled-back database transaction are compensated best-effort. Operators should still periodically detect unreferenced objects and missing objects; no automated reconciler currently exists.

## Worker and Queue Incident

```bash
bash scripts/status.sh
ps -ef | rg 'celery.*app.workers.celery_app'
```

Check exactly one intended managed node per local queue. SQL transport has no reliable fanout inspect health path. A dead worker can leave a Job running until `CELERY_STALE_JOB_TIMEOUT_SECONDS`; worker startup then marks it `CELERY_WORKER_LOST`. Verify the Stage and storage before using the retry API, which creates a new attempt.

Do not manually update Job status to succeeded. If broker messages must be purged, use the application's queue-aware cancellation operation and retain its per-queue purge result; direct SQL deletion requires an incident record and Job reconciliation.

## Authentication Incident

- A password change invalidates older access/refresh tokens through `password_changed_at`.
- Logout blacklists the current access token and available refresh token JTI in MySQL.
- Rotating `JWT_SECRET_KEY` invalidates all tokens immediately; coordinate it as a full-session logout.
- Suspected database compromise is outside the guarantees of the application-level audit log. Preserve external database, proxy, host, and backup logs.

Never repair refresh failures by disabling Secure cookies on a public network. First verify whether the deployment actually terminates TLS; the current Compose configuration does not.

## Capacity and Retention

The repository has no measured production capacity claim. Plan MySQL connections as API workers plus every Celery parent/child, Kombu/result backend, migrations, and operator sessions. SQL broker throughput, ODA CPU/memory, Excel workbook size, MinIO bandwidth, and Nginx upload concurrency require workload tests.

Celery results expire after 24 hours, while business Job/JobStep, audit, files, and object bytes have no automated retention. Define legal/operational retention and implement deletion with database/object consistency before enabling high-volume use.

## Release and Rollback Checklist

1. Record Git revision, migration head, images/digests, flags, and dependency versions.
2. Pass docs, backend, Stage, migration, infrastructure, frontend, and browser gates.
3. Back up and restore-test MySQL plus object storage.
4. Deploy during a maintenance window when migrations are not backward compatible.
5. Verify Nginx -> API -> MySQL -> Celery -> storage -> signed download with a real sample.
6. Verify unauthorized access, retry attempt isolation, SSE reconnect, and storage degradation.
7. Roll back application code only if its schema compatibility is known; never improvise a production downgrade.

TLS, automated backups, metrics/alerts, centralized logs, and documented RPO/RTO remain release blockers for public or business-critical production use.
