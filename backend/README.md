# Backend

FastAPI 后端，本阶段本机运行，不使用 Docker。Python 固定为 3.12。

```bash
uv python install 3.12  # 如果本机尚未安装 Python 3.12
uv sync --locked
cp ../.env.example .env
uv run python -m app.db.init_db
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

OpenAPI：

```text
http://127.0.0.1:8000/docs
```
