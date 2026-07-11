# 路线图

> 英文对应文档：[../roadmap.md](../roadmap.md)

## 当前基线

当前基线是已集成平台，不是骨架：

- 仅 MySQL 运行状态和 Celery SQL transport。
- Nginx、FastAPI、React、Local/MinIO 存储。
- 项目、文件、图纸、任务、结果、复核、审计工作流。
- DWG -> DXF、DXF -> DWG、DXF -> Excel、Excel Final。
- attempt-safe 重试、SQL 分页、SSE 轮询、签名下载。
- 真实 MySQL/MinIO/Compose 和浏览器闭环测试。

Redis 已从运行时完整移除。迁移文件名可保留历史描述；当前文档和配置不得把 Redis 描述为已部署组件。

## 交付优先级

### P0：保持可靠性

- 保持空 MySQL schema 可执行完整迁移。
- 保留 Kombu 队列顺序索引和 `READ COMMITTED`。
- 保留 attempt 条件更新和存储补偿。
- 状态机变更运行后端、前端和浏览器全量回归。
- 中英文 API 文档继续由 OpenAPI 同步生成。

### P1：完成 Agent 子系统

在以下全部完成前保持 `AGENT_ENABLED=false`：

- 有界 LLM/MCP 执行；
- MySQL conversation retention 和 cleanup；
- task body 和 cancellation；
- run/step 权限与项目/文件校验；
- prompt/tool 审计和脱敏；
- 集成与对抗测试。

不能因为路由和 worker 基础设施存在就启用功能开关。

### P1：生产 CAD Worker

- 认证 Windows CAD worker 通道。
- 跨远端边界定义 idempotency key 和 attempt 语义。
- 通过 storage 上传下载，禁止共享 host path。
- 增加 timeout、cancel、heartbeat、stale recovery。
- 用真实 DWG 和许可证/运行环境验收。

### P1：运维

- 增加 queue depth、claim latency、task duration、retry、storage error、DB pool 指标。
- 演练 MySQL/MinIO 卷备份恢复。
- 用 request ID、job ID、attempt 关联日志。
- 定义 audit、broker/result、derived file 保留策略。

### P2：Broker 扩展决策

SQLAlchemy broker 设计为受限规模。若需要大量 worker replica 或更高吞吐，基准测试并评估 RabbitMQ。变更仍须以 MySQL 为业务事实源，不得重新把缓存任务状态作为权威。

### P2：UX 与可访问性

- 继续用可访问名称替换依赖图标 class 的定位器。
- E2E 自建确定性 fixture，不依赖预存数据库行。
- 为核心工作流补移动和窄屏截图。
- 大型 audit/file 视图增加服务端过滤。

## 验收门槛

里程碑只有在以下证据齐全时完成：

1. Nginx -> FastAPI 路由和认证。
2. 空 schema 迁移和应用凭据。
3. MySQL broker -> Celery -> attempt-scoped 状态。
4. 存储写读、签名下载和 SHA-256。
5. 存储宕机 503，恢复无需重启 API。
6. 真实浏览器中的 refresh/retry/download。
7. 用户/项目权限隔离。
8. 中英文文档同步。

## 明确非目标

- Redis/Valkey 作为缓存、session、进度、Pub/Sub 或 broker。
- SQLite 运行时部署。
- Compose 公开 backend、MySQL 或 MinIO。
- 把占位 Agent/CAD 基础设施标记为已交付功能。
