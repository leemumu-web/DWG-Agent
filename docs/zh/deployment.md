# 部署

> 英文对应文档：[../deployment.md](../deployment.md)

## 支持模式

| 模式 | 入口 | API | 存储 | Readiness |
|---|---|---|---|---|
| 本地开发 | Vite `5173` 或 Nginx `8080` | `127.0.0.1:8010` | 默认 local；MinIO 可选 | MySQL + 已选 storage |
| Docker Compose | HTTP 宿主 `80` -> Nginx `8080` | 内部 `backend-api:8000` | 内部 MinIO | MySQL + MinIO |

两种模式都必须使用 MySQL。SQLite 只是 pytest test double。当前 Compose 没有可用 HTTPS：宿主 `443` 映射到容器 `8443`，但 Nginx 不监听该端口，也没有挂载证书。

## 仓库前提

clean-clone 部署前必须修复 `Stages/dxf2excel`。父仓库只保存 gitlink `86e99dce5ebce992273c7df78ca13d58036f7472`，没有 `.gitmodules`，也不包含该对象。backend `uv sync` 和 Docker `COPY Stages/dxf2excel` 仅因为当前工作树在父 index 外恰好已填充才可工作。

可接受修复是普通跟踪目录，或有效 submodule URL 加可获取的 pinned commit。必须用新 clone 验证，不能只测试当前 checkout。

## 本地初始化

```bash
cp .env.example .env
cp .env.example backend/.env
# 替换 secret，并保持两份文件 MYSQL_* 一致。

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

`start-dev.sh` 启动 report、dxf、dxf2dwg、dxf2excel、excel_final worker、FastAPI `8010` 和 Vite `5173`。`start-all.sh` 构建前端，并用本地 Nginx `8080` 替代 Vite。本地不启动 Agent/CAD worker。

worker 可以在 feature flag 为 false 时存活。四个转换 flag 应保持 false，直到依赖和真实样本通过各自管线检查表。

```bash
bash scripts/status.sh
bash scripts/stop-all.sh
bash scripts/db.sh status
```

脚本按 Celery app、queue 和 node name 发现受管 worker，修复 pidfile 跟踪，并避免杀死无关端口占用者。

## 数据库初始化

```bash
bash scripts/db.sh check
bash scripts/db.sh migrate
bash scripts/db.sh migration-test
```

`migration-test` 创建临时空 MySQL schema，升级到 `a74c2e9f1d30`，验证 22 张业务表和关键约束后删除。它不执行 downgrade。

本地 Uvicorn 中，FastAPI lifespan 调用幂等 `init_db`；失败时记录日志并继续启动，因此必须检查 `/health/ready`。镜像中 Docker CMD 在 Gunicorn 前执行 `alembic upgrade head` 和 `app.db.init_db`；失败会阻止 API 进程启动。

## 本地 MinIO

```dotenv
STORAGE_BACKEND=minio
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
```

已选 MinIO 不可用时，`/health/ready` 返回 503，并且必须无需重启 API 即可恢复。本地模式的持久化、bucket 创建和凭据由操作员负责。

## Compose 准备

```bash
cp .env.docker.example .env.docker
# 替换全部 CHANGE_ME_*；不要提交该文件。

npm --prefix frontend ci
npm --prefix frontend run build
docker compose config --quiet
```

`frontend/dist` 只读挂载到 Nginx，不在 Compose 中构建。frontend 变更后必须重新 build。

## Compose 服务

| Service | 默认 / profile | 用途 | 边界 |
|---|---|---|---|
| `mysql` | 默认 | 业务 DB、broker/result、手册 schema | 私网；8.4 tag，未固定 digest |
| `minio` | 默认 | 对象字节 | 私网；digest-pinned image 和 named volume |
| `backend-api` | 默认 | 内部 `8000` 上四个 Gunicorn worker | production mode 关闭运行时 API 文档 |
| `worker-report` | 默认 | framework report/stub queue | 不是 Agent |
| `nginx` | 默认 | 宿主 `80` HTTP SPA/API gateway | 尽管有 `443` 映射，但没有 TLS listener |
| conversion workers | `workers` | 四个处理队列 | pipeline flag/dependency 仍是独立条件 |
| `worker-agent` | `workers` | 占位队列进程 | task module 为空；禁止启用 Agent |

没有 `worker-cad` Compose service。

```bash
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

