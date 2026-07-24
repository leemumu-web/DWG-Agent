# Workflow DXF Canonical Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/api/v1/workflows` accept one Excel plus multiple DWGs, preserve every source file, and guarantee that every downstream drawing artifact is a verified DXF.

**Architecture:** Keep Files as byte authority, Jobs/Results as execution authority, and Workflow rows as orchestration references. Introduce one workflow contract module for artifact formats and stage lineage, make the frozen DrawingVersion point to the canonical DXF, add revision-3 data migration, and expose the same contract to the frontend and documentation.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, MySQL/SQLite tests, React 19, TypeScript 6, Ant Design, Playwright, pytest.

---

## File Structure

### Create

- `backend/app/modules/workflows/contracts.py`: artifact format, Result/File/project, required-input, and required-output invariants.
- `backend/tests/workflows/test_workflow_dxf_contracts.py`: focused behavior tests for the new template and invariant module.
- `backend/migrations/versions/c7b2d4e9f601_canonicalize_workflow_dxf_flow.py`: revision-3 data migration.
- `backend/tests/infrastructure/test_workflow_dxf_migration.py`: portable upgrade/downgrade and contradiction tests.

### Modify

- `backend/app/modules/workflows/templates.py`: exact artifact lineage and `required_outputs`.
- `backend/app/modules/workflows/schemas/orchestration.py`: publish `required_outputs`.
- `backend/app/modules/workflows/artifacts.py`: call centralized reference/format validation.
- `backend/app/modules/workflows/lifecycle.py`: revision 3 and stage input/output gates.
- `backend/app/modules/workflows/stage_execution.py`: enforce declared inputs before implemented execution.
- `backend/app/modules/workflows/job_sync.py`: refuse to advance succeeded Jobs with incomplete outputs.
- `backend/app/modules/workflows/intake/conversion.py`: verify conversion provenance and reuse the Files DXF validator.
- `backend/app/modules/workflows/intake/freeze.py`: make canonical DXF the DrawingVersion and use exact artifact types.
- `backend/app/modules/workflows/README.md`: record the DXF-only drawing boundary.
- `backend/app/modules/workflows/interface.py`: export only contract functions needed across the workflow package.
- `backend/tests/workflows/test_workflow_input_service.py`: conversion/freeze regression coverage and truthful Result fixtures.
- `backend/tests/workflows/test_workflow_production.py`: replace generic artifacts while preserving the pre-existing Job URL edit.
- `backend/tests/architecture/test_workflow_boundaries.py`: lock the new module and exact stage contracts without touching the concurrent route removal hunks.
- `frontend/src/features/workflows/workflow.ts`: add `required_outputs`.
- `frontend/src/features/workflows/WorkflowDetailPage.tsx`: display input/allowed/required contracts and DXF boundary.
- `frontend/src/features/workflows/README.md`: synchronize UI responsibility.
- `frontend/tests/e2e/workflows/workflow-input.spec.ts`: revision-3 input/classification fixtures.
- `frontend/tests/e2e/workflows/workflow-detail.spec.ts`: required-output and DXF-boundary assertions.
- `scripts/docs/generate_api.py`: update workflow API supplement.
- `scripts/docs/check.py`: lock the current DXF artifact vocabulary.
- `docs/reference/api.md`: regenerate public API documentation.
- `docs/reference/database.md`: document canonical DrawingVersion and artifact lineage.
- `docs/architecture/workflow.md`: replace generic drawing flow with the exact chain.
- `docs/architecture/implementation-status.md`: update current-fact sections only.
- `README.md`, `README_EN.md`: synchronize nine-stage and DXF-only summary.

### Preserve

- All current uncommitted project/role route changes.
- All current uncommitted Excel Final data and output directories.
- Independent DWG↔DXF, DXF→Excel, Excel Final, and remnant-inventory APIs.

---

### Task 1: Publish the revision-3 stage contract

**Files:**
- Create: `backend/tests/workflows/test_workflow_dxf_contracts.py`
- Modify: `backend/app/modules/workflows/schemas/orchestration.py`
- Modify: `backend/app/modules/workflows/templates.py`
- Modify: `backend/app/modules/workflows/lifecycle.py`
- Modify: `backend/tests/architecture/test_workflow_boundaries.py`

- [ ] **Step 1: Write the failing template tests**

Add:

```python
from uuid import uuid4

from app.modules.identity.interface import User
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows.interface import create_workflow, list_workflow_templates
from app.modules.workflows.schemas import WorkflowCreate


def _owner_project(db):
    user = User(
        username=f"dxf-contract-{uuid4().hex[:10]}",
        password_hash="not-used",
        real_name="DXF Contract Owner",
        email=f"dxf-contract-{uuid4().hex[:10]}@example.com",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"DXF-{uuid4().hex[:8]}",
        name="DXF canonical contract",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectMember(
            project_id=project.id,
            user_id=user.id,
            project_role="project_owner",
        )
    )
    db.flush()
    return user, project


def _production_workflow(db):
    user, project = _owner_project(db)
    workflow = create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="DXF canonical",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    return user, project, workflow


EXPECTED_DRAWING_FLOW = {
    "source_intake": {
        "required_inputs": ["dwg_files", "excel_file"],
        "artifact_types": ["source_dwg", "source_excel", "canonical_dxf"],
        "required_outputs": ["source_dwg", "source_excel", "canonical_dxf"],
    },
    "dxf_classification": {
        "required_inputs": ["canonical_dxf"],
        "artifact_types": [
            "classified_dxf",
            "classification_report",
            "classification_manifest",
        ],
        "required_outputs": [
            "classified_dxf",
            "classification_report",
            "classification_manifest",
        ],
    },
    "drawing_processing": {
        "required_inputs": ["classified_dxf"],
        "artifact_types": ["processed_dxf", "validation_report"],
        "required_outputs": ["processed_dxf", "validation_report"],
    },
    "excel_stage1": {
        "required_inputs": ["source_excel"],
        "artifact_types": ["stage1_excel"],
        "required_outputs": ["stage1_excel"],
    },
    "design_barrier": {
        "required_inputs": ["processed_dxf", "stage1_excel"],
        "artifact_types": ["review_record"],
        "required_outputs": ["review_record"],
    },
    "cam_packaging": {
        "required_inputs": ["processed_dxf", "stage1_excel", "review_record"],
        "artifact_types": ["cam_input_dxf", "cam_package_manifest"],
        "required_outputs": ["cam_input_dxf", "cam_package_manifest"],
    },
    "windows_cam": {
        "required_inputs": ["cam_input_dxf", "cam_package_manifest"],
        "artifact_types": ["cam_output_dxf", "runner_diagnostics"],
        "required_outputs": ["cam_output_dxf"],
    },
    "result_acceptance": {
        "required_inputs": ["cam_output_dxf"],
        "artifact_types": ["accepted_dxf", "acceptance_report"],
        "required_outputs": ["accepted_dxf", "acceptance_report"],
    },
    "delivery_archive": {
        "required_inputs": ["accepted_dxf", "stage1_excel", "acceptance_report"],
        "artifact_types": ["delivery_dxf", "delivery_excel", "archive_manifest"],
        "required_outputs": ["delivery_dxf", "delivery_excel", "archive_manifest"],
    },
}


def test_linux_production_exposes_exact_dxf_canonical_contract():
    production = next(
        template for template in list_workflow_templates()
        if template.code == "linux_production"
    )
    actual = {
        stage.code: {
            "required_inputs": stage.required_inputs,
            "artifact_types": stage.artifact_types,
            "required_outputs": stage.required_outputs,
        }
        for stage in production.stages
    }
    assert actual == EXPECTED_DRAWING_FLOW


def test_linux_production_has_no_generic_drawing_artifacts():
    forbidden = {
        "source_file",
        "derived_dxf",
        "drawing_files",
        "processed_drawing",
        "processed_drawings",
        "cam_result",
        "delivery_file",
    }
    production = next(
        template for template in list_workflow_templates()
        if template.code == "linux_production"
    )
    published = {
        value
        for stage in production.stages
        for value in (
            *stage.required_inputs,
            *stage.artifact_types,
            *stage.required_outputs,
        )
    }
    assert published.isdisjoint(forbidden)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_dxf_contracts.py
```

Expected: FAIL because `WorkflowStageCapability` has no `required_outputs` and templates still publish generic names.

- [ ] **Step 3: Add `required_outputs` to the public schema**

Update `WorkflowStageCapability`:

```python
class WorkflowStageCapability(BaseModel):
    code: str
    name: str
    description: str
    execution_mode: Literal["manual", "automated", "placeholder", "external"]
    implementation_status: Literal["implemented", "placeholder", "external"]
    execution_kind: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    artifact_types: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
```

Extend `_stage`:

```python
def _stage(
    code: str,
    name: str,
    description: str,
    *,
    execution_mode: str = "manual",
    implementation_status: str = "implemented",
    execution_kind: str | None = None,
    required_inputs: tuple[str, ...] = (),
    artifact_types: tuple[str, ...] = (),
    required_outputs: tuple[str, ...] = (),
) -> WorkflowStageCapability:
    return WorkflowStageCapability(
        code=code,
        name=name,
        description=description,
        execution_mode=execution_mode,
        implementation_status=implementation_status,
        execution_kind=execution_kind,
        required_inputs=list(required_inputs),
        artifact_types=list(artifact_types),
        required_outputs=list(required_outputs),
    )
```

Replace the nine `linux_production` input/output tuples with `EXPECTED_DRAWING_FLOW`. Update the stage descriptions to state that DWG exists only in `source_intake`.

- [ ] **Step 4: Bump new workflows to definition revision 3**

Change:

```python
if payload.workflow_type == "linux_production":
    config["definition_revision"] = 3
```

Add a focused assertion:

```python
def test_new_linux_workflow_uses_definition_revision_three(db):
    _, _, workflow = _production_workflow(db)
    assert workflow.config_json["definition_revision"] == 3
```

- [ ] **Step 5: Update the architecture tuple snapshot**

Extend each `EXPECTED_PRODUCTION_STAGES` tuple with `required_outputs` and update the projection:

```python
actual = [
    (
        stage.code,
        stage.execution_mode,
        stage.implementation_status,
        stage.execution_kind,
        tuple(stage.required_inputs),
        tuple(stage.artifact_types),
        tuple(stage.required_outputs),
    )
    for stage in production.stages
]
```

Do not edit the concurrent `EXPECTED_ROUTES` project-route changes in this task.

- [ ] **Step 6: Run GREEN**

Run:

```bash
cd backend
uv run pytest -q \
  tests/workflows/test_workflow_dxf_contracts.py \
  tests/architecture/test_workflow_boundaries.py
```

Expected: PASS.

- [ ] **Step 7: Commit the contract**

Stage only Task 1 hunks. The existing unrelated edit in `test_workflow_production.py` is not part of this commit.

```bash
git commit -m "feat(workflows): define canonical DXF stage contract"
```

---

### Task 2: Make conversion provenance and frozen DrawingVersion canonical

**Files:**
- Modify: `backend/tests/workflows/test_workflow_input_service.py`
- Modify: `backend/app/modules/workflows/intake/conversion.py`
- Modify: `backend/app/modules/workflows/intake/freeze.py`

- [ ] **Step 1: Write failing conversion provenance tests**

Add a helper so every successful fixture mirrors the real converter:

