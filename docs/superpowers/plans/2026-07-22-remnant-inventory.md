# 余料库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 DWG-Agent Web 端交付可检索、可批量导入、可人工校正、可预占和可追踪使用状态的全厂共享余料库。

**Architecture:** 后端新增独立 `remnant_inventory` 领域模块，通过现有 `files`、`jobs`、`cad_processing`、`identity` 和审计公共接口协作；确定性图纸文字解析放入独立 Stage。前端新增独立 feature，使用 React Query 恢复批次状态并复用已有 DXF 预览能力；转换和解析分别进入两个 Celery 队列，API 请求不执行 CAD 工作。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、Celery、MySQL、ezdxf、pytest、React 19、TypeScript 6、Ant Design 6、TanStack Query、Playwright。

## Global Constraints

- 领域 owner 固定为 `backend/app/modules/remnant_inventory/`，其他领域只能导入其 `interface.py`。
- 前端 owner 固定为 `frontend/src/features/remnant-inventory/`，跨 feature 只能从 `index.ts` 导入。
- Stage 不访问 HTTP、数据库、存储、用户或权限，输入单个 DXF，输出版本化 JSON。
- 原始文件字节只存现有文件/存储系统；MySQL 保存业务事实；余料模块不复制 Job 状态。
- API 只登记批次并立即响应；`remnant_convert` 默认并发 2，`remnant_parse` 默认并发 4。
- 不支持 ZIP；文件数量上限来自 `REMNANT_IMPORT_MAX_FILES`，不把 20 写成固定业务上限。
- 厚度始终人工填写；材质、项目编号和多个零件编号由系统候选加人工确认。
- 原图下载返回实际上传的 DWG/DXF；转换 DXF 只用于解析和预览。
- 功能默认关闭：`REMNANT_INVENTORY_ENABLED=false`。
- 每个实现任务遵循红—绿—重构；提交前运行该任务列出的精确测试。

---

## File Map

- `Stages/remnant_drawing_reader/`: DXF 文本提取、候选分类、JSON 契约和 CLI。
- `backend/app/modules/remnant_inventory/models.py`: 材质、别名、导入账本、正式余料和零件实体。
- `backend/app/modules/remnant_inventory/schemas.py`: HTTP 输入输出模型与稳定枚举。
- `backend/app/modules/remnant_inventory/materials.py`: 标准材质、别名及同系列解析。
- `backend/app/modules/remnant_inventory/imports.py`: 上传登记、校正、批量厚度、确认、取消与重试。
- `backend/app/modules/remnant_inventory/execution.py`: 转换/解析 attempt fencing 与 Stage 适配。
- `backend/app/modules/remnant_inventory/inventory.py`: 检索、编辑、预占、取消、使用和归档。
- `backend/app/modules/remnant_inventory/tasks.py`: 两个 Celery 队列的薄任务入口。
- `backend/app/modules/remnant_inventory/routes.py`: API 路由与权限依赖。
- `backend/app/modules/remnant_inventory/interface.py`: 未来其他领域可用的只读/消费边界。
- `frontend/src/features/remnant-inventory/`: API、类型、检索页、导入页、确认面板和样式。

### Task 1: 建立 DXF 余料解析 Stage

**Files:**
- Create: `Stages/remnant_drawing_reader/pyproject.toml`
- Create: `Stages/remnant_drawing_reader/src/remnant_drawing_reader/{__init__,models,text,reader,classifier,cli}.py`
- Create: `Stages/remnant_drawing_reader/tests/test_reader.py`
- Create: `Stages/remnant_drawing_reader/tests/fixtures/{plain_text,nested_insert,conflicting,broken}.dxf`
- Create: `Stages/remnant_drawing_reader/README.md`
- Modify: `Stages/README.md`

**Interfaces:**
- Produces: `parse_dxf(path: Path) -> ParseResult` and CLI `python -m remnant_drawing_reader INPUT --output OUTPUT`.
- Produces JSON keys: `schema_version`, `parser_version`, `source_sha256`, `material_candidates`, `project_candidates`, `part_candidates`, `warnings`.

