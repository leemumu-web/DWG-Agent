# Upstream Remnant Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the complete remnant inventory feature with `Creeken-Harrans/DWG-Agent:main@eff938e`, preserve the upstream Excel workflow, produce one Alembic head, document remnant usage, and open a mergeable upstream PR.

**Architecture:** Finish the pending remnant metadata hardening on the existing feature branch, create a dedicated integration branch, and merge upstream with a normal merge commit. Resolve shared generated contracts by regeneration, join the independent migration branches with a no-DDL merge revision, and verify each domain independently before creating the upstream PR.

**Tech Stack:** Git, Python 3.12, FastAPI, SQLAlchemy, Alembic, MySQL, Celery, React 19, TypeScript, Vite, Playwright, Docker Compose, GitHub CLI.

## Global Constraints

- Do not rebase published commits, force-push, or push directly to upstream `main`.
- Preserve all upstream Excel/Workflow Stage 1 behavior and all remnant inventory behavior.
- Do not migrate, clear, or replace the existing local acceptance database.
- Keep current untracked/user files out of commits unless explicitly listed in this plan.
- Use only the primary agent; do not dispatch subagents.
- PR base is `Creeken-Harrans/DWG-Agent:main`; PR head is the integration branch pushed to `Ranbaixin/DWG-Agent-left-and-right-reader`.

---

### Task 1: Complete the pending optional metadata hardening

**Files:**
- Modify: `backend/app/modules/remnant_inventory/imports.py`
- Modify: `backend/app/modules/remnant_inventory/routes.py`
- Modify: `backend/app/modules/remnant_inventory/export.py`
- Modify: `frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx`
- Modify: `frontend/src/features/remnant-inventory/api.ts`
- Modify: `frontend/src/features/remnant-inventory/errors.ts`
- Test: `backend/tests/remnant_inventory/test_confirmation.py`
- Test: `backend/tests/remnant_inventory/test_import_batches.py`
- Test: `backend/tests/remnant_inventory/test_api.py`
- Test: `backend/tests/remnant_inventory/test_export.py`
- Test: `frontend/tests/e2e/remnant-inventory/import.spec.ts`
- Include: `docs/plans/2026-07-24-remnant-metadata-hardening-plan.md`

**Interfaces:**
- Consumes: `POST /api/v1/remnant-import-batches/{batch_id}/bulk-optional-metadata`.
- Produces: omitted optional fields remain unchanged; explicit `null`/blank clears a field; empty update masks return `REMNANT_OPTIONAL_METADATA_REQUIRED`.

- [ ] **Step 1: Re-run the corrected import Playwright spec**

Run from `frontend` against the current-source Vite server:

```powershell
$env:PLAYWRIGHT_FRONTEND_BASE_URL='http://127.0.0.1:4181'
npx playwright test tests/e2e/remnant-inventory/import.spec.ts --project=chromium --workers=1 --reporter=line
```

Expected: all 13 tests pass; exact label locators distinguish 项目编号一 from 项目编号二.

- [ ] **Step 2: Run remnant backend hardening tests**

```powershell
cd backend
uv run pytest -q tests/remnant_inventory/test_confirmation.py tests/remnant_inventory/test_import_batches.py tests/remnant_inventory/test_api.py tests/remnant_inventory/test_export.py
uv run ruff check app/modules/remnant_inventory tests/remnant_inventory
```

Expected: zero failures and Ruff reports `All checks passed!`.

- [ ] **Step 3: Commit the completed hardening**

```powershell
git add backend/app/modules/remnant_inventory backend/tests/remnant_inventory frontend/src/features/remnant-inventory frontend/tests/e2e/remnant-inventory/import.spec.ts docs/plans/2026-07-24-remnant-metadata-hardening-plan.md
git commit -m "fix(remnants): harden optional metadata updates and export"
```

Expected: only the listed remnant files are included.

### Task 2: Create the delivery branch and merge upstream

**Files:**
- Merge all files changed by `upstream/main@eff938e`.
- Resolve: `backend/tests/architecture/test_contract_snapshot.py`
- Resolve: `backend/tests/infrastructure/test_migrations.py`
- Resolve: `docs/architecture/runtime-contract.json`
- Resolve: `docs/reference/api.md`
- Resolve: `docs/reference/database.md`

**Interfaces:**
- Consumes: clean Task 1 commit and `upstream/main@eff938e`.
- Produces: `codex/remnant-upstream-integration-2026-07-24` with a pending merge and no discarded domain changes.

- [ ] **Step 1: Create the integration branch**

```powershell
git status --short
git switch -c codex/remnant-upstream-integration-2026-07-24
git fetch upstream main --prune
```

Expected: the worktree is clean before switching and `upstream/main` resolves to `eff938e`.

- [ ] **Step 2: Start the no-commit merge**

```powershell
git merge --no-ff --no-commit upstream/main
git diff --name-only --diff-filter=U
```

Expected unresolved files are the five documented shared contract/test/reference files. Any additional conflict is reviewed semantically before proceeding.

