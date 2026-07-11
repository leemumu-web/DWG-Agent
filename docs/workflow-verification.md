# Full-Stack Workflow Verification

> **Scope:** Nginx, FastAPI, MySQL, Celery SQL transport, storage, frontend retry/SSE/download
> **Latest documentation-audit run:** 2026-07-11
> **Chinese mirror:** [zh/workflow-verification.md](zh/workflow-verification.md)

## 1. Evidence Levels

| Level | Proves | Does not prove |
|---|---|---|
| Static/docs | source/config/link/schema declarations are internally consistent | a process starts or dependency works |
| SQLite/backend tests | isolated API/service/security/state logic | MySQL locks, migrations, broker, MinIO, browser behavior |
| Stage tests | deterministic converter/parser units and parity corpus | every real CAD/workbook or platform integration |
| MySQL/infra checks | empty-schema migration and active local schema/config facts | complete Job/object/browser workflow |
| Playwright contract/UI | API reachability and browser interactions; some tests use route fixtures | every scenario uses real Celery/MinIO/valid business files |
| Live E2E | the exact deployed path and sample exercised in that run | future revisions or untested formats/outages |

An acceptance claim must name its level, environment, date, sample, and skipped cases.

## 2. Required Production-Shaped Path

```text
Browser -> Nginx HTTP :8080 local / :80 Compose
  -> FastAPI :8010 local / :8000 internal
     -> MySQL business + Celery runtime state
     -> Local FS or MinIO objects
Celery worker <- MySQL queue -> Stage -> MySQL state + storage result
```

Redis/Valkey is not present. Current Compose is HTTP only; HTTPS is not part of this verified path. `Stages/dxf2excel` clean-clone reproducibility is also outside acceptance until its gitlink is repaired.

## 3. Repeatable Gates

```bash
make docs-check

cd backend
uv run ruff check app tests ../tests/run_full_verify.py ../scripts/check_docs.py ../scripts/generate_api_docs.py
uv run pytest -q
uv run alembic check
cd ..

cd Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../excel_final && uv run pytest -q multi_split/tests
cd ../..

bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

With a local stack already running, use the read-only verifier through Nginx:

```bash
DWG_VERIFY_USERNAME=admin \
DWG_VERIFY_PASSWORD='<configured-password>' \
python tests/run_full_verify.py
```

It checks liveness, readiness, 71-path OpenAPI, login, exact paginated files/Jobs reads, and managed process topology. It does not create a processing Job, upload a file, interrupt storage, or validate a signed result digest.

## 4. Required End-to-End Scenarios

| Scenario | Required evidence |
|---|---|
| Clean checkout/build | fresh clone restores all Stage sources; locked backend/frontend installs and image build pass |
| Cold Compose | empty MySQL/MinIO volumes reach migration head and healthy core/selected workers |
| Authentication | Nginx login, access request, cookie refresh, logout/revocation and expired session |
| Job dispatch | API creates queued attempt; MySQL broker delivers to the intended worker; JobSteps and terminal state persist |
| Object closure | source/result `files` rows match stored objects and downloaded SHA-256 |
| Retry isolation | failed/cancelled Job increments attempt; old message/worker cannot update it |
| SSE | HttpOnly cookie works, current-attempt snapshot arrives, reconnect refreshes, terminal closes |
| Authorization | cross-project and unscoped result/file/review access is rejected |
| Download retry | first signed fetch fails with retryable status; second attempt obtains a different valid signature |
| Storage outage | liveness remains 200; readiness is 503; recovery needs no API restart; old object remains intact |
| Worker loss | stale running Job becomes `CELERY_WORKER_LOST`, then retry completes a new attempt |
| TLS | real HTTPS handshake, redirect, Secure refresh/SSE cookies, signed download and certificate lifecycle |

The TLS and clean-checkout rows are currently known failures, not completed acceptance items.

## 5. Latest Run Evidence

The 2026-07-11 documentation-audit run used the existing local MySQL and already-running local Nginx/FastAPI/five implemented workers; it did not restart the stack or recreate Compose volumes.

| Gate | Result | Boundary |
|---|---|---|
| Documentation checker | pass | includes bilingual commands/tokens, generated API, links, table/head, TLS/gitlink/production-doc contracts |
| Backend Ruff | pass | application, tests, verifier, documentation scripts |
| Backend pytest | **661 passed, 3 skipped** | 10 dependency/deprecation warnings; isolated tests use SQLite where configured |
| Alembic check | no new operations | known `drawings`/`drawing_versions` cycle warning remains |
| MySQL migration test | pass | empty temporary schema -> `a74c2e9f1d30`; 22 business tables |
| Infrastructure verifier | **110/110** | static contracts plus active local MySQL; not TLS/build/restore/E2E |
| Stage tests | **13 + 28 + 254 passed** | dwg2dxf, dxf2dwg, Excel Final multi_split respectively |
| Frontend build | pass | TypeScript and Vite production bundle |
| Playwright | **49 passed** | `PLAYWRIGHT_EXCEL_SAMPLE_PATH` used real `G区域四节钢柱构件零件清单毛净重.xLS`; includes Celery and fresh-signature digest closure |
| Live read-only verifier | 7 checks passed | liveness, readiness, 71 paths, auth, file/job lists, process topology |

The full run supplied the repository's known-valid Tekla list and passed successful upload -> Celery -> result -> failed-first-download -> fresh-signature digest verification. A separate `阚导出材料表.xls` probe was correctly rejected because it lacked required `构件编号` and `数量` columns; a related filename/extension is not sufficient input validity. Many other Files/Jobs UI tests use deterministic route fixtures and prove UI/API contracts rather than real object processing.

## 6. Historical Integration Record

The repository previously recorded a 2026-07-11 fresh-volume integration run with these observations:

- MySQL migrated from empty volume to `a74c2e9f1d30` and created the queue-claim index.
- A report Job traversed API -> MySQL broker -> Celery -> MySQL state -> MinIO and downloaded with matching SHA-256.
- MinIO interruption changed readiness to 503 while database remained `ok`; recovery preserved an existing object.
- A real attempt-2 probe rejected a legacy one-argument message and completed only after `(job_id, 2)` delivery.

These are retained as dated historical evidence. They were not independently repeated during the latest documentation-only run and must be rerun after relevant implementation, image, dependency, or environment changes.

## 7. Failure Triage

1. Record revision, request ID, Job ID/attempt, time, flags, sample digest, and exact entry URL.
2. Check `bash scripts/status.sh`, `/health`, and `/health/ready` without restarting first.
3. Inspect the first API/worker/storage/MySQL error, not only the browser's final message.
4. Confirm `alembic current`, Job/JobStep state, queue worker identity, and Stage source availability.
5. Compare `files.sha256`, storage bytes, and downloaded bytes.
6. Distinguish browser fixture coverage from a real backend call and a real worker/object result.
7. Add a focused regression, then rerun every affected layer and required E2E scenario.

Never make a gate pass by enabling an in-memory fallback, disabling authorization, accepting arbitrary spreadsheet content, or describing a skipped scenario as verified.