- [x] **Step 1: Write contract-first failing tests**

```python
def test_nested_blocks_preserve_evidence(fixture_dir):
    result = parse_dxf(fixture_dir / "nested_insert.dxf")
    assert result.schema_version == "1.0"
    assert [item.value for item in result.part_candidates] == ["L-101", "L-102"]
    assert result.part_candidates[0].evidence[0].block_path == ["TITLE", "PARTS"]

def test_material_suffix_is_not_truncated(fixture_dir):
    result = parse_dxf(fixture_dir / "plain_text.dxf")
    assert any(item.value == "Q235B-Z15" for item in result.material_candidates)

def test_broken_dxf_fails_with_stable_code(fixture_dir):
    with pytest.raises(ParseError, match="REMNANT_DXF_UNREADABLE"):
        parse_dxf(fixture_dir / "broken.dxf")
```

- [x] **Step 2: Run `cd Stages/remnant_drawing_reader && uv run pytest -q`; expect import/contract failures.**
- [x] **Step 3: Implement typed immutable result models, NFKC/whitespace/MIF normalization, recursive `INSERT` traversal, evidence capture (`entity_type`, `layer`, `block_path`, `x`, `y`, `handle`, `raw_text`) and conservative labelled-value classifiers.**

```python
def parse_dxf(path: Path) -> ParseResult:
    source = path.read_bytes()
    try:
        document = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as exc:
        raise ParseError("REMNANT_DXF_UNREADABLE") from exc
    evidence = tuple(iter_text_evidence(document))
    return classify(evidence, source_sha256=hashlib.sha256(source).hexdigest())
```

- [x] **Step 4: Add CLI JSON serialization and README I/O example; run Stage tests and expect all PASS.**
- [x] **Step 5: Commit `feat(stage): add remnant drawing reader`.**

### Task 2: 建立余料数据模型与空库迁移

**Files:**
- Create: `backend/app/modules/remnant_inventory/{__init__,models,README}.py`
- Create: `backend/migrations/versions/2b7e91d4c830_add_remnant_inventory.py`
- Create: `backend/tests/remnant_inventory/test_models.py`
- Modify: `backend/app/bootstrap/model_registry.py`
- Modify: `backend/tests/architecture/test_module_catalog.py`
- Modify: `docs/architecture/{module-catalog.md,module-catalog.json,traceability.md}`

**Interfaces:**
- Produces models `RemnantMaterial`, `RemnantMaterialAlias`, `RemnantImportBatch`, `RemnantImportItem`, `Remnant`, `RemnantPart`.
- Stable status strings: item `uploaded|converting|parsing|pending_confirmation|confirmed|failed|cancelled`; remnant `available|reserved|used|archived`.

- [x] **Step 1: Write failing model tests for unique source SHA, unique `(remnant_id, part_no)`, alias uniqueness, DECIMAL thickness and all foreign keys.**

