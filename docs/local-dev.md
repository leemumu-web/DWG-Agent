# 本机开发启动说明

当前阶段默认不使用 Docker。开发环境拆成三个层次：

1. 后端 FastAPI：本机 `uv` 管理 Python 3.12 环境。
2. 数据库：默认 SQLite；需要接近生产时切换 MySQL。
3. 前端 React：本机 `npm` 启动 Vite dev server。

## 后端启动

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv python install 3.12  # 如果本机尚未安装 Python 3.12
uv sync --locked
cp ../.env.example .env
uv run python -m app.db.init_db
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/health
```

## 前端启动

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/frontend
npm ci
npm run dev
```

访问：

```text
http://127.0.0.1:5173
```

## 数据库切换到 MySQL

把 `.env` 中的：

```text
DATABASE_URL=sqlite:///./var/app.db
```

改成：

```text
DATABASE_URL=mysql+pymysql://dwg_user:your_password@127.0.0.1:3306/dwg_agent
```

然后重新执行：

```bash
uv run python -m app.db.init_db
```

## Nginx 启动（阶段 A — 本地开发）

Nginx 可选启动，将前后端统一到 `http://localhost:8080`：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework

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
cd /home/Creeken/Paper/CAD_research/complete_framework

# 前置：配置 .env 密码变量（复制自 .env.example 并修改）
# 前置：frontend/dist/ 已构建

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
