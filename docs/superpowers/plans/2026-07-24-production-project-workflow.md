# Production Project Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace “create a workflow under an existing project” with one atomic action that creates a new production project and immediately starts its unique complete Linux production workflow.

**Architecture:** Add a workflow-owned application service and HTTP endpoint that compose the existing Project and Workflow services inside one database transaction. Enforce at most one `linux_production` workflow per project in the shared workflow creation service so the legacy endpoint cannot bypass the rule. Refactor the frontend into a project-first workbench and a focused creation drawer while preserving the existing workflow detail route and execution model.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic 2, React 19, TypeScript 6, Ant Design 6, TanStack Query 5, Playwright, pytest.

---

## File map

**Backend application**

- Create `backend/app/modules/workflows/production_projects.py`: atomic application service that composes Project creation, Workflow creation and Workflow start.
- Create `backend/app/modules/workflows/routes/production_projects.py`: `POST /workflows/production-projects` adapter and audit boundary.
- Create `backend/app/modules/workflows/schemas/production_projects.py`: request, response and envelope contracts.
- Modify `backend/app/modules/workflows/lifecycle.py`: lock Project and enforce one production Workflow per Project.
- Modify `backend/app/modules/workflows/schemas/__init__.py`: export the new schemas.
- Modify `backend/app/modules/workflows/routes/router.py`: mount the new route in stable order.
- Modify `backend/app/modules/workflows/routes/README.md`: name the new owned source and operation.
- Modify `backend/app/modules/projects/interface.py`: expose `ProjectRead` for the cross-domain response contract.

**Backend tests and contracts**

- Create `backend/tests/workflows/test_production_project_api.py`: success, rollback, duplicate and legacy-route bypass tests.
- Modify `backend/tests/architecture/test_workflow_boundaries.py`: public interface and route-order contract.
- Modify `backend/tests/architecture/test_contract_snapshot.py`: expected OpenAPI counts.
- Modify `backend/tests/architecture/test_module_catalog.py`: expected owned-operation count.
- Modify `backend/tests/contracts/test_frontend_contract.py`: project-first frontend assertions.

**Frontend**

- Create `frontend/src/features/workflows/ProductionProjectCreateDrawer.tsx`: project form, preparation guidance and structured error handling.
- Modify `frontend/src/features/workflows/WorkflowsPage.tsx`: project-first list, statistics, navigation and drawer orchestration.
- Modify `frontend/src/features/workflows/workflows.api.ts`: production-project request/response types and API call.
- Modify `frontend/src/features/workflows/model/workflowPresentation.tsx`: remove `suggestedBatchName`.
- Modify `frontend/src/features/dashboard/DashboardPage.tsx`: rename the production entry.
- Modify `frontend/tests/e2e/workflows/workflow-input.spec.ts`: exercise the atomic creation endpoint and new form.
- Modify `frontend/src/features/workflows/styles.css`: focused industrial drawer and project identity styling.

**Documentation and generated contracts**

- Modify `README.md`.
- Modify `docs/architecture/platform-specification.md`.
- Modify `docs/architecture/workflow.md`.
- Regenerate `docs/reference/api.md`.
- Regenerate `docs/architecture/runtime-contract.json`.

### Task 1: Enforce one production workflow per project

**Files:**

- Modify: `backend/app/modules/workflows/lifecycle.py`
- Test: `backend/tests/workflows/test_production_project_api.py`

- [ ] **Step 1: Write the failing service test**

```python
def test_second_linux_production_workflow_for_project_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    payload = WorkflowCreate(
        project_id=project.id,
        name="P001 · 完整生产流程",
        workflow_type="linux_production",
    )
    first = create_workflow(db, payload, created_by=owner.id)

    with pytest.raises(AppHTTPException) as raised:
        create_workflow(db, payload, created_by=owner.id)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "PRODUCTION_WORKFLOW_ALREADY_EXISTS"
    assert raised.value.detail["details"] == {
        "project_id": project.id,
        "workflow_id": first.id,
    }
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_production_project_api.py::test_second_linux_production_workflow_for_project_is_rejected
```

Expected: FAIL because the second Workflow is currently created.

