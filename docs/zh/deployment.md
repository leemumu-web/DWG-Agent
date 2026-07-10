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
| MySQL 或 MariaDB | 8.x | 应用数据库及 Celery SQL 传输/结果后端 |
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
# 其他数据库默认值使用 127.0.0.1:3306

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
                          ┌─────────────────┴─────────────────┐
                          ▼                                   ▼
                     ┌──────────────────┐                ┌──────────┐
                     │ MySQL :3306      │                │ 本地文件 │
                     │ 应用 + Celery SQL│                │ ./var/   │
                     └──────────────────┘                └──────────┘
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

### 3.7 MySQL 持久化运行状态

MySQL 是唯一运行数据库。应用有效 DSN 来自可选 `DATABASE_URL`，否则由 `MYSQL_*` 组件字段构造。Celery URL 从该有效 MySQL DSN 派生：

- Broker：`sqla+mysql+pymysql://...`
- Result backend：`db+mysql+pymysql://...`
- JWT 撤销：`token_blacklist`
- 密码变更撤销：`sys_users.password_changed_at`
- Agent 记忆：`agent_memory`
- 持久化 SSE 进度：`jobs.progress_data`

不要另行配置 broker/result URL。Kombu SQLAlchemy 传输不支持 fanout remote control，因此 worker 健康检查使用进程检查，而不是 `celery inspect ping`。

### 3.8 测试

```bash
cd backend
uv run ruff check app tests    # 代码检查（必须通过）
uv run pytest -q               # 约 599 条测试（必须通过）
```

单元/API 测试使用 SQLite 内存数据库（`StaticPool`）。迁移与运行验收还会连接本地 MySQL，并启动真实的 MySQL-backed Celery worker。

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
            │   ┌──────────────▼─┐ ┌────▼────┐             │
            │   │ mysql 8.4      │ │ minio   │             │
            │   │ 应用 + Celery  │ │ latest  │             │
            │   └────────────────┘ └─────────┘             │
            │                                              │
            │   ┌──────────┐ ┌──────────┐ ┌───────────┐   │
            │   │ worker-  │ │ worker-  │ │ worker-   │   │
            │   │ agent    │ │ dxf      │ │ report    │   │
            │   │ (profile)│ │ (profile)│ │ default   │   │
            │   └──────────┘ └──────────┘ └───────────┘   │
            │                                              │
            │   workers profile 还会启动 dxf2dwg、         │
            │   dxf2excel 与 excel_final 专用 worker。      │
            └──────────────────────────────────────────────┘
```

### 4.2 服务总结

| 服务 | 镜像 | 端口 | Profile | 健康检查 |
|---|---|---|---|---|
| `nginx` | `ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine` | 80→8080, 443→8443 | -- | depends_on backend-api 健康 |
| `backend-api` | 自构建 | 8000（内部） | -- | 每 10 秒 `curl /health/ready` |
| `mysql` | `container-registry.oracle.com/mysql/community-server:8.4` | 3306（内部） | -- | 每 10 秒 `mysqladmin ping` |
| `minio` | `quay.io/minio/minio:latest` | 9000, 9001（内部） | -- | `curl /minio/health/live` |
| `worker-report` | 自构建 | -- | -- | Celery 进程检查 |
| `worker-agent` | 自构建 | -- | `workers` | Celery 进程检查 |
| `worker-dxf` | 自构建 | -- | `workers` | Celery 进程检查 |
| `worker-dxf2dwg` | 自构建 | -- | `workers` | Celery 进程检查 |
| `worker-dxf2excel` | 自构建 | -- | `workers` | Celery 进程检查 |
| `worker-excel-final` | 自构建 | -- | `workers` | Celery 进程检查 |

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

此命令启动 nginx、backend-api、mysql、minio 以及默认 report 队列的 `worker-report`。

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

**第 5 步（可选）：启动功能 Worker**

```bash
# Agent、DXF、DXF→DWG、DXF→Excel 和 excel_final worker
docker compose --profile workers up -d
```

### 4.4 Dockerfile 详情

位于 `backend/Dockerfile`（多阶段构建）。**构建上下文 = 仓库根**（`compose.yaml` 中 `context: .`、`dockerfile: backend/Dockerfile`），而非 `./backend`，因为四个 `Stages/` 包均为 editable path dependency。根上下文让 Dockerfile 可复制这些源码并执行 `uv sync --frozen`。

**阶段 1（builder）：**
- 基础镜像：`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
- 使用基础镜像自带的 uv；无需 `uv:latest` 复制阶段
- `WORKDIR /app`；复制依赖清单及 `Stages/{dwg2dxf,dxf2dwg,dxf2excel,excel_final}`
- 运行 `uv sync --frozen --no-dev` 创建 `/app/.venv`；运行依赖由 `uv.lock` 锁定