```python
def _conversion_result(job, source_file_id: int, derived_file_id: int) -> AnalysisResult:
    return AnalysisResult(
        job_id=job.id,
        result_type="convert_dwg_to_dxf",
        result_json={
            "source_file_id": source_file_id,
            "dxf_file_id": derived_file_id,
        },
        result_file_id=derived_file_id,
        status="succeeded",
    )
```

Add:

```python
def test_sync_rejects_result_for_another_source_file(db, tmp_path, monkeypatch):
    user, _, _, batch, storage = _registered_batch(
        db, tmp_path, monkeypatch, dwg_names=("A.dwg",)
    )
    monkeypatch.setattr(workflow_input_conversion.settings, "dxf_pipeline_enabled", True)
    plan = workflow_input_conversion.prepare_input_conversions(
        db, batch, created_by=user.id
    )
    item = next(value for value in batch.items if value.role == "source_dwg")
    derived = _stored_object(
        db,
        storage,
        "A.dxf",
        b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n",
    )
    db.add(_conversion_result(plan.jobs[0], item.file_id + 1000, derived.id))
    plan.jobs[0].status = "succeeded"
    db.flush()

    workflow_input_conversion.sync_input_batch(db, batch)

    assert item.status == "conversion_failed"
    assert item.error_code == "INPUT_DXF_SOURCE_MISMATCH"
```

- [ ] **Step 2: Write the failing DrawingVersion assertion**

Extend the freeze test:

```python
items_by_drawing = {
    item.drawing_id: item
    for item in batch.items
    if item.role == "source_dwg"
}
for drawing in drawings:
    version = next(value for value in versions if value.id == drawing.current_version_id)
    item = items_by_drawing[drawing.id]
    assert version.file_id == item.derived_dxf_file_id
    assert version.file_id != item.file_id
    assert version.source == "workflow_input_dxf"

assert {artifact.artifact_type for artifact in workflow.artifacts} == {
    "source_dwg",
    "source_excel",
    "canonical_dxf",
}
```

- [ ] **Step 3: Run RED**

Run:

```bash
cd backend
uv run pytest -q \
  tests/workflows/test_workflow_input_service.py::test_sync_rejects_result_for_another_source_file \
  tests/workflows/test_workflow_input_service.py::test_freeze_creates_drawings_manifest_artifacts_and_completes_source_intake
```

Expected: provenance test accepts the wrong source, and DrawingVersion still points to DWG.

- [ ] **Step 4: Reuse the Files DXF validator and check provenance**

Import:

```python
from app.modules.files.interface import StoredFile, validate_dxf_structure
```

In `sync_input_batch`, after loading the successful Result:

```python
result_json = result.result_json if isinstance(result.result_json, dict) else {}
if result_json.get("source_file_id") != item.file_id:
    _mark_item_error(
        item,
        "INPUT_DXF_SOURCE_MISMATCH",
        "The conversion result does not belong to the registered DWG input.",
    )
    continue
if result_json.get("dxf_file_id") not in {None, result.result_file_id}:
    _mark_item_error(
        item,
        "INPUT_DXF_RESULT_MISMATCH",
        "The conversion result metadata and registered DXF file disagree.",
    )
    continue
```

Replace the local sentinel:

```python
payload = registration.read_verified_input_object(derived)
try:
    validate_dxf_structure(payload[:65536] + payload[-65536:])
except AppHTTPException:
    _mark_item_error(
        item,
        "INPUT_DXF_UNREADABLE",
        "The server-derived DXF does not contain a readable DXF structure.",
    )
    continue
```

- [ ] **Step 5: Point the DrawingVersion to canonical DXF**

In `freeze_input_batch`:

```python
version = DrawingVersion(
    drawing_id=drawing.id,
    file_id=derived.id,
    version_no=1,
    source="workflow_input_dxf",
    created_by=batch.created_by,
)
```

Attach:

```python
attach_artifact(
    db,
    workflow,
    stage_code="source_intake",
    artifact_type="source_dwg",
    file_id=source.id,
    metadata={"batch_id": batch.id, "drawing_id": drawing.id},
)
attach_artifact(
    db,
    workflow,
    stage_code="source_intake",
    artifact_type="canonical_dxf",
    file_id=derived.id,
    metadata={
        "batch_id": batch.id,
        "drawing_id": drawing.id,
        "source_dwg_file_id": source.id,
    },
)
```

- [ ] **Step 6: Update all conversion fixtures and run GREEN**

Replace successful bare `AnalysisResult` rows in the input service tests with `_conversion_result`.

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_input_service.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git commit -m "fix(workflows): freeze canonical DXF drawing versions"
```

---

### Task 3: Enforce artifact File, Result, project, and format integrity

**Files:**
- Create: `backend/app/modules/workflows/contracts.py`
- Modify: `backend/app/modules/workflows/artifacts.py`
- Modify: `backend/app/modules/workflows/interface.py`
- Modify: `backend/tests/workflows/test_workflow_dxf_contracts.py`
- Modify: `backend/tests/architecture/test_workflow_boundaries.py`

- [ ] **Step 1: Write failing artifact-integrity tests**

Add tests for:

```python
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.interface import (
    attach_artifact,
    bind_stage_job,
    complete_manual_stage,
    sync_workflow_from_jobs,
)
from app.platform.http.exceptions import AppHTTPException


