# Jobs module

## Responsibility

This module owns the authoritative lifecycle for asynchronous work: Job creation,
attempt-fenced claim/progress/terminal transitions, Result registration, human
Review records, current-state SSE polling, post-commit Celery dispatch and stale
worker recovery.

## Public boundary and owned data

Other business modules import only `app.modules.jobs.interface`. HTTP composition
is deliberately excluded from that interface. The module owns four MySQL tables:
`jobs`, `job_steps`, `analysis_results` and `review_records`. Celery/Kombu transport
tables remain platform infrastructure; result files remain in the files module.

## Internal layout

- `creation.py`: pipeline selection, creation, batch creation and request-key reuse.
- `lifecycle.py`: guarded status and attempt transitions, including cancel/retry.
- `dispatch.py`: pipeline routing and compensation after a definite broker error.
- `event_stream.py`: latest-row event payloads and bounded short-session polling.
- `access.py`: creator/project visibility and write/review authorization.
- `stub_execution.py`: executable framework smoke path with an explicit placeholder result.
- `recovery.py`: Celery return summaries and stale-running Job reconciliation.
- `reviews.py`: Review record creation.
- `routes/`: query, command, event, Result and Review HTTP use cases.

## Failure and transaction rules

Workers may mutate a Job only when both status and attempt match. Pending Step,
Result and file rows share the caller transaction, so losing an attempt guard
rolls them back. Dispatch occurs only after the Job commit; the current direct
dispatch implementation conditionally marks a still-queued attempt failed when
the broker call definitely raises. It does not claim atomic MySQL/broker delivery.

## Current versus target architecture

The current broker is Celery's MySQL SQLAlchemy transport, not RabbitMQ. There is
no transactional Outbox, lease/fencing-token model or durable SSE event table.
SSE persists only the latest event in `jobs.progress_data`, polls with fresh MySQL
sessions and cannot replay a numbered event history. These are explicit target
gaps from `结构图/架构设计.txt`, not hidden implementations. The framework stub
remains intentionally non-production and says so in its generated Result.

## Verification

Core regression lives in `backend/tests/jobs/test_job_*.py`,
`backend/tests/jobs/test_jobs_module_contract.py`, Result/Review security tests and
`backend/tests/architecture/test_jobs_boundaries.py`. Runtime HTTP, ORM and Celery
names are also locked by `docs/architecture/runtime-contract.json`.