**阶段 2（runtime）：**
- 基础镜像：`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
- 创建非 root 用户 `appuser`（uid 1000）
- 安装运行时系统依赖：`curl` + `ca-certificates`（健康检查）、`xvfb`（ODA AppImage 所需的无头 X）、`libfuse2`（AppImage FUSE 解压）
- `ENV ODA_HOME=/app/oda` —— `dwg_converter.check_env` 在此定位 ODA 二进制
- 从 builder 复制 `.venv` 和 `Stages/`（editable `.pth` 指向此处），然后复制 `app/`、`alembic.ini`、`migrations/`
- 将 85 MB 的 ODA File Converter AppImage 复制到 `/app/oda`（所有者为 `appuser`）—— 启用后 DXF/agent worker 管线即就绪
- 创建可写目录 `/app/var/` 和 `/home/appuser`，所有者为 `appuser`
- HEALTHCHECK：每 15 秒 `curl -f http://localhost:8000/health`，超时 3s，5 次重试，`start-period=40s`（容忍 alembic + 种子 + gunicorn 启动）
- CMD：`alembic upgrade head && python -m app.db.init_db && exec gunicorn app.main:app --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --access-logfile - --error-logfile -`
  - `init_db` 幂等地种子角色/权限/超级管理员（已存在则跳过）—— 首次部署即可用 `admin` + `SUPER_ADMIN_PASSWORD` 登录。

仓库根级的 `.dockerignore`（位于仓库根）会从构建上下文中排除每个包的 `.venv/`、`build/`、`__pycache__/`、`samples/`、`logs/`、`frontend/node_modules/`、`Data/`、`*.zip` 以及密钥文件（`.env`、`.env.docker`）。旧的 `backend/.dockerignore` 已删除，因为 dockerignore 必须位于上下文根目录。

### 4.5 卷

| 卷 | 挂载点 | 用途 | 持久性 |
|---|---|---|---|
| `mysql_data` | `/var/lib/mysql` | MySQL 数据文件 | `docker compose down` 后仍然保留 |
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
docker compose --profile workers down

