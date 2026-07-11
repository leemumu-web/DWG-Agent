# Backend

FastAPI, SQLAlchemy, Celery, MySQL, and storage integration for DWG-Agent. Python is constrained
to 3.12.

## Local Development

```bash
uv python install 3.12
uv sync --locked
cp ../.env.example .env   # configure real local credentials
uv run alembic upgrade head
uv run python -m app.db.init_db
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

OpenAPI is available at `http://127.0.0.1:8010/docs`. Prefer repository scripts for the complete
local topology: `bash scripts/start-all.sh`, `bash scripts/status.sh`, and
`bash scripts/stop-all.sh` from the repository root.

## Verification

```bash
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
cd .. && bash scripts/db.sh migration-test
```

Pytest uses isolated SQLite for unit/API tests. `migration-test` creates and removes a temporary
real MySQL schema and verifies the full Alembic chain. Compose uses MySQL for application data,
Celery SQL transport, and Celery results; it uses MinIO for object bytes.

Excel Final runs its standalone Stage in an isolated subprocess. Inputs are Tekla delimited
exports or workbooks with the required steel-list schema; locked `xlrd` provides legacy binary
`.xls` parsing.
