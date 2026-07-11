# 部署

> 英文对应文档：[../deployment.md](../deployment.md)

## 支持模式

| 模式 | API | 存储 | 入口 |
|---|---|---|---|
| 本地开发 | `127.0.0.1:8010` | 默认 `local` | Vite `:5173` 或 Nginx `:8080` |
| Docker Compose | 内部 `backend-api:8000` | MinIO | Nginx `:80/:443` |

两种模式都必须使用 MySQL。SQLite 仅是 pytest test double。

## 本地部署

```bash
cp .env.example .env
cp .env.example backend/.env
# 替换密钥，并保持两文件数据库字段一致。

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

`start-dev.sh` 启动全部五个已实现 worker、FastAPI `8010` 和 Vite `5173`。若 Vite 选择其他端口，以输出 URL 为准。`start-all.sh` 同样启动全部五个已实现 worker，按需构建前端，启动 FastAPI `8010` 并通过本地 Nginx `8080` 暴露。

```bash
bash scripts/status.sh
bash scripts/stop-all.sh
```

worker 脚本按 Celery app、队列和受管节点名发现进程。pidfile 缺失时恢复跟踪而不是重复启动。停止会等待 warm exit，不会仅因某进程占端口就杀死无关进程。

## 数据库

```bash
bash scripts/db.sh start
bash scripts/db.sh check
bash scripts/db.sh migrate
bash scripts/db.sh migration-test
bash scripts/db.sh backup
```

`migration-test` 创建临时空 MySQL schema，升级到 head，验证表/列/类型后删除。当前 head 为 `a74c2e9f1d30`。

Celery broker 从有效 MySQL DSN 派生为 `sqla+mysql+pymysql://...`，result backend 派生为 `db+mysql+pymysql://...`。不得配置可能与应用库漂移的独立 broker 凭据。

## 本地 MinIO

```dotenv
STORAGE_BACKEND=minio
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
```

配置的 MinIO 不可达时 `/health/ready` 返回 503，恢复后无需重启 API。对象字节必须使用持久卷。

## Docker Compose

```bash
cp .env.docker.example .env.docker
# 替换全部 CHANGE_ME_*。

npm --prefix frontend ci
npm --prefix frontend run build

docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

核心服务是 `nginx/backend-api/mysql/minio/worker-report`。`workers` profile 增加 `worker-agent/worker-dxf/worker-dxf2dwg/worker-dxf2excel/worker-excel-final`。

`worker-agent` 只是功能开关子系统的运行基础设施，不代表 Agent 任务逻辑已经启用。

## Compose 初始化顺序

1. MySQL 创建 `dwg_agent` 和 `hardware_handbook`。
2. `01-platform.sql` 授予应用访问和手册只读权限。
3. `02-hardware-handbook.sql` 导入手册数据。
4. MinIO 报告 live。
5. Backend 执行 `alembic upgrade head`、种子初始化并启动 Gunicorn。
6. Worker 等待 backend ready，准备 Kombu SQL 表/索引，再写 ready marker。
7. Nginx 等待 backend ready。

## 镜像与构建上下文

- Backend 使用 uv 多阶段镜像，以 UID/GID 1000 `appuser` 运行。
- MinIO 固定到已验证 digest，不使用 `latest`。
- `.dockerignore` 排除虚拟环境、Stage 样本、本地存储、测试输出和独立部署的第三方应用。
- 已验证 backend context 约 89 MB，只包含一份 ODA 二进制。

## 健康检查

| 服务 | 检查 |
|---|---|
| Backend | 本地：`GET http://127.0.0.1:8010/health/ready`；容器内：`GET http://localhost:8000/health/ready` |
| MySQL | 容器 root 凭据执行 `mysqladmin ping` |
| MinIO | `GET /minio/health/live` |
| Worker | `/tmp/dwg-celery-ready` 存在且 PID 1 为 Celery |
| Nginx | 依赖 healthy backend |

`/health` 仅检查 liveness。`/health/ready` 分别报告数据库和存储；存储宕机不能把数据库错误标记为不可用。

## Nginx

本地配置将 `/api/v1`、`/health`、`/docs`、`/redoc`、`/openapi.json` 代理到 `127.0.0.1:8010`。Compose 代理到 `backend-api:8000`。SSE location 关闭 buffering，并使用长读取超时。

## Celery SQL Transport

SQLAlchemy transport 适用于本仓库受限 worker 拓扑，不适用于任意水平扩容。配置包括：

- `READ COMMITTED` 隔离级别。
- 小连接池和 pre-ping。
- `kombu_message(queue_id, timestamp, id, visible)`。
- 不使用 remote-control fanout 或 `inspect` 健康检查。
- `worker_prefetch_multiplier=1`、late ack、lost-worker reject。

若吞吐需要大量 worker replica，应评估 RabbitMQ broker，同时继续以 MySQL 为业务事实源。

## 生产密钥

禁止提交 `.env`、`backend/.env`、`.env.docker`。替换 JWT、管理员、MySQL、MinIO 凭据。公网部署必须 TLS 和 secure refresh cookie。仅 HTTP 的私有 VPN 可显式设置 `REFRESH_COOKIE_SECURE=false`，公网禁止使用。

## 验证

```bash
bash infra/verify.sh
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 backend-api worker-report mysql minio
```

生产验收必须通过 API 提交作业、观察 Celery 消费、确认 MinIO 对象、通过签名 URL 下载并比较 SHA-256。停止并恢复 MinIO，验证 503 降级和持久对象恢复。
