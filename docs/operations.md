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
| `GET /api/v1/excel-final/health` | 当前业务数据库/存储类型与 Excel Final 依赖分项状态 | worker 一定消费任务或任意输入 schema 一定受支持 |

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
bash scripts/db.sh clean          # 清理 migration-test 残留临时库 + 退役 var/app.db
bash scripts/db.sh reap-storage --dry-run   # 预览软删除对象回收（见 database.md §6.5）
```

`migration-test` 创建并删除临时 schema，并顺带清理历史崩溃残留的临时库；当前目标为 `d5e8a1c4b720` 和 28 张模型表，额外验证 `jobs.request_key`/唯一约束及种子数据兼容；它不测试 downgrade 或生产数据迁移时长。2026-07-12 另以空 MySQL/MinIO Compose 卷验证了 Kombu 首次建表、索引和 report worker ready。需 `sudo mariadb` 的子命令先经 `ensure_sudo` 预检，无 TTY 且凭据未缓存时快速失败而非挂起。

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

数据库事务 rollback 前写入的对象会自动尝试补偿删除；失败写入 `file_transfers.status=compensation_required`。软删除对象和崩溃孤儿由数据控制台的一致性扫描发现，不能仅凭 bucket 总数差额推断具体对象。`bash scripts/db.sh reap-storage --include-orphans` 仍是保留期维护工具，必须先 dry-run；永久清理应优先在控制台选择 finding、预检并确认，禁止对真实 bucket 直接批量猜测删除。

## 数据控制台运行手册

入口为 `/admin/infrastructure`，包含总览、文件登记、存储对象、流转流水和一致性五个页签。管理员可扫描和执行处置；审计员可读取并生成预检，但不能启动扫描或执行处置。

1. 先看总览的数据库/存储健康、今日入库/出库、失败或待补偿流水、最近扫描计数。
2. 在“文件登记”按名称/ID/SHA-256、状态、bucket、格式定位 MySQL 行，并从详情复制 bucket/key 与摘要。
3. 在“存储对象”按 bucket 和前缀游标分页，核对对象大小、修改时间及关联 file ID；对象枚举期间 API 不长期占用 MySQL 连接。
4. 在“流转流水”按方向、状态和操作筛选；`failed` 表示操作已终止，`compensation_required` 表示自动补偿没有恢复一致性，必须人工核查对象和登记。
5. 启动一致性扫描后轮询 run，不刷新总览触发全量扫描。按 finding 类型和 `待处置/已处置` 筛选；每次最多选择 100 项且总量不超过 1 GiB。
6. 四种动作分别为：恢复软删除登记、补登记现有对象、软删除缺失登记、永久清理未登记对象。执行前必须预检；预检 token 绑定操作人、目标摘要和 5 分钟有效期，执行时再次锁定并重检。
7. 永久清理要求输入 `PURGE`，字节不可恢复。若对象已删而 MySQL 提交失败，流水为 `compensation_required`；保留 request ID/transfer UID，重新扫描并按事故流程处理，不能把旧 finding 手工改成 resolved。

DXF 在线预览对象会以 `operation=preview_generate` 登记内部生成流水，并发生成的锁内缓存复用写 `preview_cache_reuse`；源文件变化、缓存对象丢失或源 DXF 软删除时写 `preview_invalidate`，浏览器读取写 `direction=outbound, operation=preview`。源删除后 SVG 物理对象仍处于保留期，但登记和内容端点必须不可用。排查预览时应同时核对源 DXF、SVG `files` 行、对象 `stat` 和流水；不要把弹窗能打开当作登记一致性的充分证据。

代表性上传/幂等/预览/删除事务探针：

```bash
cd backend
STORAGE_BACKEND=local .venv/bin/python ../scripts/verify_storage_transactions.py

