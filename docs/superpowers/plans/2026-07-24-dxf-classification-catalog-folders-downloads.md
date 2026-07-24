# DXF Classification Catalog, Folders, and Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Steel DXF Classifier to 1.2, persist authoritative per-drawing classification semantics, expose folder/detail/next-stage contracts, and deliver DXF-only per-category and all-category downloads in the production UI.

**Architecture:** The classifier remains the sole owner of title-block evidence decisions and emits complete versioned semantics. The backend persists those semantics on `dxf_classification_items`, derives folder summaries from rows rather than storing duplicate folder counts, and exposes typed read/download interfaces. The frontend renders server-provided folders and paginated details while JSON/CSV remain hidden audit artifacts.

**Tech Stack:** Python 3.12, ezdxf, Pytest, FastAPI, SQLAlchemy, Alembic, MySQL, MinIO/Files transfer ledger, React 19, TypeScript 6, Ant Design 6, TanStack Query, Playwright.

---

### Task 1: Classifier 1.2 semantic contract

**Files:**
- Modify: `Stages/steel_dxf_classifier_v1.1.0/tests/test_profile.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/tests/test_classify.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/tests/test_batch.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/tests/test_model.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/tests/test_cli.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/tests/test_version.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/model.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/profile.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/classify.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/batch.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/cli.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/__init__.py`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/VERSION`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/pyproject.toml`

- [ ] **Step 1: Write failing catalog and automatic-discovery tests**

Add parameterized tests asserting every registered type parses as `catalog`, including PX:

```python
@pytest.mark.parametrize(
    "profile, part_type",
    [
        ("PX300*150*8", "PX"),
        ("BH500*300*10*16", "BH"),
        ("BOX400*300*12", "BOX"),
        ("RHS200*100*8", "RHS"),
        ("IPE300*150*8", "IPE"),
    ],
)
def test_registered_profiles_are_catalog_types(profile: str, part_type: str) -> None:
    parsed = parse_profile(profile)
    assert parsed is not None
    assert parsed.part_type == part_type
    assert parsed.type_source == "catalog"
```

Add `XY250*120*8` assertions for `auto_discovered`, and rejection assertions for `Q355B`, `M20`, `1:10`, `300*200`, `A300`, and path characters.

- [ ] **Step 2: Run profile tests and verify RED**

Run:

```bash
cd Stages/steel_dxf_classifier_v1.1.0
uv run pytest -q tests/test_profile.py tests/test_classify.py
```

Expected: FAIL because PX is absent and `type_source`, `group_key`, and `next_stage_eligible` do not exist.

- [ ] **Step 3: Extend the semantic model and parser**

Change `ProfileParse` to carry:

```python
type_source: str
```

Change `ClassificationResult` to serialize:

```python
profile_raw: str | None
profile_normalized: str | None
type_source: str | None
group_key: str
next_stage_eligible: bool
```

Keep registered single-letter types valid, add PX to the catalog, and use:

```python
type_source = "catalog" if prefix in REGISTERED_TYPES else "auto_discovered"
```

Unknown prefixes remain eligible only when they contain 2–12 ASCII letters and the body is a numeric dimension expression.

- [ ] **Step 4: Implement fail-closed group semantics**

For a unique title value return `group_key=f"type:{part_type}"` and `next_stage_eligible=True`. Add `PROFILE_TYPE_AUTO_DISCOVERED` only for automatic types. For failures use:

```python
group_key = f"status:{disposition.value}"
next_stage_eligible = False
```

- [ ] **Step 5: Verify classifier semantics GREEN**

Run:

```bash
uv run pytest -q tests/test_profile.py tests/test_classify.py tests/test_model.py
```

Expected: PASS.

- [ ] **Step 6: Write failing report/version/CLI tests**

Require:

```python
assert report["schema"] == "STEEL-DXF-CLASSIFICATION-1.2"
assert report["results"][0]["type_source"] == "catalog"
assert report["results"][0]["group_key"] == "type:PX"
assert report["results"][0]["next_stage_eligible"] is True
assert payload["schema"] == "STEEL-DXF-CLI-1.2"
assert steel_dxf_classifier.__version__ == "1.2.0"
```