- [ ] **Step 3: Add the locked uniqueness check**

In `create_workflow`, before constructing `WorkflowRun`, add:

```python
if payload.workflow_type == "linux_production":
    project = db.scalar(
        select(Project).where(Project.id == payload.project_id).with_for_update()
    )
    if project is None or project.status == "deleted":
        raise not_found("Project")
    existing = db.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == payload.project_id,
            WorkflowRun.workflow_type == "linux_production",
        )
        .order_by(WorkflowRun.id)
        .limit(1)
    )
    if existing is not None:
        raise AppHTTPException(
            409,
            "PRODUCTION_WORKFLOW_ALREADY_EXISTS",
            "This project already has its complete production workflow.",
            {"project_id": project.id, "workflow_id": existing.id},
        )
```

Import `Project` only through `app.modules.projects.interface`.

- [ ] **Step 4: Prove compatibility workflows remain repeatable**

Add:

```python
def test_compatibility_workflows_remain_repeatable_for_project(db):
    owner = _user(db)
    project = _project(db, owner)
    payload = WorkflowCreate(
        project_id=project.id,
        name="File delivery",
        workflow_type="file_delivery",
    )

    first = create_workflow(db, payload, created_by=owner.id)
    second = create_workflow(db, payload, created_by=owner.id)

    assert first.id != second.id
```

- [ ] **Step 5: Run the focused service tests**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_production_project_api.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/workflows/lifecycle.py \
  backend/tests/workflows/test_production_project_api.py
git commit -m "feat(workflows): enforce one production flow per project"
```

### Task 2: Add the atomic production-project application service

**Files:**

- Create: `backend/app/modules/workflows/production_projects.py`
- Create: `backend/app/modules/workflows/schemas/production_projects.py`
- Modify: `backend/app/modules/workflows/schemas/__init__.py`
- Modify: `backend/app/modules/projects/interface.py`
- Test: `backend/tests/workflows/test_production_project_api.py`

- [ ] **Step 1: Write the service success test**

```python
def test_create_production_project_builds_and_starts_complete_workflow(db):
    owner = _user(db)

    result = create_production_project(
        db,
        ProductionProjectCreate(
            code="P-2026-001",
            name="一号厂房",
            description="主结构生产",
        ),
        created_by=owner.id,
    )

    assert result.project.code == "P-2026-001"
    assert result.project.owner_id == owner.id
    assert result.workflow.project_id == result.project.id
    assert result.workflow.workflow_type == "linux_production"
    assert result.workflow.name == "P-2026-001 · 一号厂房"
    assert result.workflow.status == "waiting_input"
    assert result.workflow.current_stage == "source_intake"
    assert len(result.workflow.stages) == 9
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_production_project_api.py::test_create_production_project_builds_and_starts_complete_workflow
```

Expected: collection failure because the new service and schema do not exist.

- [ ] **Step 3: Define the request and response schemas**

Create `schemas/production_projects.py`:

```python
from datetime import datetime

from pydantic import BaseModel

from app.modules.projects.interface import ProjectCreate, ProjectRead
from app.modules.workflows.schemas.orchestration import WorkflowDetail


class ProductionProjectResponseMeta(BaseModel):
    request_id: str
    timestamp: datetime


class ProductionProjectCreate(ProjectCreate):
    pass


class ProductionProjectRead(BaseModel):
    project: ProjectRead
    workflow: WorkflowDetail


class ProductionProjectEnvelope(BaseModel):
    data: ProductionProjectRead
    meta: ProductionProjectResponseMeta
```

Export these names from `schemas/__init__.py`. Export `ProjectRead` from
`projects/interface.py`; do not import the projects schema package directly from the workflow service.

- [ ] **Step 4: Implement the application service**

Create `production_projects.py`:

```python
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.projects.interface import Project, ProjectCreate, create_project
from app.modules.workflows.lifecycle import create_workflow, start_workflow
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.schemas import ProductionProjectCreate, WorkflowCreate


@dataclass(frozen=True)
class ProductionProjectResult:
    project: Project
    workflow: WorkflowRun


