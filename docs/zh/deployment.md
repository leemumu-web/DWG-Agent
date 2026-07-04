# DWG-Agent 平台 — 部署与运维指南

> **目标读者：** 部署、运行和排查本系统的运维工程师。
> **规范权威来源：** `DWG-Agent企业平台技术规范.md`（仓库根目录）—— 所有设计决策均源自此文档。

---

## 目录

1. [前置条件](#1-前置条件)
2. [快速启动（5 分钟）](#2-快速启动5-分钟)
3. [本地开发环境搭建](#3-本地开发环境搭建)
4. [Docker Compose 部署](#4-docker-compose-部署)
5. [配置参考](#5-配置参考)
6. [脚本参考](#6-脚本参考)
7. [健康检查与监控](#7-健康检查与监控)
8. [故障排除](#8-故障排除)
9. [备份与恢复](#9-备份与恢复)
10. [阶段一 限制说明](#10-阶段一-限制说明)

---

## 1. 前置条件

### 本地开发

| 依赖项 | 最低版本 | 用途 |
|---|---|---|
| Python | 3.12（精确版本 -- `<3.13`） | 后端运行时 |
| uv | 最新版（通过 `ghcr.io/astral-sh/uv`） | Python 包管理器 |
| MySQL 或 MariaDB | 8.x | 运行时数据库 |
| Redis 或 Valkey | 7.x / 9.x | 会话缓存、消息代理（Celery） |
| Node.js | 18+（LTS） | 前端构建 |
| npm | 9+ | 前端包管理器 |

### Docker 部署

| 依赖项 | 最低版本 | 用途 |
|---|---|---|
| Docker Engine | 24+ | 容器运行时 |
| Docker Compose | v2（插件或独立版） | 编排 |
| Node.js | 18+（LTS） | 前端构建（必须在 `docker compose up` 之前运行 `npm run build`） |

### Arch Linux 快速安装

```bash
# Python 3.12
sudo pacman -S python python-pip
# 然后安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh

# MySQL
sudo pacman -S mysql
sudo systemctl enable --now mysqld

# Redis (Valkey)
sudo pacman -S redis
sudo systemctl enable --now redis

# Node.js + npm
sudo pacman -S nodejs npm
```

---

## 2. 快速启动（5 分钟）

以下步骤可从全新克隆的仓库在本地开发模式下启动平台。
所有命令均在**仓库根目录**下执行。

```bash
# 0. 进入仓库
cd /path/to/complete_framework

# 1. 配置环境变量
cp .env.example .env
# 编辑 .env：设置 MYSQL_PASSWORD、MYSQL_ROOT_PASSWORD
# 其他所有默认值（127.0.0.1:3306，无 Redis 密码）均可直接使用

# 2. 安装后端依赖
cd backend
uv python install 3.12   # 仅首次执行
uv sync --locked
cd ..

# 3. 初始化 MySQL
bash scripts/db.sh start        # 确保 MySQL 正在运行
bash scripts/db.sh setup-user   # 创建 dwg_user + 授予权限
bash scripts/db.sh init         # 创建数据库 + alembic 升级 + 种子超级管理员

# 4. 启动平台
bash scripts/start-dev.sh       # 后端（uvicorn --reload）+ 前端（Vite HMR）

# 5. 验证
curl http://127.0.0.1:8000/health
# => {"data": {"status": "ok"}, "meta": {...}}
```

**访问地址：**
- 前端：`http://127.0.0.1:5173`（Vite 开发服务器，支持 HMR）
- API 文档：`http://127.0.0.1:8000/docs`
- 默认登录：`admin` / `SuperAdminPass1`（**生产环境中务必立即修改**）

**停止：**
```bash
bash scripts/stop-all.sh
```

---

## 3. 本地开发环境搭建

### 3.1 架构概览（本地）

```
┌──────────┐     ┌──────────────┐     ┌─────────┐
│  浏览器   │────▶│  Vite :5173  │────▶│  后端    │
│          │     │  (HMR 代理)  │     │  :8000  │
└──────────┘     └──────────────┘     └────┬────┘
                                           │
                          ┌─────────────────┼─────────────────┐
                          ▼                 ▼                  ▼
                     ┌─────────┐     ┌──────────┐     ┌──────────┐
                     │  MySQL  │     │  Redis   │     │ 本地文件系统 │
                     │  :3306  │     │  :6379   │     │ ./var/   │
                     └─────────┘     └──────────┘     └──────────┘
```

可选地，Nginx 可将前端和后端统一到一个地址 `http://localhost:8080` 后面（参见第 3.5 节）。

### 3.2 环境变量文件

两个 `.env` 文件必须保持同步：

| 文件 | 用途 |
|---|---|
| `.env`（仓库根目录） | 主配置文件；供 `scripts/db.sh` 和入口脚本使用 |
| `backend/.env` | 后端运行时配置；由 `pydantic-settings` 在启动时读取 |

两份文件中的 `DATABASE_URL`、`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 必须包含相同的值。`db.sh check` 命令会自动验证这一点。

### 3.3 后端

```bash
cd backend
uv sync --locked
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

关键要点：
- 使用 `--reload` 以在开发期间启用热重载。
- 仅绑定到 `127.0.0.1`（而非 `0.0.0.0`）。
- 健康检查端点：`GET /health` 返回 `{"data": {"status": "ok"}, "meta": {...}}`。
- 旧的 `/api/v1/health` 端点已移除 —— 仅保留 `/health`。

### 3.4 前端

```bash
cd frontend
npm ci              # 依据锁文件进行干净安装
npm run dev         # Vite 开发服务器，带 HMR，监听 :5173
```

Vite 开发服务器将 `/api/*` 和 `/health` 代理到 `http://127.0.0.1:8000`。代理规则定义在 `frontend/vite.config.ts` 中 —— 开发模式下 `VITE_API_BASE_URL` 在 `.env` 中应为空。

### 3.5 Nginx（可选的本地网关）

可以在本地启动 Nginx 来提供构建后的前端，并从单一端口代理 API 调用：

```bash
# 前置条件：后端在 :8000 运行，前端已构建（frontend/dist/ 存在）
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf
```

该配置提供以下功能：

| 路径 | 行为 |
|---|---|
| `http://localhost:8080` | React SPA，带 BrowserRouter 回退 |
| `http://localhost:8080/api/v1/*` | 反向代理到 FastAPI `:8000` |
| `http://localhost:8080/health` | 健康检查代理 |

Nginx 管理命令：
```bash
# 无停机重载配置
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s reload

# 优雅关闭
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit

# 语法检查
sudo nginx -t -c $(pwd)/infra/nginx/nginx.local.conf
```

### 3.6 数据库管理

所有 MySQL 操作均通过 `scripts/db.sh` 执行：

```bash
bash scripts/db.sh start          # 确保 MySQL 运行，验证凭据
bash scripts/db.sh setup-user     # 首次：创建 dwg_user + 授权
bash scripts/db.sh init           # 创建数据库 + alembic + 种子超级管理员
bash scripts/db.sh migrate        # alembic upgrade head（修复 schema 漂移）
bash scripts/db.sh status         # 配置、凭据、schema、连接状态
bash scripts/db.sh check          # 非破坏性 CI/验证检查
bash scripts/db.sh shell          # 使用应用凭据打开 MySQL shell
bash scripts/db.sh logs           # 跟踪 MySQL/MariaDB systemd 日志
bash scripts/db.sh migration-test # 创建临时 schema，完整执行迁移，验证，清理
```

`db.sh` 在 shell 层面强制要求使用 MySQL 的 URL —— `sqlite://` 格式的 URL 在运行时操作中会被拒绝。SQLite 仅保留用于 pytest 隔离。

### 3.7 Redis（Valkey 9.x）

```bash
# 检查状态
redis-cli ping   # => PONG

# 服务管理
sudo systemctl status redis
sudo systemctl restart redis
```

关键要点：
- 本地开发：无密码，监听 `127.0.0.1:6379`。
- 后端采用惰性连接 —— Redis 宕机**不会**导致 API 服务器崩溃。
- 记忆服务：键格式为 `agent:memory:{session_id}`，TTL 7200 秒，最多 20 条消息。
- 缓存服务：键格式为 `cache:{namespace}:{key}`，所有方法在 Redis 不可用时均安全无异常。

### 3.8 测试

```bash
cd backend
uv run ruff check app tests    # 代码检查（必须通过）
uv run pytest -q               # 432 条测试（必须通过）
```

测试使用 SQLite 内存数据库（`StaticPool`）和 FakeRedis —— 无需外部服务。`test_redis_real.py` 中的真实 Redis 集成测试在 Redis 不可用时会自动跳过。

---

## 4. Docker Compose 部署

### 4.1 架构概览（Docker）

```
                    ┌──────────────────────────────────────┐
                    │          公网                        │
                    │                                      │
                    │  ┌──────────┐                        │
                    │  │  Nginx   │ :80 :443               │
                    │  │  1.27    │                        │
                    │  └────┬─────┘                        │
                    │       │                               │
                    └───────┼───────────────────────────────┘
                            │
            ┌───────────────┼─────────────── 内部网络 ────┐
            │               │                             │
            │     ┌─────────▼──────────┐                  │
            │     │   backend-api      │                  │
            │     │   gunicorn :8000   │                  │
            │     └──┬──────┬──────┬───┘                  │
            │        │      │      │                      │
            │   ┌────▼──┐ ┌─▼──┐ ┌─▼────┐                 │
            │   │ mysql │ │redis│ │minio │                 │
            │   │ 8.4   │ │9.0  │ │latest│                 │
            │   └───────┘ └─────┘ └──────┘                 │
            │                                              │
            │   ┌──────────┐ ┌──────────┐ ┌───────────┐   │
            │   │ worker-  │ │ worker-  │ │ worker-   │   │
            │   │ agent    │ │ dxf      │ │ report    │   │
            │   │ (profile)│ │ (profile)│ │ default   │   │
            │   └──────────┘ └──────────┘ └───────────┘   │
            │                                              │
            │   ┌──────────┐                               │
            │   │ flower   │  （monitoring profile）       │
            │   │ :5555    │                               │
            │   └──────────┘                               │
            └──────────────────────────────────────────────┘
```

### 4.2 服务总结

| 服务 | 镜像 | 端口 | Profile | 健康检查 |
|---|---|---|---|---|
| `nginx` | `ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine` | 80→8080, 443→8443 | -- | depends_on backend-api 健康 |
| `backend-api` | 自构建 | 8000（内部） | -- | 每 10 秒 `curl /health` |
| `mysql` | `container-registry.oracle.com/mysql/community-server:8.4` | 3306（内部） | -- | 每 10 秒 `mysqladmin ping` |
| `redis` | `ghcr.io/valkey-io/valkey:9.0-alpine` | 6379（内部） | -- | 每 10 秒 `redis-cli ping` |
| `minio` | `quay.io/minio/minio:latest` | 9000, 9001（内部） | -- | `curl /minio/health/live` |
| `worker-agent` | 自构建 | -- | `workers` | 每 10 秒 `celery inspect ping` |
| `worker-dxf` | 自构建 | -- | `workers` | 每 10 秒 `celery inspect ping` |
| `worker-report` | 自构建 | -- | -- | 每 10 秒 `celery inspect ping` |
| `flower` | 自构建 | 5555（内部） | `monitoring` | 每 10 秒 `curl :5555` |

### 4.3 分步部署

**第 1 步：创建 Docker 环境变量文件**

```bash
cp .env.docker.example .env.docker
```

编辑 `.env.docker`，替换所有 `CHANGE_ME_*` 占位符：

| 占位符 | 说明 |
|---|---|
| `CHANGE_ME_MYSQL_PASSWORD` | MySQL 应用用户密码 |
| `CHANGE_ME_MYSQL_ROOT_PASSWORD` | MySQL root 密码 |
| `CHANGE_ME_REDIS_PASSWORD` | Redis requirepass 密码 |
| `CHANGE_ME_MINIO_ROOT_USER` | MinIO 管理员用户名 |
| `CHANGE_ME_MINIO_ROOT_PASSWORD` | MinIO 管理员密码 |
| `CHANGE_ME_256_BIT_JWT_SECRET` | JWT 签名密钥（至少 32 个字符） |
| `CHANGE_ME_SUPER_ADMIN_PASSWORD` | 引导超级管理员密码 |

**第 2 步：构建前端**

```bash
cd frontend
npm ci
npm run build
cd ..
```

**第 3 步：启动核心服务**

```bash
docker compose up -d
```

此命令启动：nginx、backend-api、mysql、redis、minio 以及 `worker-report`（用于阶段一 Celery 模拟作业）。

**第 4 步：验证部署**

```bash
# 检查所有容器
docker compose ps

# 查看日志
docker compose logs -f nginx backend-api

# 通过 nginx 进行健康检查
curl http://localhost/health
# => {"data": {"status": "ok"}, "meta": {...}}
```

**访问地址：** `http://localhost`

登录：`admin` / 你配置的 `SUPER_ADMIN_PASSWORD`

**第 5 步（可选）：启动 Agent/DXF Worker 和监控**

```bash
# Agent/DXF 占位 worker
docker compose --profile workers up -d

# Agent/DXF worker + Flower 监控面板
docker compose --profile workers --profile monitoring up -d
```

Flower 面板：`http://localhost:5555`（内部网络，如需外部访问请配置端口映射）。

### 4.4 Dockerfile 详情

位于 `backend/Dockerfile`（多阶段构建）：

**阶段 1（builder）：**
- 基础镜像：`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
- 使用基础镜像自带的 uv；无需 `uv:latest` 复制阶段
- 运行 `uv sync --frozen --no-dev` 创建虚拟环境

**阶段 2（runtime）：**
- 基础镜像：`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
- 创建非 root 用户 `appuser`（uid 1000）
- 安装 `curl` + `ca-certificates` 用于健康检查
- 从 builder 阶段复制 `.venv`，然后复制 `app/`、`alembic.ini`、`migrations/`
- 创建可写目录 `/app/var/`，所有者为 `appuser`
- HEALTHCHECK：每 15 秒 `curl -f http://localhost:8000/health`，超时 3s，5 次重试
- CMD：`alembic upgrade head && exec gunicorn app.main:app --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --access-logfile - --error-logfile -`

### 4.5 卷

| 卷 | 挂载点 | 用途 | 持久性 |
|---|---|---|---|
| `mysql_data` | `/var/lib/mysql` | MySQL 数据文件 | `docker compose down` 后仍然保留 |
| `redis_data` | `/data` | Redis AOF + 数据 | `docker compose down` 后仍然保留 |
| `minio_data` | `/data` | 对象存储数据 | `docker compose down` 后仍然保留 |

完全重置：`docker compose down -v`

### 4.6 网络

| 网络 | 类型 | 用途 |
|---|---|---|
| `public` | 对外暴露 | Nginx 入口（暴露 80/443 端口） |
| `internal` | `internal: true` | 所有后端服务（无外部访问） |

### 4.7 停止

```bash
# 停止所有服务（保留卷）
docker compose --profile workers --profile monitoring down

# 停止并删除卷（完全重置）
docker compose --profile workers --profile monitoring down -v
```

---

## 5. 配置参考

所有配置均通过环境变量驱动。规范定义位于 `backend/app/core/config.py`（pydantic-settings，45 个字段 + 5 个计算属性）。

### 5.1 应用

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_NAME` | `DWG-Agent Platform` | 显示名称 |
| `APP_ENV` | `development` | 运行环境：`development` / `production` |
| `DEBUG` | `true` | 调试模式（生产环境中应禁用） |
| `API_V1_PREFIX` | `/api/v1` | API URL 前缀 |
| `BACKEND_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 逗号分隔的 CORS 来源列表 |

**计算属性：`settings.cors_origins`** —— 将 `BACKEND_CORS_ORIGINS` 拆分为列表。

### 5.2 数据库（MySQL）

| 变量 | 本地默认值 | Docker 默认值 | 说明 |
|---|---|---|---|
| `DATABASE_URL` | `mysql+pymysql://dwg_user:...@127.0.0.1:3306/dwg_agent` | `...@mysql:3306/dwg_agent` | 完整连接 URL |
| `MYSQL_HOST` | `127.0.0.1` | `mysql` | 主机名 |
| `MYSQL_PORT` | `3306` | `3306` | 端口号 |
| `MYSQL_DATABASE` | `dwg_agent` | `dwg_agent` | 数据库名称 |
| `MYSQL_USER` | `dwg_user` | `dwg_user` | 应用用户 |
| `MYSQL_PASSWORD` | （必填） | （必填） | 应用用户密码 |
| `MYSQL_ROOT_PASSWORD` | （必填） | （必填） | MySQL root 密码 [^1] |

[^1]: `MYSQL_ROOT_PASSWORD` 是**基础设施专用变量**。它被 `compose.yaml` 用于 MySQL 容器的健康检查（`mysqladmin ping -u root -p`）。它**不是** `backend/app/core/config.py` 中的字段（config.py 设了 `extra="ignore"`）—— 后端应用程序永远不会读取它。它只在 `.env.example` 和 `.env.docker.example` 中定义，供 Compose 使用。

**计算属性：`settings.mysql_url`** —— 由各组件字段组装，密码经 URL 编码。

连接池设置（硬编码在 `app/db/session.py` 中，仅限 MySQL）：
- `pool_recycle=3600`
- `pool_size=10`
- `max_overflow=20`

### 5.3 Redis（兼容 Valkey）

| 变量 | 本地默认值 | Docker 默认值 | 说明 |
|---|---|---|---|
| `REDIS_HOST` | `localhost` | `redis` | 主机名 |
| `REDIS_PORT` | `6379` | `6379` | 端口号 |
| `REDIS_DB` | `0` | `0` | 数据库编号 |
| `REDIS_PASSWORD` | （空） | （必填） | 认证密码 |
| `REDIS_MEMORY_TTL` | `7200` | `7200` | Agent 记忆 TTL（秒） |
| `REDIS_MAX_MESSAGES` | `20` | `20` | 每个会话的最大消息数 |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | `false` | 同步执行任务（测试中覆盖为 `true`） |

**计算属性：`settings.redis_url`** —— 由各组件字段组装。
**计算属性：`settings.celery_broker_url`** —— `redis://.../{host}:{port}/0`
**计算属性：`settings.celery_result_backend`** —— `redis://.../{host}:{port}/1`

运行时 Celery URL 在 `config.py` 中由 `REDIS_*` 组件字段派生，因此 Redis 和 Celery 的配置不会出现漂移。`CELERY_BROKER_URL` 和 `CELERY_RESULT_BACKEND` 条目仍保留在环境变量模板中，作为与规范兼容的镜像值；在复制环境变量文件时，请保持它们与 Redis 字段一致。

### 5.4 存储

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` 或 `minio` |
| `LOCAL_STORAGE_ROOT` | `./var/storage` | 本地文件系统路径（相对于 CWD） |
| `MAX_UPLOAD_SIZE_MB` | `512` | 最大文件上传大小 |

### 5.5 MinIO（对象存储）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO 服务器端点 |
| `MINIO_ACCESS_KEY` / `MINIO_ROOT_USER` | （必填） | MinIO 访问密钥 |
| `MINIO_SECRET_KEY` / `MINIO_ROOT_PASSWORD` | （必填） | MinIO 密钥 |
| `MINIO_BUCKET_ORIGINAL` | `dwg-original` | 上传的 DWG 文件 |
| `MINIO_BUCKET_DERIVED` | `dwg-derived` | 处理后的衍生文件 |
| `MINIO_BUCKET_REPORTS` | `dwg-reports` | 生成的报告 |
| `MINIO_BUCKET_TEMP` | `dwg-temp` | 临时文件 |

Docker Compose 将 `MINIO_ROOT_USER` 同时传递给 `MINIO_ACCESS_KEY` 和 `MINIO_ROOT_USER`，将 `MINIO_ROOT_PASSWORD` 同时传递给 `MINIO_SECRET_KEY` 和 `MINIO_ROOT_PASSWORD`。

### 5.6 JWT 认证

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JWT_SECRET_KEY` | `change-me-in-dev-change-me-in-prod-32chars` | **必须修改** —— 至少 32 个随机字符 |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | 访问令牌 TTL |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `14` | 刷新令牌 TTL |

### 5.7 超级管理员引导

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SUPER_ADMIN_USERNAME` | `admin` | 引导管理员用户名 |
| `SUPER_ADMIN_PASSWORD` | `SuperAdminPass1` | **生产环境中必须修改** |
| `SUPER_ADMIN_REAL_NAME` | `系统管理员` | 显示名称 |

此用户在首次运行时由 `app/db/init_db.py` 进行种子初始化。

### 5.8 功能开关

| 变量 | 默认值 | 效果 |
|---|---|---|
| `AGENT_ENABLED` | `false` | 为 false 时，`/api/v1/agent-runs/*` 返回 503 |
| `DXF_PIPELINE_ENABLED` | `false` | 为 false 时，DXF 解析端点返回 503 |
| `CAD_WORKER_ENABLED` | `false` | 为 false 时，CAD Worker 端点返回 503 |

这三个开关在阶段一中均为 `false`。它们按 Python 布尔值解析（`true`/`false`，不区分大小写）。

### 5.9 LLM（阶段二）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MODEL_NAME` | `deepseek-chat` | LLM 模型标识符 |
| `MODEL_API_KEY` | （空） | LLM 提供商的 API 密钥 |
| `MODEL_BASE_URL` | `https://api.deepseek.com` | LLM API 基础 URL |

### 5.10 MCP（阶段二）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MCP_CAD_COMMAND` | `uvx` | MCP 客户端命令 |
| `MCP_CAD_ARGS` | `cad-mcp-server,stdio` | MCP 客户端参数 |

### 5.11 CAD Worker（阶段四）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CAD_WORKER_API_BASE` | `http://cad-worker.internal:8080` | Windows CAD Worker 端点 |
| `CAD_WORKER_API_KEY` | （空） | CAD Worker 认证密钥 |

### 5.12 前端

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | （空） | 前端 API 基础 URL；为空表示使用相对路径（通过 Nginx 代理） |

---

## 6. 脚本参考

所有脚本位于 `scripts/` 目录中，从仓库根目录调用。它们共享 `scripts/lib.sh` 中的公共函数。

### `lib.sh` —— 共享函数

所有其他脚本均引用此文件。提供以下功能：

| 函数 | 用途 |
|---|---|
| `port_free <port>` | 端口未被占用时返回 true |
| `check_port <port> <label>` | 报告端口状态（健康检查聚合） |
| `kill_by_pidfile <pidfile> <label>` | 通过 PID 文件终止进程 |
| `pidfile_running <pidfile>` | 检查一个 PID 文件是否指向一个存活的进程 |
| `start_report_worker` | 启动本地 Celery `worker-report` 并写入 `/tmp/dwg-agent-worker-report.pid` |
| `wait_port <host> <port> <timeout> <label>` | 阻塞等待端口接受连接 |
| `ensure_service <port> <name...>` | 如果端口未在监听，则启动 systemd 服务 |
| `ok` / `warn` / `err` / `info` / `step` | 带颜色的控制台输出 |

设置环境变量 `PROJECT_ROOT` 为仓库根目录。

### `db.sh` —— MySQL 管理

```
用法: bash scripts/db.sh <command>

命令:
  start           启动 MySQL/MariaDB，验证 .env 凭据可以连接
  setup-user      创建 dwg_agent 数据库 + dwg_user + 授权（首次设置）
  init            完整初始化：创建 schema + alembic upgrade head + 种子超级管理员
  migrate         运行 alembic upgrade head（修复已有 schema 漂移）
  migration-test  创建临时 MySQL schema，从头完整运行迁移，验证，清理
  check           非破坏性验证：配置一致性、凭据、schema、SQLite fd 检查
  status          打印数据库配置和诊断摘要
  shell           使用 backend/.env 中的应用凭据打开 MySQL shell
  logs            跟踪 MySQL/MariaDB systemd 日志
```

关键行为：
- 要求 `DATABASE_URL` 必须为 `mysql+pymysql://` 格式（在 shell 层面拒绝 `sqlite://` 格式）。
- 验证 `.env` 和 `backend/.env` 的数据库配置一致。
- 能检测 MariaDB 与 MySQL 的 systemd 服务名称差异。
- `migration-test` 创建一个临时的 `dwg_agent_migration_test_<pid>` 数据库，从空 schema 运行完整的 Alembic 链，验证所有 17 张预期表和 TimestampMixin 列，然后删除临时数据库。

### `start-dev.sh` —— 开发模式

```
用法: bash scripts/start-dev.sh
```

- 启动 MySQL + Redis，初始化数据库，启动本地 Celery `worker-report`、后端（`uvicorn --reload` 监听 `:8000`）和前端（Vite HMR 监听 `:5173`）。
- 将 PID 文件写入 `/tmp/dwg-agent-worker-report.pid`、`/tmp/dwg-agent-backend.pid` 和 `/tmp/dwg-agent-frontend.pid`。
- 自动检测 `VITE_API_BASE_URL` 是否为空（Nginx 模式），并临时将其设置为 `http://127.0.0.1:8000` 以直接访问后端。
- 阻塞等待直到 Ctrl+C（使用 `wait`），然后打印停止说明。

### `start-all.sh` —— 全栈（Nginx 网关）

```
用法: bash scripts/start-all.sh [--rebuild]
```

- 启动 MySQL、Redis、本地 Celery `worker-report`、后端（`uvicorn --reload` 监听 `:8000`），按需构建前端，启动 Nginx 监听 `:8080`。
- `--rebuild` 标志强制重新构建前端，即使 `dist/` 已存在。
- 统一访问：`http://localhost:8080`（通过 Nginx 提供 SPA + API + 健康检查）。
- 如果 8080 端口被未知进程占用则停止。

### `stop-all.sh` —— 优雅关闭

```
用法: bash scripts/stop-all.sh
```

- 停止 Nginx（通过 `nginx -s quit`）。
- 通过 PID 文件 `/tmp/dwg-agent-backend.pid` 终止后端。
- 通过 PID 文件 `/tmp/dwg-agent-worker-report.pid` 终止本地 Celery `worker-report`。
- 验证 8000 端口已释放。
- **不**停止 MySQL/Redis（它们是共享基础设施）。

### `status.sh` —— 健康检查聚合

```
用法: bash scripts/status.sh
```

检查项目：
1. MySQL 状态（通过 `db.sh status`）
2. Redis 端口 6379
3. Celery worker-report PID
3. 后端端口 8000 + `GET /health` 端点
4. Nginx 端口 8080 + API 反向代理 + SPA 静态文件服务

打印带颜色编码的摘要，显示"全部正常"或"部分失败"及恢复提示。

---

## 7. 健康检查与监控

### 7.1 后端健康检查端点

```
GET /health
```

响应（200 OK）：
```json
{
  "data": {"status": "ok"},
  "meta": {"request_id": "...", "timestamp": "..."}
}
```

失败响应：
- 若数据库不可达（通过 `db_health()` 检测），返回 503
- 意外错误时返回 500

出于安全考虑，该端点**不**暴露内部组件详细信息（数据库 URL、Redis 状态等）。

### 7.2 Docker 健康检查

| 服务 | 检查方式 | 间隔 | 超时 | 重试次数 |
|---|---|---|---|---|
| `backend-api` | `curl -f http://localhost:8000/health` | 10s | 3s | 5 |
| `mysql` | `mysqladmin ping -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}"` | 10s | 3s | 5 |
| `redis` | `redis-cli -a "${REDIS_PASSWORD}" ping` | 10s | 3s | 5 |
| `minio` | `curl -f http://localhost:9000/minio/health/live` | 10s | 3s | 5 |
| `worker-agent` | `celery inspect ping -d agent@$HOSTNAME` | 10s | 8s | 5 |
| `worker-dxf` | `celery inspect ping -d dxf@$HOSTNAME` | 10s | 8s | 5 |
| `worker-report` | `celery inspect ping -d report@$HOSTNAME` | 10s | 8s | 5 |
| `flower` | `curl -fsS http://localhost:5555/` | 10s | 5s | 5 |

Nginx 通过 `depends_on` `backend-api` 并设置 `condition: service_healthy`，因此在后端健康之前 Nginx 不会启动（也不会接受流量）。

### 7.3 Dockerfile 健康检查

后端 Dockerfile 内置了面向容器运行时的 HEALTHCHECK 指令：

```dockerfile
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1
```

这在容器内部运行，独立于 Docker Compose 的健康检查。

### 7.4 Nginx 监控要点

- **访问日志格式：** `extended` 格式包含 `$request_id`、`$request_time`、`$upstream_connect_time`、`$upstream_header_time`、`$upstream_response_time`。
- **认证日志：** 登录端点（`/api/v1/auth/sessions`）在 Docker 中使用相同的 stdout 访问流；本地/系统 Nginx 部署可根据配置将其路由到专用的认证日志文件。
- **速率限制：** 登录端点限速 2 req/s（突发 3），通用 API 限速 100 req/s（突发 20）—— 超过限制时均返回 HTTP 429。
- **健康检查端点：** 对 `/health` 设置 `access_log off` 以减少日志噪音。

### 7.5 Flower（Celery 监控）

使用 `monitoring` profile 启动：

```bash
docker compose --profile monitoring up -d flower
```

Flower 面板位于 `http://localhost:5555`，提供以下信息：
- Worker 状态（在线/离线）
- 任务队列长度
- 任务成功/失败率
- 任务执行时间分布

### 7.6 基础设施验证

`infra/verify.sh` 脚本执行全面的静态 + 运行时验证：

```bash
bash infra/verify.sh
```

检查项（6 个部分）：
1. **Nginx 配置** —— 语法验证、关键指令（upstream、速率限制、安全头、SPA 回退）
2. **Docker Compose** —— 服务数量（9）、镜像版本、卷挂载、环境变量空值检查、健康检查、profiles
3. **Dockerfile** —— 多阶段构建、非 root 用户、HEALTHCHECK、STOPSIGNAL、gunicorn CMD
4. **MySQL 集成** —— 数据库可访问性、全部 17 张表、TimestampMixin 列、角色种子数据、管理员用户、dwg_user 权限、应用凭据
5. **文件完整性** —— 所有必需的配置文件存在、环境变量模板键值一致性
6. **死代码检查** —— 确认已删除的目录（`conf.d/`、`snippets/`）未被引用

---

## 8. 故障排除

### 8.1 后端无法启动

**症状：** `bash scripts/start-dev.sh` 卡住或后端 8000 端口未在监听。

**检查：**
```bash
# 验证 MySQL 正在运行
bash scripts/db.sh status

# 验证 Redis 正在运行
redis-cli ping

# 检查 backend/.env 是否存在且值正确
cat backend/.env | grep DATABASE_URL

# 尝试手动启动后端以查看错误信息
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**常见原因：**
- MySQL 未运行（`sudo systemctl start mariadb` 或 `mysqld`）
- `.env` / `backend/.env` 中的 `MYSQL_PASSWORD` 不正确
- 数据库未初始化（`bash scripts/db.sh init`）
- Redis 未运行（`sudo systemctl start redis`）

### 8.2 数据库连接被拒绝

**症状：** 后端日志显示 `Can't connect to MySQL server on '127.0.0.1' (111)`。

**修复：**
```bash
# 检查 MySQL 是否运行
sudo systemctl status mariadb   # 或 mysqld

# 如果未运行
sudo systemctl start mariadb    # 或 mysqld

# 验证端口
bash scripts/db.sh status

# 如需重新初始化
bash scripts/db.sh init
```

### 8.3 "DATABASE_URL 不是 mysql+pymysql:// 格式"

**症状：** `db.sh` 命令报方案错误而失败。

**修复：** `.env` 和 `backend/.env` 必须使用：
```
DATABASE_URL=mysql+pymysql://dwg_user:YOUR_PASSWORD@127.0.0.1:3306/dwg_agent
```

不能使用 `sqlite:///...`。SQLite 仅用于 pytest 隔离。

### 8.4 前端显示"无法连接到 API"

**症状：** 浏览器控制台显示对 `/api/v1/...` 的请求失败。

**修复：**
- 在 Vite 开发模式下（`http://localhost:5173`）：Vite 代理将 `/api/` 转发到后端。确保 `frontend/.env` 中的 `VITE_API_BASE_URL` 为空。
- 在 Nginx 模式下（`http://localhost:8080`）：确保后端在 `:8000` 上运行且 Nginx 已正确启动。
- 在 Docker 模式下（`http://localhost`）：检查 `docker compose ps` —— 所有服务应显示为 `Up (healthy)`。

### 8.5 Docker 容器反复崩溃重启

**症状：** `docker compose ps` 显示容器在不断重启。

**检查：**
```bash
# 查看特定服务日志
docker compose logs backend-api --tail 50
docker compose logs mysql --tail 50

# 检查 .env.docker 是否存在且包含真实密码
grep CHANGE_ME .env.docker
# 如果仍然存在 CHANGE_ME 值，容器将无法连接
```

**常见原因：**
- `.env.docker` 中仍有 `CHANGE_ME_*` 占位符值
- MySQL 数据卷损坏（执行 `docker compose down -v && docker compose up -d` 彻底重置）
- 宿主机 80 端口已被占用（`sudo ss -tlnp 'sport = :80'`）

### 8.6 端口 8080 已被占用

**症状：** `bash scripts/start-all.sh` 失败并提示"端口 8080 已被占用"。

**修复：**
```bash
# 检查哪个进程在使用 8080 端口
sudo ss -tlnp 'sport = :8080'

# 如果是残留的 nginx 实例
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit

# 如果是其他进程
sudo kill <PID>
```

### 8.7 .env / backend/.env 不一致

**症状：** `db.sh check` 报告数据库配置值不匹配。

**修复：** 将一个文件的数据库部分复制到另一个文件使其一致：
```bash
# 方案 1：将根目录 .env 复制到 backend/.env（然后编辑后端特有的值）
cp .env backend/.env

# 方案 2：手动编辑使其一致
vim -d .env backend/.env
```

### 8.8 Alembic 迁移错误

**症状：** `alembic upgrade head` 因重复列或重复表错误而失败。

**修复：**
```bash
# 检查当前 alembic head
cd backend && uv run alembic current

# 如果数据库版本超出迁移版本太多，可能需要：
# 1. 删除并重建（破坏性操作）
bash scripts/db.sh init

# 或者在不影响生产数据的情况下从头测试迁移：
bash scripts/db.sh migration-test
```

### 8.9 Agent 端点返回 503

这是**阶段一的预期行为**。功能开关 `AGENT_ENABLED=false` 导致所有 `/api/v1/agent-runs/*` 端点返回 503，附带消息"Agent subsystem not yet available."（Agent 子系统尚未可用）。DXF 管道和 CAD Worker 端点与其各自的功能开关同理。

### 8.10 Redis 不可用（本地开发）

**症状：** `redis-cli ping` 失败或后端日志显示 Redis 连接错误。

**修复：**
```bash
# 检查服务
sudo systemctl status redis

# 如需启动
sudo systemctl start redis

# 验证
redis-cli ping   # => PONG
```

注意：后端设计上能够容忍 Redis 不可用 —— 它采用惰性连接，所有使用 Redis 的代码路径都有当 Redis 宕机时的回退方案。

---

## 9. 备份与恢复

### 9.1 MySQL 数据库

**备份（本地）：**
```bash
# 使用应用凭据导出
mysqldump -h 127.0.0.1 -u dwg_user -p dwg_agent \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --add-drop-database \
  > dwg_agent_backup_$(date +%Y%m%d_%H%M%S).sql
```

**备份（Docker）：**
```bash
docker compose exec mysql mysqldump \
  -u dwg_user -p"${MYSQL_PASSWORD}" dwg_agent \
  --single-transaction \
  > dwg_agent_backup_$(date +%Y%m%d_%H%M%S).sql
```

**恢复：**
```bash
# 本地
mysql -h 127.0.0.1 -u dwg_user -p dwg_agent < dwg_agent_backup_YYYYMMDD_HHMMSS.sql

# Docker
docker compose exec -T mysql mysql \
  -u dwg_user -p"${MYSQL_PASSWORD}" dwg_agent \
  < dwg_agent_backup_YYYYMMDD_HHMMSS.sql
```

然后运行迁移以确保 schema 是最新的：
```bash
bash scripts/db.sh migrate
```

### 9.2 Docker 卷

**备份卷：**
```bash
# MySQL
docker run --rm -v complete_framework_mysql_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/mysql_data_backup.tar.gz -C /data .

# Redis
docker run --rm -v complete_framework_redis_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/redis_data_backup.tar.gz -C /data .

# MinIO
docker run --rm -v complete_framework_minio_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/minio_data_backup.tar.gz -C /data .
```

**恢复卷：**
```bash
# 先停止服务
docker compose down

# 恢复
docker run --rm -v complete_framework_mysql_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/mysql_data_backup.tar.gz -C /data

docker compose up -d
```

### 9.3 本地文件存储

当 `STORAGE_BACKEND=local` 时，文件存储在 `backend/var/storage/`（相对于 CWD）。备份此目录：

```bash
tar czf storage_backup_$(date +%Y%m%d_%H%M%S).tar.gz -C backend var/storage
```

### 9.4 推荐备份计划

| 资源 | 频率 | 保留期限 |
|---|---|---|
| MySQL 逻辑转储 | 每日 | 30 天 |
| 文件存储（local/minio） | 每日 | 30 天 |
| Docker 卷 | 每周 | 4 周 |
| schema 迁移前 | 手动 | 保留至迁移验证通过 |

---

## 10. 阶段一 限制说明

以下组件在阶段一中**已配置但尚未投入运行**：

| 组件 | 状态 | 行为 | 计划阶段 |
|---|---|---|---|
| **Agent 子系统** | 功能开关关闭 | `/api/v1/agent-runs/*` 返回 503 | 阶段二 |
| **DXF 管道** | 功能开关关闭 | DXF 解析端点返回 503 | 阶段二 |
| **CAD Worker** | 功能开关关闭 | CAD Worker 端点返回 503 | 阶段四 |
| **Agent/DXF Celery Worker** | 仅 Compose profile | `worker-agent` 和 `worker-dxf` 可以启动，但具体任务体被推迟 | 阶段二/三 |
| **MinIO（对象存储）** | Docker 默认启用 | 当 `STORAGE_BACKEND=minio` 时后端使用 MinIO；本地开发仍默认使用本地文件系统 | 部署已完成 |
| **Flower 监控** | 仅 Compose profile | 面板可以监控 `worker-report`；默认有意不暴露外部端口映射 | 阶段二加固 |
| **SSL/TLS（HTTPS）** | 未配置 | Nginx 监听 443 端口但没有 SSL 证书 | 阶段 C |
| **MCP CAD 集成** | 仅存根代码 | `app/mcp_client/` 包含占位模块 | 阶段二 |
| **ZWCAD 集成** | 仅存根代码 | `app/integrations/zwcad/` 包含占位模块 | 阶段四 |
| **Agent 工具注册表** | 空注册表 | `app/agents/tool_registry.py` 为空注册表 | 阶段二 |
| **Repository 层** | 尚未提取 | 业务逻辑在服务中直接读取数据库 | 进行中 |

### 阶段一中正在运行且已验证的功能：

- 完整的 RESTful API，涵盖 `/api/v1` 下的 11 个路由模块
- RBAC 包含 7 个角色、权限和用户-角色映射
- JWT 认证（访问令牌 + 刷新令牌）
- 通过存储后端进行文件上传/下载（本地开发用 local，Docker 中用 MinIO）
- Celery `worker-report` 模拟任务，演示队列 → 运行中 → 成功的作业流程
- 项目、图纸、文件和作业的增删改查操作
- 审计日志（所有变更操作均被记录）
- 数据库迁移（Alembic，3 个版本，17 张表）
- 引导超级管理员种子数据
- 432 条测试通过（pytest + FakeRedis；当 Redis 可用时运行真实 Redis 测试）
- Docker Compose 部署，包含 9 个服务
- Nginx 网关，具备速率限制、安全头和 SPA 回退功能

### 阶段一环境标志参考：

```bash
AGENT_ENABLED=false          # Agent 返回 503
DXF_PIPELINE_ENABLED=false   # DXF 管道返回 503
CAD_WORKER_ENABLED=false     # CAD Worker 返回 503
STORAGE_BACKEND=local        # 本地开发文件系统；Docker 的 .env.docker 使用 minio
CELERY_TASK_ALWAYS_EAGER=false  # 测试中覆盖为 true；运行时使用真实 worker
```

升级到阶段二/三时，仅在实现对应的任务体之后再修改 `AGENT_ENABLED` / `DXF_PIPELINE_ENABLED`，然后启动相应 profile 的 worker。

---

## 附录 A：常用命令速查表

```bash
# ---- 本地开发 ----
bash scripts/start-dev.sh                           # 启动开发模式下的后端 + 前端
bash scripts/stop-all.sh                            # 停止后端 + nginx
bash scripts/status.sh                              # 检查所有服务
bash scripts/db.sh status                           # MySQL 详细状态
bash scripts/db.sh shell                            # MySQL shell
bash scripts/db.sh init                             # 完整数据库初始化
bash scripts/db.sh migration-test                   # 从头测试迁移

# ---- 后端 ----
cd backend && uv run uvicorn app.main:app --reload  # 手动启动后端
cd backend && uv run pytest -q                      # 运行所有测试
cd backend && uv run ruff check app tests           # 代码检查
cd backend && uv run alembic current                # 检查迁移状态
cd backend && uv run alembic upgrade head           # 运行待处理的迁移
cd backend && uv run python -m app.db.init_db       # 手动种子数据库

# ---- 前端 ----
cd frontend && npm ci                               # 安装依赖
cd frontend && npm run dev                          # Vite 开发服务器
cd frontend && npm run build                        # 生产构建
cd frontend && npm run lint                         # ESLint

# ---- Docker ----
docker compose up -d                                # 核心服务
docker compose --profile workers --profile monitoring up -d  # 全栈
docker compose ps                                   # 服务状态
docker compose logs -f <service>                    # 跟踪日志
docker compose down                                 # 停止所有（保留卷）
docker compose down -v                              # 停止并删除卷
docker compose exec backend-api sh                  # 进入后端容器 shell
docker compose exec mysql mysql -u dwg_user -p      # 容器内 MySQL shell

# ---- 健康检查 ----
curl http://127.0.0.1:8000/health                   # 后端（本地）
curl http://localhost:8080/health                   # 通过 Nginx（本地）
curl http://localhost/health                        # 通过 Nginx（Docker）
redis-cli ping                                      # Redis
bash infra/verify.sh                                # 完整基础设施验证
```

## 附录 B：端口映射表

| 端口 | 服务 | 模式 | 协议 |
|---|---|---|---|
| 80 | Nginx | 仅 Docker | HTTP |
| 443 | Nginx（SSL 占位） | 仅 Docker | HTTPS |
| 3306 | MySQL | 两种模式 | MySQL wire |
| 5173 | Vite HMR | 仅本地开发 | HTTP |
| 6379 | Redis/Valkey | 两种模式 | Redis wire |
| 8000 | 后端（FastAPI/Gunicorn） | 两种模式 | HTTP |
| 8080 | Nginx（可选本地） | 仅本地开发 | HTTP |
| 9000 | MinIO API | 仅 Docker | HTTP |
| 9001 | MinIO 控制台 | 仅 Docker | HTTP |
| 5555 | Flower | Docker（profile） | HTTP |
