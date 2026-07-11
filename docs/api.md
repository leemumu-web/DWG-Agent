# API Reference

This file is generated from the FastAPI OpenAPI schema by `cd backend && uv run python ../scripts/generate_api_docs.py`. Change code and tests first, then regenerate both languages. A listed route proves interface presence only; feature flags, permissions, and external dependencies may still prevent execution.

## Conventions

- Local API: `http://127.0.0.1:8010`; Nginx: `http://127.0.0.1:8080`; container API port: `8000`.
- Business routes require a Bearer access token except `/health`, `/health/ready`, `POST /api/v1/auth/sessions`, and refresh.
- Success uses `{data, meta}`. Paginated responses add `pagination`; `total` is an exact SQL `COUNT(*)`.
- Errors use `{error: {code, message, details}, meta}` and never expose tracebacks, DSNs, or host paths.
- `GET /api/v1/jobs/{job_id}/events` uses an SSE cookie and polls authoritative MySQL state; no token is placed in the URL.
- Downloads first obtain a short-lived signed URL, then download with Bearer auth. A retry after network, 403, 408, 429, or 5xx obtains a fresh signature.
- Job retries increment `attempt`; steps accept `?attempt=N`, and stale workers cannot overwrite a newer attempt.
- SSE snapshots contain only the current attempt's steps. Results of an unscoped job are restricted to administrators or its creator.
- With `AGENT_ENABLED=false`, Agent routes return 503. When enabled, run details and steps are scoped to the creator, administrators, or project members.

## Health

| Method | Path |
|---|---|
| `GET` | `/health` |
| `GET` | `/health/ready` |

## Authentication

| Method | Path |
|---|---|
| `POST` | `/api/v1/auth/sessions` |
| `DELETE` | `/api/v1/auth/sessions/current` |
| `POST` | `/api/v1/auth/tokens/refresh` |
| `GET` | `/api/v1/auth/me` |
| `PATCH` | `/api/v1/auth/password` |

## Users

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/users` |
| `GET, PATCH, DELETE` | `/api/v1/users/{user_id}` |
| `PATCH` | `/api/v1/users/me` |
| `POST` | `/api/v1/users/{user_id}/roles` |
| `DELETE` | `/api/v1/users/{user_id}/roles/{role_id}` |
| `POST` | `/api/v1/users/{user_id}/password-reset-requests` |
| `POST` | `/api/v1/users/{user_id}/disable-requests` |
| `POST` | `/api/v1/users/{user_id}/enable-requests` |

## Roles and permissions

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/roles` |
| `PUT` | `/api/v1/roles/{role_id}/permissions` |
| `GET` | `/api/v1/permissions` |

## Projects

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/projects` |
| `GET, PATCH, DELETE` | `/api/v1/projects/{project_id}` |
| `GET, POST` | `/api/v1/projects/{project_id}/members` |
| `PATCH, DELETE` | `/api/v1/projects/{project_id}/members/{member_id}` |

## Files and downloads

| Method | Path |
|---|---|
| `POST, GET` | `/api/v1/files` |
| `POST` | `/api/v1/files/upload-zip` |
| `GET` | `/api/v1/files/batches` |
| `DELETE` | `/api/v1/files/batches/{batch_name}` |
| `GET` | `/api/v1/files/batches/{batch_name}/download-zip` |
| `GET` | `/api/v1/files/{file_id}/excel-preview` |
| `GET, DELETE` | `/api/v1/files/{file_id}` |
| `GET` | `/api/v1/files/{file_id}/download-url` |
| `GET` | `/api/v1/files/{file_id}/download` |
| `POST` | `/api/v1/files/bulk-delete` |
| `POST` | `/api/v1/files/download-zip` |

## Drawings

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/drawings` |
| `GET, PATCH, DELETE` | `/api/v1/drawings/{drawing_id}` |
| `GET, POST` | `/api/v1/drawings/{drawing_id}/versions` |
| `GET` | `/api/v1/drawings/{drawing_id}/preview` |

## Jobs

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/jobs` |
| `GET` | `/api/v1/jobs/{job_id}` |
| `POST` | `/api/v1/jobs/{job_id}/cancellation-requests` |
| `POST` | `/api/v1/jobs/{job_id}/retry-requests` |
| `GET` | `/api/v1/jobs/{job_id}/steps` |
| `GET` | `/api/v1/jobs/{job_id}/logs` |
| `GET` | `/api/v1/jobs/{job_id}/events` |
| `GET` | `/api/v1/jobs/{job_id}/results` |
| `POST` | `/api/v1/jobs/cancel-all-active` |

## Results and reviews

| Method | Path |
|---|---|
| `GET` | `/api/v1/results/{result_id}` |
| `GET` | `/api/v1/results/{result_id}/download-url` |
| `POST, GET` | `/api/v1/results/{result_id}/reviews` |
| `GET` | `/api/v1/reviews/pending` |

## Audit

| Method | Path |
|---|---|
| `GET` | `/api/v1/audit-logs` |
| `GET` | `/api/v1/audit-logs/{audit_log_id}` |

## Agent

| Method | Path |
|---|---|
| `POST` | `/api/v1/agent-runs` |
| `GET` | `/api/v1/agent-runs/{agent_run_id}` |
| `GET` | `/api/v1/agent-runs/{agent_run_id}/steps` |
| `GET` | `/api/v1/agent-tools` |

## System

| Method | Path |
|---|---|
| `GET` | `/api/v1/system/health` |
| `GET` | `/api/v1/system/health/oda` |

## Excel Final

| Method | Path |
|---|---|
| `POST` | `/api/v1/excel-final/upload` |
| `POST` | `/api/v1/excel-final/process` |
| `POST` | `/api/v1/excel-final/upload-and-process` |
| `GET` | `/api/v1/excel-final/process/{job_id}` |
| `GET` | `/api/v1/excel-final/process/{job_id}/download` |
| `GET` | `/api/v1/excel-final/batches` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/parts` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/parts/{part_id}` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/components` |
| `GET` | `/api/v1/excel-final/parts/search` |
| `GET` | `/api/v1/excel-final/weights/lookup` |
| `GET` | `/api/v1/excel-final/health` |

## Runtime documentation

Use `/docs`, `/redoc`, or `/openapi.json` for request and response schemas while the API is running in development/debug mode.
With `APP_ENV=production` and `DEBUG=false`, all three runtime documentation endpoints are intentionally disabled; production should use this generated file and a versioned OpenAPI artifact.