def create_production_project(
    db: Session,
    payload: ProductionProjectCreate,
    *,
    created_by: int,
) -> ProductionProjectResult:
    project = create_project(
        db,
        ProjectCreate.model_validate(payload.model_dump()),
        owner_id=created_by,
    )
    workflow = create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name=f"{project.code} · {project.name}",
            workflow_type="linux_production",
        ),
        created_by=created_by,
    )
    start_workflow(db, workflow)
    db.flush()
    return ProductionProjectResult(project=project, workflow=workflow)
```

The service must not call `commit()` or write HTTP audit records.

- [ ] **Step 5: Add the rollback proof**

```python
def test_production_project_creation_rolls_back_when_start_fails(db, monkeypatch):
    owner = _user(db)
    monkeypatch.setattr(
        "app.modules.workflows.production_projects.start_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AppHTTPException(409, "START_FAILED", "Injected start failure.")
        ),
    )

    with pytest.raises(AppHTTPException):
        create_production_project(
            db,
            ProductionProjectCreate(code="ROLLBACK-1", name="回滚项目"),
            created_by=owner.id,
        )
    db.rollback()

    assert db.scalar(select(Project).where(Project.code == "ROLLBACK-1")) is None
    assert db.scalar(select(WorkflowRun)) is None
```

- [ ] **Step 6: Run service tests**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_production_project_api.py
uv run ruff check app/modules/workflows/production_projects.py \
  app/modules/workflows/schemas/production_projects.py
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/modules/projects/interface.py \
  backend/app/modules/workflows/production_projects.py \
  backend/app/modules/workflows/schemas/production_projects.py \
  backend/app/modules/workflows/schemas/__init__.py \
  backend/tests/workflows/test_production_project_api.py
git commit -m "feat(workflows): compose atomic production project creation"
```

### Task 3: Expose the atomic HTTP endpoint

**Files:**

- Create: `backend/app/modules/workflows/routes/production_projects.py`
- Modify: `backend/app/modules/workflows/routes/router.py`
- Modify: `backend/app/modules/workflows/routes/README.md`
- Modify: `backend/tests/architecture/test_workflow_boundaries.py`
- Test: `backend/tests/workflows/test_production_project_api.py`

- [ ] **Step 1: Write the public API success test**

```python
def test_create_production_project_api_returns_project_and_started_workflow():
    client = workflow_test_api.client()
    admin = workflow_test_api.admin_headers(client)
    _, owner = workflow_test_api.create_engineer_user(client, admin, "production-project")

    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner,
        json={
            "code": "P-API-001",
            "name": "API 项目",
            "description": "完整生产流程",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["project"]["code"] == "P-API-001"
    assert data["workflow"]["project_id"] == data["project"]["id"]
    assert data["workflow"]["status"] == "waiting_input"
    assert data["workflow"]["current_stage"] == "source_intake"
```

- [ ] **Step 2: Run the API test and verify 404**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_production_project_api.py::test_create_production_project_api_returns_project_and_started_workflow
```

Expected: FAIL with HTTP 404.

- [ ] **Step 3: Implement the route**

Create `routes/production_projects.py`:

```python
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import ProjectRead
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.production_projects import create_production_project
from app.modules.workflows.schemas import (
    ProductionProjectCreate,
    ProductionProjectEnvelope,
    ProductionProjectRead,
    WorkflowDetail,
)
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.config.constants import ROLE_OPERATOR

router = APIRouter()


@router.post(
    "/production-projects",
    status_code=status.HTTP_201_CREATED,
    response_model=ProductionProjectEnvelope,
    summary="创建生产项目及其唯一完整工作流",
)
def create_production_project_api(
    payload: ProductionProjectCreate,
    request: Request,
    current_user: User = Depends(require_roles(ROLE_OPERATOR)),
    db: Session = Depends(get_db),
):
    result = create_production_project(db, payload, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="production_projects.create",
        resource_type="project",
        resource_id=result.project.id,
        after_json={"workflow_id": result.workflow.id, **payload.model_dump()},
        request=request,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflows.start",
        resource_type="workflow",
        resource_id=result.workflow.id,
        after_json={"project_id": result.project.id, "atomic_creation": True},
        request=request,
    )
    db.commit()
    workflow = load_workflow_detail(db, result.workflow.id)
    return ok(
        ProductionProjectRead(
            project=ProjectRead.model_validate(result.project),
            workflow=WorkflowDetail.model_validate(workflow),
        ).model_dump(),
        request.state.request_id,
    )
