# Development

> Chinese mirror: [zh/development.md](zh/development.md)

## Prerequisites and Setup

- Python 3.12 and `uv`
- Node.js/npm matching `frontend/package-lock.json`
- MySQL 8.x or compatible MariaDB
- Nginx for gateway checks and Docker for Compose/MinIO acceptance

```bash
cp .env.example .env
cp .env.example backend/.env
bash scripts/db.sh setup-user
bash scripts/db.sh init
cd backend && uv sync --frozen
cd ../frontend && npm ci
```

Runtime `.env` files must use MySQL. Tests set `DATABASE_URL=sqlite://` explicitly and isolate each test with an in-memory `StaticPool`.

## Repository Map

```text
backend/app/api/v1/       FastAPI routes and dependency boundaries
backend/app/services/     Business state transitions
backend/app/models/       SQLAlchemy models
backend/app/schemas/      Pydantic request/response models
backend/app/storage/      Local and MinIO adapters
backend/app/workers/      Celery app and task wrappers
backend/migrations/       Alembic history
frontend/src/api/         Axios clients and pagination helpers
frontend/src/features/    Page workflows
frontend/tests/e2e/       Playwright browser/API tests
Stages/                    CAD and Excel processing projects
infra/                     Nginx/MySQL/deployment verification
scripts/                   Local operations and doc generation
```

## Run

```bash
bash scripts/start-dev.sh
# Frontend :5173, API :8010

bash scripts/start-all.sh --rebuild
# Nginx :8080 -> API :8010
```

Do not use port 8000 for local scripts. It is the container-internal API port. If Vite uses 5174, set `PLAYWRIGHT_FRONTEND_BASE_URL` accordingly.

## Backend Workflow

1. Route validates input and calls permission helpers.
2. Service owns state transitions and transaction semantics.
3. Commit before dispatching a Celery task.
4. Worker atomically claims `queued + attempt`.
5. Every worker update includes the captured attempt.
6. Object writes register rollback compensation.
7. Public errors are stable and sanitized.

Never add a second session inside a worker failure handler when the current session has uncommitted steps. Failure step and terminal job state belong to one transaction.

## API and Pagination

Use `paginate_scalars()` for SQL lists. Add deterministic order with ID tie-breakers. Do not load all rows and slice in Python. Access filters belong in SQL, especially for files and jobs.

After route changes:

```bash
cd backend && uv run python ../scripts/generate_api_docs.py
cd .. && make docs-check
git diff -- docs/api.md docs/zh/api.md
```

## Frontend Workflow

- Use `apiClient`; do not duplicate auth/refresh fetch logic.
- Do not automatically retry non-idempotent uploads at network level.
- File download retries request a fresh signed URL on each attempt.
- Use `fetchAllPages()` only when a workflow truly needs all rows.
- Store access state in `sessionStorage`.
- Give icon-only buttons `aria-label` and a tooltip.
- Browser tests select row checkboxes from `.ant-table-tbody`, never the header select-all checkbox.

## Celery Development

Queues are `report`, `dxf`, `dxf2dwg`, `dxf2excel`, `excel_final`, `agent`, and `cad`. The MySQL SQL transport does not support remote-control fanout; do not use `celery inspect` as a health check.

Worker boot creates Kombu tables, closes the bootstrap channel, adds the queue-order index, then starts the consumer. The ready marker is created only by `worker_ready`.

## Tests

```bash
cd backend
uv run ruff check app tests
uv run pytest -q

cd ../frontend
npm run build
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:5173 \
PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:8010 \
npx playwright test  # defaults to Nginx http://127.0.0.1:8080
```

Focused real Excel Final flow:

The sample must be a Tekla tab/whitespace export or an Excel workbook containing the required steel-list columns; a generic `.xls`/`.xlsx` file is an intentional negative case.

```bash
PLAYWRIGHT_EXCEL_SAMPLE_PATH=/absolute/path/to/sample.xls \
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:5173 \
PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:8010 \
npx playwright test tests/e2e/excel-final-flow.spec.ts
```

## TDD and Debugging

Reproduce, capture the failing boundary, add a regression test, verify it fails for the right reason, implement the smallest fix, then run related and full suites. Multi-component failures require evidence at Nginx, API, DB, broker, worker, storage, and browser boundaries.

## Database Changes

```bash
bash scripts/db.sh revision "message"
bash scripts/db.sh migrate
bash scripts/db.sh migration-test
cd backend && uv run alembic check
```

Do not let Alembic autogenerate Celery-owned tables or their sequence tables. Runtime maintenance owns the required Kombu index. `alembic check` must report no new upgrade operations; ORM indexes added by migrations must also exist in model metadata.

## Generated and Temporary Files

Do not commit `.playwright-cli`, `frontend/test-results`, `frontend/dist`, backend storage, local `.env` files, or ad-hoc output. Durable tests and docs belong in tracked directories.