- [ ] **Step 7: Run report/version tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_batch.py tests/test_cli.py tests/test_version.py
```

Expected: FAIL on the old 1.1 schemas and version.

- [ ] **Step 8: Upgrade report, CLI, CSV, and package version**

Set:

```text
VERSION=1.2.0
REPORT_SCHEMA=STEEL-DXF-CLASSIFICATION-1.2
CLI_SCHEMA=STEEL-DXF-CLI-1.2
```

Add CSV columns `规格规范值`, `类型来源`, and `下一阶段可用`, populated from the same `ClassificationResult` fields as JSON.

- [ ] **Step 9: Run the complete classifier suite**

Run:

```bash
uv run pytest -q
uv run python -m compileall -q src
```

Expected: all tests PASS and compilation exits 0.

- [ ] **Step 10: Commit the classifier slice**

```bash
git add Stages/steel_dxf_classifier_v1.1.0
git commit -m "feat(classifier): recognize PX and safe discovered types"
```

### Task 2: Durable database classification fields

**Files:**
- Create: `backend/migrations/versions/d6f3a8c2e710_add_dxf_classification_semantics.py`
- Modify: `backend/tests/infrastructure/test_migrations.py`
- Modify: `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`
- Modify: `backend/app/modules/dxf_classification/models.py`
- Modify: `backend/app/modules/dxf_classification/persistence.py`
- Modify: `backend/app/modules/dxf_classification/adapter.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`

- [ ] **Step 1: Write failing migration contract tests**

Assert the new revision:

```python
for column in (
    "profile_raw",
    "profile_normalized",
    "type_source",
    "group_key",
    "next_stage_eligible",
):
    assert f'"{column}"' in source
assert '"ix_dxf_classification_items_group"' in source
assert '"ix_dxf_classification_items_next_stage"' in source
assert 'type_source="legacy"' in source
```

- [ ] **Step 2: Run migration tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/infrastructure/test_migrations.py -k classification
```

Expected: FAIL because the semantic migration is absent.

- [ ] **Step 3: Add the migration and ORM columns**

Use `down_revision="c7b2d4e9f601"`. Add nullable string columns first, add non-null `group_key` and `next_stage_eligible` with temporary server defaults, backfill:

```text
classified + part_type + output_file_id -> type:<part_type>, eligible=1, type_source=legacy
review_required -> status:review_required, eligible=0
unreadable -> status:unreadable, eligible=0
other legacy status -> status:<disposition>, eligible=0
```

Remove temporary defaults after backfill and add indexes `(run_id, group_key)` and `(run_id, next_stage_eligible)`.

- [ ] **Step 4: Write failing persistence assertions**

Extend the integration result fixture with:

```json
{
  "profile_raw": "PX300*150*8",
  "profile_normalized": "PX300*150*8",
  "type_source": "catalog",
  "group_key": "type:PX",
  "next_stage_eligible": true
}
```

Assert the saved `DxfClassificationItem` has exactly those values.

- [ ] **Step 5: Run the pipeline test and verify RED**

Run:

```bash
uv run pytest -q tests/dxf_classification/test_dxf_classification_pipeline.py -k persists
```

Expected: FAIL because persistence ignores the new fields.

- [ ] **Step 6: Persist and validate classifier semantics**

Update `record_classification_item` to reject inconsistent payloads and assign every new column. Classified output is eligible only when the classifier says so and `part_type` is present; review/unreadable results always override eligibility to false.

Update adapter/backend dependency constants to `1.2.0`, `STEEL-DXF-CLI-1.2`, and `STEEL-DXF-CLASSIFICATION-1.2`, then run:

```bash
uv lock
uv run pytest -q tests/infrastructure/test_migrations.py -k classification
uv run pytest -q tests/dxf_classification/test_dxf_classification_pipeline.py
```

Expected: PASS.

- [ ] **Step 7: Commit the persistence slice**

```bash
git add backend/migrations/versions/d6f3a8c2e710_add_dxf_classification_semantics.py \
  backend/tests/infrastructure/test_migrations.py \
  backend/tests/dxf_classification/test_dxf_classification_pipeline.py \
  backend/app/modules/dxf_classification backend/pyproject.toml backend/uv.lock
git commit -m "feat(classification): persist authoritative drawing semantics"
```

### Task 3: Folder summaries, paginated details, and next-stage reads