```

- [ ] **Step 4: Mount and lock the route contract**

Import `production_projects_router` in `routes/router.py` and mount it after the workflow collection
command router but before `/{workflow_id}` detail routes:

```python
_mount(command_collection_router, tag="workflows")
_mount(production_projects_router, tag="workflows")
```

Add the exact expected route tuple to `EXPECTED_ROUTES`:

```python
(("POST",), "/production-projects", "create_production_project_api"),
```

Add `production_projects.py` and the updated operation count to the routes README.

- [ ] **Step 5: Prove the legacy route cannot bypass uniqueness**

```python
def test_legacy_workflow_route_cannot_add_second_production_flow():
    client, headers = _client_and_owner()
    created = client.post(
        "/api/v1/workflows/production-projects",
        headers=headers,
        json={"code": "P-ONE", "name": "唯一流程"},
    ).json()["data"]

    duplicate = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "project_id": created["project"]["id"],
            "name": "Second",
            "workflow_type": "linux_production",
        },
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PRODUCTION_WORKFLOW_ALREADY_EXISTS"
```

- [ ] **Step 6: Prove HTTP failure leaves no partial project**

```python
def test_atomic_api_rolls_back_project_when_workflow_start_fails(monkeypatch):
    client, headers = _client_and_owner()
    monkeypatch.setattr(
        "app.modules.workflows.production_projects.start_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AppHTTPException(409, "START_FAILED", "Injected start failure.")
        ),
    )

    failed = client.post(
        "/api/v1/workflows/production-projects",
        headers=headers,
        json={"code": "NO-PARTIAL", "name": "不能残留"},
    )

    assert failed.status_code == 409
    projects = client.get(
        "/api/v1/workflows/projects",
        headers=headers,
    ).json()["data"]
    assert all(project["code"] != "NO-PARTIAL" for project in projects)
```

- [ ] **Step 7: Run route and boundary tests**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_production_project_api.py \
  tests/architecture/test_workflow_boundaries.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/modules/workflows/routes/production_projects.py \
  backend/app/modules/workflows/routes/router.py \
  backend/app/modules/workflows/routes/README.md \
  backend/tests/workflows/test_production_project_api.py \
  backend/tests/architecture/test_workflow_boundaries.py
git commit -m "feat(workflows): expose production project creation"
```

### Task 4: Add the frontend production-project contract and drawer

**Files:**

- Create: `frontend/src/features/workflows/ProductionProjectCreateDrawer.tsx`
- Modify: `frontend/src/features/workflows/workflows.api.ts`
- Modify: `frontend/src/features/workflows/styles.css`
- Modify: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: Write the failing frontend source contract**

Replace the old production-submission assertions with:

```python
def test_production_project_drawer_uses_atomic_project_contract():
    page = _frontend_source("features/workflows/WorkflowsPage.tsx")
    drawer = _frontend_source("features/workflows/ProductionProjectCreateDrawer.tsx")
    api = _frontend_source("features/workflows/workflows.api.ts")

    assert "新建生产项目" in page
    assert "<ProductionProjectCreateDrawer" in page
    assert "项目编号" in drawer
    assert "项目名称" in drawer
    assert "项目说明" in drawer
    assert "创建项目并进入工作流" in drawer
    assert "project_id" not in drawer
    assert "批次名称" not in drawer
    assert "/production-projects" in api
    assert "createProductionProject" in api
```

- [ ] **Step 2: Run the contract and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/contracts/test_frontend_contract.py -k production_project
```

Expected: FAIL because the drawer and API do not exist.

- [ ] **Step 3: Add API types and call**

In `workflows.api.ts`:

```typescript
import type { Project } from '../projects';

export interface ProductionProjectCreatePayload {
  code: string;
  name: string;
  description?: string;
}

export interface ProductionProjectCreateResult {
  project: Project;
  workflow: WorkflowDetail;
}

