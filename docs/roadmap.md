# Roadmap

> Chinese mirror: [zh/roadmap.md](zh/roadmap.md)

## Current Baseline

The current baseline is an integrated platform, not a skeleton:

- MySQL-only runtime state and Celery SQL transport.
- Nginx, FastAPI, React, Local/MinIO storage.
- Project/file/drawing/job/result/review/audit workflows.
- DWG -> DXF, DXF -> DWG, DXF -> Excel, and Excel Final pipelines.
- Attempt-safe retries, SQL pagination, SSE polling, signed downloads.
- Real MySQL/MinIO/Compose and browser closure tests.

Redis removal is complete at runtime. Migration names may retain historical wording; active docs and configuration must not describe Redis as deployed.

## Delivery Priorities

### P0: Maintain Reliability

- Keep migrations valid from an empty MySQL schema.
- Keep the Kombu queue-order index and `READ COMMITTED` transport settings.
- Preserve attempt-conditional worker updates and storage compensation.
- Run full backend, frontend, and browser regression on state-machine changes.
- Keep English/Chinese API docs generated from OpenAPI.

### P1: Complete Agent Subsystem

`AGENT_ENABLED=false` remains required until all are implemented:

- bounded LLM/MCP execution;
- MySQL-backed conversation retention and cleanup;
- task body and cancellation;
- run/step permissions and project/file validation;
- prompt/tool audit and redaction;
- integration and adversarial tests.

Do not enable the flag merely because routes and worker infrastructure exist.

### P1: Production CAD Worker

- Authenticate the Windows CAD worker channel.
- Define idempotency keys and attempt semantics across the remote boundary.
- Upload/download through storage rather than shared host paths.
- Add timeout, cancellation, heartbeat, and stale-worker recovery.
- Validate real DWG samples and license/runtime prerequisites.

### P1: Operations

- Add structured metrics for queue depth, claim latency, task duration, retry count, storage errors, and DB pool pressure.
- Add backup/restore drills for MySQL and MinIO volumes.
- Add log correlation by request ID, job ID, and attempt.
- Define retention for audit rows, broker/result rows, and derived files.

### P2: Broker Scale Decision

The SQLAlchemy broker is intentionally bounded. If deployment needs many worker replicas or higher message throughput, benchmark and evaluate RabbitMQ. Such a change must preserve MySQL as business truth and must not reintroduce cached job state as authoritative.

### P2: UX and Accessibility

- Continue replacing implementation-specific icon locators with accessible names.
- Add deterministic E2E fixtures instead of relying on pre-existing database rows.
- Add mobile and narrow-layout screenshots for core workflows.
- Improve large audit/file views with server-driven filters.

## Acceptance Gates

A milestone is complete only with evidence for:

1. Nginx -> FastAPI routing and auth.
2. Empty-schema migrations and application credentials.
3. MySQL broker -> Celery -> attempt-scoped state.
4. Storage object write/read, signed download, and SHA-256.
5. Storage outage 503 and recovery without API restart.
6. Frontend refresh/retry/download behavior in a real browser.
7. Permission isolation across users and projects.
8. English/Chinese documentation synchronization.

## Explicit Non-Goals

- Redis/Valkey as cache, session store, progress store, Pub/Sub, or broker.
- SQLite runtime deployment.
- Publicly exposing backend, MySQL, or MinIO in Compose.
- Marking placeholder Agent/CAD infrastructure as a delivered feature.