# 停止并删除卷（完全重置）
docker compose --profile workers down -v
```

---

## 5. 配置参考

所有配置均通过环境变量驱动。规范定义位于 `backend/app/core/config.py`（pydantic-settings，62 个字段 + 6 个计算属性）。

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
| `DATABASE_URL` | 未设置 | 未设置 | 可选的权威 MySQL DSN 覆盖 |
| `MYSQL_HOST` | `127.0.0.1` | `mysql` | 主机名 |
| `MYSQL_PORT` | `3306` | `3306` | 端口号 |
| `MYSQL_DATABASE` | `dwg_agent` | `dwg_agent` | 数据库名称 |
| `MYSQL_USER` | `dwg_user` | `dwg_user` | 应用用户 |
| `MYSQL_PASSWORD` | （必填） | （必填） | 应用用户密码 |
| `MYSQL_ROOT_PASSWORD` | （必填） | （必填） | MySQL root 密码 [^1] |

[^1]: `MYSQL_ROOT_PASSWORD` 是**基础设施专用变量**。它被 `compose.yaml` 用于 MySQL 容器的健康检查（`mysqladmin ping -u root -p`）。它**不是** `backend/app/core/config.py` 中的字段（config.py 设了 `extra="ignore"`）—— 后端应用程序永远不会读取它。它只在 `.env.example` 和 `.env.docker.example` 中定义，供 Compose 使用。

**计算属性：`settings.mysql_url`** —— 由各组件字段组装，密码经 URL 编码。
**有效值：`settings.sqlalchemy_database_url`** —— 设置了 `DATABASE_URL` 时使用该值，否则使用 `settings.mysql_url`。

连接池设置（硬编码在 `app/db/session.py` 中，仅限 MySQL）：
- `pool_recycle=3600`
- `pool_size=10`
- `max_overflow=20`

### 5.3 Celery 与持久化运行状态

| 变量 | 本地默认值 | Docker 默认值 | 说明 |
|---|---|---|---|
| `CELERY_TASK_ALWAYS_EAGER` | `false` | `false` | 同步执行任务（测试中覆盖为 `true`） |
| `AGENT_MEMORY_TTL` | `7200` | `7200` | MySQL Agent 记忆保留秒数 |
| `AGENT_MAX_MESSAGES` | `20` | `20` | 每个 Agent 会话最多消息数 |

Celery 端点在 `config.py` 中从有效 MySQL DSN 派生，因此应用、broker 和 result 配置不会漂移：

- `settings.celery_broker_url`：`sqla+mysql+pymysql://...`
- `settings.celery_result_backend`：`db+mysql+pymysql://...`

环境中不再存在独立的 `CELERY_BROKER_URL` 或 `CELERY_RESULT_BACKEND` 键。Celery 自有表随 MySQL 一起备份；结果记录保留 24 小时，并在 worker 启动时清理。

### 5.4 存储

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` 或 `minio` |
| `LOCAL_STORAGE_ROOT` | `./var/storage` | 本地文件系统路径（相对于 CWD） |
| `MAX_UPLOAD_SIZE_MB` | `512` | 最大单次上传大小（与 Nginx `client_max_body_size 512m` 一致） |
| `MAX_ZIP_EXTRACT_MB` | `2048` | 解压 ZIP 时的最大解压总大小（仅 config.py -- 不在 `.env` 模板中） |
| `MAX_ZIP_ENTRY_COUNT` | `1000` | 单个 ZIP 内最大文件数（仅 config.py -- 不在 `.env` 模板中） |

### 5.5 MinIO（对象存储）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO 服务器端点 |
| `MINIO_ACCESS_KEY` / `MINIO_ROOT_USER` | （必填） | MinIO 访问密钥 |
| `MINIO_SECRET_KEY` / `MINIO_ROOT_PASSWORD` | （必填） | MinIO 密钥 |
| `MINIO_BUCKET_ORIGINAL` | `dwg-original` | 上传的 DWG 文件 |
| `MINIO_BUCKET_DERIVED` | `dwg-derived` | 处理后的衍生文件（DXF→DWG 输出 + 桩 JSON 结果） |
| `MINIO_BUCKET_REPORTS` | `dwg-reports` | 生成的报告（DXF→Excel `.xlsx`） |
| `MINIO_BUCKET_TEMP` | `dwg-temp` | 临时文件（预留） |
| `MINIO_BUCKET_DXF_ORIGINAL` | `dxf-original` | 上传的非 DWG（如 `.dxf`）文件（仅 config.py -- 不在 `.env` 模板中） |
| `MINIO_BUCKET_DXF_DERIVED` | `dxf-derived` | DWG→DXF 转换输出（仅 config.py -- 不在 `.env` 模板中） |

Docker Compose 将 `MINIO_ROOT_USER` 同时传递给 `MINIO_ACCESS_KEY` 和 `MINIO_ROOT_USER`，将 `MINIO_ROOT_PASSWORD` 同时传递给 `MINIO_SECRET_KEY` 和 `MINIO_ROOT_PASSWORD`。

### 5.6 JWT 认证

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JWT_SECRET_KEY` | `change-me-in-dev-change-me-in-prod-32chars` | **必须修改** —— 至少 32 个随机字符 |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | 访问令牌 TTL |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `14` | 刷新令牌 TTL |
| `REFRESH_COOKIE_SECURE` | （未设置 → 自动） | `dwg_refresh_token` cookie 的 Secure 标志。未设置 = 自动（仅当 `APP_ENV=production` 时为 Secure）。HTTP-only 内网部署设为 `false`，避免浏览器丢弃该 cookie 而导致刷新流程静默失效。两份 `.env` 模板中均已注释掉。通过 `refresh_cookie_secure_enabled` 属性解析。 |

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
| `AGENT_ENABLED` | `false` | 为 false 时，全部四个 agent 端点（`POST /api/v1/agent-runs`、`GET /api/v1/agent-runs/{id}`、`GET /api/v1/agent-runs/{id}/steps`、`GET /api/v1/agent-tools`）返回 503 `AGENT_DISABLED` |
| `DXF_PIPELINE_ENABLED` | `false` | 为 false 时，`task_type=convert_dwg_to_dxf` 的 `POST /api/v1/jobs` 返回 503 `DXF_PIPELINE_DISABLED` |
| `DXF2DWG_PIPELINE_ENABLED` | `false` | 为 false 时，`task_type=convert_dxf_to_dwg` 的 `POST /api/v1/jobs` 返回 503 `DXF2DWG_PIPELINE_DISABLED` |
| `DXF2EXCEL_PIPELINE_ENABLED` | `false` | 为 false 时，`task_type=extract_dxf_to_excel` 的 `POST /api/v1/jobs` 返回 503 `DXF2EXCEL_PIPELINE_DISABLED` |
| `CAD_WORKER_ENABLED` | `false` | 在 `GET /api/v1/system/health` 的 features 中呈现；**不**直接拦截任何 HTTP 端点（在 worker/管线层强制） |

