# 后端

后端基于 Python 3.12、FastAPI、SQLAlchemy、Alembic 和 Celery，负责 HTTP API、认证授权、MySQL 事务、Local/MinIO 存储适配、Job 状态机和 Stage 调用。React、Nginx/TLS 与 Stage 内部算法不属于后端。Redis/Valkey 不是依赖或故障降级路径。

```bash
uv python install 3.12
uv sync --locked
cp ../.env.example .env
uv run alembic upgrade head
uv run python -m app.bootstrap.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

`uv sync` 依赖父仓库正常跟踪的 `../Stages/dwg2dxf`、`../Stages/dxf2dwg` 和 `../Stages/dxf2excel`。三者都是 editable path dependency；不得只复制 `backend/` 单独安装，也不得把 Stage 改回不可还原的 gitlink。

运行规则：`main.py` 只保留稳定 ASGI facade，`bootstrap/` 装配应用、模型与任务，`platform/` 提供数据库、HTTP、配置、消息、安全和存储技术接口，业务逐步归入 `modules/`。Identity/projects/files 已完成归域，跨领域代码只导入各自 `interface.py`。过渡期 route/service/task 仍保持原有事务边界；worker 的领取、进度、终态和恢复写入必须匹配 status + attempt；Local/MinIO 保存字节，MySQL `files` 保存权限元数据和 SHA-256，`file_transfers` 保存补偿账本。Agent/CAD task 是占位，相关 flag 保持 false。

development/debug 暴露 `/docs`、`/redoc` 和 `/openapi.json`，production 且 `DEBUG=false` 时关闭。`/health` 仅表示进程存活；`/health/ready` 同时探测 MySQL 和已配置存储。

```bash
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
uv run python ../scripts/docs/check.py
cd .. && bash scripts/db.sh migration-test
```

SQLite pytest、空 MySQL migration 和真实 broker/storage/browser E2E 是不同证据层，不能互相替代。详细边界见[架构](../docs/architecture/overview.md)和[开发说明](../docs/guides/development.md)。
