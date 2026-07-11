# 运维

## 运维边界

这是当前仓库本地与 Compose 拓扑的 runbook。它不声称监控、备份调度、TLS、日志汇聚、滚动升级或灾难恢复已经自动化。操作员必须在 Git 外记录环境凭据、存储位置、保留期、责任人和恢复目标。

## 启动、状态与停止

本地受管拓扑：

```bash
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/stop-all.sh
```

`start-all.sh` 按需构建前端，启动五个已实现队列 worker、FastAPI `8010` 和本地 Nginx `8080`。`start-dev.sh` 用 Vite 替代 Nginx/静态服务。脚本按 Celery app、queue 和 node name 识别 worker；pidfile 只是跟踪辅助，不是唯一进程身份。

Compose 拓扑优先使用带环境预检的包装脚本：

```bash
bash scripts/docker.sh check
bash scripts/docker.sh up
bash scripts/docker.sh up-workers
bash scripts/docker.sh status
bash scripts/docker.sh smoke
bash scripts/docker.sh logs
```

仅需要转换 worker 时才执行第二个 `up`。它也会启动占位 `worker-agent`；其健康不表示 Agent task 存在。

## 健康信号解释

| 信号 | 能证明 | 不能证明 |
|---|---|---|
| `GET /health` 200 | FastAPI 进程可响应 | MySQL、存储、worker 或 Stage 可用 |
| `GET /health/ready` 200 | MySQL 和已配置存储探测通过 | 特定 feature flag、queue worker、ODA 或手册查询可用 |
| worker healthy | Celery PID 存活且发出 `worker_ready` | 队列 task 实现或 Stage 依赖可用 |
| MySQL healthy | server 接受 root ping | 应用授权、迁移 head、broker 索引或查询延迟正确 |
| MinIO live | server 进程响应 | bucket、凭据、对象持久化或下载授权可用 |
| Nginx `HTTP_PORT` 可达 | HTTP 网关和静态根响应 | TLS 已配置；Compose 不发布 443 |
| `GET /api/v1/system/infrastructure` | 管理员可见 DB/storage/目录即时概览 | 自动备份、对象与 metadata 完全一致或恢复可用 |

应同时使用 readiness 和一笔代表性业务交易。不要把 `/health` 改成深度依赖检查；依赖中断时 liveness 仍需有用。

## 日志与首次响应

本地日志位于 `/tmp`：

```bash
tail -n 200 /tmp/dwg-agent-backend.log
tail -n 200 /tmp/dwg-agent-worker-report.log
tail -n 200 /tmp/dwg-agent-nginx-error.log
```

Compose 日志：

```bash
docker compose logs --since=15m backend-api worker-report mysql minio nginx
docker compose --profile workers logs --since=15m worker-dxf worker-dxf2dwg worker-dxf2excel worker-excel-final
```

重启前保留首个异常、request ID、Job ID/attempt、worker node、依赖状态和时间戳。当前日志没有集中保留或关联后端；`/tmp` 日志会在重启时丢失，容器日志保留取决于 Docker logging driver。

## 数据库操作

本地 MySQL 命令：

```bash
bash scripts/db.sh status
bash scripts/db.sh check
bash scripts/db.sh tables
bash scripts/db.sh backup /secure/path/dwg_agent.sql.gz
bash scripts/db.sh migration-test
```

`migration-test` 创建并删除临时 schema。本轮已证明从空库升级到 `e4a1c7f2b930` 并得到 25 张模型表；它不测试 downgrade、Celery runtime 按需建表或生产数据迁移时长。

迁移前：

1. 验证最近数据库和对象存储备份可恢复。
2. 停止应用写入，或进入有记录的维护窗口。
3. 针对准确 revision 运行 `migration-test`。
4. 仅执行一次 `alembic upgrade head`，监控耗时与锁，然后运行 readiness/schema 检查。
5. 启动 worker/API，并执行一笔代表性上传/处理/下载。

当前 `drawings`/`drawing_versions` 循环 FK 会产生 Alembic autogenerate 排序 warning。`alembic check` 仍应报告无新操作；不能因为已知 warning 就忽略新的 operation。

## 备份与恢复

仓库有本地 MySQL helper和 Compose `scripts/docker.sh` 备份/恢复命令，但没有调度、离机复制、加密、PITR 或自动恢复演练。可恢复集合必须包括：

| 数据 | 必要内容 |
|---|---|
| 应用 MySQL | 全部业务表、`alembic_version` 和当前 Celery runtime 表 |
| 对象存储 | 每个已配置 bucket，包括 original、derived、report、按需 temporary 和 DXF bucket |
| 手册库 | `hardware_handbook` schema/data 或独立管理的权威源 |
| 密钥/配置 | 部署值的加密副本；禁止提交真实 `.env.docker` |
| 证据 | 备份时间、应用 revision、迁移 head、对象快照标记、checksum 和恢复测试结果 |