# Compose MinIO 不发布宿主端口；只为探针读取内部地址和容器凭据，不打印 secret。
MINIO_IP=$(docker inspect complete_framework-minio-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
MINIO_ACCESS_KEY=$(sed -n 's/^MINIO_ACCESS_KEY=//p' ../.env.docker | head -n 1)
MINIO_SECRET_KEY=$(sed -n 's/^MINIO_SECRET_KEY=//p' ../.env.docker | head -n 1)
STORAGE_BACKEND=minio MINIO_ENDPOINT="http://$MINIO_IP:9000" \
  MINIO_ACCESS_KEY="$MINIO_ACCESS_KEY" MINIO_SECRET_KEY="$MINIO_SECRET_KEY" \
  .venv/bin/python ../scripts/verify_storage_transactions.py
```

脚本创建独立探针对象，验证 Excel 重放只登记一个文件/Job、DXF SVG 入库与鉴权出库、源删除联动和传输终态；结束时软删除登记、删除合成 Job 并物理移除仅由本次创建的对象。它不会处置既有 finding。宿主 `.env` 与 `.env.docker` 的 MinIO endpoint/凭据必须分别核对；`SignatureDoesNotMatch` 是凭据不一致，不是网络故障。

每次事故记录 scan ID、finding ID、transfer UID、request ID、操作人、时间、预检范围和最终对象 stat。不要把浏览器提示当作唯一证据，应同时查询流水详情、finding 状态和对象存储。

## Worker 与队列事故

```bash
bash scripts/status.sh
ps -ef | rg 'celery.*app.workers.celery_app'
```

检查每个本地队列恰好有一个预期受管 node。SQL transport 没有可靠 fanout inspect 健康路径。worker 死亡可使 Job 保持 running，直到 `CELERY_STALE_JOB_TIMEOUT_SECONDS`；随后 worker 启动将其标记 `CELERY_WORKER_LOST`。使用创建新 attempt 的 retry API 前，先验证 Stage 和存储。

若空库 worker 一直 `health: starting/unhealthy` 且 `/tmp/dwg-celery-ready` 不存在，检查 MySQL `SHOW FULL PROCESSLIST`。`CREATE INDEX ix_kombu_message_queue_timestamp_id_visible` 若等待 metadata lock，说明 Kombu 建表连接未释放；当前版本已有显式 commit/close 回归，出现该现象通常意味着运行了旧镜像。记录 image digest，停止旧 worker，升级镜像后在隔离空卷重验，禁止手工杀连接后继续声称冷启动正常。

不要手工把 Job status 改成 succeeded。如必须清除 broker message，使用应用的 queue-aware cancellation 操作并保留逐队列 purge 结果；直接 SQL 删除需要事故记录和 Job reconciliation。

## 认证事故

- 改密通过 `password_changed_at` 使旧 access/refresh token 失效。
- 登出把当前 access token 和可用 refresh token JTI 写入 MySQL blacklist。
- 轮换 `JWT_SECRET_KEY` 会立即使全部 token 失效；按全 session logout 协调。
- 疑似数据库泄露超出应用层审计日志保证。保留外部数据库、代理、主机和备份日志。

不要在公网通过关闭 Secure cookie 修复 refresh。首先验证部署是否真实终止 TLS；当前 Compose 没有。

## 容量与保留

仓库没有经过测量的生产容量声明。MySQL 连接规划需计入 API worker、每个 Celery parent/child、Kombu/result backend、migration 和操作员 session。SQL broker 吞吐、ODA CPU/内存、Excel 工作簿大小、MinIO 带宽和 Nginx 上传并发都需要负载测试。

Celery result 在 24 小时后过期，业务 Job/JobStep、audit、file 和对象字节没有自动保留策略。高容量使用前，先定义法律/运维保留期，并实现数据库/对象一致删除。对象侧回收已有手动工具 `bash scripts/db.sh reap-storage`（软删除对象 + 孤儿，见 database.md §6.5）；磁盘水位可经 `GET /api/v1/system/infrastructure` 的 `capacity`（local 后端上报 total/used/free 字节）观察。二者仍是手动/可调度手段，非自动保留。

## 发布与回滚检查表

1. 记录 Git revision、迁移 head、image/digest、flag 和依赖版本。
2. 通过文档、backend、Stage、migration、infrastructure、frontend 和 browser 门禁。
3. 备份并恢复测试 MySQL 与对象存储。
4. 迁移不向后兼容时在维护窗口部署。
5. 用真实样本验证 Nginx -> API -> MySQL -> Celery -> storage -> signed download。
6. 验证未授权访问、retry attempt 隔离、SSE reconnect 和 storage degradation。
7. 只有明确 schema 兼容时才回滚应用代码；禁止临时发挥执行生产 downgrade。

TLS、自动备份、metrics/alerts、集中日志和已记录 RPO/RTO，仍是公网或业务关键生产使用的发布阻断项。
