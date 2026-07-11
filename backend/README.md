# Backend / 后端

## English

The backend owns FastAPI routes, synchronous SQLAlchemy services, MySQL/Alembic schema, Celery tasks, Local/MinIO adapters, authentication/authorization, and processing orchestration. Python is pinned to 3.12 for the platform package.

It does not own React UI, Nginx/TLS, Stage algorithms, or a completed Agent/Windows CAD implementation. Redis/Valkey is not a dependency or fallback.

### Setup

```bash
uv python install 3.12
uv sync --locked
cp ../.env.example .env
uv run alembic upgrade head
uv run python -m app.db.init_db
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

`uv sync` depends on three paths under `../Stages`. `Stages/dxf2excel` is currently a broken parent-repository gitlink; this populated checkout can resolve it, but a clean clone cannot until ownership is repaired.

Development/debug exposes `/docs`, `/redoc`, and `/openapi.json`. Production settings disable them. `/health` is liveness; `/health/ready` requires MySQL and configured storage.

### Runtime Rules

- MySQL is authoritative and also backs Celery SQL transport/results.
- Routes handle HTTP; services own transactions; tasks call services.
- Worker state writes match status + attempt.
- Storage APIs own bytes; MySQL owns metadata and SHA-256.
- Result/file access is always rechecked server-side.
- Agent/CAD task modules are placeholders; keep their flags false.

### Verification

```bash
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
uv run python ../scripts/check_docs.py
cd .. && bash scripts/db.sh migration-test
```

Pytest uses in-memory SQLite for isolated logic/API coverage. `migration-test` is the empty-schema MySQL proof; neither replaces a real broker/storage/browser workflow.

## 中文

后端负责 FastAPI 路由、同步 SQLAlchemy service、MySQL/Alembic schema、Celery task、Local/MinIO adapter、认证授权和处理编排。平台包固定使用 Python 3.12。

它不负责 React UI、Nginx/TLS、Stage 内部算法，也没有完成 Agent/Windows CAD 实现。Redis/Valkey 不是依赖或 fallback。

### 初始化

```bash
uv python install 3.12
uv sync --locked
cp ../.env.example .env
uv run alembic upgrade head
uv run python -m app.db.init_db
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

`uv sync` 依赖 `../Stages` 下三个路径。`Stages/dxf2excel` 当前是损坏的父仓库 gitlink；已填充 checkout 可以解析，但修复归属前 clean clone 不可复现。

development/debug 暴露 `/docs`、`/redoc`、`/openapi.json`，生产设置关闭它们。`/health` 是 liveness；`/health/ready` 要求 MySQL 和已配置存储可用。

### 运行规则

- MySQL 是权威状态，并承载 Celery SQL transport/result。
- Route 处理 HTTP；service 负责 transaction；task 调用 service。
- Worker 状态写入匹配 status + attempt。
- Storage API 管字节；MySQL 管 metadata 和 SHA-256。
- Result/file access 始终由服务端重新检查。
- Agent/CAD task module 是占位；对应 flag 保持 false。

### 验证

```bash
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
uv run python ../scripts/check_docs.py
cd .. && bash scripts/db.sh migration-test
```

Pytest 使用内存 SQLite 覆盖隔离逻辑/API。`migration-test` 是空 schema MySQL 证据；两者都不能替代真实 broker/storage/browser 工作流。