```python
def test_remnant_source_sha_is_unique(db_session, user, stored_file, material):
    first = make_remnant(source_sha256="a" * 64)
    db_session.add(first); db_session.commit()
    db_session.add(make_remnant(source_sha256="a" * 64))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [x] **Step 2: Run `cd backend && uv run pytest tests/remnant_inventory/test_models.py -q`; expect missing module failure.**
- [x] **Step 3: Implement typed SQLAlchemy models with `DECIMAL(10,3)` thickness, JSON candidate/evidence/warning columns, batch counters, item `attempt`, remnant `version`, audit user/time fields, indexes for `(material_id, thickness_mm, status)` and all named constraints.**
- [x] **Step 4: Add Alembic upgrade/downgrade creating tables in FK order, register models, then run `cd backend && uv run pytest tests/remnant_inventory/test_models.py tests/infrastructure/test_migrations.py -q`; expect PASS.**
- [x] **Step 5: Regenerate module catalog with `backend/.venv/bin/python scripts/architecture/snapshot_contracts.py` and commit `feat(remnants): add inventory persistence model`.**

### Task 3: 实现材质目录、别名和权限

**Files:**
- Create: `backend/app/modules/remnant_inventory/{schemas,materials,access}.py`
- Create: `backend/tests/remnant_inventory/test_materials.py`
- Modify: `backend/app/bootstrap/seed.py`

**Interfaces:**
- Produces: `resolve_material_candidate(session, raw: str) -> MaterialResolution | None`.
- Produces API service functions `list_materials`, `create_material`, `update_material`, `replace_aliases`.
- Roles: `remnant_worker` can read/use; `admin` and `super_admin` can manage catalog.

- [ ] **Step 1: Test exact code/alias normalization, preserved suffixes, disabled material rejection, same-series expansion, duplicate alias conflict, and non-admin mutation denial.**

```python
def test_family_expansion_does_not_strip_suffix(session):
    seed_materials(session, [("Q235B", "Q235"), ("Q235D", "Q235"), ("Q235B-Z15", "Q235")])
    assert material_ids_for_search(session, "Q235B-Z15", include_family=False) == [3]
    assert material_ids_for_search(session, "Q235B-Z15", include_family=True) == [1, 2, 3]
```

- [ ] **Step 2: Run focused tests; expect missing services.**
- [ ] **Step 3: Implement uppercase+NFKC lookup without truncating codes, exact alias resolution, enabled checks and SQL family expansion; seed `remnant_worker` permission role without granting admin catalog mutations.**
- [ ] **Step 4: Run `cd backend && uv run pytest tests/remnant_inventory/test_materials.py tests/identity/test_rbac_deep.py -q`; expect PASS.**
- [ ] **Step 5: Commit `feat(remnants): add managed material catalog`.**

### Task 4: 实现批量上传登记与重复检测

**Files:**
- Create: `backend/app/modules/remnant_inventory/imports.py`
- Create: `backend/tests/remnant_inventory/test_import_batches.py`
- Modify: `backend/app/modules/files/interface.py`
- Modify: `backend/app/platform/config/settings.py`
- Modify: `.env.example`
- Modify: `.env.docker.example`

**Interfaces:**
- Produces `create_import_batch(session, *, actor, uploads: Sequence[UploadFile], request_id: str) -> RemnantImportBatch`.
- Exposes file validation helpers for DWG and DXF through `files.interface` only.
- Adds settings `remnant_inventory_enabled: bool = False`, `remnant_import_max_files: int = 100`.

- [ ] **Step 1: Test mixed DWG/DXF, zero files, over-config limit, forged extension/header, same-batch SHA duplicate, existing-remnant SHA duplicate and partial storage compensation.**

```python
def test_duplicate_source_returns_existing_remnant(client, worker_headers, existing_remnant, same_dwg):
    response = client.post("/api/v1/remnant-import-batches", headers=worker_headers,
                           files=[("files", ("same.dwg", same_dwg, "application/acad"))])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REMNANT_SOURCE_DUPLICATE"
    assert response.json()["error"]["details"]["remnant_id"] == existing_remnant.id
```

- [ ] **Step 2: Run focused test; expect missing service and settings.**
- [ ] **Step 3: Stream each upload once into the files registry, calculate SHA-256 there, validate AC10xx DWG header or parseable DXF structure, persist item rows independently, and schedule post-commit dispatch data without running conversion in the request.**
- [ ] **Step 4: Run import tests plus `backend/tests/files`; expect PASS and no leaked temp objects.**
- [ ] **Step 5: Commit `feat(remnants): register mixed drawing import batches`.**

### Task 5: 接入批量 DWG 转换与逐图解析任务

**Files:**
- Create: `backend/app/modules/remnant_inventory/{execution,tasks,stage_adapter}.py`
- Create: `backend/tests/remnant_inventory/test_execution.py`
- Modify: `backend/app/modules/cad_processing/interface.py`
- Modify: `backend/app/bootstrap/task_registry.py`
- Modify: `backend/app/platform/messaging/celery_app.py`
- Modify: `compose.yaml`
- Modify: `compose.dev.yaml`

**Interfaces:**
- Produces Celery tasks `app.modules.remnant_inventory.tasks.convert_batch(batch_id, expected_attempts)` and `parse_item(item_id, expected_attempt)`.
- Consumes one public CAD function `convert_dwg_directory(inputs: Mapping[int, Path], output_dir: Path) -> Mapping[int, Path]`.

- [ ] **Step 1: Test one ODA invocation per batch, direct-DXF parse dispatch, per-file conversion failure, four parallel queue consumers by compose contract, retry attempt increment, and stale attempt update rejection.**

```python
def test_old_parse_attempt_cannot_overwrite_retry(session, item, stage_result):
    item.attempt = 2; item.status = "parsing"; session.commit()
    assert store_parse_result(session, item.id, expected_attempt=1, result=stage_result) is False
    session.refresh(item)
    assert item.status == "parsing"
