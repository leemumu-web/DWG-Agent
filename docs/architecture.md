# Architecture

> Chinese mirror: [zh/architecture.md](zh/architecture.md)

## System Context

DWG-Agent is an internal operational platform for CAD/Excel intake, asynchronous processing, result review, and audit. Nginx serves the React SPA and proxies requests; FastAPI owns validation and authorization; MySQL is authoritative; Celery executes long work; Local FS or MinIO stores bytes.

```text
Browser
  -> Nginx :8080 local / :80 Compose host
     -> React SPA
     -> FastAPI :8010 local / backend-api:8000 Compose
        -> MySQL business + Celery runtime tables
        -> LocalStorage or MinIO
Celery workers
  -> MySQL + storage + processing Stages
```

Redis/Valkey is absent from the runtime. Token revocation, password-change checks, Agent memory, Job progress, SSE snapshots, broker messages, and task results all use MySQL.

## Deployment Reality

| Property | Local | Compose |
|---|---|---|
| User entry | Vite `5173` or Nginx `8080` | Nginx host `80` |
| API | `127.0.0.1:8010` | internal `backend-api:8000` |
| Storage | local by default, MinIO optional | internal MinIO |
| TLS | absent | absent; `443:8443` mapping has no listener/certificate |
| Runtime docs | available with development/debug | disabled by production settings |

Compose is production-shaped for network separation, non-root backend, health dependencies, and persistent volumes, but it is not a complete production platform.

## Ownership Boundaries

| Layer | Owns | Must not own |
|---|---|---|
| Nginx | entry, SPA fallback, proxy limits, SSE transport settings | business authorization or current TLS claims |
| Frontend | workflow UX, finite retries, query cache, download orchestration | final permission decisions or durable state |
| API route/dependency | HTTP validation, auth context, envelopes | duplicated domain transactions |
| Service | transactions, permissions, state transitions, storage compensation | UI state or broker-specific business truth |
| Worker/task | attempt claim and Stage orchestration | unconditional status writes or in-memory fallback |
| MySQL | business truth, audit rows, Celery broker/results | object bytes or high-throughput broker promises |
| Storage | opaque bytes keyed by bucket/key | user/project access rules |
| Stage | deterministic CAD/Excel transformation | platform auth, Job ownership, or public errors |

## Synchronous Request Path

```text
Browser -> Nginx -> FastAPI dependency auth -> service -> MySQL -> envelope response
```

List endpoints execute access filtering, `COUNT(*)`, stable ordering, and `LIMIT/OFFSET` in SQL. UI guards and Nginx locations do not replace API checks. Unhandled production errors are logged server-side and returned as a generic envelope.

## Asynchronous Request Path

```text
POST
  -> feature flag + input + access validation
  -> INSERT Job(queued, attempt=N) + COMMIT
  -> publish (job_id, attempt) to MySQL SQL transport
  -> worker conditional claim
  -> attempt-scoped JobSteps and progress
  -> source bytes -> Stage -> result bytes
  -> files + AnalysisResult + optional domain rows
  -> conditional terminal update
```

Dispatch compensation updates only the still-queued attempt. Retry increments attempt; stale messages and workers cannot claim or update a newer attempt. Worker-start recovery handles long-stale running Jobs but is not a continuous heartbeat.

## SSE Path

Native EventSource carries a short-lived HttpOnly `dwg_sse_token` cookie. FastAPI authenticates and authorizes the Job before streaming, polls MySQL, and emits current-attempt snapshots/progress/terminal events. Reconnect starts with a fresh authoritative snapshot; no event-ID replay or Pub/Sub guarantee exists.

Nginx disables SSE buffering/cache and extends read/send timeouts. The frontend leaves transient CONNECTING state to browser reconnection and closes on a terminal event or hard close.

## Download Path

```text
Bearer request -> permission check -> 300-second HMAC path
Bearer download -> permission + expiry + signature check -> storage stream
```

The frontend makes at most two single-file attempts and re-signs each time. Only network, 403, 408, 429, and 5xx failures are retried. ZIP downloads use authenticated POST streaming and do not share that re-sign loop. Database SHA-256 is the integrity reference.

## Storage Consistency

Objects written before a database commit are registered on the SQLAlchemy session. Rollback performs best-effort deletion; commit clears the compensation list. A successful output is exposed only after object and metadata persistence. There is no background object/database reconciler yet, so operations must detect missing and orphaned objects.

## MySQL and Celery

The broker is `sqla+mysql+pymysql://...`; the result backend is `db+mysql+pymysql://...`. Both derive from the effective application MySQL DSN. Celery engines use `READ COMMITTED`, bounded pools, pre-ping, LIFO, and recycle. `kombu_message(queue_id, timestamp, id, visible)` narrows message claims by queue.

SQL transport has no fanout remote control. Worker health is a Celery PID plus `worker_ready` marker, not `inspect`. Result rows expire after 24 hours; business Job history has no automated retention.

## Processing Boundaries

| Pipeline | Queue | Implementation | Delivery constraint |
|---|---|---|---|
| framework smoke | `report` | executable stub result | not a report Agent |
| DWG -> DXF | `dxf` | ODA service/task | flag off; external ODA/runtime/sample compatibility |
| DXF -> DWG | `dxf2dwg` | ODA service/task | flag off; external ODA/runtime/sample compatibility |
| DXF -> Excel | `dxf2excel` | service/task and local files | flag off; broken parent gitlink blocks clean clone |
| Excel Final | `excel_final` | isolated Stage + relational import | flag off; content schema and handbook DB required |
| Agent | `agent` | API/models only | task module is empty placeholder |
| Windows CAD | `cad` | configuration placeholder | no task, worker, service, or Compose node |

See [processing pipelines](processing-pipelines.md) for step names, formats, outputs, and enabling checks.

## Security Model

Global roles are `super_admin/admin/engineer/reviewer/operator/viewer/auditor`; project membership adds owner/engineer/reviewer/viewer scopes. Administrative access is explicit. File reads require administrator, uploader, or associated active-project membership. Results and reviews inherit the Job boundary; an unscoped Job is limited to administrator or creator.

Access tokens live in `sessionStorage`, so same-origin XSS remains a threat. Refresh and SSE tokens are HttpOnly cookies with SameSite=Lax. Public deployment requires real TLS and Secure cookies, which the current Compose file does not yet deliver.

## Health and Observability

- `/health` is liveness only.
- `/health/ready` independently reports MySQL and configured storage and returns 503 if either fails.
- Generic worker health proves broker connection, not pipeline dependencies.
- Local logs use `/tmp`; Compose uses container stdout/stderr.
- Metrics, distributed tracing, central logging, alerting, SLOs, and automated retention are not implemented.

## Architectural Constraints

- Do not add process-local correctness fallbacks when MySQL/storage fails.
- Do not split broker credentials from the authoritative MySQL DSN without an explicit migration design.
- Do not enable Agent/CAD flags while their tasks are placeholders.
- Do not claim clean-clone/Docker reproducibility until `Stages/dxf2excel` ownership is repaired.
- Do not claim HTTPS until Nginx has a tested TLS listener and certificate lifecycle.
- If worker scale exceeds bounded SQL transport, evaluate RabbitMQ while keeping MySQL as business truth.