def _stored_file(db, *, name: str, uploaded_by: int) -> StoredFile:
    extension = "." + name.rsplit(".", 1)[-1].lower()
    stored = StoredFile(
        bucket="workflow-contract-tests",
        storage_key=f"tests/{uuid4().hex}{extension}",
        original_name=name,
        file_ext=extension,
        content_type="application/octet-stream",
        size_bytes=32,
        sha256="a" * 64,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def _result(
    db,
    *,
    project_id: int,
    created_by: int,
    file_id: int,
) -> AnalysisResult:
    job = Job(
        project_id=project_id,
        created_by=created_by,
        task_type="drawing_processing",
        precision_level="normal",
        status="succeeded",
        progress=100,
        attempt=1,
    )
    db.add(job)
    db.flush()
    result = AnalysisResult(
        job_id=job.id,
        result_type="drawing_processing",
        result_file_id=file_id,
        status="succeeded",
    )
    db.add(result)
    db.flush()
    return result


def test_dxf_artifact_rejects_excel_file(db):
    user, _, workflow = _production_workflow(db)
    excel_file = _stored_file(db, name="wrong.xlsx", uploaded_by=user.id)
    with pytest.raises(AppHTTPException) as caught:
        attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=excel_file.id,
        )
    assert caught.value.detail["code"] == "WORKFLOW_ARTIFACT_FORMAT_INVALID"


def test_artifact_rejects_result_file_mismatch(db):
    user, project, workflow = _production_workflow(db)
    dxf_file = _stored_file(db, name="drawing.dxf", uploaded_by=user.id)
    other_dxf_file = _stored_file(db, name="other.dxf", uploaded_by=user.id)
    result = _result(
        db,
        project_id=project.id,
        created_by=user.id,
        file_id=other_dxf_file.id,
    )
    with pytest.raises(AppHTTPException) as caught:
        attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=dxf_file.id,
            result_id=result.id,
        )
    assert caught.value.detail["code"] == "WORKFLOW_ARTIFACT_RESULT_FILE_MISMATCH"


def test_artifact_rejects_cross_project_result(db):
    user, _, workflow = _production_workflow(db)
    foreign_user, foreign_project = _owner_project(db)
    dxf_file = _stored_file(db, name="drawing.dxf", uploaded_by=user.id)
    foreign_result = _result(
        db,
        project_id=foreign_project.id,
        created_by=foreign_user.id,
        file_id=dxf_file.id,
    )
    with pytest.raises(AppHTTPException) as caught:
        attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=dxf_file.id,
            result_id=foreign_result.id,
        )
    assert caught.value.detail["code"] == "WORKFLOW_ARTIFACT_PROJECT_MISMATCH"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_dxf_contracts.py -k artifact
```

Expected: wrong formats and unrelated Results are currently accepted.

- [ ] **Step 3: Implement the centralized format map**

Create `contracts.py` with:

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.models import WorkflowRun
from app.platform.config.constants import EXCEL_FILE_EXTENSIONS
from app.platform.http.exceptions import AppHTTPException

DXF_ARTIFACT_TYPES = frozenset(
    {
        "canonical_dxf",
        "classified_dxf",
        "processed_dxf",
        "cam_input_dxf",
        "cam_output_dxf",
        "accepted_dxf",
        "delivery_dxf",
    }
)
DWG_ARTIFACT_TYPES = frozenset({"source_dwg"})
EXCEL_ARTIFACT_TYPES = frozenset(
    {"source_excel", "stage1_excel", "delivery_excel"}
)


def _expected_extensions(artifact_type: str) -> set[str] | None:
    if artifact_type in DXF_ARTIFACT_TYPES:
        return {".dxf"}
    if artifact_type in DWG_ARTIFACT_TYPES:
        return {".dwg"}
    if artifact_type in EXCEL_ARTIFACT_TYPES:
        return set(EXCEL_FILE_EXTENSIONS)
    return None


def validate_artifact_reference(
    db: Session,
    workflow: WorkflowRun,
    *,
    artifact_type: str,
    file_id: int | None,
    result_id: int | None,
) -> None:
    stored = db.get(StoredFile, file_id) if file_id is not None else None
    expected = _expected_extensions(artifact_type)
    if expected is not None:
        if stored is None or stored.status == "deleted" or stored.file_ext.lower() not in expected:
            raise AppHTTPException(
                422,
                "WORKFLOW_ARTIFACT_FORMAT_INVALID",
                "The workflow artifact does not have the required file format.",
                {
                    "artifact_type": artifact_type,
                    "file_id": file_id,
                    "expected_extensions": sorted(expected),
                },
            )
    if result_id is None:
        return
    result = db.get(AnalysisResult, result_id)
    if result is None:
        raise AppHTTPException(404, "RESULT_NOT_FOUND", "Result not found.")
    job = db.get(Job, result.job_id)
    if job is None:
        raise AppHTTPException(404, "JOB_NOT_FOUND", "Job not found.")
    if workflow.workflow_type == "linux_production" and job.project_id != workflow.project_id:
        raise AppHTTPException(
            409,
            "WORKFLOW_ARTIFACT_PROJECT_MISMATCH",
            "The result belongs to another project.",
        )
    if file_id is not None and result.result_file_id != file_id:
        raise AppHTTPException(
            409,
            "WORKFLOW_ARTIFACT_RESULT_FILE_MISMATCH",
            "The result and file references do not describe the same artifact.",
        )
```

- [ ] **Step 4: Call the validator from `attach_artifact`**

Before the idempotency query:

```python
validate_artifact_reference(
    db,
    workflow,
    artifact_type=artifact_type,
    file_id=file_id,
    result_id=result_id,
)
```

Keep legacy templates unrestricted except for the existing non-empty reference check.

- [ ] **Step 5: Lock the new internal file boundary**

Add `contracts.py` to `EXPECTED_INTERNAL_LAYERS["modules/workflows"]`. Export only functions that another workflow module imports; do not expose the extension sets outside the workflow package.

- [ ] **Step 6: Run GREEN**

Run:

```bash
cd backend
uv run pytest -q \
  tests/workflows/test_workflow_dxf_contracts.py \
  tests/workflows/test_workflow_production.py \
  tests/architecture/test_workflow_boundaries.py
```

Expected: PASS, including the pre-existing workflow Job URL edit.

- [ ] **Step 7: Commit**

