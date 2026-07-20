# 部署

## 支持的拓扑

```text
浏览器 -> 宿主 HTTP_PORT -> frontend/Nginx :8080
    ├─ /api、/health、/docs   -> backend-api :8010 -> MySQL 8.4 / MinIO
Celery workers（无入站监听端口）──出站──> MySQL:3306（broker+result）/ MinIO
```

Docker Compose 是最终部署路径。MySQL 与 MinIO 仅位于 Compose 的 `internal` 私有网，端口不发布宿主。公开容器已经包含编译后的 SPA 和 Nginx 配置，部署不再依赖宿主机 `frontend/dist` bind mount。

## 当前与目标消息拓扑

必须区分仓库当前运行事实和目标架构：

- 当前 Celery broker 是 **MySQL SQLAlchemy transport**，地址从有效 MySQL DSN 派生为 `sqla+mysql+pymysql://...`。
- 当前 Celery result backend 同样从有效 DSN 派生为 `db+mysql+pymysql://...`；这些 Result Backend 行是有界清理的运行时数据，不是业务 Job、结果或审计的正式事实源。
- RabbitMQ 是目标消息基础设施，当前 `compose.yaml` 尚未部署 RabbitMQ，也没有经过持久消息、故障恢复或 worker 重连验收。
- 目标 Outbox 与 Celery Beat 尚未实现；当前维护任务由已认证 API 显式提交，不得写成周期调度已经存在。
- 生产 Compose 的对象适配器是 MinIO；本地开发默认可使用 local storage。两者都必须通过 `files` 与 `file_transfers` 保持登记和补偿语义。
- 当前 Compose 仅发布 HTTP，**不发布 443**，没有可用 HTTPS；TLS、证书生命周期和可信代理配置仍是生产阻断项。

`compose.dev.yaml` 只用于本地源码热更新：它会将 API 绑定到宿主 `127.0.0.1:8010` 并挂载工作树。生产、共享测试环境和备份恢复演练不得叠加这个覆盖文件。

网络语义须区分两件事：**不发布端口**只是没有宿主 ingress 映射；而 `internal: true` 是 externally-isolated 网络，意味着 backend-api / worker 都**没有外部 egress**。启用 `CAD_WORKER_ENABLED`（须访问外部 `cad-worker.internal:8080`）或 `AGENT_ENABLED`（须访问外部 LLM）前，必须为相应容器补上可 egress 的网络，并解决 `cad-worker.internal` 的解析（企业 DNS / `extra_hosts` / IP 环境变量）——两者缺一，即使名称可解析，`internal: true` 仍会阻断链路。

当前仍是纯 HTTP，Compose **不发布 443**。完成经审查的 TLS listener 与证书生命周期前，不要自行宣称 HTTPS。仅在可信内网纯 HTTP 场景显式设置 `REFRESH_COOKIE_SECURE=false`；公网部署必须先增加 TLS，并保持 Secure cookie。

## 干净克隆边界

`Stages/dxf2excel` 已作为普通 tracked source 纳入父仓库，backend editable dependency 和 Docker build context 不再依赖不可还原 gitlink。其 419 文件历史验证 corpus、生成工作簿和虚拟环境仍按设计排除；干净克隆能重放内置单测，但不能在没有外部 corpus 的情况下重现该历史规模结论。发布验收仍需在临时 clean checkout 实际执行锁定安装和镜像构建。

## 准备

要求：Docker Engine、Compose v2，以及到所配置镜像仓库/包索引的网络连接。

```bash
cp .env.docker.example .env.docker
# 替换全部 CHANGE_ME_*。
# 当前可信内网纯 HTTP 场景还应加入：
# REFRESH_COOKIE_SECURE=false

bash scripts/docker.sh check
```

检查会拒绝缺失/空的必要凭据与占位密钥，验证两个 Compose profile，并确认必要 Stage 源码存在；不会打印密钥值。

重要配置：

- `HTTP_PORT`：宿主 HTTP 端口，默认 `80`。
- `DWG_AGENT_IMAGE`、`DWG_AGENT_FRONTEND_IMAGE`：默认本地 tag；CI/CD 可改为不可变 registry tag/digest。
- `VITE_API_BASE_URL`：前端构建期变量。留空表示 same-origin `/api`；修改后必须重建前端镜像。
- `DXF_PIPELINE_ENABLED`、`DXF2DWG_PIPELINE_ENABLED`、`DXF2EXCEL_PIPELINE_ENABLED`、`DXF_CLASSIFICATION_PIPELINE_ENABLED`、`EXCEL_FINAL_PIPELINE_ENABLED`：在各自真实样本验收前保持 false；分类 worker 使用独立 `dxf_classification` 队列。
- `AGENT_ENABLED`、`CAD_WORKER_ENABLED`：必须保持 false；任务实现仍是占位。

## 构建与启动

```bash
# 构建两个镜像。Dockerfile 使用 cache mount，因此需要 BuildKit/buildx。
bash scripts/docker.sh build

# 核心服务：Nginx、API、MySQL、MinIO、report worker
bash scripts/docker.sh up

# 核心服务 + 转换 workers + 禁用的 Agent 占位队列
bash scripts/docker.sh up-workers

bash scripts/docker.sh status
bash scripts/docker.sh smoke
```

对应 Make target：`docker-check`、`docker-build`、`docker-up`、`docker-up-workers`、`docker-status`、`docker-smoke`、`docker-down`。

