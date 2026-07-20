# Storage interaction, worker operations and frontend resilience hardening

## Verified baseline

MySQL is the business source of truth. File bytes are stored through the configured
storage adapter (`local` in the local stack, `minio` in Compose production). Existing
file transfers already persist a MySQL intent before an object write and settle it
after success, failure or compensation. Storage consistency scans run asynchronously
on the report worker and persist findings.

The existing maintenance worker is a queue/process identity only. It has no safe
operator action. The current frontend has many local error alerts, but no application
error boundary or persistent offline/reconnection indication.

## Design

### Storage and database interaction

Reuse the existing infrastructure overview as the read-only storage readiness
contract. The data console will display the active backend, per-bucket MySQL/object
counts and a clear distinction between local development storage and MinIO. No UI
will claim MinIO is active when the configured backend is local. The existing
asynchronous consistency scan and two-step remediation remain the only mutating
storage controls.

### Worker framework

Implement one bounded maintenance task: stale-running-job reconciliation. It calls
the existing conditional/attempt-fenced recovery function, writes a control-plane
event, and returns a concise result. An admin endpoint queues this task onto the
existing `maintenance` queue. The endpoint records a queue event before dispatch and
marks it failed if broker enqueue throws; it does not introduce Celery Beat, a durable
outbox, RabbitMQ, or an unsafe generic task runner.

The runtime console shows the action as a manual recovery operation, explains that
it only affects jobs already older than the configured stale threshold, and refreshes
the worker/event view after successful submission.

### Frontend resilience

Add an application-level React error boundary with a recovery action and an
online/offline banner driven by browser connectivity events. Configure React Query to
avoid retrying authorization/validation failures while retaining bounded retry for
transient network/5xx failures. These protections apply uniformly to all existing
pages without changing page-specific business workflows.

The dashboard also derives an ordered “today's work suggestions” list from the
existing project, failed-job and pending-review queries. Every suggestion is a normal
keyboard-accessible route action; recent-job rows are semantic buttons rather than
click-only containers.

## Acceptance criteria

- Admin can queue only the bounded stale-job reconciliation; auditor gets 403.
- The maintenance worker records the executed recovery count in MySQL events.
- The runtime page exposes both the manual recovery boundary and its pending status.
- Infrastructure overview visibly reports active storage backend and bucket counts.
- An uncaught route error renders a recoverable Chinese fallback; browser offline and
  reconnect state are visible without blocking the current page.
- Documentation, generated API reference, backend tests, frontend build/browser
  tests and live status are updated without claiming unimplemented services.
