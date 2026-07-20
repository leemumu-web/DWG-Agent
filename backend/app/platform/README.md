# Backend platform seams

`platform/` contains technical mechanisms that business modules may use without owning their domain state.

| Package | Responsibility | Excludes |
|---|---|---|
| `config` | Pydantic settings, shared constants and validated sort keys | Project membership or workflow state transitions |
| `database` | SQLAlchemy base/session/pagination and idempotent seed command | Domain repositories and business transactions |
| `http` | Error/envelope transport shapes | Domain authorization decisions |
| `messaging` | Current Celery application and MySQL SQL-transport lifecycle | MySQL business rows as a message/result substitute |
| `observability` | Process logging setup | Monitoring stack claims |
| `security` | Password hashing and JWT primitives | Role/project access policy |
| `storage` | Local/MinIO byte adapters, safe paths and hashing | File registry permissions and compensation transactions |

The current messaging implementation truthfully retains the MySQL SQLAlchemy broker/result backend. RabbitMQ, transactional outbox and Beat remain target contracts, not implemented services. Platform code must never import `app.modules`; composition belongs in `app.bootstrap`.