默认 Compose 备份：

```bash
bash scripts/docker.sh backup /secure/backups/dwg-agent-YYYY-MM-DD
```

该命令导出 `dwg_agent` 与 `hardware_handbook`，归档 MinIO volume 并生成 `SHA256SUMS`。它不停止 writer，也不生成数据库与对象的原子快照；严格恢复点必须在维护窗口先停止写入。Compose service name 不支持 `worker-*` wildcard，人工停服时必须逐个列出服务。

只在隔离或维护环境恢复；脚本要求全部 Compose 服务已停止，并会清空目标 MinIO volume 后解包：

```bash
bash scripts/docker.sh down
bash scripts/docker.sh restore /secure/backups/dwg-agent-YYYY-MM-DD
bash scripts/docker.sh up
bash scripts/docker.sh smoke
```

随后运行 `alembic current`，把代表性 `files.sha256` 与字节比较，并执行完整工作流。恢复 broker row 可能重新引入 queued delivery；启动 worker 前检查 queued/running Job 和 broker table。脚本不备份 `app_var`、live secret 或镜像，恢复成功也不等于已有 RPO/RTO 证据。

## 存储事故

存储不可用时：

1. 确认 `/health` 保持 200，`/health/ready` 报告 database `ok`、storage `error`。
2. 停止提交新文件 Job；不要切换到未规划的本地 fallback。
3. 检查 endpoint、凭据、网络、bucket 是否存在和 volume 状态，不记录 secret。
4. 恢复存储，验证无需重启 FastAPI 即恢复 readiness，再下载事故前对象并比较 SHA-256。
5. 复查中断期间失败的 Job，只从受支持终态重试。

数据库事务 rollback 前写入的对象会 best-effort 补偿。操作员仍应定期发现无引用对象和缺失对象；当前没有自动 reconciler。

## Worker 与队列事故

```bash
bash scripts/status.sh
ps -ef | rg 'celery.*app.workers.celery_app'
```

检查每个本地队列恰好有一个预期受管 node。SQL transport 没有可靠 fanout inspect 健康路径。worker 死亡可使 Job 保持 running，直到 `CELERY_STALE_JOB_TIMEOUT_SECONDS`；随后 worker 启动将其标记 `CELERY_WORKER_LOST`。使用创建新 attempt 的 retry API 前，先验证 Stage 和存储。

不要手工把 Job status 改成 succeeded。如必须清除 broker message，使用应用的 queue-aware cancellation 操作并保留逐队列 purge 结果；直接 SQL 删除需要事故记录和 Job reconciliation。

## 认证事故

- 改密通过 `password_changed_at` 使旧 access/refresh token 失效。
- 登出把当前 access token 和可用 refresh token JTI 写入 MySQL blacklist。
- 轮换 `JWT_SECRET_KEY` 会立即使全部 token 失效；按全 session logout 协调。
- 疑似数据库泄露超出应用层审计日志保证。保留外部数据库、代理、主机和备份日志。

不要在公网通过关闭 Secure cookie 修复 refresh。首先验证部署是否真实终止 TLS；当前 Compose 没有。

## 容量与保留

仓库没有经过测量的生产容量声明。MySQL 连接规划需计入 API worker、每个 Celery parent/child、Kombu/result backend、migration 和操作员 session。SQL broker 吞吐、ODA CPU/内存、Excel 工作簿大小、MinIO 带宽和 Nginx 上传并发都需要负载测试。

Celery result 在 24 小时后过期，业务 Job/JobStep、audit、file 和对象字节没有自动保留策略。高容量使用前，先定义法律/运维保留期，并实现数据库/对象一致删除。

## 发布与回滚检查表

1. 记录 Git revision、迁移 head、image/digest、flag 和依赖版本。
2. 通过文档、backend、Stage、migration、infrastructure、frontend 和 browser 门禁。
3. 备份并恢复测试 MySQL 与对象存储。
4. 迁移不向后兼容时在维护窗口部署。
5. 用真实样本验证 Nginx -> API -> MySQL -> Celery -> storage -> signed download。
6. 验证未授权访问、retry attempt 隔离、SSE reconnect 和 storage degradation。
7. 只有明确 schema 兼容时才回滚应用代码；禁止临时发挥执行生产 downgrade。

TLS、自动备份、metrics/alerts、集中日志和已记录 RPO/RTO，仍是公网或业务关键生产使用的发布阻断项。