```

- [ ] **Step 2: Run execution and compose tests; expect task/queue failures.**
- [ ] **Step 3: Implement per-task temp directories, one directory conversion call, output reconciliation, derived-file registration, Stage subprocess with timeout, sanitized stable errors, `status + attempt` guarded updates, counters recalculated from item rows, and task dispatch only after DB commit.**
- [ ] **Step 4: Route `remnant_convert` and `remnant_parse`, add dedicated workers with concurrency 2 and 4, and include tasks in both registries. Run `cd backend && uv run pytest tests/remnant_inventory/test_execution.py tests/infrastructure/test_compose.py tests/architecture -q`; expect PASS.**
- [ ] **Step 5: Commit `feat(remnants): process imports on dedicated workers`.**

### Task 6: 实现人工校正、批量厚度和幂等确认

**Files:**
- Modify: `backend/app/modules/remnant_inventory/imports.py`
- Modify: `backend/app/modules/remnant_inventory/schemas.py`
- Create: `backend/tests/remnant_inventory/test_confirmation.py`

**Interfaces:**
- Produces `update_import_item`, `bulk_apply_thickness`, `retry_import_item`, `cancel_import_batch`, `confirm_import_items`.
- Confirmation input: `item_ids: list[int]`; output lists `confirmed`, `invalid`, `already_confirmed` independently.

- [ ] **Step 1: Test editable candidates, importer/admin ownership, positive 3-decimal thickness, enabled material, required project number, de-duplicated non-empty parts, selected-only partial success, repeated confirmation identity and cancellation cleanup request.**

```python
def test_repeat_confirmation_returns_same_inventory_id(service, ready_item, actor):
    first = service.confirm_import_items([ready_item.id], actor=actor)
    second = service.confirm_import_items([ready_item.id], actor=actor)
    assert first.confirmed[0].remnant_id == second.already_confirmed[0].remnant_id
```

- [ ] **Step 2: Run confirmation tests; expect missing operations.**
- [ ] **Step 3: Validate and normalize worker edits, persist corrections only on import items, create `Remnant` and `RemnantPart` transactionally, catch source-SHA races by re-reading the winner, and recalculate batch counters after every transition.**
- [ ] **Step 4: Run all `backend/tests/remnant_inventory`; expect PASS.**
- [ ] **Step 5: Commit `feat(remnants): confirm parsed drawings into inventory`.**

### Task 7: 实现库存检索、生命周期和原图下载权限

**Files:**
- Create: `backend/app/modules/remnant_inventory/inventory.py`
- Create: `backend/app/modules/remnant_inventory/interface.py`
- Create: `backend/tests/remnant_inventory/test_inventory.py`
- Modify: `backend/app/modules/files/interface.py`

**Interfaces:**
- Produces `search_remnants(material_id, thickness_mm, include_family, statuses, page, page_size)`.
- Produces atomic actions `reserve`, `release`, `mark_used`, `archive` and read-only future domain boundary `find_available_remnants`.
- Produces `build_original_download(remnant_id, actor)` using `source_file_id`, never `dxf_file_id`.

- [ ] **Step 1: Test required exact filters, family expansion, default statuses/order, history opt-in, importer edit rule, reserved/used locks, two-session atomic reservation, occupant visibility, preview-for-all, and original-download matrix.**

```python
@pytest.mark.parametrize("source_ext", ["dwg", "dxf"])
def test_download_uses_actual_uploaded_source(service, reserved_by_actor, source_ext):
    download = service.build_original_download(reserved_by_actor.id, reserved_by_actor.reserved_by)
    assert download.file_id == reserved_by_actor.source_file_id
    assert download.file_name.endswith(f".{source_ext}")
    assert download.file_id != reserved_by_actor.dxf_file_id or source_ext == "dxf"
