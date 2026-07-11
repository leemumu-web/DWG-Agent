# Full-Stack Workflow Verification

> **Scope:** Nginx, FastAPI, MySQL, Celery SQL transport, MinIO, frontend retries, and signed downloads
> **Last verified:** 2026-07-11
> **Chinese mirror:** [`zh/workflow-verification.md`](zh/workflow-verification.md)

## 1. Acceptance Boundary

Verification must exercise the actual production-shaped path, not only mocked API tests:

```text
Browser -> Nginx :8080 -> FastAPI :8010 local / :8000 container
                         |-> MySQL authoritative state
                         |-> MySQL Celery broker and result backend
                         |-> MinIO objects
Celery worker <- MySQL queue -> stage process -> MySQL state + MinIO result
```

Redis/Valkey is not a component of this topology. Job progress, token revocation, SSE snapshots, broker messages, and task results are durable MySQL data.

## 2. Repeatable Commands

Run static and isolated tests first:

```bash
cd backend
uv run ruff check app tests
uv run pytest -q
uv run python ../scripts/check_docs.py
cd ..

cd Stages/excel_final
uv run pytest -q multi_split/tests
cd ../..

bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

With the local stack already running, execute the non-destructive smoke verifier through Nginx:

```bash
DWG_VERIFY_USERNAME=admin \
DWG_VERIFY_PASSWORD='<configured-password>' \
python tests/run_full_verify.py
```

The verifier checks liveness, readiness, OpenAPI generation, authentication, exact paginated file/job reads, and managed process topology. It never resets the database or creates business records.

## 3. Required End-to-End Scenarios

| Scenario | Expected evidence |
|---|---|
| Cold Compose start | Empty volumes migrate to Alembic head; backend and worker become healthy |
| FastAPI -> MySQL | Authenticated requests persist and read authoritative rows |
| FastAPI -> broker -> Celery | A submitted job leaves `queued`, records its attempt and steps, and reaches a terminal state |
| Celery -> MinIO | Successful output has a `files` row and a matching object digest |
| Signed download | Frontend requests a fresh URL, downloads bytes, and can re-sign after URL expiry/failure |
| Retry | A failed/cancelled job creates the next attempt without overwriting earlier steps |
| SSE | Browser receives the current attempt snapshot from MySQL; credentials are carried by the HttpOnly SSE cookie |
| Result isolation | Unscoped result details, download URLs, and reviews reject users other than the creator/admin |
| Storage outage | `/health` remains 200; `/health/ready` is 503 with database `ok` and storage `error` |
| Storage recovery | Existing object remains downloadable with the original SHA-256 |
| Worker restart | Managed scripts do not create duplicate named workers when pidfiles are missing |
| Stale delivery | A one-argument legacy message cannot claim attempt 2; `(job_id, 2)` can execute it |

## 4. Verified Evidence

The 2026-07-11 acceptance run used fresh Compose volumes and a digest-pinned MinIO image:

- Alembic reached `a74c2e9f1d30` from an empty MySQL schema.
- The broker created `kombu_message(queue_id, timestamp, id, visible)` and query planning selected that composite index.
- A report job completed through API -> MySQL broker -> Celery -> MySQL state -> MinIO; the downloaded SHA-256 matched the stored object.
- Stopping MinIO made readiness return 503 while database status remained `ok`; restarting MinIO preserved the object and digest.
- A standalone MinIO persistence test independently completed an Excel Final job and recovered the same result bytes after restart.
- Excel Final's own profile/VBA-parity suite passed 254 tests; legacy binary `.xls` parsing includes `xlrd` and falls through from failed text detection.
- Browser tests exercised real upload, job polling, failed-job retry with incremented attempt, signed URL refresh, and result download.
- A real MySQL/report-worker probe left an attempt 2 job queued after a legacy one-argument delivery, then completed it only after a `(job_id, 2)` delivery.

These observations are evidence for that run, not a substitute for rerunning the commands after future changes.

## 5. Failure Triage

1. Check `bash scripts/status.sh` and `/health/ready` before examining business logic.
2. Inspect `/tmp/dwg-agent-backend.log` and `/tmp/dwg-agent-worker-*.log` for the first error.
3. Confirm `alembic current` is the documented head and the application MySQL user can connect.
4. Confirm exactly one managed worker node per queue and that `/tmp/dwg-celery-ready` exists in Compose workers.
5. Compare the `files.sha256` value with downloaded bytes and the MinIO object before blaming the frontend.
6. Use the browser network trace to distinguish an expired signed URL from a failed object fetch.

Never repair a failed verification by enabling an in-memory fallback: that would hide loss of the authoritative path.