这五个开关默认均为 `false`。它们按 Python 布尔值解析（`true`/`false`，不区分大小写）。仅 `AGENT_ENABLED`、`DXF_PIPELINE_ENABLED` 和 `CAD_WORKER_ENABLED` 出现在 `.env` 模板中；`DXF2DWG_PIPELINE_ENABLED` 和 `DXF2EXCEL_PIPELINE_ENABLED` 仅定义于 `config.py` -- 需手动添加以覆盖默认值。

### 5.9 ODA 转换器（DWG↔DXF 引擎）

以下参数驱动 DWG→DXF 和 DXF→DWG 管线所用的 ODA File Converter 子进程。**它们均不出现在 `.env` 模板中** -- 除非显式设置，否则按下方 `config.py` 默认值运行。后端 Dockerfile 会将 ODA AppImage 内置进镜像的 `/app/oda`，并通过 `ENV` 设置 `ODA_HOME`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ODA_CONVERTER_VERSION` | `ACAD2018` | DWG→DXF 输出 CAD 版本 |
| `ODA_CONVERTER_AUDIT` | `true` | 对 DWG→DXF 执行 ODA 审计 |
| `ODA_CONVERTER_TIMEOUT` | `300` | DWG→DXF 转换超时（秒） |
| `ODA_CONVERTER_RETRIES` | `1` | DWG→DXF 重试次数 |
| `ODA_XVFB_RUN` | `true` | 用 `xvfb-run` 包装 ODA（无头 X） |
| `DXF2DWG_CONVERTER_VERSION` | `ACAD2018` | DXF→DWG 输出 CAD 版本 |
| `DXF2DWG_CONVERTER_AUDIT` | `true` | 对 DXF→DWG 执行 ODA 审计 |
| `DXF2DWG_CONVERTER_TIMEOUT` | `300` | DXF→DWG 转换超时（秒） |
| `DXF2DWG_CONVERTER_RETRIES` | `1` | DXF→DWG 重试次数 |
| `ODA_HOME` | （空） | ODA 安装路径；`check_env.py` 优先读取 `$ODA_HOME`。Dockerfile 设为 `ODA_HOME=/app/oda` |

### 5.10 LLM（阶段二）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MODEL_NAME` | `deepseek-chat` | LLM 模型标识符 |
| `MODEL_API_KEY` | （空） | LLM 提供商的 API 密钥 |
| `MODEL_BASE_URL` | `https://api.deepseek.com` | LLM API 基础 URL |

