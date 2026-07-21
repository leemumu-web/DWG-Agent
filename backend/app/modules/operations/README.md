# Operations domain

Operations is split by use-case owner rather than by transport layer:

- `audit`: append/read audit facts;
- `daily_archive`: signed frozen previews and non-destructive archive execution;
- `data_catalog`: read-only MySQL/MinIO operational projections and health probes;
- `storage_reconciliation`: registry/object comparison and guarded remediation;
- `control_plane`: worker observations, queue projections, events and messages.

`router.py` composes `/data-admin` in the historical operation order so static/dynamic route
precedence and OpenAPI operation IDs do not change. Other domains may write audit or control-plane
facts only through the corresponding `interface.py`.

The current broker reported by this domain is MySQL SQLAlchemy transport. RabbitMQ, a transactional
Outbox, Celery Beat, automatic off-site backup and complete metrics/alerting remain target gaps.
