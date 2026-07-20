# RabbitMQ 目标契约

Status: target contract, not deployed in current Compose.

Current runtime: MySQL SQLAlchemy Celery transport.

RabbitMQ 是架构图中的消息基础设施目标，不是当前运行事实。当前 `compose.yaml` 没有 `rabbitmq` 服务、volume、healthcheck、凭据、vhost、policy、应用连接配置或恢复测试；业务状态仍由 MySQL Job/Workflow 账本负责。

## 接入前置证据

- 固定版本镜像、非默认凭据、独立 vhost、最小权限与 secret 轮换方案。
- durable queue/exchange、publisher confirm、consumer ack、prefetch、dead-letter 与过期策略。
- Compose healthcheck、持久卷、备份边界和节点重启后的消息恢复测试。
- FastAPI commit/outbox/投递顺序、幂等 consumer 与 attempt fencing 回归。
- worker 断连、broker 重启、网络分区和重复投递的端到端证据。
- 迁移/回滚 ADR；不得通过切换 broker 丢弃已排队 SQL transport 消息。

Completion evidence required: Compose service, healthcheck, durable volume, application configuration, worker recovery tests and operations runbook.