### 5.11 MCP（阶段二）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MCP_CAD_COMMAND` | `uvx` | MCP 客户端命令 |
| `MCP_CAD_ARGS` | `cad-mcp-server,stdio` | MCP 客户端参数 |

### 5.12 CAD Worker（阶段四）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CAD_WORKER_API_BASE` | `http://cad-worker.internal:8080` | Windows CAD Worker 端点 |
| `CAD_WORKER_API_KEY` | （空） | CAD Worker 认证密钥 |

### 5.13 前端

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
| `start_report_worker` | 启动本地 Celery `worker-report`（`-Q report --concurrency=1`），PID `/tmp/dwg-agent-worker-report.pid` |
| `start_dxf_worker` | 启动本地 Celery `worker-dxf`（`-Q dxf --concurrency=2`），PID `/tmp/dwg-agent-worker-dxf.pid` |
| `start_dxf2dwg_worker` | 启动本地 Celery `worker-dxf2dwg`（`-Q dxf2dwg --concurrency=2`），PID `/tmp/dwg-agent-worker-dxf2dwg.pid` |
| `start_dxf2excel_worker` | 启动本地 Celery `worker-dxf2excel`（`-Q dxf2excel --concurrency=1`），PID `/tmp/dwg-agent-worker-dxf2excel.pid` |
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
- `migration-test` 创建临时 `dwg_agent_migration_test_<pid>` 数据库，从空 schema 运行完整 Alembic 链，验证 22 张业务表、持久化状态列和精确 Alembic head，然后删除临时数据库。

### `start-dev.sh` —— 开发模式

```
用法: bash scripts/start-dev.sh
```

- 启动 MySQL、初始化数据库，启动五个本地 Celery worker（`worker-report`、`worker-dxf`、`worker-dxf2dwg`、`worker-dxf2excel`、`worker-excel-final`）、后端（`uvicorn --reload` 监听 `:8000`）和前端（Vite HMR 监听 `:5173`）。
- 为每个 worker、后端和前端在 `/tmp/dwg-agent-*.pid` 写入其拥有的 PID 文件。
- 自动检测 `VITE_API_BASE_URL` 是否为空（Nginx 模式），并临时将其设置为 `http://127.0.0.1:8000` 以直接访问后端。
- 阻塞等待直到 Ctrl+C（使用 `wait`），然后打印停止说明。

### `start-all.sh` —— 全栈（Nginx 网关）

```
用法: bash scripts/start-all.sh [--rebuild]
```

- 启动 MySQL、本地 Celery `worker-report`、后端（`uvicorn --reload` 监听 `:8000`），按需构建前端，并启动 Nginx 监听 `:8080`。
- `--rebuild` 标志强制重新构建前端，即使 `dist/` 已存在。
- 统一访问：`http://localhost:8080`（通过 Nginx 提供 SPA + API + 健康检查）。
- 如果 8080 端口被未知进程占用则停止。

### `stop-all.sh` —— 优雅关闭

```
用法: bash scripts/stop-all.sh
```

- 停止 Nginx（通过 `nginx -s quit`）。
- 通过 PID 文件 `/tmp/dwg-agent-backend.pid` 终止后端。
- 通过各自 PID 文件停止全部本地管理的 Celery worker。
- 验证 8000 端口已释放。
- **不**停止 MySQL（它是共享基础设施）。

### `status.sh` —— 健康检查聚合

```
用法: bash scripts/status.sh
```

