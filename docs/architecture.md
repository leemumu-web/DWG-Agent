# Architecture

> Chinese mirror: [zh/architecture.md](zh/architecture.md)

## System Context

DWG-Agent is a work-focused CAD processing platform. Nginx is the only public deployment entry, React implements operator workflows, FastAPI owns validation and authorization, MySQL is the authoritative state store, Celery executes long-running work, and Local FS or MinIO stores bytes.

```text
Browser
  -> Nginx :8080 local / :80,:443 Compose
     -> React SPA
     -> FastAPI :8010 local / :8000 container
        -> MySQL business schema
        -> MySQL Celery broker/result tables
        -> LocalStorage or MinIO
Celery workers
  -> MySQL + storage + processing Stages
```

There is no Redis/Valkey runtime dependency. Token revocation, password-change checks, Agent memory, job progress, SSE snapshots, broker messages, and task results all use MySQL.

## Boundaries

| Layer | Owns | Must not own |
|---|---|---|
| Nginx | Entry routing, TLS, SPA fallback, SSE proxy | Business authorization |
| Frontend | User interaction, retry and download orchestration | Final permission decisions |
| API | Validation, RBAC, transactions, dispatch, queries | Long CAD/Excel execution |
| Services | State transitions and business invariants | HTTP presentation |
| Workers | Task claim and pipeline execution | Unconditional status writes |
| MySQL | Business truth, broker/result, audit | Large file payloads |
| Storage | Immutable object bytes | User/project authorization |

## Request Paths

### Normal API

```text
Browser -> Nginx -> FastAPI dependency auth -> service -> MySQL -> envelope response
```

List endpoints perform `COUNT(*)` and `LIMIT/OFFSET` in SQL. Stable sorts append an ID tie-breaker. Access filters are part of the SQL query; file lists do not perform one authorization query per row.

### Asynchronous job

```text
POST request
  -> validate project/file permission
  -> INSERT jobs(status=queued, attempt=1)
  -> COMMIT
  -> publish (job_id, attempt) to MySQL SQLAlchemy transport
  -> worker atomically claims queued+expected attempt
  -> write attempt-scoped JobSteps
  -> write object + DB metadata/result
  -> conditionally complete same attempt
```

Dispatch compensation updates only the still-queued attempt. If a worker already claimed it, the API cannot overwrite it. Retry increments `jobs.attempt`; stale messages and workers cannot claim or update the new generation. Legacy one-argument Celery messages default to attempt 1.

### SSE

EventSource authenticates with the short-lived `dwg_sse_token` cookie. FastAPI checks job access, polls MySQL, and emits snapshots and terminal events containing only the current attempt's steps. A reconnect starts with a fresh authoritative snapshot; the stream does not promise replay by event ID. No access token appears in a URL and no Pub/Sub component is required.

### Download

The client requests a signed download URL after normal authorization, then downloads with Bearer auth. A retry obtains a new signature. When one source has multiple successful conversions, file and ZIP resolution deterministically selects the newest job and newest result row. Local storage returns a file response; MinIO returns a streamed object. The database SHA-256 is the integrity reference.

## Storage Consistency

An object written before database commit is registered on the SQLAlchemy session. Rollback deletes pending objects; commit discards the compensation list. Worker output is not exposed as a result until both object and database records succeed.

## Celery on MySQL

Broker URL is derived as `sqla+mysql+pymysql://...`; result backend is `db+mysql+pymysql://...`. Connections use bounded pools and `READ COMMITTED`. The message table has `(queue_id, timestamp, id, visible)` so consumers do not scan or lock unrelated queues.

The SQL transport has no fanout remote control. Health uses a `worker_ready` marker plus PID 1 verification. The local launcher also discovers managed worker command lines so a missing pidfile cannot create duplicate consumers.

## Processing Pipelines

| Pipeline | Queue | Output |
|---|---|---|
| framework smoke | `report` | JSON result |
| DWG -> DXF | `dxf` | DXF |
| DXF -> DWG | `dxf2dwg` | DWG |
| DXF -> Excel | `dxf2excel` | XLSX |
| Excel Final | `excel_final` | final XLSX + relational parts/components |

Excel Final runs its legacy Stage in a child process. Accepted content is a Tekla tab/whitespace export (often named `.xls`) or an Excel initial table with the required steel-list schema; the extension alone does not make an arbitrary workbook processable. Legacy binary `.xls` parsing uses `xlrd`. This isolates imports and keeps child stderr out of API errors.

## Security Model

Global roles are `super_admin`, `admin`, `engineer`, `reviewer`, `operator`, `viewer`, and `auditor`. Project membership adds owner/engineer/reviewer/viewer scopes. A global role never replaces resource validation unless explicitly designated as administrative access.

File reads are allowed to administrators, uploaders, or members of an associated active project. Result details, download URLs, and reviews inherit their job boundary; an unscoped job is visible only to administrators or its creator. Agent runs, when enabled, are visible to administrators, creators, or linked project members.

## Health

- `/health`: process liveness only.
- `/health/ready`: independent MySQL and storage probes; returns 503 if either fails.
- Worker container: ready marker and Celery PID.
- MinIO recovery does not require API restart; named-volume objects remain readable.

## Feature Flags

`AGENT_ENABLED` and `CAD_WORKER_ENABLED` remain off by default. Conversion flags are `DXF_PIPELINE_ENABLED`, `DXF2DWG_PIPELINE_ENABLED`, `DXF2EXCEL_PIPELINE_ENABLED`, and `EXCEL_FINAL_PIPELINE_ENABLED`.

## Ports

Local API is fixed at `8010`. Container `8000` is an internal deployment detail. Local Nginx proxies `8080 -> 8010`; Compose Nginx proxies to `backend-api:8000`.