**Files:**
- Modify: `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`
- Modify: `backend/tests/architecture/test_cad_processing_boundaries.py`
- Modify: `backend/app/modules/dxf_classification/schemas.py`
- Modify: `backend/app/modules/dxf_classification/persistence.py`
- Modify: `backend/app/modules/dxf_classification/presentation.py`
- Modify: `backend/app/modules/dxf_classification/interface.py`
- Modify: `backend/app/modules/workflows/routes/classification.py`

- [ ] **Step 1: Write failing folder and internal-interface tests**

Create PX, XY, review, and unreadable records and assert:

```python
assert [(group.group_key, group.count) for group in payload.groups] == [
    ("status:review_required", 1),
    ("status:unreadable", 1),
    ("type:PX", 2),
    ("type:XY", 1),
]
assert [item.part_type for item in list_next_stage_inputs(db, workflow.id)] == [
    "PX",
    "PX",
    "XY",
]
```

Assert detail responses contain `output_name`, specifications, source, disposition, diagnostics, and size, but contain no `id`, `file_id`, `bucket`, `storage_key`, `report_file`, or `manifest_file`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/dxf_classification/test_dxf_classification_pipeline.py \
  -k 'group or next_stage'
```

Expected: FAIL because group/detail/internal read contracts are missing.

- [ ] **Step 3: Implement database projections**

Add Pydantic schemas:

```python
class DxfClassificationGroupRead(BaseModel):
    group_key: str
    label: str
    part_type: str | None
    type_source: str | None
    disposition: str
    count: int
    warning_count: int
    total_size_bytes: int

class DxfClassificationGroupPage(BaseModel):
    items: list[DxfClassificationGroupItemRead]
    total: int
    page: int
    page_size: int
```

Aggregate groups from authoritative rows and registered output sizes. Sort review/unreadable first and normal types naturally. Implement `list_next_stage_inputs` against the latest run with `next_stage_eligible=True`.

- [ ] **Step 4: Add the paginated group route**

Add:

```text
GET /{workflow_id}/dxf-classification/groups/{group_key}
```

Validate `page >= 1`, `1 <= page_size <= 100`, require project membership, and return `CLASSIFICATION_GROUP_NOT_FOUND` for an absent key.

- [ ] **Step 5: Verify group and boundary tests GREEN**

Run:

```bash
uv run pytest -q tests/dxf_classification/test_dxf_classification_pipeline.py
uv run pytest -q tests/architecture/test_cad_processing_boundaries.py
```

Expected: PASS.

- [ ] **Step 6: Commit the read-contract slice**

```bash
git add backend/app/modules/dxf_classification \
  backend/app/modules/workflows/routes/classification.py \
  backend/tests/dxf_classification/test_dxf_classification_pipeline.py \
  backend/tests/architecture/test_cad_processing_boundaries.py
git commit -m "feat(classification): expose folders and next-stage inputs"
```

### Task 4: DXF-only category and complete downloads

**Files:**
- Modify: `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`
- Modify: `backend/tests/workflows/test_workflow_input_api.py`
- Modify: `backend/app/modules/workflows/routes/classification.py`
- Modify: `backend/app/modules/workflows/routes/archive.py`
- Modify: `backend/app/modules/workflows/routes/README.md`

- [ ] **Step 1: Write failing real ZIP tests**

Request:

```text
GET /api/v1/workflows/{id}/dxf-classification/groups/type:PX/download-archive
GET /api/v1/workflows/{id}/dxf-classification/download-archive
```

Open each response with `zipfile.ZipFile`. Assert category ZIP includes only PX DXFs. Assert all-DXF ZIP includes every group directory. For both:

```python
assert all(name.lower().endswith(".dxf") for name in names)
assert not any(name.lower().endswith((".json", ".csv", ".dwg")) for name in names)
```

Also assert missing group, empty run, deleted output, forbidden project, outbound transfer, and audit behavior.

- [ ] **Step 2: Run ZIP tests and verify RED**

Run:

```bash
uv run pytest -q tests/dxf_classification/test_dxf_classification_pipeline.py -k archive
```

Expected: FAIL because the DXF-only routes do not exist.

- [ ] **Step 3: Extract a registered-DXF archive helper**

Reuse `build_registered_files_zip_to_path`, `prepare_transfer_in_transaction`, `settle_stream`, file read access, audit logging, and cleanup from workflow archives. The helper accepts only classification item `output_file_id` values and constructs:

```text
<project_name>/<group_label>/<output_name>
```

Never resolve `report_file_id`, `manifest_file_id`, or other workflow artifacts in these endpoints.

- [ ] **Step 4: Add both endpoints and stable errors**

Use:

```text
CLASSIFICATION_ARCHIVE_EMPTY
CLASSIFICATION_GROUP_NOT_FOUND
CLASSIFICATION_OUTPUT_MISSING
```

Record distinct operations `dxf_classification_group_download_zip` and `dxf_classification_all_download_zip`.

- [ ] **Step 5: Reconcile the existing stage ZIP test**

Preserve the existing generic stage archive contract for audit/production output. Adjust only classification-page expectations so its buttons use DXF-only routes. Do not delete the user's stage navigation/download work.

- [ ] **Step 6: Run ZIP and workflow archive regression tests**

Run:

```bash
uv run pytest -q tests/dxf_classification/test_dxf_classification_pipeline.py \
  tests/workflows/test_workflow_input_api.py -k 'archive or classification'