检查项目：
1. MySQL 状态（通过 `db.sh status`）
2. Celery worker-report PID
3. 后端端口 8000 + `GET /health/ready`
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

出于安全考虑，该端点**不**暴露内部数据库细节或凭据。

### 7.2 Docker 健康检查

| 服务 | 检查方式 | 间隔 | 超时 | 重试次数 |
|---|---|---|---|---|
| `backend-api` | `curl -f http://localhost:8000/health/ready` | 10s | 3s | 5 |
| `mysql` | `mysqladmin ping -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}"` | 10s | 3s | 5 |
| `minio` | `curl -f http://localhost:9000/minio/health/live` | 10s | 3s | 5 |
| 全部 Celery worker | `grep -aq celery /proc/1/cmdline` | 10s | 3-8s | 5 |

Nginx 通过 `depends_on` `backend-api` 并设置 `condition: service_healthy`，因此在后端健康之前 Nginx 不会启动（也不会接受流量）。

### 7.3 Dockerfile 健康检查

后端 Dockerfile 内置了面向容器运行时的 HEALTHCHECK 指令：

```dockerfile
HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=40s \
    CMD curl -f http://localhost:8000/health || exit 1
```

这在容器内部运行，独立于 Docker Compose 的健康检查。

### 7.4 Nginx 监控要点

- **访问日志格式：** `extended` 格式包含 `$request_id`、`$request_time`、`$upstream_connect_time`、`$upstream_header_time`、`$upstream_response_time`。
- **认证日志：** 登录端点（`/api/v1/auth/sessions`）在 Docker 中使用相同的 stdout 访问流；本地/系统 Nginx 部署可根据配置将其路由到专用的认证日志文件。
- **速率限制：** 登录端点限速 2 req/s（突发 3），通用 API 限速 100 req/s（突发 20）—— 超过限制时均返回 HTTP 429。
- **健康检查端点：** 对 `/health` 设置 `access_log off` 以减少日志噪音。

### 7.5 Celery SQL 监控

Kombu SQLAlchemy 传输不支持 fanout/remote control，因此部署中不提供基于 inspect 的面板。worker 存活通过容器/进程健康检查监控；任务生命周期通过 `jobs` 表和 API 监控；队列与结果增长通过 `kombu_message`、`celery_taskmeta` 监控。应用指标应由专用指标导出器提供，而不是依赖 Celery remote control。

### 7.6 基础设施验证

`infra/verify.sh` 脚本执行全面的静态 + 运行时验证：

```bash
bash infra/verify.sh
```

检查项（6 个部分）：
1. **Nginx 配置** —— 语法验证、关键指令（upstream、速率限制、安全头、SPA 回退）
2. **Docker Compose** —— 服务数量（10）、镜像版本、卷挂载、环境变量空值检查、健康检查、profiles
3. **Dockerfile** —— 多阶段构建、非 root 用户、HEALTHCHECK、STOPSIGNAL、gunicorn CMD
4. **MySQL 集成** —— 数据库可访问性、当前业务/运行表、持久化列、角色种子数据、管理员用户、授权与应用凭据
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

# 检查 backend/.env 是否存在且值正确
cat backend/.env | grep DATABASE_URL

# 尝试手动启动后端以查看错误信息
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**常见原因：**
- MySQL 未运行（`sudo systemctl start mariadb` 或 `mysqld`）
- `.env` / `backend/.env` 中的 `MYSQL_PASSWORD` 不正确
- 数据库未初始化（`bash scripts/db.sh init`）

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

这是**阶段一的预期行为**。功能开关 `AGENT_ENABLED=false` 导致所有 `/api/v1/agent-runs/*` 端点返回 503（错误码 `AGENT_DISABLED`），附带消息"Agent subsystem is intentionally disabled in stage 1."（Agent 子系统在阶段一被有意禁用）。DXF 管道和 CAD Worker 端点与其各自的功能开关同理。

### 8.10 Celery SQL 队列不推进