Stage only this task's hunks in the already-dirty `test_workflow_production.py`.

```bash
git commit -m "fix(workflows): enforce artifact lineage integrity"
```

---

### Task 4: Enforce stage inputs and required outputs

**Files:**
- Modify: `backend/app/modules/workflows/contracts.py`
- Modify: `backend/app/modules/workflows/lifecycle.py`
- Modify: `backend/app/modules/workflows/stage_execution.py`
- Modify: `backend/app/modules/workflows/job_sync.py`
- Modify: `backend/app/modules/workflows/intake/freeze.py`
- Modify: `backend/app/modules/workflows/routes/commands.py`
- Modify: `backend/tests/workflows/test_workflow_dxf_contracts.py`
- Modify: `backend/tests/workflows/test_workflow_production.py`
- Modify: `backend/tests/workflows/test_workflow_boundaries.py`
- Modify: `backend/tests/workflows/test_workflow_framework.py`
- Modify: `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`

- [ ] **Step 1: Write failing input/output-gate tests**

Add:

```python
def _set_current_stage(workflow, stage_code: str) -> None:
    target = next(
        stage for stage in workflow.stages if stage.stage_code == stage_code
    )
    for stage in workflow.stages:
        if stage.sequence < target.sequence:
            stage.status = "succeeded"
            stage.progress = 100
        elif stage.id == target.id:
            stage.status = "waiting_input"
            stage.progress = 0
        else:
            stage.status = "pending"
            stage.progress = 0
    workflow.current_stage = stage_code
    workflow.status = "waiting_input"


def test_drawing_processing_requires_classified_dxf(
    db,
):
    _, _, workflow = _production_workflow(db)
    _set_current_stage(workflow, "drawing_processing")
    with pytest.raises(AppHTTPException) as caught:
        complete_manual_stage(db, workflow, "drawing_processing")
    assert caught.value.detail["code"] == "WORKFLOW_STAGE_INPUT_INCOMPLETE"
    assert caught.value.detail["details"]["missing_inputs"] == ["classified_dxf"]


def test_drawing_processing_requires_dxf_and_validation_report(
    db,
):
    user, _, workflow = _production_workflow(db)
    _set_current_stage(workflow, "drawing_processing")
    classified_dxf = _stored_file(
        db, name="classified.dxf", uploaded_by=user.id
    )
    processed_dxf = _stored_file(
        db, name="processed.dxf", uploaded_by=user.id
    )
    attach_artifact(
        db,
        workflow,
        stage_code="dxf_classification",
        artifact_type="classified_dxf",
        file_id=classified_dxf.id,
    )
    attach_artifact(
        db,
        workflow,
        stage_code="drawing_processing",
        artifact_type="processed_dxf",
        file_id=processed_dxf.id,
    )
    with pytest.raises(AppHTTPException) as caught:
        complete_manual_stage(db, workflow, "drawing_processing")
    assert caught.value.detail["code"] == "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
    assert caught.value.detail["details"]["missing_outputs"] == ["validation_report"]


def test_succeeded_job_without_required_output_fails_workflow_stage(
    db,
):
    user, project, workflow = _production_workflow(db)
    _set_current_stage(workflow, "excel_stage1")
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="process_excel_final",
        precision_level="normal",
        status="succeeded",
        progress=100,
        attempt=1,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code="excel_stage1", job=job)
    job.status = "succeeded"
    job.progress = 100
    db.flush()

    sync_workflow_from_jobs(db, workflow)
    stage = next(
        value for value in workflow.stages
        if value.stage_code == "excel_stage1"
    )
    assert stage.status == "failed"
    assert stage.error_code == "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
    assert workflow.current_stage == "excel_stage1"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_dxf_contracts.py -k "requires or incomplete"
```

Expected: stages still accept any single artifact and succeeded Jobs advance without outputs.

- [ ] **Step 3: Implement contract projections**

Add to `contracts.py`:

```python
def _artifact_types_before_stage(
    workflow: WorkflowRun,
    stage_code: str,
) -> set[str]:
    stage = next(
        value for value in workflow.stages if value.stage_code == stage_code
    )
    prior_stage_ids = {
        value.id for value in workflow.stages if value.sequence < stage.sequence
    }
    return {
        artifact.artifact_type
        for artifact in workflow.artifacts
        if artifact.stage_run_id in prior_stage_ids
    }


def require_stage_inputs(workflow: WorkflowRun, stage_code: str) -> None:
    capability = get_stage_capability(workflow, stage_code)
    if stage_code == "source_intake":
        return
    available = _artifact_types_before_stage(workflow, stage_code)
    missing = [
        value for value in capability.required_inputs if value not in available
    ]
    if missing:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_INPUT_INCOMPLETE",
            "The workflow stage is missing required upstream artifacts.",
            {"stage_code": stage_code, "missing_inputs": missing},
        )


def require_stage_outputs(workflow: WorkflowRun, stage_code: str) -> None:
    capability = get_stage_capability(workflow, stage_code)
    stage = next(
        value for value in workflow.stages if value.stage_code == stage_code
    )
    available = {
        artifact.artifact_type for artifact in stage.artifacts
    }
    missing = [
        value for value in capability.required_outputs if value not in available
    ]
    if missing:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_OUTPUT_INCOMPLETE",
            "The workflow stage has not produced every required artifact.",
            {"stage_code": stage_code, "missing_outputs": missing},
        )
```

Add:

```python
def verify_required_dxf_objects(
    db: Session,
    workflow: WorkflowRun,
    stage_code: str,
) -> None:
    capability = get_stage_capability(workflow, stage_code)
    required_dxf = set(capability.required_outputs) & DXF_ARTIFACT_TYPES
    if not required_dxf:
        return
    stage = next(
        value for value in workflow.stages if value.stage_code == stage_code
    )
    for artifact in stage.artifacts:
        if artifact.artifact_type not in required_dxf:
            continue
        stored = db.get(StoredFile, artifact.file_id)
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "WORKFLOW_ARTIFACT_FILE_MISSING",
                "A required workflow DXF is unavailable.",
                {"artifact_id": artifact.id, "file_id": artifact.file_id},
            )
        payload = registration.read_verified_input_object(stored)
        try:
            validate_dxf_structure(payload[:65536] + payload[-65536:])
        except AppHTTPException as exc:
            raise AppHTTPException(
                422,
                "WORKFLOW_ARTIFACT_FORMAT_INVALID",
                "A required workflow drawing is not a readable DXF.",
                {
                    "artifact_id": artifact.id,
                    "file_id": stored.id,
                    "artifact_type": artifact.artifact_type,
                },
            ) from exc
```

- [ ] **Step 4: Add the lifecycle gates**

Change the signature:

```python
def complete_manual_stage(
    db: Session,
    workflow: WorkflowRun,
    stage_code: str,
) -> WorkflowRun:
```

Before marking success:

```python
require_stage_inputs(workflow, stage_code)
require_stage_outputs(workflow, stage_code)
if workflow.workflow_type == "linux_production" and stage_code != "source_intake":
    verify_required_dxf_objects(db, workflow, stage_code)
```

Delete the weaker `and not stage.artifacts` placeholder check.

Update every caller to pass `db`, including freeze and routes. Do not alter the concurrent project-route removal in `routes/commands.py`.

- [ ] **Step 5: Gate automated execution**

After checking `implementation_status == "implemented"` and before preparing a real Job:

```python
require_stage_inputs(workflow, stage_code)
```

Keep the more specific frozen-batch and source-Excel checks.

- [ ] **Step 6: Stop Job synchronization on incomplete output**

After attaching all successful Results:

```python
try:
    require_stage_outputs(workflow, stage.stage_code)
except AppHTTPException as exc:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    stage.status = "failed"
    stage.error_code = str(
        detail.get("code") or "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
    )
    stage.error_message = str(
        detail.get("message") or "Required workflow output is missing."
    )
    stage.finished_at = now
    continue
```

Only create/activate the next stage after this succeeds.

- [ ] **Step 7: Update real workflow fixtures**

Every fixture that advances Linux stages must bind the exact required upstream and output artifacts. Use valid `.dxf` fixtures for drawing types, `.xlsx` for Excel types, and `.json`/`.csv` files for reports/manifests. Do not continue using one Excel file under every artifact name.

- [ ] **Step 8: Run GREEN**

Run:

```bash
cd backend
uv run pytest -q \
  tests/workflows \
  tests/dxf_classification/test_dxf_classification_pipeline.py \
  tests/architecture/test_workflow_boundaries.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

Stage only the workflow contract hunks in files that also contain current project/role work.

```bash
git commit -m "fix(workflows): require complete stage artifacts"
```

---

### Task 5: Migrate revision-2 workflow data safely

**Files:**
- Create: `backend/migrations/versions/c7b2d4e9f601_canonicalize_workflow_dxf_flow.py`
- Create: `backend/tests/infrastructure/test_workflow_dxf_migration.py`
- Modify: `backend/tests/infrastructure/test_migrations.py`

- [ ] **Step 1: Write a portable migration harness**

Create SQLite tables for:

```python
workflow_runs(id, workflow_type, config_json)
workflow_stage_runs(id, workflow_run_id, stage_code)
workflow_artifacts(id, workflow_run_id, stage_run_id, artifact_type, file_id)
workflow_input_items(id, input_batch_id, file_id, role, status,
                     derived_dxf_file_id, drawing_id)
drawing_versions(id, drawing_id, file_id, source)
files(id, file_ext, status)
```

Load the migration with `importlib.util.spec_from_file_location`, matching the existing workflow stage migration test.

- [ ] **Step 2: Write failing upgrade/downgrade tests**

Cover:

```python
def test_revision_two_upgrade_canonicalizes_artifacts_and_drawing_version():
    # source_file -> source_dwg
    # derived_dxf -> canonical_dxf
    # processed_drawing -> processed_dxf
    # cam_result -> cam_output_dxf
    # delivery_file(.dxf) -> delivery_dxf
    # workflow_input_dwg version file_id -> derived DXF and source name
    # definition_revision -> 3


def test_upgrade_rejects_frozen_item_without_derived_dxf():
    with pytest.raises(RuntimeError, match="missing derived DXF"):
        migration._upgrade_linux_workflows(connection)


def test_upgrade_rejects_generic_cam_package_with_unknown_format():
    with pytest.raises(RuntimeError, match="cannot infer revision 3 artifact"):
        migration._upgrade_linux_workflows(connection)


def test_upgrade_rejects_legacy_dxf_artifact_pointing_to_excel():
    with pytest.raises(RuntimeError, match="does not reference a DXF"):
        migration._upgrade_linux_workflows(connection)


def test_safe_revision_three_downgrade_restores_revision_two_names():
    migration._downgrade_linux_workflows(connection)
    # Exact old names, DWG DrawingVersion, definition_revision 2.


def test_downgrade_rejects_new_artifacts_without_revision_two_equivalent():
    with pytest.raises(RuntimeError, match="cannot be represented by revision 2"):
        migration._downgrade_linux_workflows(connection)
```

- [ ] **Step 3: Verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/infrastructure/test_workflow_dxf_migration.py
```

Expected: FAIL because the migration does not exist.

- [ ] **Step 4: Implement upgrade**

The migration must:

1. select only `workflow_type = 'linux_production'`;
2. parse `config_json` as an object;
3. resolve stage IDs before renaming artifacts;
4. validate the referenced file extension before every new DXF name;
5. reject `cam_package` and progressed result-acceptance rows that cannot satisfy revision 3;
6. update `workflow_input_dwg` versions only when one input item gives both source DWG and derived DXF;
7. set revision 3 last, after all validation succeeds.

