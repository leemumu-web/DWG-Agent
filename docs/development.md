# Development

> Chinese mirror: [zh/development.md](zh/development.md)

## Toolchains

| Area | Toolchain | Lock/install |
|---|---|---|
| Backend | Python 3.12, uv, FastAPI, SQLAlchemy 2, Pydantic 2 | `cd backend && uv sync --locked` |
| Frontend | Node/npm, React 19, TypeScript 6, Vite 8 | `cd frontend && npm ci` |
| Excel Final Stage | Python >=3.11, standalone scripts | `cd Stages/excel_final && uv sync --locked` |
| ODA Stages | Python >=3.12 plus external AppImage runtime | per-Stage `uv sync --locked` |

The backend lock contains editable path dependencies under `Stages/`. A clean environment is currently blocked by the broken `Stages/dxf2excel` gitlink; do not treat success in the populated working tree as clone reproducibility.

## Repository Map

| Path | Ownership |
|---|---|
| `backend/app/api/` | HTTP dependencies and routing |
| `backend/app/services/` | transactions, permissions, orchestration |
| `backend/app/workers/` | Celery configuration and task entrypoints |
| `backend/app/storage/` | local/MinIO byte adapters |
| `backend/migrations/` | Alembic-owned business schema |
| `frontend/src/api/` | typed HTTP clients, auth refresh, downloads |
| `frontend/src/features/` | workflow pages |
| `Stages/` | independently runnable domain processors |
| `infra/` | Nginx, MySQL initialization, deployment verification |
| `scripts/` | local lifecycle, DB and documentation tools |
| `third_parts/` | upstream/vendored code; not a platform module by default |

## Run Locally

```bash
# Vite :5173, FastAPI :8010, five implemented queue workers
bash scripts/start-dev.sh

# Built SPA via Nginx :8080 -> FastAPI :8010
bash scripts/start-all.sh
```

Port `8000` is container-internal. If Vite selects another port, use its printed URL and set Playwright overrides when testing directly. Prefer the Nginx `8080` path for production-shaped browser work.

## Backend Change Rules

- Routes handle HTTP schema/dependencies; services own business transactions; tasks call services.
- Use sync SQLAlchemy patterns already established by the repository.
- Any worker claim/progress/terminal write must match status and attempt.
- Keep file bytes behind storage adapters and metadata in MySQL.
- A storage object written before commit must join session compensation.
- Reuse resource permission helpers; SQL list filtering must not degrade into row-by-row N+1 checks.
- Do not put traceback, DSN, child stderr, secret, or host path into client-visible errors.
- Do not add Redis/Valkey or in-memory correctness fallback to mask dependency failure.

FastAPI lifespan seed initialization is best-effort in local runtime. Docker performs migrations/seeding before Gunicorn. Tests must account for the actual mode rather than assuming process startup proves readiness.

## API Changes

Use the standard success/error envelopes and exact SQL pagination. Add a stable ID tie-breaker to ordered lists. A route change requires:

1. schema/service/route tests;
2. permission and negative cases;
3. `make docs-generate`;
4. English/Chinese narrative updates when behavior or boundary changes;
5. `make docs-check`.

Runtime `/docs` and `/openapi.json` are development/debug surfaces only. The generated Markdown API reference is the production-readable inventory.

## Frontend Changes

- Keep API requests relative behind Nginx; use `VITE_API_BASE_URL` only for direct Vite development.
- Access state belongs in `sessionStorage`; refresh and SSE rely on HttpOnly cookies.
- The Axios 401 interceptor performs one shared refresh and must not retry login/refresh recursively.
- React Query retry applies to queries; single-file download has its own one-retry/new-signature loop.
- UI guards improve navigation but never replace API authorization.
- Polling and SSE must stop or settle on terminal Job state.
- Add Playwright coverage for visible workflow changes and failure/retry behavior.

## Worker Changes

Queues are `report`, `dxf`, `dxf2dwg`, `dxf2excel`, `excel_final`, `agent`, and `cad`, but only the first five have task implementations. Do not route work to placeholder modules.

MySQL SQL transport lacks fanout remote control. Health uses process identity and worker-ready marker. When adding a task, test routing, eager execution, real broker dispatch, attempt claims, failure mapping, stale execution, cancellation, and object cleanup separately.

Never open a second failure-handler session while the active session has uncommitted JobSteps. Failure step and terminal Job state should commit together unless the service explicitly defines a compensating boundary.

## Database Changes

```bash
cd backend
uv run alembic revision --autogenerate -m "description"
# Review generated operations and circular FK behavior.
cd ..
bash scripts/db.sh migration-test
cd backend && uv run alembic check
```

Alembic owns 22 business tables, not the eight Celery runtime tables. Test upgrade from empty MySQL and, for destructive changes, a representative populated copy. `migration-test` does not validate downgrade.

## Test Layers

```bash
# Backend static and isolated API/service tests
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q

# Focused Stage tests
cd ../Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../excel_final && uv run pytest -q multi_split/tests

# MySQL/infrastructure
cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

# Frontend
cd frontend
npm run build
npx playwright test
```

SQLite tests are fast logic checks, not MySQL concurrency or migration proof. Mocked Playwright routes verify UI contracts, not MinIO/Celery. A release-sensitive pipeline change also requires a real Nginx/MySQL/worker/storage/sample workflow.

## Debugging Order

1. Reproduce the smallest failing path and record request ID, Job ID, attempt, endpoint, and time.
2. Check `/health/ready`, managed processes, flags, and Stage source/dependency availability.
3. Find the first backend/worker error, not the final frontend symptom.
4. Inspect authoritative Job/JobStep rows and storage object/digest.
5. Test the hypothesis with a focused regression before changing behavior.
6. Run the narrow test, then the full affected layer and end-to-end gate.

## Documentation and Generated Files

`docs/api.md` and `docs/zh/api.md` are generated; edit their generator, not the files. Other language pairs are edited together. Generated frontend `dist`, Playwright traces, local storage, `.env*` secrets, virtualenvs, caches, logs, and test artifacts must not be committed.

Component-specific algorithms belong in their Stage docs. Platform docs should link to them and state the integration boundary rather than duplicating hundreds of algorithm steps.
