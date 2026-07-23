# Remnant Preview, Project Title, and Worker Material Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render readable Chinese in DXF previews, extract the complete drawing project title, auto-select known materials, and let remnant workers create and immediately use a missing full material grade.

**Architecture:** Keep the existing server-side DXF-to-safe-SVG preview and versioned cache. Extend the isolated drawing reader for complete title candidates, add a narrowly scoped idempotent material resolve/create endpoint, and connect that endpoint to the existing confirmation modal without changing thickness or confirmation semantics.

**Tech Stack:** Python 3.12, ezdxf 1.4.4, FastAPI, SQLAlchemy 2, MySQL/SQLite tests, React 19, TypeScript 6, Ant Design 6, TanStack Query 5, Playwright, Docker Compose.

## Global Constraints

- Project numbers come only from reliable text inside the drawing; never infer them from filenames.
- Thickness remains manually entered by the worker.
- Worker-created material stores the same complete grade in `code` and `family_code` and is enabled immediately.
- Remnant workers may create through the dedicated endpoint but may not edit, disable, or manage aliases.
- Missing CJK fonts must not fail preview generation; rendering may fall back to boxes.
- Preserve safe path-based SVG output with no external font links or active content.
- Use TDD for every behavior change and keep each task independently committable.

---

## File Structure

- `Stages/remnant_drawing_reader/src/remnant_drawing_reader/classifier.py`: classify a reliable full project title and enforce the 128-character persistence boundary.
- `Stages/remnant_drawing_reader/src/remnant_drawing_reader/__init__.py`: bump parser version for changed output semantics.
- `Stages/remnant_drawing_reader/tests/test_reader.py`: parser red/green coverage for complete, absent, conflicting, and oversized titles.
- `backend/app/modules/cad_processing/preview_rendering.py`: discover/register the CJK font and apply an in-memory preview-only style.
- `backend/app/modules/cad_processing/preview.py`: consume the bumped renderer version for automatic cache invalidation.
- `backend/tests/cad_processing/test_dxf_preview_service.py`: font-present/font-missing preview behavior and cache-key assertions.
- `backend/Dockerfile`: install `fonts-noto-cjk` in the runtime image.
- `backend/tests/infrastructure/test_compose.py`: assert the runtime image declares the CJK font package.
- `backend/app/modules/remnant_inventory/materials.py`: idempotent `resolve_or_create_material()` service.
- `backend/app/modules/remnant_inventory/schemas.py`: request and response contracts for worker creation.
- `backend/app/modules/remnant_inventory/routes.py`: worker-authorized route and creation audit.
- `backend/tests/remnant_inventory/test_materials.py`: service normalization, duplicate, and race behavior.
- `backend/tests/remnant_inventory/test_api.py`: worker permissions and API envelope/audit behavior.
- `backend/tests/remnant_inventory/test_inventory_mysql.py`: real unique-key race verification when MySQL acceptance tests are enabled.
- `frontend/src/features/remnant-inventory/api.ts`: typed resolve/create call.
- `frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx`: automatic selection and inline “create and use” action.
- `frontend/tests/e2e/remnant-inventory/import.spec.ts`: confirmation UI behavior and form-state preservation.

---

### Task 1: Extract the Complete Project Title

**Files:**
- Modify: `Stages/remnant_drawing_reader/src/remnant_drawing_reader/classifier.py`
- Modify: `Stages/remnant_drawing_reader/src/remnant_drawing_reader/__init__.py`
- Test: `Stages/remnant_drawing_reader/tests/test_reader.py`

**Interfaces:**
- Consumes: `classify(items: list[Evidence])` and normalized `Evidence.normalized_text`.
- Produces: full-title `project_candidates`; `PROJECT_TITLE_TOO_LONG` warning; parser version `0.2.0`.

- [ ] **Step 1: Write failing parser tests**

