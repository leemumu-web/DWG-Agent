# 本机开发启动说明

> **前置：所有命令在仓库根目录执行。**
> ```bash
> cd /path/to/complete_framework   # 替换为你的实际路径
> ```

当前阶段默认不使用 Docker。开发环境拆成三个层次：

1. 后端 FastAPI：本机 `uv` 管理 Python 3.12 环境。
2. 数据库：默认 MySQL（本机 `127.0.0.1:3306`）；pytest 才使用内存 SQLite test double。
3. 前端 React：本机 `npm` 启动 Vite dev server。

## 后端启动

```bash
# Redis（可选但推荐——无 Redis 时后端以 degraded 模式运行）
sudo pacman -S redis
sudo systemctl enable --now redis

cp .env.example .env
cp .env.example backend/.env
# 修改 .env 和 backend/.env 中所有 CHANGE_ME_* 值，并保持两者一致

cd backend
uv python install 3.12  # 如果本机尚未安装 Python 3.12
uv sync --locked
cd ..

bash scripts/db.sh start
bash scripts/db.sh setup-user   # 首次部署/密码变更时执行
bash scripts/db.sh init
bash scripts/start-dev.sh       # 后端 --reload + Vite HMR
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/health
```

## 前端启动

```bash
cd frontend
npm ci
npm run dev
```

访问：

```text
http://127.0.0.1:5173
```

## MySQL 数据库配置

`.env` / `backend/.env` 中默认使用：

```text
DATABASE_URL=mysql+pymysql://dwg_user:your_password@127.0.0.1:3306/dwg_agent
```

初始化或补齐种子数据：

```bash
bash scripts/db.sh init
```

Docker Compose 不复用本机 `127.0.0.1`，而是通过 `.env.docker` 使用容器服务名 `mysql`。

常用数据库调试入口：

```bash
bash scripts/db.sh status       # 配置、凭据、schema、SQLite 退出状态
bash scripts/db.sh check        # 非破坏性检查，适合 CI/本机验收
bash scripts/db.sh shell        # 使用应用凭据进入 MySQL shell
bash scripts/db.sh logs         # 查看 MySQL/MariaDB systemd 日志
```

## Nginx 启动（阶段 A — 本地开发）

Nginx 可选启动，将前后端统一到 `http://localhost:8080`：

```bash
# 从仓库根目录执行
# 1. 确认后端已启动（127.0.0.1:8000）
# 2. 确认前端已构建（frontend/dist/ 存在）

# 语法检查
sudo nginx -t -c $(pwd)/infra/nginx/nginx.local.conf

# 启动
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf

# 重载配置（不中断服务）
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s reload

# 停止
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit
```

启动后 Nginx 将：

| 访问路径 | 行为 |
|---------|------|
| `http://localhost:8080` | React SPA（BrowserRouter fallback） |
| `http://localhost:8080/api/v1/*` | 反向代理到 FastAPI :8000 |
| `http://localhost:8080/health` | 健康检查代理 |

详见 `infra/nginx/README.md`。

## Docker Compose 启动（阶段 B）

```bash
# 从仓库根目录执行
# 前置：从 Docker 专用模板创建 .env.docker 并修改密码（首次）
cp .env.docker.example .env.docker
# 编辑 .env.docker 中所有 CHANGE_ME_* 值

# 前置：frontend/dist/ 已构建
cd frontend && npm run build && cd ..

# 核心服务
docker compose up -d

# 完整平台（含 Worker + Flower）
docker compose --profile workers --profile monitoring up -d

# 查看状态
docker compose ps
docker compose logs -f nginx backend-api

# 停止
docker compose down
```

访问: `http://localhost`

## 本阶段不启动的组件

| 组件 | 说明 |
|------|------|
| Agent 内部工具链 | `AGENT_ENABLED=false`，API 返回 503 |
| DXF 解析 Worker | `DXF_PIPELINE_ENABLED=false` |
| Windows CAD Worker | `CAD_WORKER_ENABLED=false` |
| Celery Workers | compose 中定义为 `profiles: [workers]`，阶段二实现 |
| MinIO | compose 可用但后端使用 local 存储 |

这些模块已保留配置、目录和接口边界，后续逐步接入。