## 初始化顺序

1. 新 volume 中，MySQL 创建 `dwg_agent` 并从 init script 导入 `hardware_handbook`。
2. 平台 SQL 授予应用 schema 权限和手册 `SELECT`。
3. MinIO 报告进程 live。
4. Backend 执行迁移和幂等 seed，再启动 Gunicorn。
5. Worker 等待 backend ready，创建 Celery runtime table/index，运行启动维护并发出 ready marker。
6. Nginx 等待 healthy backend。

MySQL init script 只在 data directory 首次初始化时运行。修改脚本不会变更已有 named volume。

## 镜像与构建上下文

- Backend 使用 Python 3.12/uv 多阶段镜像，以 UID/GID 1000 `appuser` 运行。
- Backend 和所有 worker 共用一个镜像，因此即使进程不用，也包含 ODA/Stage code。
- `Stages/dwg2dxf`、`Stages/dxf2dwg` 和 `Stages/dxf2excel` 是 build 时 editable path dependency。
- Excel Final 作为独立脚本树复制，并在子进程中启动。
- `.dockerignore` 排除本地 virtualenv、sample、browser trace、storage 和无关 third-party preview application。
- 禁止记录固定 context 大小；它会随跟踪 Stage binary 和源码归属变化。

## 健康检查

| Service | 实现 | 解释 |
|---|---|---|
| Backend | internal `8000` 上 `curl /health/ready` | MySQL 和 MinIO 可达 |
| MySQL | root `mysqladmin ping` | server 接受连接，不证明 schema 正确 |
| MinIO | `/minio/health/live` | server 进程 live，不证明对象完整性 |
| Worker | ready marker + Celery PID 1 | 启动并连接，不证明特定 pipeline ready |
| Nginx | 只有依赖 | 无独立 Compose healthcheck |

## Nginx 与 TLS

本地和 Compose 配置代理 API、health 和开发文档路径，并为 Job SSE 关闭 buffering。`APP_ENV=production` 且 `DEBUG=false` 时，即使 Nginx 能路由，FastAPI 仍对 `/docs`、`/redoc` 和 `/openapi.json` 返回 404。

增加 TLS 时，应创建单独审查的 container `8443 ssl` server，只读挂载证书，重定向 HTTP，在 HTTPS 验证后再加 HSTS，并测试过期/续期。完成前在部署时删除或明确忽略无效 host 443 映射。

## Celery SQL Transport

broker 和 result backend 从有效 MySQL DSN 派生为 `sqla+mysql+pymysql://...` 和 `db+mysql+pymysql://...`；操作员不配置第二套凭据。

SQLAlchemy broker 面向本仓库有界队列布局。它使用 `READ COMMITTED`、单消息 prefetch、late ack、lost-worker reject、有界 engine pool 和复合 queue claim index。它不支持 fanout remote control，也不保证高吞吐多 replica 调度。

当实测吞吐或路由有需求时评估 RabbitMQ。那只是 broker 变更；MySQL 仍是 Job/progress/authorization 事实源。

## 生产缺口

- 可用 TLS、证书生命周期和公网加固。
- clean-clone `dxf2excel` 源码归属。
- secret manager 和轮换工作流。
- 协调 MySQL/MinIO 备份、保留、恢复自动化及 RPO/RTO 证据。
- 集中日志、metrics、tracing、alerting、SLO 和容量测试。
- 滚动部署/schema 兼容性和多 replica 协调。
- 完成 Agent 和 Windows CAD worker。

## 验证

```bash
bash infra/verify.sh
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 backend-api worker-report mysql minio nginx
```

静态/配置检查不能证明已部署工作流。验收必须通过 Nginx 提交认证 Job，观察 Celery，验证 MySQL 状态和 MinIO 字节，通过新签名 path 下载、比较 SHA-256，并覆盖存储中断/恢复。
