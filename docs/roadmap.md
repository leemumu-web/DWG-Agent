# Roadmap

> Chinese mirror: [zh/roadmap.md](zh/roadmap.md)

## Baseline

As audited from `main@d178fcf` on 2026-07-11, the repository contains a real React/FastAPI/MySQL/Celery/storage platform, not only a skeleton. It has authentication/RBAC, project/file/Job/result/review/audit models, 71 OpenAPI paths, attempt-safe Job execution, Local/MinIO adapters, four conversion service paths, Excel Final relational import, and broad automated tests.

This does not make every directory production-ready. Feature flags are off by default, Agent/CAD tasks are placeholders, Compose lacks TLS/operations automation, and `Stages/dxf2excel` cannot be reconstructed from a clean clone.

Redis/Valkey removal is complete in active runtime architecture. Historical migration or explanation text may mention it only as removed history.

## P0: Repository Reproducibility

Repair `Stages/dxf2excel` ownership first because it affects backend dependency resolution and Docker builds.

Completion criteria:

- choose a normal tracked directory or restore valid `.gitmodules` metadata;
- pin a reachable reviewed commit/source;
- remove reliance on the currently populated but untracked nested working tree;
- pass a brand-new clone, `uv sync --locked`, Stage tests, backend tests, and Docker build;
- document source license/provenance and update the Stage component README.

## P0: Honest HTTP/TLS Deployment

Choose one explicit short-term state:

- remove the dead `443:8443` mapping and document HTTP-only private use; or
- implement container `8443 ssl`, read-only certificate mounts, HTTP redirect, HSTS after validation, renewal/expiry checks, and secure-cookie browser tests.

Completion requires a real TLS handshake and refresh/SSE/download flow through HTTPS. A Compose port mapping alone is not evidence.

## P0: Reliability Gates

- Keep MySQL as business truth and prevent process-memory fallback.
- Preserve status + attempt predicates across every Job state write.
- Keep permission checks on derived results and batch metadata.
- Maintain object rollback compensation and add periodic object/database reconciliation.
- Test clean migration, downgrade where supported, and representative populated upgrades.
- Run real broker/storage/browser workflows after changes to tasks, storage, auth, or downloads.

## P1: Operations Baseline

Implement rather than merely document:

- coordinated MySQL + MinIO backups with encryption and retention;
- scheduled restore tests with checksum evidence and measured RPO/RTO;
- metrics for API latency/errors, DB pool, queue depth/age, Job duration/failure, storage, and worker health;
- centralized structured logs and request/Job/attempt correlation;
- actionable alerts, dashboards, runbook links, and incident retention;
- capacity tests and explicit connection/worker/object limits;
- retention/deletion jobs that preserve database/object consistency.

## P1: Processing Hardening

- Build a representative, licensed DWG/DXF/Excel corpus with expected outputs and failure classes.
- Sandbox or isolate ODA/Excel processing of untrusted files; add CPU, memory, disk, process, and output limits.
- Define Stage version metadata in every result and migration behavior for changed algorithms.
- Add malware scanning/quarantine before complex file processing.
- Verify cancellation terminates child work where safe, not only Job state updates.
- Add deterministic object reconciliation and retry policy for partial external failures.

## P1: Agent Subsystem

Keep `AGENT_ENABLED=false` until all criteria pass:

- replace `tasks_agent.py` placeholder with a bounded, cancellable task;
- implement model/MCP client timeouts, retries, tool allowlist, payload/result validation, and secret isolation;
- persist only bounded memory and safe step summaries; do not expose hidden reasoning or tool secrets;
- enforce creator/admin/project access on run, steps, source and output files;
- audit model/tool selection and resulting artifacts without logging sensitive payloads;
- cover prompt/tool injection, unauthorized tool calls, stale attempts, cancellation, dependency outage, and real E2E.

## P1: Windows CAD Worker

Keep `CAD_WORKER_ENABLED=false` until all criteria pass:

- define authenticated, replay-resistant worker registration/dispatch protocol;
- implement the Celery CAD task and an actual Windows service, not only `CAD_WORKER_API_BASE`;
- make dispatch idempotent by Job attempt and enforce timeout/cancellation;
- securely transfer source/result artifacts and verify SHA-256;
- map worker errors to safe stable codes and retain server-side diagnostics;
- add Compose/external-service topology, health, upgrade, and real ZWCAD sample tests.

## P2: Broker and Scale Decision

Benchmark the current MySQL SQL transport with realistic queue count, Job duration, worker concurrency, API load, and failure recovery. Record connection consumption and broker table growth. If requirements exceed it, adopt RabbitMQ or another fit broker through an ADR while leaving Job/progress/result authorization in MySQL.

Do not restore Redis as a business state source. A future cache would need an explicit consistency model and may never authorize or determine Job truth.

## P2: Identity and Security Maturity

- Refresh-token rotation, session/device inventory, forced session revocation, and key rotation.
- External identity/SSO and MFA for privileged roles.
- Tamper-evident audit export with independent credentials and retention.
- Dependency/container/SBOM scanning and patch SLA.
- CSP tightening, XSS-focused tests, and secure file preview isolation.
- Least-privilege database users split by API, worker, migration, broker, and audit needs where operationally justified.

## P2: User Experience

- Accessibility audit for keyboard, focus, labels, contrast, tables, dialogs, progress, and errors.
- Clear offline/reconnecting/expired-session/storage-outage states.
- Large-list and large-file performance tests.
- Consistent retry/cancel/download behavior across every pipeline.
- Operator-visible attempt history without confusing stale steps with the active attempt.

## Documentation Acceptance

Every delivery changes the relevant English/Chinese pair, generated API reference, root status matrix, and component README. Claims must state code, default flag, dependencies, verification environment/date, and residual limits.

`make docs-check` must reject stale API output, broken links, pair-structure drift, obsolete local ports, current-branch references to `codex`, false TLS claims, and missing known repository blockers.

## Explicit Non-Goals

- Redis/Valkey as session, authorization, progress, Pub/Sub, broker, result, or fail-open store.
- In-process state used to hide MySQL, broker, or storage failure.
- Enabling placeholder Agent/CAD features for demonstration without their safety gates.
- Treating mocked/SQLite tests as proof of MySQL/MinIO/Celery production behavior.
- Treating HTTP Compose, uncoordinated backups, or a populated local gitlink as production readiness.
- Large rewrites that break the buildable/testable vertical path without staged migration evidence.
