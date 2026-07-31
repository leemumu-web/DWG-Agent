# Jobs module

## Responsibility

This module owns the authoritative lifecycle for asynchronous work: Job creation,
attempt-fenced claim/progress/terminal transitions, Result registration, human
Review records, current-state SSE polling, durable Celery dispatch and stale
worker recovery.

## Public boundary and owned data

Other business modules import only `app.modules.jobs.interface`. HTTP composition
is deliberately excluded from that interface. The module owns five MySQL tables:
`jobs`, `job_dispatches`, `job_steps`, `analysis_results` and `review_records`.
Celery/Kombu transport tables remain platform infrastructure; result files remain
in the files module.

## Internal layout

- `creation.py`: pipeline selection, creation, batch creation and request-key reuse.
- `lifecycle.py`: guarded status and attempt transitions, including cancel/retry.
- `routes/commands.py`：公共 Job 命令；工作流管理的 DXF 拆板拒绝从这里创建或重试，避免绕过阶段血缘和三次 attempt 预算。
- `outbox.py`: same-transaction staging, group leases, retry settlement and draining.
- `dispatcher.py`: resilient single-purpose outbox process entry point.
- `dispatch.py`: immutable message encoding and stable Celery task IDs.
- `event_stream.py`: latest-row event payloads and bounded short-session polling.
- `diagnostics.py`: safe current-attempt projection for operator task diagnosis;
  it whitelists business metrics and never returns raw worker logs or paths.
- `access.py`: creator/project visibility and write/review authorization.
- `stub_execution.py`: executable framework smoke path with an explicit placeholder result.
- `recovery.py`: Celery return summaries and stale-running Job reconciliation.
- `reviews.py`: Review record creation.
- `routes/`: query, command, event, Result and Review HTTP use cases.
- `models.py`: Job, JobDispatch, JobStep, AnalysisResult and ReviewRecord ownership.
- `schemas.py`: stable create/read/bulk-cancel/Result/Review HTTP data contracts.
- `tasks.py`: the historical report-queue compatibility task that delegates to
  `stub_execution.py`; it does not contain a second Job state machine.
- `interface.py`: the only cross-domain import surface, including execution summaries,
  stale recovery and the local stub entry point.

## Failure and transaction rules

Workers may mutate a Job only when both status and attempt match. Pending Step,
Result and file rows share the caller transaction, so losing an attempt guard
rolls them back. Each new Job attempt stages one `job_dispatches` intent in that
transaction. The dispatcher first commits expired-lease recovery, then commits
a pure `SKIP LOCKED` claim lease before broker I/O; ambiguous
delivery may repeat a stable task ID, while the worker's status/attempt claim
keeps business side effects effective once.

## Current versus target architecture

The current broker is Celery's MySQL SQLAlchemy transport, not RabbitMQ. The
transactional outbox and lease token absorb the application/broker dual-write
window, but do not claim broker exactly-once delivery. SSE still persists only
the latest event in `jobs.progress_data` and cannot replay numbered history. The
framework stub remains intentionally non-production and says so in its Result.

## Verification

Core regression lives in `backend/tests/jobs/test_job_*.py`,
`backend/tests/jobs/test_jobs_module_contract.py`, Result/Review security tests and
`backend/tests/architecture/test_jobs_boundaries.py`. Runtime HTTP, ORM and Celery
names are also locked by `docs/architecture/runtime-contract.json`.
The historical `/{job_id}/logs` route now returns structured, sanitized
diagnostics under its compatibility key rather than a placeholder or server log.