export async function createProductionProject(
  payload: ProductionProjectCreatePayload,
) {
  const response = await apiClient.post<ApiEnvelope<ProductionProjectCreateResult>>(
    '/api/v1/workflows/production-projects',
    payload,
  );
  return response.data.data;
}
```

- [ ] **Step 4: Implement the focused drawer**

Create `ProductionProjectCreateDrawer.tsx` with props:

```typescript
interface Props {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onSubmit: (payload: ProductionProjectCreatePayload) => void;
}
```

Use these exact form fields:

```tsx
<Form.Item
  name="code"
  label="项目编号"
  normalize={(value: string) => value.toUpperCase()}
  rules={[
    { required: true, message: '请输入项目编号' },
    { pattern: /^[A-Za-z0-9_-]+$/, message: '只能使用字母、数字、下划线和连字符' },
    { max: 64, message: '项目编号不能超过 64 个字符' },
  ]}
>
  <Input placeholder="例如 P-2026-001" autoFocus />
</Form.Item>
<Form.Item
  name="name"
  label="项目名称"
  rules={[
    { required: true, message: '请输入项目名称' },
    { max: 128, message: '项目名称不能超过 128 个字符' },
  ]}
>
  <Input placeholder="例如 一号厂房主结构" />
</Form.Item>
<Form.Item name="description" label="项目说明">
  <Input.TextArea rows={4} maxLength={1000} showCount />
</Form.Item>
```

The drawer must contain the confirmed three steps and file preparation checklist, use
`destroyOnHidden`, and disable close/mask close while `pending`.

Expose an imperative `setCodeError(message: string)` through a ref or accept a `codeError` prop;
prefer the prop so the parent owns API state.

- [ ] **Step 5: Add focused industrial styling**

Add CSS classes with existing workflow variables and no new dependency:

```css
.production-project-create__identity {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 9rem;
  gap: 1rem;
  padding: 1.25rem;
  border: 1px solid #cbd5e1;
  border-left: 4px solid #0f766e;
  background:
    linear-gradient(135deg, rgb(240 253 250 / 92%), rgb(248 250 252 / 96%));
}

.production-project-code {
  font-variant-numeric: tabular-nums;
  letter-spacing: .06em;
}
```

Reuse current `production-create-*` styles where they still describe the same visual element;
remove styles that only supported project selection or batch-name suggestions.

- [ ] **Step 6: Build and run the source contract**

Run:

```bash
cd frontend
npm run build
cd ../backend
uv run pytest -q tests/contracts/test_frontend_contract.py -k production
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/workflows/ProductionProjectCreateDrawer.tsx \
  frontend/src/features/workflows/workflows.api.ts \
  frontend/src/features/workflows/styles.css \
  backend/tests/contracts/test_frontend_contract.py
git commit -m "feat(frontend): add production project creation drawer"
```

### Task 5: Convert the workflow list into the production-project workbench

**Files:**

- Modify: `frontend/src/features/workflows/WorkflowsPage.tsx`
- Modify: `frontend/src/features/workflows/model/workflowPresentation.tsx`
- Modify: `frontend/src/features/dashboard/DashboardPage.tsx`
- Test: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: Add the project-first list assertions**

```python
def test_workflow_list_presents_one_complete_flow_as_a_project():
    source = _frontend_source("features/workflows/WorkflowsPage.tsx")

    assert 'title="生产项目"' in source
    assert "一个项目贯穿" in source
    assert "项目总数" in source
    assert "进入项目" in source
    assert "新建生产批次" not in source
    assert "suggestedBatchName" not in source
    assert "batchNameTouched" not in source
    assert "listProjects" in source
```

- [ ] **Step 2: Run the assertion and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/contracts/test_frontend_contract.py -k project
```

Expected: FAIL on old batch language and state.

- [ ] **Step 3: Replace the creation mutation**

Use:

```typescript
const createM = useMutation({
  mutationFn: createProductionProject,
  onSuccess: ({ workflow }) => {
    setCreateOpen(false);
    void queryClient.invalidateQueries({ queryKey: ['workflows'] });
    void queryClient.invalidateQueries({ queryKey: ['projects'] });
    message.success('生产项目与完整工作流已创建');
    navigate(`/workflows/${workflow.id}`);
  },
  onError: (error) => {
    const parsed = parseApiError(error, '生产项目创建失败');
    if (parsed.code === 'PROJECT_CODE_EXISTS') {
      setCodeError('该项目编号已存在，请更换编号');
    }
    message.error(parsed.message);
  },
});
```