- [ ] **Step 3: Combine non-generated conflict semantics**

For `backend/tests/infrastructure/test_migrations.py`, retain constants and assertions for:

```python
REMNANT_AUTO_IMPORT_REVISION = (
    VERSIONS_DIR / "9d6e4a1b2c70_add_remnant_auto_import.py"
)
WORKFLOW_EXCEL_VALIDATION_REVISION = (
    VERSIONS_DIR / "4e7c2a9b1d30_add_workflow_excel_validation.py"
)
LINUX_EXCEL_STAGE_REVISION = (
    VERSIONS_DIR / "5f8d3b0c2e41_normalize_linux_excel_stage.py"
)
```

Retain both the remnant auto-import migration test and both upstream workflow migration tests. Keep the upstream dynamic MySQL head validation and remnant table assertions.

- [ ] **Step 4: Remove conflict markers without finalizing generated files**

```powershell
rg -n '^(<<<<<<<|=======|>>>>>>>)' backend docs frontend
git diff --check
```

Expected: no conflict markers remain; generated snapshot/count assertions may still fail until Task 4.

### Task 3: Join the Alembic migration heads

**Files:**
- Create: `backend/migrations/versions/8a6c1f4e2b90_merge_remnant_and_excel_workflow_heads.py`
- Modify: `backend/tests/infrastructure/test_migrations.py`

**Interfaces:**
- Consumes: heads `6f4a8c2d1e90` and `5f8d3b0c2e41`.
- Produces: sole Alembic head `8a6c1f4e2b90`.

- [ ] **Step 1: Write the failing merge-revision test**

Add:

```python
REMNANT_EXCEL_WORKFLOW_MERGE_REVISION = (
    VERSIONS_DIR / "8a6c1f4e2b90_merge_remnant_and_excel_workflow_heads.py"
)


def test_remnant_excel_workflow_merge_revision_joins_heads_without_ddl():
    source = REMNANT_EXCEL_WORKFLOW_MERGE_REVISION.read_text(encoding="utf-8")
    assert 'revision: str = "8a6c1f4e2b90"' in source
    assert 'down_revision: tuple[str, str] = ("6f4a8c2d1e90", "5f8d3b0c2e41")' in source
    assert "op." not in source
```

- [ ] **Step 2: Verify the test fails because the revision is absent**

```powershell
cd backend
uv run pytest tests/infrastructure/test_migrations.py -q -k remnant_excel_workflow_merge
```

Expected: failure reading the missing revision file.

- [ ] **Step 3: Add the no-DDL merge revision**

Create:

```python
"""Merge remnant inventory and Excel workflow migration heads.

Revision ID: 8a6c1f4e2b90
Revises: 6f4a8c2d1e90, 5f8d3b0c2e41
"""

from collections.abc import Sequence

revision: str = "8a6c1f4e2b90"
down_revision: tuple[str, str] = ("6f4a8c2d1e90", "5f8d3b0c2e41")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

- [ ] **Step 4: Verify one head and migration DAG tests**

```powershell
cd backend
uv run alembic heads
uv run pytest tests/infrastructure/test_migrations.py -q
```

Expected: only `8a6c1f4e2b90 (head)` and all migration tests pass.

### Task 4: Regenerate runtime contracts and combine reference docs

**Files:**
- Modify: `docs/architecture/runtime-contract.json`
- Modify: `backend/tests/architecture/test_contract_snapshot.py`
- Modify: `docs/reference/api.md`
- Modify: `docs/reference/database.md`
- Modify: `backend/migrations/README.md`

**Interfaces:**
- Consumes: merged application, worker, Compose, frontend route, ORM, and migration registrations.
- Produces: committed contract exactly matching runtime discovery.

- [ ] **Step 1: Regenerate the runtime contract**

Run from the repository root:

```powershell
backend/.venv/Scripts/python.exe scripts/architecture/snapshot_contracts.py --write
```

Do not manually delete entries from the generated JSON.

- [ ] **Step 2: Verify the regenerated snapshot**

Run:

```powershell
cd backend
uv run pytest tests/architecture/test_contract_snapshot.py -q
```

Expected: snapshot equality passes. Update exact path/operation/table/task counts in `test_contract_snapshot.py` to the regenerated values and set the sole head assertion to `8a6c1f4e2b90`.

- [ ] **Step 3: Combine API and database references**

Ensure `docs/reference/api.md` lists both upstream workflow validation endpoints and all remnant endpoints, including:

```text
POST /api/v1/remnant-import-batches/{batch_id}/bulk-optional-metadata
DELETE /api/v1/remnants/{remnant_id}
GET /api/v1/remnants/export.xlsx
```

Update `docs/reference/database.md` and `backend/migrations/README.md` with both branches and the new merge head:

```text
6f4a8c2d1e90 + 5f8d3b0c2e41 -> 8a6c1f4e2b90
```

- [ ] **Step 4: Stage all conflict resolutions**

```powershell
git add backend/tests/architecture/test_contract_snapshot.py backend/tests/infrastructure/test_migrations.py backend/migrations docs/architecture/runtime-contract.json docs/reference/api.md docs/reference/database.md
git diff --name-only --diff-filter=U
```

Expected: no unresolved paths.

### Task 5: Add the remnant inventory user guide

**Files:**
- Create: `docs/guides/remnant-inventory.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/operations/remnant-inventory.md`

**Interfaces:**
- Consumes: final remnant UI/API behavior.
- Produces: one worker-facing guide linked from both repository documentation indexes.

- [ ] **Step 1: Write the guide**

The guide must include concrete sections and exact operator behavior for:

```text
启用与登录权限
普通批量导入
自动导入与文件夹导入
offcut_zh_cn / GG / CZ / YLBH 格式
解析确认与批量附加字段
材质自动创建和启停
检索、预占、释放、领用
归档、删除与同源图重新提交
预览、原图下载和 Excel 导出
Worker 队列和常见中文错误
```

State that project number two, storage location, remark one, and remark two may be empty; batch updates modify only explicitly selected fields.

- [ ] **Step 2: Link the guide**

Add a “余料库” link in root `README.md` and `docs/README.md`. Add a reciprocal “日常使用说明” link near the top of `docs/operations/remnant-inventory.md`.

- [ ] **Step 3: Validate documentation references**

```powershell
rg -n "docs/guides/remnant-inventory.md|guides/remnant-inventory.md" README.md docs/README.md docs/operations/remnant-inventory.md
git diff --check
```

Expected: all three entry points link to the guide and no whitespace errors exist.

### Task 6: Complete merge verification and commit

**Files:**
- Verify all merged source, tests, migrations, docs, and Compose definitions.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: one tested merge commit with no untracked build/test output.

- [ ] **Step 1: Static and backend verification**

```powershell
cd backend
uv lock --check
uv run ruff check .
uv run pytest tests/architecture tests/infrastructure tests/remnant_inventory tests/workflows tests/excel_processing tests/files tests/cad_processing/test_dxf_preview_service.py -q
```

Expected: zero failures; only documented environment skips.

- [ ] **Step 2: Stage verification**

```powershell
cd Stages/remnant_drawing_reader
uv run pytest -q
cd ../excel_final
uv run pytest -q
```

Expected: both the remnant parser and the complete upstream Excel Final Stage suites pass. The upstream Excel Stage 1 workflow integration is covered by the backend `tests/workflows` and `tests/excel_processing` command from Step 1.

- [ ] **Step 3: Frontend verification**

```powershell
cd frontend
npm run build
$env:PLAYWRIGHT_FRONTEND_BASE_URL='http://127.0.0.1:4181'
npx playwright test tests/e2e/remnant-inventory --project=chromium --workers=1 --reporter=line
npx playwright test tests/e2e/workflows tests/e2e/excel-processing --project=chromium --workers=1 --reporter=line
```

Expected: production build and all selected Playwright specs pass.

- [ ] **Step 4: Temporary MySQL migration verification**

Create three uniquely named temporary schemas under the configured local MySQL service. Run:

```text
empty -> 8a6c1f4e2b90
6f4a8c2d1e90 -> 8a6c1f4e2b90
5f8d3b0c2e41 -> 8a6c1f4e2b90
```

Drop only those exact temporary schemas after verification. Never target the configured acceptance schema.

- [ ] **Step 5: Compose verification**

```powershell
docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml config --quiet
docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml build backend-api nginx worker-excel-final worker-remnant-convert worker-remnant-parse
```

Expected: configuration and image builds succeed without starting database migrations.

- [ ] **Step 6: Final diff and merge commit**

```powershell
rg -n '^(<<<<<<<|=======|>>>>>>>)' --glob '!frontend/public/luckyexcel.umd.js' .
git diff --check
git status --short
git commit
```

Use merge commit message:

```text
merge: integrate remnant inventory with upstream Excel workflow
```

### Task 7: Push and create the upstream PR

**Files:**
- No source changes unless verification identifies a regression.

**Interfaces:**
- Consumes: clean tested integration branch.
- Produces: non-draft PR to `Creeken-Harrans/DWG-Agent:main`.

- [ ] **Step 1: Push the integration branch**

```powershell
git push -u origin codex/remnant-upstream-integration-2026-07-24
```

- [ ] **Step 2: Create the PR**

Create a non-draft PR with:

```text
Title: feat: add remnant inventory and automatic drawing import
Base: Creeken-Harrans/DWG-Agent:main
Head: Ranbaixin:codex/remnant-upstream-integration-2026-07-24
```

The body must summarize feature scope, migration merge, the user-guide path, and exact verification results.

- [ ] **Step 3: Verify mergeability and enable auto-merge**

Inspect the PR merge state and checks. If GitHub reports it mergeable and repository settings permit:

```powershell
gh pr merge --repo Creeken-Harrans/DWG-Agent --auto --merge <PR-URL>
```

Do not bypass required checks or branch protection. If permissions prevent auto-merge, leave the PR open and report the exact GitHub restriction.