```python
def test_unlabelled_project_candidate_keeps_complete_drawing_title(tmp_path: Path) -> None:
    title = "北工大定位板及南京北站017计划天窗2批激光零件 2026-7-03"
    source = tmp_path / "arbitrary-file-name.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text(title).set_placement((0, 0))
    document.saveas(source)
    result = parse_dxf(source)
    assert [item.value for item in result.project_candidates] == [title]

def test_plain_filename_is_never_used_as_project_candidate(tmp_path: Path) -> None:
    source = tmp_path / "南京北站999计划.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text("Q355B").set_placement((0, 0))
    document.saveas(source)
    assert parse_dxf(source).project_candidates == []

def test_multiple_complete_titles_emit_project_conflict(tmp_path: Path) -> None:
    titles = ["南京北站016计划桁架零件", "南京北站017计划天窗零件"]
    source = tmp_path / "conflicting-titles.dxf"
    document = ezdxf.new("R2013")
    for index, title in enumerate(titles):
        document.modelspace().add_text(title).set_placement((0, index * 10))
    document.saveas(source)
    result = parse_dxf(source)
    assert [item.value for item in result.project_candidates] == titles
    assert "PROJECT_CANDIDATES_CONFLICT" in [warning.code for warning in result.warnings]

def test_oversized_project_title_is_not_persistable_candidate(tmp_path: Path) -> None:
    title = "南京北站001计划" + "超" * 128
    source = tmp_path / "oversized-title.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text(title).set_placement((0, 0))
    document.saveas(source)
    result = parse_dxf(source)
    assert result.project_candidates == []
    assert "PROJECT_TITLE_TOO_LONG" in [warning.code for warning in result.warnings]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
uv run pytest tests/test_reader.py -q -k "complete_drawing_title or filename_is_never or multiple_complete_titles or oversized_project"
```

Working directory: `Stages/remnant_drawing_reader`.

Expected: complete-title test reports `['017']` instead of the full title; new boundary test fails because the warning does not exist.

- [ ] **Step 3: Implement conservative full-title classification**

Replace the three-digit plan extractor with a complete-title predicate and length guard:

```python
_PROJECT_TITLE = re.compile(r"^(?=.*[\u3400-\u9fff]).*(?<!\d)\d{3}\s*计划.*$")
_MAX_PROJECT_LENGTH = 128

# In the unlabelled branch, after material and part checks:
elif _PROJECT_TITLE.fullmatch(text):
    has_oversized_project_title |= not _append_project(projects, text, evidence)
```

Implement `_append_project()` so both labelled and unlabelled project values use the same 128-character guard. Append `ParseWarning("PROJECT_TITLE_TOO_LONG", "图纸项目标题超过 128 个字符，请人工填写")` when the flag is set. Keep labelled project fields working and change `__version__` to `0.2.0`; update existing version assertions.

- [ ] **Step 4: Run the full reader suite and real corpus diagnostic**

Run:

```powershell
uv run pytest tests -q
```

Expected: all reader tests pass. Then parse all 11 南京北站 DXFs and assert the specified sample returns the exact complete title, 11 materials still resolve, and the total part count remains 38.

- [ ] **Step 5: Commit Task 1**

```powershell
git add Stages/remnant_drawing_reader
git commit -m "fix(remnants): preserve complete drawing project titles"
```

---

### Task 2: Render Chinese Preview Text with a Server Font

**Files:**
- Modify: `backend/app/modules/cad_processing/preview_rendering.py`
- Modify: `backend/Dockerfile`
- Test: `backend/tests/cad_processing/test_dxf_preview_service.py`
- Test: `backend/tests/infrastructure/test_compose.py`

**Interfaces:**
- Produces: `_prepare_cjk_preview_style(document: Any) -> bool`; `PREVIEW_RENDERER_VERSION = "svg-v2-cjk"`.
- Consumes: existing `inspect_dxf()` and `render_inspected_dxf_to_svg()` pipeline.

- [ ] **Step 1: Write failing preview and Docker tests**

```python
def _dxf_bytes_with_text(value: str) -> bytes:
    document = ezdxf.new("R2013")
    document.modelspace().add_text(value).set_placement((0, 0))
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode(document.output_encoding, errors="replace")

def test_cjk_preview_style_is_applied_only_to_chinese_text(monkeypatch) -> None:
    document = ezdxf.new("R2013")
    chinese = document.modelspace().add_text("南京北站017计划")
    latin = document.modelspace().add_text("NJB-47-1")
    monkeypatch.setattr(rendering, "_find_cjk_font", lambda: "NotoSansCJK-Regular.ttc")
    assert rendering._prepare_cjk_preview_style(document) is True
    assert chinese.dxf.style == rendering.CJK_PREVIEW_STYLE
    assert latin.dxf.style == "Standard"

def test_missing_cjk_font_keeps_preview_renderable(monkeypatch) -> None:
    monkeypatch.setattr(rendering, "_find_cjk_font", lambda: None)
    rendered = rendering.render_dxf_to_svg(_dxf_bytes_with_text("南京北站017计划"))
    assert b"<svg" in rendered.payload.lower()

def test_runtime_installs_noto_cjk_font_package() -> None:
    runtime = DOCKERFILE_PATH.read_text(encoding="utf-8").split(" AS runtime", 1)[1]
    assert "fonts-noto-cjk" in runtime
```

