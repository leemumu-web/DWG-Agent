# Infrastructure / 基础设施

## English

`infra/` owns Nginx gateway configuration, MySQL bootstrap data/grants, and deployment-contract verification. It supports the repository's local and Compose topology; it is not a complete production operations stack.

| Component | Current behavior | Missing boundary |
|---|---|---|
| Nginx | HTTP SPA/API gateway, rate/connection limits, SSE proxy | no TLS listener/certificates/HSTS |
| MySQL 8.4 | application, Celery broker/result, handbook schemas | tag not digest-pinned; no automated backup/PITR |
| MinIO | internal, digest-pinned, named volume | no coordinated backup/reconciliation |
| Backend | internal `backend-api:8000`, non-root image | depends on broken `dxf2excel` gitlink for clean build |
| `worker-report` | default framework queue | not an Agent |
| `worker-dxf` / `worker-dxf2dwg` / `worker-dxf2excel` / `worker-excel-final` | `workers` profile | flags and Stage dependencies still apply |
| `worker-agent` | `workers` profile placeholder | Agent feature remains disabled; no Agent task |

Redis/Valkey and Flower are intentionally absent. MySQL and MinIO are not published to the host.

### Compose

```bash
cp .env.docker.example .env.docker
npm --prefix frontend ci && npm --prefix frontend run build
docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

Host `80` is functional HTTP. Although Compose maps `443:8443`, Nginx listens only on `8080`; HTTPS is not available.

### Local

```bash
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/stop-all.sh
```

Local FastAPI is `127.0.0.1:8010`; local Nginx is `8080`.

### Verification

```bash
bash infra/verify.sh
docker compose config --quiet
```

`infra/verify.sh` checks static Nginx/Compose/Dockerfile/env contracts and, when available, live local MySQL/schema facts. It does not perform a TLS handshake, coordinated backup restore, Docker image build, or complete browser/job/storage workflow.

## 中文

`infra/` 负责 Nginx gateway 配置、MySQL bootstrap data/grant 和部署契约验证。它支持仓库本地/Compose 拓扑，但不是完整生产运维栈。

| 组件 | 当前行为 | 缺失边界 |
|---|---|---|
| Nginx | HTTP SPA/API gateway、rate/connection limit、SSE proxy | 无 TLS listener/certificate/HSTS |
| MySQL 8.4 | 应用、Celery broker/result、手册 schema | tag 未固定 digest；无自动 backup/PITR |
| MinIO | internal、digest-pinned、named volume | 无协调 backup/reconciliation |
| Backend | internal `backend-api:8000`、非 root image | clean build 依赖损坏 `dxf2excel` gitlink |
| `worker-report` | 默认 framework queue | 不是 Agent |
| `worker-dxf` / `worker-dxf2dwg` / `worker-dxf2excel` / `worker-excel-final` | `workers` profile | 仍受 flag 和 Stage dependency 限制 |
| `worker-agent` | `workers` profile 占位 | Agent feature remains disabled；没有 Agent task |

Redis/Valkey 和 Flower 有意不存在。MySQL 与 MinIO 不发布宿主端口。

### Compose

```bash
cp .env.docker.example .env.docker
npm --prefix frontend ci && npm --prefix frontend run build
docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

宿主 `80` 是可用 HTTP。尽管 Compose 映射 `443:8443`，Nginx 只监听 `8080`；HTTPS 不可用。

### 本地

```bash
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/stop-all.sh
```

本地 FastAPI 为 `127.0.0.1:8010`；本地 Nginx 为 `8080`。

### 验证

```bash
bash infra/verify.sh
docker compose config --quiet
```

`infra/verify.sh` 检查静态 Nginx/Compose/Dockerfile/env 契约，并在可用时检查本地 MySQL/schema 事实。它不执行 TLS handshake、协调 backup restore、Docker image build 或完整 browser/job/storage 工作流。