```

Expected: PASS.

- [ ] **Step 7: Commit the download slice**

```bash
git add backend/app/modules/workflows/routes/classification.py \
  backend/app/modules/workflows/routes/archive.py \
  backend/app/modules/workflows/routes/README.md \
  backend/tests/dxf_classification/test_dxf_classification_pipeline.py \
  backend/tests/workflows/test_workflow_input_api.py
git commit -m "feat(classification): download category and all-DXF archives"
```

### Task 5: Production folder console

**Files:**
- Modify: `frontend/src/features/workflows/workflow.ts`
- Modify: `frontend/src/features/workflows/workflows.api.ts`
- Modify: `frontend/src/features/workflows/DxfClassificationPanel.tsx`
- Modify: `frontend/src/features/workflows/styles.css`
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`
- Modify: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: Write failing browser/contract tests**

Mock a completed run with PX, XY, review, and unreadable groups. Assert:

```typescript
await expect(page.getByRole('button', { name: /PX.*12 张/ })).toBeVisible();
await expect(page.getByText('自动发现')).toBeVisible();
await expect(page.getByText(/3 张图纸需要处理/)).toBeVisible();
await expect(page.getByText('分类报告已纳入生产压缩包')).toHaveCount(0);
await expect(page.getByText('分类清单已纳入生产压缩包')).toHaveCount(0);
```

Click PX, assert the detail request includes `page=1&page_size=20`, and assert no file ID/JSON/CSV appears. Intercept category and all-DXF download URLs.

- [ ] **Step 2: Run browser test and verify RED**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts \
  --grep "classification folders"
```

Expected: FAIL because the panel still renders tags/collapsible flat items.

- [ ] **Step 3: Add frontend types and API functions**

Add:

```typescript
export interface DxfClassificationGroup {
  group_key: string;
  label: string;
  part_type?: string | null;
  type_source?: 'catalog' | 'auto_discovered' | 'legacy' | null;
  disposition: 'classified' | 'review_required' | 'unreadable';
  count: number;
  warning_count: number;
  total_size_bytes: number;
}

export interface DxfClassificationGroupItem {
  output_name: string;
  part_type?: string | null;
  profile_raw?: string | null;
  profile_normalized?: string | null;
  type_source?: 'catalog' | 'auto_discovered' | 'legacy' | null;
  disposition: 'classified' | 'review_required' | 'unreadable';
  diagnostics: string[];
  size_bytes: number;
}

export interface DxfClassificationGroupPage {
  items: DxfClassificationGroupItem[];
  total: number;
  page: number;
  page_size: number;
}
```

Add `getDxfClassificationGroup`, `downloadDxfClassificationGroupArchive`, and `downloadAllDxfClassificationArchive`. Reuse the existing Blob error parser and object-URL cleanup.

- [ ] **Step 4: Implement the folder console**

Replace the flat table/tags with:

- summary counters;
- warning alert with shortcuts;
- “下载全部 DXF” action;
- accessible folder buttons sorted by server order;
- per-folder download action with propagation stopped;
- right-side drawer with server pagination;
- catalog/automatic-discovery information tags;
- translated warning diagnostics.

Do not render `report_file`, `manifest_file`, file IDs, result IDs, bucket/key, or raw evidence.

- [ ] **Step 5: Integrate with current stage-navigation edits**

Preserve the current uncommitted `WorkflowStageRail`, historical-stage behavior, styles, and stage archive work. Classification execution remains enabled only when `isCurrent` and the stage status allows it.

- [ ] **Step 6: Verify frontend GREEN**

Run:

```bash
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
npm run build
cd ../backend
uv run pytest -q tests/contracts/test_frontend_contract.py
```

Expected: all commands PASS.

- [ ] **Step 7: Commit the frontend slice**

```bash
git add frontend/src/features/workflows frontend/tests/e2e/workflows/workflow-detail.spec.ts \
  backend/tests/contracts/test_frontend_contract.py