Also assert `PREVIEW_RENDERER_VERSION == "svg-v2-cjk"` and `preview_batch_name(source)` includes it.

- [ ] **Step 2: Run focused tests and verify RED**

Run from `backend`:

```powershell
uv run pytest tests/cad_processing/test_dxf_preview_service.py tests/infrastructure/test_compose.py -q -k "cjk or renderer_version or noto"
```

Expected: failures for missing helper/style constant, old `svg-v1`, and absent package.

- [ ] **Step 3: Implement in-memory CJK font substitution and fallback**

Add constants and helpers:

```python
PREVIEW_RENDERER_VERSION = "svg-v2-cjk"
CJK_PREVIEW_STYLE = "__DXF_PREVIEW_CJK__"
CJK_FONT_PATHS = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
)
_CJK = re.compile(r"[\u3400-\u9fff]")
```

`_find_cjk_font()` returns the first existing path. `_prepare_cjk_preview_style()` creates/updates the preview-only DXF style, registers the font folder with `ezdxf.fonts.font_manager.scan_folder()`, and changes only CJK-bearing `TEXT`, `MTEXT`, `ATTRIB`, and `ATTDEF` entities in layouts and blocks. Call it after reading the document and before constructing `RenderContext`. Catch font discovery/registration errors, log a warning, and return `False` so rendering proceeds unchanged.

- [ ] **Step 4: Add the runtime font package**

Add `fonts-noto-cjk` to the runtime `apt-get install --no-install-recommends` list and document it beside `libfontconfig1` in `backend/Dockerfile`.

- [ ] **Step 5: Verify unit tests, build, and a real preview**

Run:

```powershell
uv run pytest tests/cad_processing/test_dxf_preview_service.py tests/infrastructure/test_compose.py -q
docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml build backend-api
```

Render the specified 南京北站 sample inside `dwg-agent-backend:local`; compare the Chinese path geometry with the old repeated rectangle pattern and visually open the new SVG. Expected: readable Chinese with Noto present; forced missing-font test still returns a safe SVG.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/Dockerfile backend/app/modules/cad_processing/preview_rendering.py backend/tests/cad_processing/test_dxf_preview_service.py backend/tests/infrastructure/test_compose.py
git commit -m "fix(preview): render DXF Chinese with bundled font"
```

---

### Task 3: Add Idempotent Worker Material Creation

**Files:**
- Modify: `backend/app/modules/remnant_inventory/materials.py`
- Modify: `backend/app/modules/remnant_inventory/schemas.py`
- Modify: `backend/app/modules/remnant_inventory/routes.py`
- Test: `backend/tests/remnant_inventory/test_materials.py`
- Test: `backend/tests/remnant_inventory/test_api.py`
- Test: `backend/tests/remnant_inventory/test_inventory_mysql.py`

**Interfaces:**
- Produces: `resolve_or_create_material(db: Session, *, code: str, actor_id: int | None) -> tuple[RemnantMaterial, bool]`.
- Produces: `POST /api/v1/remnant-materials/resolve-or-create` with `{code: string}` and `{material: MaterialRead, created: boolean}` data.

- [ ] **Step 1: Write failing service and API tests**

```python
def test_resolve_or_create_uses_full_code_as_family(db) -> None:
    material, created = resolve_or_create_material(db, code=" q355b ", actor_id=7)
    assert created is True
    assert (material.code, material.family_code, material.enabled) == ("Q355B", "Q355B", True)

def test_resolve_or_create_returns_existing_material(db) -> None:
    first, _ = resolve_or_create_material(db, code="Q355B", actor_id=7)
    second, created = resolve_or_create_material(db, code="q355b", actor_id=8)
    assert (second.id, created) == (first.id, False)
```

API coverage must authenticate a `remnant_worker`, assert the dedicated endpoint returns 200/201 data with the new material, assert the worker still receives 403 from PATCH and alias endpoints, and assert only a true creation writes `remnants.material.create`.

Add a real worker fixture and assertions:

```python
@pytest.fixture
def worker_headers(client: TestClient) -> dict[str, str]:
    with get_test_session_factory()() as db:
        role = db.scalar(select(Role).where(Role.code == "remnant_worker"))
        user = User(
            username="material-worker",
            real_name="材质工人",
            password_hash=hash_password("WorkerPass123"),
            roles=[role],
        )
        db.add(user)
        db.commit()
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "material-worker", "password": "WorkerPass123"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

