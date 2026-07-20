# 监控与可观测性

Status: target contract; centralized monitoring is not implemented.

当前证据只有 API health/readiness、worker ready marker、控制平面事件、MySQL/存储管理视图以及进程/容器日志。它们不构成 metrics、trace、集中日志、告警或 SLO。

目标至少覆盖 API 延迟/错误、MySQL pool、broker queue/age、worker heartbeat/task、Job stage duration、MinIO 容量/错误、Nginx 4xx/5xx/499、归档/扫描失败，并为每个告警定义 owner、阈值、runbook、抑制和保留策略。
