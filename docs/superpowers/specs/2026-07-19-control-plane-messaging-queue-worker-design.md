# Control-plane messaging, queue and worker framework

## Context and verified boundary

The target architecture describes RabbitMQ, Celery Beat and a Windows Node Agent.
The current deployed Linux framework does **not** provide those components: Celery
uses Kombu's MySQL SQLAlchemy transport (`sqla+mysql+pymysql`), jobs are directly
dispatched after their database transaction, and SSE reads authoritative job rows.
Remote Celery control and worker event broadcasts are disabled by design because the
SQL transport cannot provide its fanout exchange.

This change must therefore add an observable control plane around the existing
runtime, not pretend that the target infrastructure has already been deployed.

## Goals

1. Persist worker lifecycle/activity records and control-plane events in MySQL.
2. Present actual queue, worker and message state to administrators in the existing
   infrastructure console.
3. Reserve process/queue identities for future dispatch and maintenance workers,
   without routing production work into unimplemented core functions.
4. Publish an explicit, versioned communication contract for the future Windows
   Node Agent and CAD runner.

## Non-goals

- No RabbitMQ migration, Celery Beat scheduler, durable transactional outbox, or
  Windows Node Agent implementation in this branch.
- No command dispatch from the browser, remote process control, lease fencing, or
  CAD runner execution protocol.
- No claim that SQL-broker counts are a complete view of in-flight tasks.

## Data model

`worker_runtimes` is one upserted record per live worker identity. It records the
worker name, hostname/PID, declared queues/concurrency, lifecycle status and last
observed activity. `last_seen_at` is updated by lifecycle/task signals; it is an
activity heartbeat, not an independent liveness probe. The API derives `stale`
when it exceeds the configured threshold.

`control_plane_events` is an append-only operational event log. Worker lifecycle
and task-routing events are recorded here. The event shape also supports future
agent-to-control and control-to-agent messages, but those directions remain
contract-only until an agent is implemented.

`platform_messages` is a user-facing projection for administrators. It carries
severity, title/body, status and a related control-plane event. Initially only
system-generated operational messages are created; there is no user-to-user chat.

## Queue diagnostics

The overview identifies the broker as `mysql_sqlalchemy`. It combines:

- business job counts grouped by the actual Celery queue/pipeline;
- ready SQL-transport message counts from `kombu_message`, only when the Kombu
  tables are available; and
- registered worker records.

Ready-message counts exclude reserved/in-flight work and are labelled accordingly.
The API must return an availability/source field rather than invent a zero value
when Kombu's tables cannot be read.

## Multi-worker process topology

The current business workers remain unchanged. Two queues are added as deliberate
placeholders:

- `dispatch`: future durable outbox/command delivery worker;
- `maintenance`: future scheduled reconciliation/retention worker.

Both processes consume their named queue and register in the control plane, but no
production task is routed to them. Local scripts and the worker Compose profile
start them with concurrency 1, making the topology verifiable without activating
unfinished core logic.

## API and UI

Admin/auditor endpoints expose an overview, paginated event log, messages and a
read acknowledgement. A communication-contract endpoint returns the future Windows
Node Agent interface and clearly marks it `pending`.

The existing `/admin/infrastructure` page gains a “运行与通信” tab with refreshable
cards, queue/worker tables, message timeline and a boundary card listing what is
implemented versus pending. There are no misleading action buttons for unavailable
RabbitMQ, Beat or Windows-node controls.

## Verification

Backend tests cover authorization, SQL-broker disclosure, persisted lifecycle
records/events/messages and stale-state derivation. Frontend contract and browser
tests cover the tab, actual-state labels and pending contract presentation. Database
migration and generated API documentation are updated together.