def test_worker_resolves_or_creates_material_but_cannot_administer_it(
    client, worker_headers
) -> None:
    created = client.post(
        "/api/v1/remnant-materials/resolve-or-create",
        headers=worker_headers,
        json={"code": "q355b"},
    )
    assert created.status_code == 201
    payload = created.json()["data"]
    assert payload["created"] is True
    assert payload["material"]["code"] == payload["material"]["family_code"] == "Q355B"
    repeated = client.post(
        "/api/v1/remnant-materials/resolve-or-create",
        headers=worker_headers,
        json={"code": "Q355B"},
    )
    assert repeated.json()["data"]["created"] is False
    material_id = payload["material"]["id"]
    assert client.patch(
        f"/api/v1/remnant-materials/{material_id}",
        headers=worker_headers,
        json={"enabled": False},
    ).status_code == 403
```

- [ ] **Step 2: Run focused tests and verify RED**

Run from `backend`:

```powershell
uv run pytest tests/remnant_inventory/test_materials.py tests/remnant_inventory/test_api.py -q -k "resolve_or_create or worker"
```

Expected: import/route failures because the service and endpoint do not exist.

- [ ] **Step 3: Implement the transactional service**

Normalize `code`, reject blank input, and query by exact normalized code. Return an enabled match; reject a disabled match with `REMNANT_MATERIAL_DISABLED` so a worker cannot undo an administrator's stop action. Otherwise create an enabled row with `family_code=code`. Protect the insert with `db.begin_nested()`; on `IntegrityError`, re-query the unique code after the savepoint rolls back. Return `(row, True)` only for the transaction that inserted it.

Extend the existing MySQL-gated remnant test with two synchronized sessions:

```python
def test_two_workers_resolve_one_material_row() -> None:
    assert MYSQL_URL is not None
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    code = f"RACE-{uuid4().hex[:12]}".upper()
    barrier = Barrier(2)

    def create_from_worker(_worker_slot: int) -> tuple[int, bool]:
        with factory() as session:
            barrier.wait(timeout=10)
            material, created = resolve_or_create_material(
                session, code=code, actor_id=None
            )
            session.commit()
            return material.id, created

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create_from_worker, (1, 2)))
        assert len({material_id for material_id, _created in results}) == 1
        assert sum(created for _material_id, created in results) == 1
        with factory() as check:
            count = check.scalar(
                select(func.count()).select_from(RemnantMaterial).where(
                    RemnantMaterial.code == code
                )
            )
            assert count == 1
    finally:
        with factory() as cleanup:
            cleanup.execute(delete(RemnantMaterial).where(RemnantMaterial.code == code))
            cleanup.commit()
        engine.dispose()
```

- [ ] **Step 4: Implement the worker-scoped API and audit**

Add:

```python
class MaterialResolveCreate(BaseModel):
    code: str = Field(max_length=64)

class MaterialResolveCreateResult(BaseModel):
    material: MaterialRead
    created: bool