Remove separate `createWorkflow`, `startWorkflow`, `batchNameTouched`, `suggestedBatchName` and
project selection logic.

- [ ] **Step 4: Rebuild columns around Project identity**

The first column must render:

```tsx
<div className="production-project-identity">
  <Typography.Text className="production-project-code" strong>
    {project?.code ?? `PROJECT-${record.project_id}`}
  </Typography.Text>
  <Typography.Text>{project?.name ?? '项目资料加载中'}</Typography.Text>
  <small>Workflow #{record.id} · {templateName}</small>
</div>
```

Remove the separate project column. Rename action to “进入项目”; preserve row double-click and
`/workflows/{id}` navigation.

- [ ] **Step 5: Update page and Dashboard language**

Use:

```tsx
<PageHeader
  title="生产项目"
  subtitle="一个项目贯穿从资料入库到交付归档的完整工作流"
/>
```

Update empty state, total count, statistics and Dashboard action to “新建生产项目”.

- [ ] **Step 6: Remove dead presentation code**

Delete `suggestedBatchName` from `workflowPresentation.tsx`. Verify no caller remains:

```bash
rg -n "suggestedBatchName|batchNameTouched|新建生产批次" frontend/src
```

Expected: no matches.

- [ ] **Step 7: Build and run frontend contracts**

Run:

```bash
cd frontend
npm run build
cd ../backend
uv run pytest -q tests/contracts/test_frontend_contract.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/workflows/WorkflowsPage.tsx \
  frontend/src/features/workflows/model/workflowPresentation.tsx \
  frontend/src/features/dashboard/DashboardPage.tsx \
  backend/tests/contracts/test_frontend_contract.py
git commit -m "feat(frontend): present production workflows as projects"
```

### Task 6: Update browser E2E for atomic project creation

**Files:**

- Modify: `frontend/tests/e2e/workflows/workflow-input.spec.ts`

- [ ] **Step 1: Replace the old creation mocks**

Remove mocks for:

```text
POST /api/v1/workflows
POST /api/v1/workflows/41/start
```

Add:

```typescript
let submittedProject: Record<string, unknown> | null = null;
await page.route('**/api/v1/workflows/production-projects', async (route) => {
  submittedProject = route.request().postDataJSON() as Record<string, unknown>;
  await json(route, {
    project: {
      id: 7,
      code: submittedProject.code,
      name: submittedProject.name,
      description: submittedProject.description,
      owner_id: 1,
      owner_name: '生产操作员',
      status: 'active',
      created_at: now,
      updated_at: now,
    },
    workflow: workflowDetail(),
  }, 201);
});
```

- [ ] **Step 2: Replace the old form interaction**

Use:

```typescript
await page.getByRole('button', { name: '新建生产项目' }).click();
const drawer = page.getByRole('dialog', { name: '新建生产项目' });
await expect(drawer.getByText('填写项目资料')).toBeVisible();
await drawer.getByLabel('项目编号').fill('P-2026-001');
await drawer.getByLabel('项目名称').fill('浏览器生产项目');
await drawer.getByLabel('项目说明').fill('完整流程 E2E');
await drawer.getByRole('button', { name: '创建项目并进入工作流' }).click();

await expect(page).toHaveURL(/\/workflows\/41$/);
expect(submittedProject).toEqual({
  code: 'P-2026-001',
  name: '浏览器生产项目',
  description: '完整流程 E2E',
});
```

Assert the drawer has no “所属项目” or “批次名称”.

- [ ] **Step 3: Run Workflow E2E**

Run:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-input.spec.ts
```

Expected: `1 passed`.

- [ ] **Step 4: Run all Workflow E2E**

Run:

```bash
cd frontend
npm run test:e2e:workflows
```

Expected: all Workflow specs PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/workflows/workflow-input.spec.ts
git commit -m "test(workflows): cover atomic production project entry"
```