**症状：** 作业长期停留在 `queued`，或 worker 日志报告 MySQL 传输错误。

**修复：** 检查 `/health/ready`、应用 MySQL 凭据、对应队列 worker 进程及日志。不要使用 `celery inspect`；SQLAlchemy 传输不支持 remote control。确认 worker 消费了任务路由对应的队列：`report`、`dxf`、`dxf2dwg`、`dxf2excel` 或 `excel_final`。

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
| **DXF 管道** | 功能开关关闭 | `POST /api/v1/jobs` 的 DWG↔DXF / DXF→Excel 转换任务返回 503 | 阶段三 |
| **CAD Worker** | 功能开关关闭 | CAD Worker 端点返回 503 | 阶段四 |
| **Agent Worker** | 仅 Compose profile | 队列已存在；Agent 实现仍受功能开关限制 | 阶段二 |
| **DXF/excel_final Worker** | 仅 Compose profile | 真实的队列专用任务体；仅在流水线开关和依赖就绪后启用 | 已实现 |
| **MinIO（对象存储）** | Docker 默认启用 | 当 `STORAGE_BACKEND=minio` 时后端使用 MinIO；本地开发仍默认使用本地文件系统 | 部署已完成 |
| **SSL/TLS（HTTPS）** | 未配置 | Nginx 监听 443 端口但没有 SSL 证书 | 阶段 C |
| **MCP CAD 集成** | 仅存根代码 | `app/mcp_client/` 包含占位模块 | 阶段二 |
| **ZWCAD 集成** | 仅存根代码 | `app/integrations/zwcad/` 包含占位模块 | 阶段四 |
| **Agent 工具注册表** | 空注册表 | `app/agents/tool_registry.py` 为空注册表 | 阶段二 |
| **Repository 层** | 尚未提取 | 业务逻辑在服务中直接读取数据库 | 进行中 |

### 阶段一中正在运行且已验证的功能：

- 完整的 RESTful API，涵盖 `/api/v1` 下的 12 个路由模块
- RBAC 包含 7 个角色、权限和用户-角色映射
- JWT 认证（访问令牌 + 刷新令牌）
- 通过存储后端进行文件上传/下载（本地开发用 local，Docker 中用 MinIO）
- Celery `worker-report` 模拟任务，演示队列 → 运行中 → 成功的作业流程
- 项目、图纸、文件和作业的增删改查操作
- 审计日志（所有变更操作均被记录）
- 数据库迁移（Alembic，6 个版本，22 张业务表）
- 引导超级管理员种子数据
- 后端单元/集成测试，以及真实 MySQL/Celery 验收探针
- Docker Compose 部署，包含 10 个服务
- Nginx 网关，具备速率限制、安全头和 SPA 回退功能

### 阶段一环境标志参考：

```bash
AGENT_ENABLED=false          # Agent 返回 503
DXF_PIPELINE_ENABLED=false   # DXF 管道返回 503
DXF2DWG_PIPELINE_ENABLED=false
DXF2EXCEL_PIPELINE_ENABLED=false
EXCEL_FINAL_PIPELINE_ENABLED=false
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
docker compose --profile workers up -d              # 核心服务 + 功能 worker
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
bash infra/verify.sh                                # 完整基础设施验证
```

## 附录 B：端口映射表

| 端口 | 服务 | 模式 | 协议 |
|---|---|---|---|
| 80 | Nginx | 仅 Docker | HTTP |
| 443 | Nginx（SSL 占位） | 仅 Docker | HTTPS |
| 3306 | MySQL | 两种模式 | MySQL wire |
| 5173 | Vite HMR | 仅本地开发 | HTTP |
| 8000 | 后端（FastAPI/Gunicorn） | 两种模式 | HTTP |
| 8080 | Nginx（可选本地） | 仅本地开发 | HTTP |
| 9000 | MinIO API | 仅 Docker | HTTP |
| 9001 | MinIO 控制台 | 仅 Docker | HTTP |