Compose 只构建一个共享后端镜像和一个前端镜像。所有 worker 复用 `DWG_AGENT_IMAGE`。前端镜像用 Node 22 执行锁定的 `npm ci` 构建，再由非特权 Nginx 提供静态文件。

## 服务行为

| 服务 | 默认/profile | 持久化 | 健康含义 |
|---|---|---|---|
| `nginx` | 默认 | 镜像内 SPA | Nginx 响应 `/nginx-health` |
| `backend-api` | 默认 | `app_var` 运行目录 | `/health/ready` 可连接 MySQL 与 MinIO |
| `worker-report` | 默认 | `app_var` | 启动 marker + Celery PID 1 |
| 转换 workers | `workers` | `app_var` | worker 已连接；不等于功能已验收 |
| `worker-agent` | `workers` | `app_var` | 仅队列进程，没有 Agent task |
| `mysql` | 默认 | `mysql_data` | root ping 可用 |
| `minio` | 默认 | `minio_data` | MinIO 进程存活 |

容器设置 `no-new-privileges`；应用与 Nginx 镜像以非 root 运行。Nginx 根文件系统只读，`/tmp` 使用 tmpfs。MySQL 与 MinIO 有两分钟停止宽限。后端启动时先执行 Alembic migration 和幂等 seed，再启动 Gunicorn；worker 和 Nginx 等待其 ready。

数据控制台的一致性扫描由默认启用的 `worker-report` 异步执行，API 总览和清单请求不会在请求线程中全量枚举 MinIO。空库首次启动时，worker 会先提交并关闭 Kombu `queue_declare` 使用的 session，再维护 SQL transport 索引；这避免同一进程的后续 DDL 被自身 metadata lock 阻塞。部署健康检查必须确认 worker ready marker，而不能只看容器进程存活。

MySQL 初始化 SQL 只在新 `mysql_data` volume 上执行。修改初始化文件不会更新已有数据库，应用 schema 变更必须使用 migration。Celery broker/result URL 从有效 MySQL DSN 派生，分别为 `sqla+mysql+pymysql://...` 和 `db+mysql+pymysql://...`，因此操作员不维护第二套凭据。

`app_var` 挂载到 API 和所有 worker 的 `/app/var`，用于共享运行目录。Compose 的正常配置是 `STORAGE_BACKEND=minio`，业务对象应进入 MinIO；备份脚本不包含 `app_var`。若部署者改为 local storage，必须重新设计 volume 和备份集合，不能继续套用默认恢复说明。

## 日志与停止

```bash
bash scripts/docker.sh logs
bash scripts/docker.sh down       # preserves named volumes
```

不要把 `docker compose down -v` 当作日常清理命令：它会删除数据库、对象和应用 volume。

## 备份

部署脚本会创建单事务 MySQL 逻辑 dump、归档 MinIO volume，并写入 SHA-256：

```bash
bash scripts/docker.sh backup /secure/backups/dwg-agent-2026-07-11
```

MySQL dump 自身使用 `--single-transaction`，但随后执行的 MinIO tar 与数据库不是同一原子快照，因此这是有边界的在线单机备份，不是严格跨系统一致备份或 PITR。若要求一致恢复点，先暂停 API/worker 写入并确认队列静止。备份目录必须离机复制并设置保留/加密策略。

## 恢复

恢复会替换目标 MinIO volume 内容，属于破坏性操作。先停止 stack，并确认使用正确备份目录：

```bash
bash scripts/docker.sh down
bash scripts/docker.sh restore /secure/backups/dwg-agent-2026-07-11
bash scripts/docker.sh up
bash scripts/docker.sh smoke
```

恢复会校验已有 checksum、替换 MinIO 内容、启动 MySQL，并导入应用库与五金手册库。之后验证登录、Job 状态、对象下载和 SHA-256。正式生产验收必须在独立恢复主机演练。

## 升级与回滚

1. 升级前备份并测试备份。
2. 构建/拉取不可变版本的后端与前端镜像。
3. 审查 Alembic migration 的前后兼容性。
4. 执行 `bash scripts/docker.sh up-workers`（有意不启转换 worker 时使用 `up`）。
5. 执行 smoke 和一个已认证端到端 Job。
6. 只有数据库 migration 向后兼容时才可仅回滚镜像；否则恢复协调备份。

Compose 是单机编排，不提供 rolling deployment 或多副本 migration 协调。

## 验证门禁

```bash
bash -n scripts/docker.sh
bash infra/verify.sh
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker --profile workers config --quiet
bash scripts/docker.sh build
bash scripts/docker.sh up-workers
bash scripts/docker.sh smoke
```

静态检查和镜像构建成功不能证明转换管线。最终验收必须经 Nginx 登录、提交真实 Job、观察 Celery/SSE、验证 MySQL 状态和 MinIO bytes、下载并比较 SHA-256、验证重启持久化、测试存储中断恢复，并执行备份恢复。

## 剩余生产边界

- 可用 TLS、证书续期、DNS/防火墙加固和可信代理策略。
- `Stages/dxf2excel` 干净克隆可复现所有权。
- Secret manager 与轮换流程；Compose `env_file` 不是 secret manager。
- 离机加密备份、保留策略、自动恢复演练、实测 RPO/RTO 与 PITR。
- 集中日志、指标、trace、告警、SLO 与容量测试。
- 镜像漏洞/SBOM/签名策略，以及全部基础/运行镜像 digest 固定。
- 多副本 rolling deployment 与 schema 兼容。
- Agent 执行与 Windows Node Agent/CAM worker 尚未实现；保持其 flag 关闭并避免把占位队列当成已交付执行面。
