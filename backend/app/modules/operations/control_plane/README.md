# Control plane

The control plane persists best-effort worker lifecycle observations, administrator messages and
operational events, then projects business-job and SQL-broker queue counts. It also exposes the
draft Windows Node Agent contract and an explicitly requested stale-job recovery command.

These records are observability facts, not worker leases. RabbitMQ, Beat, durable Outbox, node
authentication, lease renewal and fencing tokens are still pending. Reserved queues display as
contract-only and do not prove that their executors exist.