```

- [ ] **Step 2: Run inventory tests; expect missing service.**
- [ ] **Step 3: Implement SQL-filtered pagination and conditional `UPDATE remnants SET ... WHERE id=:id AND status='available' AND version=:version`; enforce state/action permissions and write audit events for every mutation.**
- [ ] **Step 4: Run inventory, audit, file and MySQL concurrency tests; expect one reservation success and one `REMNANT_ALREADY_RESERVED`.**
- [ ] **Step 5: Commit `feat(remnants): add searchable inventory lifecycle`.**

### Task 8: 发布 API 路由和 OpenAPI 契约

**Files:**
- Create: `backend/app/modules/remnant_inventory/routes.py`
- Create: `backend/tests/remnant_inventory/test_api.py`
- Modify: `backend/app/bootstrap/router.py`
- Modify: `docs/api/openapi.json`
- Modify: `docs/api/API_REFERENCE.md`

**Interfaces:**
- Routes: `/api/v1/remnant-materials`, `/api/v1/remnant-import-batches`, `/api/v1/remnants` plus static action routes before `/{id}`.
- Uses platform success/page envelopes and stable error codes beginning `REMNANT_`.

- [ ] **Step 1: Write API tests for all list/detail/mutation endpoints, multipart batch upload, 401/403/404/409/422 envelopes, pagination, request ID and traceback/path/stderr redaction.**
- [ ] **Step 2: Run API tests; expect 404.**
- [ ] **Step 3: Add thin FastAPI routes delegating to services; put `/search`, `/bulk-confirm` and other static routes before parameter routes; hide the entire router behind the feature flag with stable 404 when disabled.**
- [ ] **Step 4: Run API and contract tests, then `make docs-generate && make docs-check`; expect generated docs clean.**
- [ ] **Step 5: Commit `feat(remnants): expose inventory API`.**

### Task 9: 构建前端余料检索和详情

**Files:**
- Create: `frontend/src/features/remnant-inventory/{index,types,api,RemnantInventoryPage,RemnantSearchPanel,RemnantDetailDrawer,styles}.ts{x,}`
- Create: `frontend/tests/e2e/remnant-inventory/search.spec.ts`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/layout.tsx`
- Modify: `frontend/scripts/check-architecture.mjs`

**Interfaces:**
- Exports only `RemnantInventoryPage` from feature `index.ts`.
- Query key: `['remnants', {materialId, thicknessMm, includeFamily, statuses, page}]`.

- [ ] **Step 1: In Playwright, mock material/search/detail/preview/download APIs and assert required material+thickness, same-series switch, available-first table, reserver display, history filter, reserve conflict refresh and download button permission.**
- [ ] **Step 2: Run `cd frontend && npx playwright test tests/e2e/remnant-inventory/search.spec.ts`; expect missing route.**
- [ ] **Step 3: Implement Ant Design page matching current shell, typed API client, URL-backed search filters, React Query invalidation, status tags, detail drawer, shared preview export, and source download label showing DWG or DXF.**
- [ ] **Step 4: Add `/remnants` route/nav and architecture allow-list; run focused E2E, `npm run check:architecture`, and `npm run build`; expect PASS.**
- [ ] **Step 5: Commit `feat(frontend): add remnant search experience`.**

### Task 10: 构建批量导入、刷新恢复和解析确认界面

