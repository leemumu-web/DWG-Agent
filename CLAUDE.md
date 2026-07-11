# DWG-Agent Platform - Agent Instructions

The current specification is `DWG-Agent企业平台技术规范.md`. The human overview is
`README.md`; detailed English documentation is under `docs/`, with a matching Chinese
document under `docs/zh/`.

## Runtime Architecture

```text
Browser -> Nginx :8080 local / :80 container -> FastAPI :8010 local / :8000 container
                                                    |-> MySQL
                                                    |-> MinIO or local storage
Celery workers <-> MySQL SQL transport/result backend -> CAD and Excel stages
```

- MySQL is the authoritative runtime database and also backs Celery transport/results.
- Redis/Valkey is not part of the runtime, dependencies, Compose topology, or fallback path.
- MinIO is used in Compose; local storage is supported for local development.
- Implemented worker queues are `report`, `dxf`, `dxf2dwg`, `dxf2excel`, and `excel_final`.
- Agent and Windows CAD execution remain disabled/incomplete and must not be presented as delivered.

## Engineering Rules

- Use Python 3.12 and `uv` in `backend/`; use the locked npm dependencies in `frontend/`.
- Keep runtime code synchronous with SQLAlchemy 2.x and Pydantic v2 conventions already in the repo.
- Routes handle HTTP concerns; services own orchestration; Celery tasks call services.
- Use conditional `status + attempt` updates for worker state. A stale worker must not overwrite a retry or cancellation.
- Use storage APIs for bytes and MySQL for metadata. Never add process-local or in-memory correctness fallbacks.
- Enforce uploader/admin/project-member access on file and job-derived resources.
- Do not expose tracebacks, DSNs, host paths, passwords, or signed credentials to clients or startup output.
- Do not commit `.env`, `.env.docker`, local storage, browser traces, or test output.
- The only intentional host-specific path is `infra/nginx/nginx.local.conf`; its header documents replacement.

## Documentation Rules

- An endpoint change requires code/tests first, then:
  `cd backend && uv run python ../scripts/generate_api_docs.py`.
- Update each `docs/*.md` file and its `docs/zh/*.md` mirror together.
- Local API examples use `8010`; `8000` is container-internal only. Nginx local entry is `8080`.
- Historical Redis descriptions and obsolete exploration reports must not be reintroduced.

## Verification Gates

```bash
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
cd ..

bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

For a running local stack, use `python tests/run_full_verify.py`; optional credentials are passed
through `DWG_VERIFY_USERNAME` and `DWG_VERIFY_PASSWORD`. The verifier is read-only.

## Key Paths

| Purpose | Path |
|---|---|
| FastAPI app | `backend/app/main.py` |
| API routes | `backend/app/api/v1/` |
| Runtime settings | `backend/app/core/config.py` |
| DB engine/session | `backend/app/db/session.py` |
| Job state machine | `backend/app/services/job_service.py` |
| Celery configuration | `backend/app/workers/celery_app.py` |
| Storage adapters | `backend/app/storage/` |
| Migrations | `backend/migrations/versions/` |
| Frontend API clients | `frontend/src/api/` |
| Compose | `compose.yaml` |
| Local operations | `scripts/` |