```

Create `POST /resolve-or-create` before dynamic material routes, authorize with `_require_user`, call the service, write `remnants.material.create` only when `created` is true, commit, and return the standard envelope. Leave existing admin POST/PATCH/aliases behavior unchanged.

- [ ] **Step 5: Run all remnant backend tests**

```powershell
uv run pytest tests/remnant_inventory -q
```

Expected: all tests pass; no permission regression.

- [ ] **Step 6: Commit Task 3**

```powershell
git add backend/app/modules/remnant_inventory backend/tests/remnant_inventory
git commit -m "feat(remnants): let workers create detected materials"
```

---

### Task 4: Auto-fill and Create Material in the Confirmation Modal

**Files:**
- Modify: `frontend/src/features/remnant-inventory/api.ts`
- Modify: `frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx`
- Test: `frontend/tests/e2e/remnant-inventory/import.spec.ts`

**Interfaces:**
- Consumes: Task 3 endpoint and existing `RemnantMaterial` type.
- Produces: `resolveOrCreateRemnantMaterial(code: string): Promise<{material: RemnantMaterial; created: boolean}>`.

- [ ] **Step 1: Extend the mocked E2E scenario and write failing assertions**

Use one item with `material_candidates: [{value: 'Q355B'}]`, no `material_id`, and a full project candidate. Mock `/api/v1/remnant-materials/resolve-or-create` to return a new `Q355B` material. Assert:

```ts
await confirmation.getByRole('button', { name: '编辑' }).first().click();
await expect(editor.getByLabel('项目编号')).toHaveValue(
  '北工大定位板及南京北站017计划天窗2批激光零件 2026-7-03',
);
await editor.getByRole('button', { name: '新建并使用 Q355B' }).click();
await expect(editor.getByLabel('标准材质')).toContainText('Q355B');
```

Add a failed mocked request case and assert the thickness/project/parts values remain unchanged.

```ts
test('failed material creation preserves confirmation fields', async ({ page }) => {
  await mockImport(page, { materialCandidate: 'Q355B', failMaterialCreate: true });
  await page.goto('/remnants?tab=import&batch=77');
  await page.getByRole('button', { name: '编辑' }).first().click();
  const editor = page.getByRole('dialog', { name: /确认/ });
  await editor.getByLabel('厚度（mm）').fill('10');
  const expectedProject = await editor.getByLabel('项目编号').inputValue();
  const expectedParts = await editor.getByLabel(/零件编号/).inputValue();
  await editor.getByRole('button', { name: '新建并使用 Q355B' }).click();
  await expect(editor.getByLabel('厚度（mm）')).toHaveValue('10');
  await expect(editor.getByLabel('项目编号')).toHaveValue(expectedProject);
  await expect(editor.getByLabel(/零件编号/)).toHaveValue(expectedParts);
});
```

- [ ] **Step 2: Run Playwright and verify RED**

Run from `frontend`:

```powershell
npx playwright test tests/e2e/remnant-inventory/import.spec.ts
```

Expected: the create button cannot be found and the unmatched material is not selected.

- [ ] **Step 3: Add the typed API call**

```ts
export async function resolveOrCreateRemnantMaterial(code: string) {
  const response = await apiClient.post<ApiEnvelope<{
    material: RemnantMaterial;
    created: boolean;
  }>>('/api/v1/remnant-materials/resolve-or-create', { code });
  return response.data.data;
}
```

- [ ] **Step 4: Implement confirmation form behavior**

When `edit(item)` runs, derive a unique candidate and locate its normalized code in `materials`; use that ID when `item.material_id` is null. When the unique candidate is absent from `materials`, render “检测到未建档材质” plus a mutation-backed “新建并使用 `<code>`” button. On success:

```ts
queryClient.setQueryData<RemnantMaterial[]>(['remnant-materials'], (current = []) =>
  current.some((row) => row.id === result.material.id) ? current : [...current, result.material],
);
form.setFieldValue('material_id', result.material.id);
message.success(result.created ? '材质已创建并选中' : '已选中现有材质');
```

Do not reset or recreate the form on mutation failure; show `describeApiError()` and keep all current values.

- [ ] **Step 5: Verify E2E and production build**

```powershell
npx playwright test tests/e2e/remnant-inventory/import.spec.ts
npm run build
```

Expected: E2E passes and TypeScript/Vite build exits 0.

- [ ] **Step 6: Commit Task 4**

```powershell
git add frontend/src/features/remnant-inventory frontend/tests/e2e/remnant-inventory/import.spec.ts
git commit -m "feat(frontend): create detected remnant materials inline"
```

---

### Task 5: Integrated Real-Sample Acceptance

**Files:**
- Modify only if verification exposes a defect in files already listed above.

**Interfaces:**
- Consumes all Task 1–4 deliverables.
- Produces a rebuilt local acceptance environment and recorded verification evidence.

- [ ] **Step 1: Run complete relevant automated suites**

```powershell
uv run pytest tests -q
uv run pytest tests/cad_processing/test_dxf_preview_service.py tests/remnant_inventory tests/infrastructure/test_compose.py -q
npx playwright test tests/e2e/remnant-inventory/import.spec.ts
npm run build
```

Run each command in its owning `Stages/remnant_drawing_reader`, `backend`, or `frontend` directory. Expected: zero failures; documented skips only.

- [ ] **Step 2: Rebuild and restart acceptance services**

```powershell
docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml build backend-api nginx
docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml up -d --force-recreate backend-api worker-remnant-convert worker-remnant-parse nginx
```

Expected: backend, nginx, MySQL, MinIO, convert worker, and parse worker are healthy.

- [ ] **Step 3: Verify the real samples end to end**

- Upload the specified DXF and confirm the preview shows readable Chinese.
- Confirm the project input equals the complete title exactly.
- Confirm existing `Q355B` is selected automatically.
- Use a controlled new grade to verify a remnant worker can create and immediately select it.
- Parse all 11 DXFs and assert 11 material-bearing drawings, the expected complete titles where reliable, and 38 total part candidates.
- Convert at least one original DWG in the final image and verify the converted DXF produces the same title/material/parts.

- [ ] **Step 4: Review diff and commit any acceptance-only correction**

```powershell
git diff --check
git status --short
```

If no correction is required, do not create an empty commit. If a defect was fixed under TDD, stage only its files and use `git commit -m "fix(remnants): address integrated acceptance finding"`.
