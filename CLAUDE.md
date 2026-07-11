# DWG-Agent Platform - Repository Instructions

This file is an implementation guide for coding agents. User-facing status begins in `README.md`; normative design is `DWG-Agent企业平台技术规范.md`; English/Chinese details are paired under `docs/` and `docs/zh/`.

## Baseline Facts

```text
Browser -> Nginx :8080 local / :80 Compose -> FastAPI :8010 local / :8000 internal
                                                    |-> MySQL
                                                    |-> MinIO or local storage
Celery workers <-> MySQL SQL transport/result backend -> tracked/external Stages
```

- MySQL is authoritative for business data, revocation, Agent memory, Job/steps/progress, broker, and results.
- Redis/Valkey is absent; do not add a cache/fallback that changes correctness.
- Implemented task queues are `report`, `dxf`, `dxf2dwg`, `dxf2excel`, and `excel_final`.
- `tasks_agent.py` and `tasks_cad.py` are placeholders. Keep `AGENT_ENABLED=false` and `CAD_WORKER_ENABLED=false`.
- All four conversion flags default false; worker health alone does not make a pipeline available.
- Compose is HTTP-only. `443:8443` has no Nginx listener/certificate and is not TLS.
- `Stages/dxf2excel` is a broken gitlink with no `.gitmodules` or reachable target object. The populated checkout is not clean-clone evidence.
- Production disables runtime OpenAPI/Swagger/ReDoc.

## Engineering Rules

- Use Python 3.12 and locked `uv` dependencies in `backend/`; use `npm ci` in `frontend/`.
- Routes own HTTP; services own transactions/invariants; Celery tasks call services.
- Every worker claim/progress/terminal/cancel/compensation write matches status + attempt.
- Storage adapters own bytes; MySQL owns metadata and SHA-256. Join pre-commit writes to rollback compensation.
- Reuse file/Job/result/project permission helpers. SQL lists must filter access before pagination.
- Do not expose traceback, child stderr, DSN, secret, host path, or signed credentials to clients.
- Do not commit `.env`, `.env.docker`, local storage, browser traces, logs, virtualenvs, or generated test output.
- Treat `third_parts/` as upstream/vendored ownership, not automatically as delivered platform code.
- Do not claim production, TLS, immutable audit, automated backup, Agent, CAD worker, or Stage compatibility without direct evidence.

## Documentation Rules

- Change routes/tests first, then run `make docs-generate`.
- Update every `docs/*.md` and same-name `docs/zh/*.md` pair together.
- Local API examples use `8010`; container `8000` is internal; local Nginx is `8080`; Compose public HTTP is `80`.
- State code presence, default flag, external dependency, verification level/date, and residual boundary separately.
- Keep algorithm detail in tracked Stage docs and platform integration detail in `docs/processing-pipelines.md`.
- Do not edit upstream `third_parts/` docs to make platform claims.
- Run `make docs-check` before completion.

## Verification Gates

```bash
make docs-check

cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ..

cd Stages/dwg2dxf && uv run pytest -q && cd ../..
cd Stages/dxf2dwg && uv run pytest -q && cd ../..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

Run focused Stage tests when their code/docs change. A full workflow claim additionally requires real Nginx, MySQL, Celery, MinIO, valid input, retry/SSE/download, and outage recovery evidence.

## Key Paths

| Purpose | Path |
|---|---|
| FastAPI application | `backend/app/main.py` |
| API router | `backend/app/api/v1/router.py` |
| Runtime settings | `backend/app/core/config.py` |
| DB engine/session | `backend/app/db/session.py` |
| Job state machine | `backend/app/services/job_service.py` |
| Celery configuration | `backend/app/workers/celery_app.py` |
| Storage adapters | `backend/app/storage/` |
| Migrations | `backend/migrations/versions/` |
| Frontend API clients | `frontend/src/api/` |
| Compose/Nginx | `compose.yaml`, `infra/nginx/` |
| Local operations | `scripts/` |
| Documentation governance | `scripts/check_docs.py`, `scripts/generate_api_docs.py` |