Use bound parameters and SQLAlchemy `sa.text`; do not use dialect-specific JSON SQL.

- [ ] **Step 5: Implement downgrade**

Reverse:

```text
source_dwg       -> source_file
canonical_dxf    -> derived_dxf
processed_dxf    -> processed_drawing
cam_output_dxf   -> cam_result
delivery_dxf     -> delivery_file
workflow_input_dxf DrawingVersion -> source input DWG
definition_revision 3 -> 2
```

Abort if `cam_input_dxf`, `cam_package_manifest`, `accepted_dxf`, `delivery_excel`, or `archive_manifest` exists.

- [ ] **Step 6: Run migration tests and head check**

Run:

```bash
cd backend
uv run pytest -q \
  tests/infrastructure/test_workflow_dxf_migration.py \
  tests/infrastructure/test_migrations.py
uv run alembic heads
```

Expected: tests PASS; exactly `c7b2d4e9f601 (head)`.

- [ ] **Step 7: Test upgrade against a temporary empty MySQL schema**

Run the repository isolation migration command:

```bash
cd ..
bash scripts/db.sh migration-test
```

Expected: upgrade from zero reaches `c7b2d4e9f601`, required tables/seeds exist, and the temporary schema is cleaned.

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(migrations): canonicalize workflow DXF lineage"
```

---

### Task 6: Synchronize the frontend contract

**Files:**
- Modify: `frontend/src/features/workflows/workflow.ts`
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `frontend/tests/e2e/workflows/workflow-input.spec.ts`
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`

- [ ] **Step 1: Write failing Playwright assertions**

Update template fixtures to include:

```typescript
required_inputs: ['canonical_dxf'],
artifact_types: ['classified_dxf', 'classification_report', 'classification_manifest'],
required_outputs: ['classified_dxf', 'classification_report', 'classification_manifest'],
```

Add:

```typescript
await expect(page.getByText('图纸主格式：DXF')).toBeVisible();
await expect(page.getByText(/完成必需产物：processed_dxf、validation_report/)).toBeVisible();
await expect(page.getByText(/DWG 只在输入阶段留档/)).toBeVisible();
```

- [ ] **Step 2: Verify RED**

Run:

```bash
cd frontend
npx playwright test \
  tests/e2e/workflows/workflow-input.spec.ts \
  tests/e2e/workflows/workflow-detail.spec.ts
```

Expected: fixture type checking or visibility assertions fail because `required_outputs` is absent.

- [ ] **Step 3: Extend the TypeScript contract**

```typescript
export interface WorkflowStageCapability {
  code: string;
  name: string;
  description: string;
  execution_mode: 'manual' | 'automated' | 'placeholder' | 'external';
  implementation_status: 'implemented' | 'placeholder' | 'external';
  execution_kind?: string | null;
  required_inputs: string[];
  artifact_types: string[];
  required_outputs: string[];
}
```

- [ ] **Step 4: Render the exact server contract**

For the current-stage alert:

```tsx
description={[
  `所需输入：${currentCapability.required_inputs.join('、') || '无'}`,
  `允许产物：${currentCapability.artifact_types.join('、') || '无'}`,
  `完成必需产物：${currentCapability.required_outputs.join('、') || '无'}`,
].join('；')}
```

Add an informational alert for `linux_production`:

```tsx
<Alert
  type="info"
  showIcon
  message="图纸主格式：DXF"
  description="DWG 只在输入阶段留档；服务器转换并冻结后，后续图纸产物必须是 DXF。Excel、报告和清单保持各自格式。"
/>
```

- [ ] **Step 5: Run GREEN and production build**

Run:

```bash
cd frontend
npx playwright test \
  tests/e2e/workflows/workflow-input.spec.ts \
  tests/e2e/workflows/workflow-detail.spec.ts
npm run build
```

Expected: workflow specs PASS and Vite production build succeeds.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(frontend): expose DXF workflow contracts"
```

---

### Task 7: Synchronize generated and authored documentation

**Files:**
- Modify: `backend/app/modules/workflows/README.md`
- Modify: `frontend/src/features/workflows/README.md`
- Modify: `scripts/docs/generate_api.py`
- Modify: `scripts/docs/check.py`
- Modify: `docs/reference/api.md`
- Modify: `docs/reference/database.md`
- Modify: `docs/architecture/workflow.md`
- Modify: `docs/architecture/implementation-status.md`
- Modify: `README.md`
- Modify: `README_EN.md`

- [ ] **Step 1: Write the failing documentation contract**

Extend the existing docs check to require current workflow docs to contain:

```text
source_dwg
canonical_dxf
classified_dxf
processed_dxf
cam_input_dxf
cam_output_dxf
accepted_dxf
delivery_dxf
definition_revision 3
```

and reject the old generic chain in current-fact sections:

```text
source_file/derived_dxf
drawing_files
processed_drawing
processed_drawings
cam_result
delivery_file
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python scripts/docs/check.py
```

Expected: FAIL on stale workflow terms.

- [ ] **Step 3: Update authored sources**

Describe the business action chain:

```text
一个 Excel + 多个 DWG
→ 所有源对象登记
→ 每个 DWG 转 canonical DXF
→ 冻结 manifest 和 DXF DrawingVersion
→ classified DXF
→ processed DXF
→ CAM input/output DXF
→ accepted DXF
→ delivery DXF
```

State explicitly that Excel/reports/manifests retain their formats and that unfinished algorithms remain placeholders.

Update only the current-fact sections of `implementation-status.md`; keep dated historical snapshots labeled as historical.

- [ ] **Step 4: Regenerate API documentation**

Run:

```bash
python scripts/docs/generate_api.py
```

Confirm the generated input-batch section names `source_dwg` and `canonical_dxf`, and template output includes `required_outputs`.

- [ ] **Step 5: Run docs and architecture checks**

Run:

```bash
make docs-check
make architecture-check
git diff --check
```

Expected: all PASS.

- [ ] **Step 6: Commit**

Stage only these documentation changes; do not include unrelated Excel Final docs.

```bash
git commit -m "docs(workflows): document canonical DXF flow"
```

---

### Task 8: Run automated regression and live data-flow acceptance

**Files:**
- Modify after verification only if needed: `docs/verification/current.md`

- [ ] **Step 1: Run focused backend gates**

```bash
cd backend
uv run pytest -q \
  tests/workflows \
  tests/files \
  tests/jobs \
  tests/cad_processing/test_dxf_pipeline.py \
  tests/cad_processing/test_cad_batch_jobs.py \
  tests/dxf_classification \
  tests/infrastructure/test_workflow_dxf_migration.py \
  tests/architecture/test_workflow_boundaries.py