git commit -m "feat(workflows): browse and download DXF classification folders"
```

### Task 6: Documentation, real artifacts, and release gates

**Files:**
- Modify: `Stages/steel_dxf_classifier_v1.1.0/README.md`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/docs/CLASSIFICATION_RULES.md`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/docs/IO_CONTRACT.md`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/docs/VALIDATION.md`
- Modify: `Stages/steel_dxf_classifier_v1.1.0/CHANGELOG.md`
- Modify: `backend/app/modules/dxf_classification/README.md`
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/verification/current.md`

- [ ] **Step 1: Write failing documentation assertions**

Require documentation to mention:

```text
Steel DXF Classifier 1.2.0
PX
auto_discovered
next_stage_eligible
分类文件夹
下载全部 DXF
JSON/CSV 仅作为审计产物
```

- [ ] **Step 2: Run documentation tests and verify RED**

Run:

```bash
cd Stages/steel_dxf_classifier_v1.1.0
uv run pytest -q tests/test_documentation.py tests/test_version.py
cd ../../backend
uv run pytest -q tests/contracts/test_docs_consistency.py
```

Expected: FAIL on stale 1.1 wording and missing contracts.

- [ ] **Step 3: Synchronize documentation**

Document the catalog-versus-discovery policy, PX, database authority, hidden audit artifacts, both DXF-only ZIPs, warning semantics, next-stage boundary, and current validation evidence.

- [ ] **Step 4: Run focused and full automated gates**

Run:

```bash
cd Stages/steel_dxf_classifier_v1.1.0
uv run pytest -q
uv run python -m compileall -q src
cd ../../backend
uv run pytest -q tests/dxf_classification tests/workflows tests/infrastructure/test_migrations.py \
  tests/contracts/test_frontend_contract.py tests/architecture/test_cad_processing_boundaries.py
uv run ruff check app tests
cd ../frontend
npm run build
npx playwright test tests/e2e/workflows
```

Expected: all gates PASS.

- [ ] **Step 5: Verify migrations and live workflow**

From the repository root run:

```bash
bash scripts/db.sh migration-test
DXF_CLASSIFICATION_PIPELINE_ENABLED=true ./start-dev.sh
```

Require the isolated MySQL migration command to exit 0 and the status output to show the API plus `dxf_classification` worker healthy. Submit a frozen batch containing at least one PX drawing, one catalog type, one safe discovered type, and one uncertain drawing.

Verify in MySQL that every item has the expected `part_type`, `type_source`, `group_key`, and `next_stage_eligible`. Query the next-stage interface and confirm the uncertain row is absent.

- [ ] **Step 6: Inspect browser and downloaded artifacts**

Open the classification stage in a real browser, open PX and warning folders, and download:

```text
PX category ZIP
all DXF ZIP
```

Inspect ZIP member names, file counts, `.dxf` extensions, DXF headers, and SHA-256. Require zero JSON/CSV members.

- [ ] **Step 7: Commit docs and verification evidence**

```bash
git add Stages/steel_dxf_classifier_v1.1.0/README.md \
  Stages/steel_dxf_classifier_v1.1.0/docs \
  Stages/steel_dxf_classifier_v1.1.0/CHANGELOG.md \
  backend/app/modules/dxf_classification/README.md \
  frontend/src/features/workflows/README.md README.md README_EN.md \
  docs/verification/current.md
git commit -m "docs(classification): publish 1.2 folder workflow"
```

- [ ] **Step 8: Final repository audit**

Run:

```bash
git status --short
git log --oneline --decorate -10
```

Confirm only pre-existing user-owned untracked data remains, no implementation files are uncommitted, and all commits are reviewable.
