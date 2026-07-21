# Control plane

`models.py` owns worker runtime, administrator message and operational event
rows. `service.py` queries those rows plus business Job and Kombu SQL queue
counts. `routes.py` exposes the authenticated read/message/recovery API,
`tasks.py` runs explicit stale-job reconciliation, and `interface.py` is the
worker-signal/bootstrap seam.

The control plane persists best-effort worker lifecycle observations, administrator messages and
operational events, then projects business-job and SQL-broker queue counts. It also exposes the
draft Windows Node Agent contract and an explicitly requested stale-job recovery command.

These records are observability facts, not worker leases. RabbitMQ, Beat, durable Outbox, node
authentication, lease renewal and fencing tokens are still pending. Reserved queues display as
contract-only and do not prove that their executors exist.