```

Expected: PASS with no workflow regression.

- [ ] **Step 2: Run the full backend and frontend suites**

```bash
cd backend
uv run pytest -q
cd ../frontend
npx playwright test
```

Expected: all non-environment tests PASS; conditional skips retain their documented reasons.

- [ ] **Step 3: Select real approved samples**

Use:

```text
Excel: /home/Creeken/Paper/CAD_research/complete_framework/Stages/excel_final/data/核心筒F20F21层-构件零件清单.xlsx
DWG: two files from /home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图
```

Before mutation, record sample names, byte sizes, and SHA-256. Do not copy source files into git.

- [ ] **Step 4: Refresh the live runtime**

```bash
bash scripts/start-all.sh --restart-backend
./scripts/status.sh
```

Expected: MySQL, storage, API, Nginx, `worker-dxf`, `worker-dxf-classification`, and `worker-excel-final` are current and healthy. A healthy worker alone is not acceptance.

- [ ] **Step 5: Run the real API chain**

Using a dedicated test project and authenticated operator:

1. create/start `linux_production`;
2. upload the Excel and two DWGs through `/api/v1/files`;
3. register all three file IDs into the input batch;
4. submit conversion requests;
5. poll until both bound attempts succeed;
6. verify both derived files are registered `.dxf` and have valid `SECTION`/`EOF`;
7. freeze the batch;
8. start classification and wait for completion;
9. query workflow detail, DrawingVersions, artifacts, Jobs, Results, and input items.

Expected SQL invariants:

```sql
SELECT COUNT(*)
FROM workflow_input_items
WHERE input_batch_id = :batch_id AND role = 'source_dwg';
-- 2

SELECT COUNT(*)
FROM workflow_input_items
WHERE input_batch_id = :batch_id
  AND role = 'source_dwg'
  AND derived_dxf_file_id IS NOT NULL
  AND drawing_id IS NOT NULL;
-- 2

SELECT COUNT(*)
FROM drawing_versions dv
JOIN workflow_input_items wi ON wi.drawing_id = dv.drawing_id
JOIN files f ON f.id = dv.file_id
WHERE wi.input_batch_id = :batch_id
  AND dv.source = 'workflow_input_dxf'
  AND f.file_ext = '.dxf';
-- 2

SELECT COUNT(*)
FROM workflow_artifacts wa
JOIN files f ON f.id = wa.file_id
WHERE wa.workflow_run_id = :workflow_id
  AND wa.artifact_type NOT IN ('source_dwg', 'source_excel')
  AND f.file_ext = '.dwg';
-- 0
```

- [ ] **Step 6: Inspect real artifacts**

Download both canonical DXFs plus at least one classified DXF. Record:

- source DWG file IDs;
- conversion Job IDs and attempts;
- canonical DXF file IDs and SHA-256;
- Drawing IDs/current version IDs;
- manifest SHA-256;
- classification Job/run IDs;
- classified DXF/report/manifest file IDs.

Confirm classified DXF bytes match their canonical source where the classifier only routes/renames.

- [ ] **Step 7: Run the unified release gate**

```bash
bash scripts/verify.sh full
```

Expected: no FAIL or BLOCKED item. If the migration gate needs cached sudo, refresh it in a TTY and rerun in the same credential window.

- [ ] **Step 8: Update current verification evidence**

Record only freshly observed counts, IDs, timestamps, real sample summaries, and gate results. Do not reuse the memory snapshot counts.

- [ ] **Step 9: Final diff and repository review**

```bash
git diff --check
git status --short --branch
git log --oneline -12
```

Verify:

- no source DWG is referenced as a frozen/current DrawingVersion;
- no generic drawing artifact remains in current runtime/template code;
- no unrelated project/role or Excel Final changes were committed;
- all new files appear in their partition README/architecture checks;
- all task commits are reviewable.

- [ ] **Step 10: Commit verification evidence**

```bash
git commit -m "test(workflows): verify real DXF canonical flow"
```

Do not push unless the user separately authorizes publication.

---

## Plan Self-Review

- Spec coverage: input upload, all-file registration, conversion, DXF-only drawing flow, required artifacts, migration, frontend, docs, real ODA verification, and git isolation are each assigned to a task.
- Placeholder scan: every implementation step names an exact file, command, behavior, error code, or code body; unfinished production algorithms remain explicitly out of scope rather than implementation placeholders.
- Type consistency: `required_outputs`, `canonical_dxf`, `processed_dxf`, `cam_input_dxf`, `cam_output_dxf`, `accepted_dxf`, and `delivery_dxf` use identical names across schema, templates, tests, frontend, migration, and docs.
- Worktree safety: current project/role and Excel Final changes are preserved, and overlapping test/route files require hunk-only staging.
