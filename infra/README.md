# 基础设施目录

`infra/` 按运行责任分类，只保存部署资产、目标契约与验证入口。根 `compose.yaml` 是当前服务拓扑事实源；目录存在不表示服务已部署。

## 分类

| 目录 | 状态 | 内容 |
|---|---|---|
| `gateway/nginx/` | implemented | React SPA、FastAPI、SSE 的 Nginx 配置与本地运行日志边界 |
| `database/mysql/` | implemented | 首次启动平台授权与 `hardware_handbook` 初始化 SQL |
| `storage/minio/` | implemented in Compose | MinIO 对象存储边界；字节实际位于命名卷 |
| `messaging/rabbitmq/` | target placeholder | RabbitMQ 目标拓扑；当前 Compose 不含该服务 |
| `operations/backup/` | partial | 现有手动备份能力与自动化缺口 |
| `operations/monitoring/` | placeholder | 指标、日志、告警目标契约 |
| `verification/` | implemented | 静态/活动基础设施验证入口 |

## 当前 Compose

核心服务为 `nginx`、`backend-api`、`mysql`、`minio` 与 `worker-report`。`workers` profile 增加 `worker-maintenance`、`worker-dispatch`、`worker-dxf`、`worker-dxf2dwg`、`worker-dxf2excel`、`worker-dxf-classification`、`worker-excel-final` 与占位 `worker-agent`。

```bash
docker compose up -d
docker compose --profile workers up -d
docker compose config --quiet
bash infra/verification/verify.sh
```

MySQL 和 MinIO 不发布宿主端口。Compose 当前只发布 Nginx HTTP；没有完成 TLS。生产 Compose 使用 MinIO，Celery broker/result URL 从 MySQL DSN 派生。Agent 功能保持禁用；RabbitMQ、Outbox、Beat 和 Windows 执行面尚未部署。

详细部署和恢复边界见[部署指南](../docs/guides/deployment.md)与[运维指南](../docs/guides/operations.md)。
