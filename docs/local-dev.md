# 本机开发启动说明

当前阶段不使用 Docker。开发环境拆成三个层次：

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

## 本阶段不启动的组件

- Redis
- Celery
- MinIO
- Docker Compose
- Nginx
- Agent 内部工具链
- DXF 解析 Worker
- Windows CAD Worker

这些模块已保留配置、目录和接口边界，后续逐步接入。