### Task 7: Synchronize API, architecture and product documentation

**Files:**

- Modify: `README.md`
- Modify: `docs/architecture/platform-specification.md`
- Modify: `docs/architecture/workflow.md`
- Generate: `docs/reference/api.md`
- Generate: `docs/architecture/runtime-contract.json`
- Modify: `backend/tests/architecture/test_contract_snapshot.py`
- Modify: `backend/tests/architecture/test_module_catalog.py`

- [ ] **Step 1: Update product language**

Replace the old business action:

```text
选择已有项目 → 创建生产批次 → 启动 Workflow
```

with:

```text
填写项目资料 → 原子创建 Project 与唯一 linux_production Workflow → 启动完整流程 → 上传生产文件夹
```

Keep “输入批次” only where it means the frozen input version inside WorkflowDetail. The generated
API narrative already describes folder intake rather than project selection, so
`scripts/docs/generate_api.py` is not changed in this task.

- [ ] **Step 2: Regenerate API documentation**

Run:

```bash
cd backend
uv run python ../scripts/docs/generate_api.py
uv run python ../scripts/architecture/snapshot_contracts.py --write
```

Expected: OpenAPI gains exactly one path and one operation.

- [ ] **Step 3: Update locked counts from generated evidence**

Read the actual lengths from the snapshot:

```bash
cd backend
uv run python - <<'PY'
from scripts.architecture.snapshot_contracts import build_contract_snapshot
s = build_contract_snapshot()
print(len(s["http_paths"]), len(s["http_operations"]))
PY
```

Set the exact printed counts in `test_contract_snapshot.py`,
`test_module_catalog.py` and the root README. Do not guess counts.

- [ ] **Step 4: Run documentation and architecture gates**

Run:

```bash
cd backend
uv run pytest -q tests/architecture tests/contracts/test_docs_consistency.py
uv run python ../scripts/docs/check.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/architecture/platform-specification.md \
  docs/architecture/workflow.md docs/architecture/runtime-contract.json \
  docs/reference/api.md \
  backend/tests/architecture/test_contract_snapshot.py \
  backend/tests/architecture/test_module_catalog.py
git commit -m "docs(workflows): document project-scoped production flow"
```

### Task 8: Full verification and review

**Files:**

- No planned production-code changes; fix only failures caused by Tasks 1–7.

- [ ] **Step 1: Run backend lint**

Run:

```bash
cd backend
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 2: Run backend full suite**

Run:

```bash
cd backend
uv run pytest -q
```

Expected: zero failures; record actual passed and skipped counts.

- [ ] **Step 3: Run frontend production build**

Run:

```bash
cd frontend
npm run build
```

Expected: TypeScript and Vite build PASS.

- [ ] **Step 4: Run Workflow browser suite**

Run:

```bash
cd frontend
npm run test:e2e:workflows
```

Expected: all Workflow specs PASS.

- [ ] **Step 5: Run documentation check**

Run:

```bash
backend/.venv/bin/python scripts/docs/check.py
```

Expected: documentation consistency PASS.

- [ ] **Step 6: Inspect the real creation result**

Using the test API or local stack, submit one production project and verify:

```text
Project code/name/owner are persisted once.
Exactly one linux_production Workflow references the Project.
Workflow status is waiting_input.
Current stage is source_intake.
All nine ordered stages exist.
The response contains both Project and WorkflowDetail.
```

- [ ] **Step 7: Request independent code review**

Ask the reviewer to inspect:

```text
Transaction rollback and audit atomicity.
Row-lock uniqueness and legacy API bypass.
Cross-domain interface imports.
Frontend removal of batch/project duality.
Structured field errors and duplicate-submit behavior.
E2E and documentation coverage.
```

Fix every Critical and Important finding, then rerun the affected gates.

- [ ] **Step 8: Verify clean scoped status and commit fixes**

Run:

```bash
git diff --check
git status --short
```

Do not stage `Stages/excel_final/data/`, `output/`, or any unrelated user files.

If review fixes exist, interactively select only those reviewed hunks:

```bash
git add --patch
git diff --cached --check
git commit -m "fix(workflows): harden production project creation"
```
