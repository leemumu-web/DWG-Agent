# Backend platform seams

`platform/` contains technical mechanisms that business modules may use without owning their domain state.

| Package | Responsibility | Excludes |
|---|---|---|
| `config` | Pydantic settings, shared constants and validated sort keys | Project membership or workflow state transitions |
| `database` | SQLAlchemy base/session/timestamp mixins/pagination | Domain repositories, seed data and business transactions |
| `http` | Error/envelope transport shapes | Domain authorization decisions |
| `messaging` | Current Celery application, MySQL SQL-transport lifecycle and generic worker-ready callbacks | Job/Result ORM, recovery policy or MySQL business rows as a message/result substitute |
| `observability` | Process logging setup | Monitoring stack claims |
| `security` | Password hashing and JWT primitives | Role/project access policy |
| `storage` | Local/MinIO byte adapters, safe paths, hashing, configured adapter factory and health | File registry permissions, metadata and compensation transactions |
| `time.py` | Authoritative `Asia/Shanghai` business clock and persisted-wall-time normalization | Domain scheduling or retention policy |

The current messaging implementation truthfully retains the MySQL SQLAlchemy broker/result backend. RabbitMQ, transactional outbox and Beat remain target contracts, not implemented services. Job stale recovery registers through the generic callback seam before the readiness marker is published. Platform code never imports `app.modules`; composition belongs in `app.bootstrap`.