**Files:**
- Create: `frontend/src/features/remnant-inventory/{RemnantImportPanel,RemnantBatchProgress,RemnantConfirmationPanel,useRemnantBatch}.ts{x,}`
- Create: `frontend/tests/e2e/remnant-inventory/import.spec.ts`
- Modify: `frontend/src/features/remnant-inventory/{RemnantInventoryPage,index,styles}.ts{x,}`

**Interfaces:**
- Batch ID persists in URL as `?tab=import&batch=<id>`.
- Polling stops only at batch terminal state; pending-confirmation remains recoverable after refresh.

- [ ] **Step 1: Mock mixed file upload and assert no ZIP input, per-item progress/failure/retry, refresh restoration, row selection, bulk thickness, editable material/project/parts, warnings/evidence, selected-valid partial confirmation and original filename display.**
- [ ] **Step 2: Run import E2E; expect missing controls.**
- [ ] **Step 3: Implement multi-file DWG/DXF uploader, progress table, 2-second active polling with background refetch disabled at terminal state, bulk thickness modal, split preview/editor confirmation view, part-tag editing and row-level validation messages.**
- [ ] **Step 4: Run all remnant E2E, architecture check and production build; expect PASS.**
- [ ] **Step 5: Commit `feat(frontend): add remnant batch confirmation flow`.**

### Task 11: 真实样本校准、回归夹具和上线门禁

**Files:**
- Create: `scripts/remnant_inventory/report_corpus.py`
- Create: `backend/tests/remnant_inventory/test_real_fixture_regressions.py`
- Create: `docs/operations/remnant-inventory.md`
- Modify: `docs/architecture/runtime-contract.json`
- Modify: `docs/architecture/implementation-status.md`
- Modify: `backend/tests/contracts/test_stage1_boundaries.py`

**Interfaces:**
- Corpus command accepts explicit external directory and writes only candidate JSON/CSV to an explicit output path; no business DWG enters git.
- Feature remains disabled until material setup and acceptance checklist are complete.

- [ ] **Step 1: Add tests proving the corpus script requires explicit paths, excludes source bytes, handles 144 AC1032 DWGs, and runtime contract lists both queues/concurrency values.**
- [ ] **Step 2: Run contract tests; expect missing runtime entries.**
- [ ] **Step 3: Implement report command that hashes files, invokes existing batch conversion adapter, parses each DXF, emits candidate/evidence/warnings and aggregate counts; document admin material setup, retry, queue health, permission matrix, backup/rollback and feature-flag enablement.**
- [ ] **Step 4: Run against `C:\Users\Ran-xin\Desktop\kuak\余料库\手动拆分清单` into a gitignored temp output; inspect that 144 DWGs are enumerated and no source drawing appears in `git status`.**
- [ ] **Step 5: Run `make verify-quick`, Stage tests, all backend remnant tests, frontend remnant E2E/build, `make docs-check`, `git diff --check`; all must exit 0.**
- [ ] **Step 6: Commit `docs(remnants): add rollout and sample calibration gate`.**

### Task 12: 最终验收与发布准备

**Files:**
- Modify only files required by failures discovered below; every fix must add a regression test beside the affected subsystem.

**Interfaces:**
- Acceptance batch: mixed 2–10 DWG/DXF plus a separate >20-file backpressure run.

- [ ] **Step 1: On an empty MySQL schema run upgrade to head and downgrade/upgrade the new revision; expect all six tables and named constraints.**
- [ ] **Step 2: With two worker users, verify exact/family search, preview, concurrent reserve, non-owner download denial, owner original DWG/DXF download, cancel, re-reserve and mark-used lock.**
- [ ] **Step 3: Import a mixed batch, force one conversion failure, retry it, refresh the browser, bulk-fill thickness, edit candidates and confirm only valid selected rows.**
- [ ] **Step 4: Run `make verify-full`; expect exit 0 and retain `REMNANT_INVENTORY_ENABLED=false` in examples/defaults.**
- [ ] **Step 5: Review the complete diff against `docs/superpowers/specs/2026-07-22-remnant-inventory-design.md`, verify no external drawings/secrets/temp outputs are tracked, then commit any final regression-only changes.**
