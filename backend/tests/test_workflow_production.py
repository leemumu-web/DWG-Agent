from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.models.job import Job
from app.models.project import Project, ProjectMember
from app.models.result import AnalysisResult
from app.models.user import User
from app.schemas.workflow_schema import WorkflowCreate
from app.services import workflow_service


def _owner_project(db):
    user = User(
        username=f"production-wf-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Production Workflow Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"PROD-{uuid4().hex[:6]}",
        name="Linux Production",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, project_role="project_owner"))
    db.flush()
    return user, project


def test_linux_production_template_has_complete_ordered_server_framework(db):
    user, project = _owner_project(db)

    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="Production run",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )

    assert [stage.stage_code for stage in workflow.stages] == [
        "source_intake",
        "drawing_processing",
        "excel_stage1",
        "design_barrier",
        "excel_final",
        "cam_packaging",
        "windows_cam",
        "result_acceptance",
        "delivery_archive",
    ]


def test_linux_production_template_exposes_honest_capabilities():
    templates = workflow_service.list_workflow_templates()
    production = next(item for item in templates if item.code == "linux_production")
    stages = {stage.code: stage for stage in production.stages}

    assert stages["source_intake"].execution_mode == "manual"
    assert stages["excel_stage1"].implementation_status == "implemented"
    assert stages["excel_stage1"].execution_kind == "dxf_to_excel"
    assert stages["excel_final"].implementation_status == "implemented"
    assert stages["excel_final"].execution_kind == "excel_final"
    assert stages["drawing_processing"].implementation_status == "placeholder"
    assert stages["cam_packaging"].implementation_status == "placeholder"
    assert stages["windows_cam"].implementation_status == "external"
    assert stages["result_acceptance"].implementation_status == "placeholder"


def test_legacy_workflow_templates_keep_their_stage_order(db):
    user, project = _owner_project(db)

    excel = workflow_service.create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Excel", workflow_type="excel_delivery"),
        created_by=user.id,
    )
    files = workflow_service.create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Files", workflow_type="file_delivery"),
        created_by=user.id,
    )

    assert [stage.stage_code for stage in excel.stages] == [
        "source_upload",
        "excel_process",
        "quality_review",
        "delivery",
    ]
    assert [stage.stage_code for stage in files.stages] == [
        "source_upload",
        "quality_review",
        "delivery",
    ]


def _stored_file(db, *, name: str = "source.dxf", status: str = "available"):
    stored = StoredFile(
        bucket="test-bucket",
        storage_key=f"workflow/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=f".{name.rsplit('.', 1)[-1].lower()}",
        content_type="application/octet-stream",
        size_bytes=128,
        sha256=uuid4().hex + uuid4().hex,
        status=status,
    )
    db.add(stored)
    db.flush()
    return stored


def _production_workflow(db):
    user, project = _owner_project(db)
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="Production run",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    workflow_service.start_workflow(db, workflow)
    return user, project, workflow


def test_source_intake_requires_a_bound_file_before_freeze(db):
    _, _, workflow = _production_workflow(db)

    with pytest.raises(AppHTTPException, match="source file"):
        workflow_service.complete_manual_stage(workflow, "source_intake")

    stored = _stored_file(db)
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=stored.id,
    )
    workflow_service.complete_manual_stage(workflow, "source_intake")

    assert workflow.current_stage == "drawing_processing"


def test_repeated_file_binding_is_idempotent(db):
    _, _, workflow = _production_workflow(db)
    stored = _stored_file(db)

    first = workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=stored.id,
    )
    second = workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=stored.id,
    )

    assert second.id == first.id
    assert len(workflow.artifacts) == 1


def test_artifact_api_reuses_files_and_is_idempotent():
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-artifact")
    project_id = _project(client, owner_headers)
    created = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "File-bound production",
            "workflow_type": "linux_production",
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["data"]["id"]
    started = client.post(f"/api/v1/workflows/{workflow_id}/start", headers=owner_headers)
    assert started.status_code == 200, started.text
    uploaded = client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={"upload": ("source.xlsx", b"workflow source", "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["data"]["id"]
    payload = {
        "stage_code": "source_intake",
        "artifact_type": "source_file",
        "file_id": file_id,
    }

    first = client.post(
        f"/api/v1/workflows/{workflow_id}/artifacts", headers=owner_headers, json=payload
    )
    second = client.post(
        f"/api/v1/workflows/{workflow_id}/artifacts", headers=owner_headers, json=payload
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"]["artifact"]["id"] == first.json()["data"]["artifact"]["id"]
    assert len(second.json()["data"]["workflow"]["artifacts"]) == 1


def test_non_member_cannot_bind_workflow_artifact():
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-owner")
    _, stranger_headers = _engineer_user(client, admin_headers, "prod-stranger")
    project_id = _project(client, owner_headers)
    created = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "Private production",
            "workflow_type": "linux_production",
        },
    )
    workflow_id = created.json()["data"]["id"]

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/artifacts",
        headers=stranger_headers,
        json={"stage_code": "source_intake", "artifact_type": "source_file", "file_id": 1},
    )

    assert response.status_code == 403, response.text


def _api_workflow_at_excel_stage(client, owner_headers, project_id: int):
    batch_name = f"production-{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "Executable production",
            "workflow_type": "linux_production",
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["data"]["id"]
    uploaded = client.post(
        "/api/v1/files",
        headers=owner_headers,
        params={"batch_name": batch_name},
        files={"upload": ("drawing.dxf", b"0\nSECTION\n2\nHEADER\n0\nEOF\n", "image/vnd.dxf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["data"]["id"]
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/artifacts",
        headers=owner_headers,
        json={
            "stage_code": "source_intake",
            "artifact_type": "source_file",
            "file_id": file_id,
        },
    ).status_code == 201
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/start", headers=owner_headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/stages/source_intake/completion",
        headers=owner_headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/artifacts",
        headers=owner_headers,
        json={
            "stage_code": "drawing_processing",
            "artifact_type": "processed_drawing",
            "file_id": file_id,
            "metadata": {"handoff": "test-fixture"},
        },
    ).status_code == 201
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/stages/drawing_processing/completion",
        headers=owner_headers,
    ).status_code == 200
    return workflow_id, batch_name


def test_excel_stage1_execution_creates_binds_and_reuses_real_job(monkeypatch):
    from app.api.v1 import workflows_api
    from app.core.config import settings
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-exec")
    project_id = _project(client, owner_headers)
    workflow_id, batch_name = _api_workflow_at_excel_stage(
        client, owner_headers, project_id
    )
    dispatched: list[int] = []
    monkeypatch.setattr(settings, "dxf2excel_pipeline_enabled", True)
    monkeypatch.setattr(
        workflows_api,
        "dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
        raising=False,
    )
    payload = {"execution_kind": "dxf_to_excel", "batch_name": batch_name}

    first = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json=payload,
    )
    second = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json=payload,
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert first_data["job"]["task_type"] == "extract_dxf_to_excel"
    assert first_data["job"]["project_id"] == project_id
    assert first_data["workflow"]["current_stage"] == "excel_stage1"
    assert first_data["workflow"]["status"] == "running"
    assert second_data["job"]["id"] == first_data["job"]["id"]
    assert second_data["reused"] is True
    assert dispatched == [first_data["job"]["id"]]


def test_excel_stage1_execution_honors_pipeline_feature_gate(monkeypatch):
    from app.core.config import settings
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-gate")
    project_id = _project(client, owner_headers)
    workflow_id, batch_name = _api_workflow_at_excel_stage(
        client, owner_headers, project_id
    )
    monkeypatch.setattr(settings, "dxf2excel_pipeline_enabled", False)

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json={"execution_kind": "dxf_to_excel", "batch_name": batch_name},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "DXF2EXCEL_PIPELINE_DISABLED"


def test_successful_job_sync_attaches_result_once_and_advances(db):
    _, project, workflow = _production_workflow(db)
    source = _stored_file(db)
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=source.id,
    )
    workflow_service.complete_manual_stage(workflow, "source_intake")
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="drawing_processing",
        artifact_type="processed_drawing",
        file_id=source.id,
    )
    workflow_service.complete_manual_stage(workflow, "drawing_processing")
    job = Job(
        project_id=project.id,
        task_type="extract_dxf_to_excel",
        pipeline="dxf2excel",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json={"batch_name": "sync-test"},
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(db, workflow, stage_code="excel_stage1", job=job)
    output = _stored_file(db, name="stage1.xlsx")
    result = AnalysisResult(
        job_id=job.id,
        result_type="extract_dxf_to_excel",
        result_file_id=output.id,
        status="succeeded",
    )
    db.add(result)
    job.status = "succeeded"
    job.progress = 100
    db.flush()

    workflow_service.sync_workflow_from_jobs(db, workflow)
    workflow_service.sync_workflow_from_jobs(db, workflow)

    stage = next(item for item in workflow.stages if item.stage_code == "excel_stage1")
    artifacts = [item for item in workflow.artifacts if item.stage_run_id == stage.id]
    assert workflow.current_stage == "design_barrier"
    assert workflow.status == "waiting_input"
    assert len(artifacts) == 1
    assert artifacts[0].artifact_type == "stage1_excel"
    assert artifacts[0].file_id == output.id
    assert artifacts[0].result_id == result.id


def _api_workflow_at_excel_final(
    client, owner_headers, project_id: int, monkeypatch
):
    from app.api.v1 import workflows_api
    from app.core.config import settings
    from tests import conftest

    workflow_id, batch_name = _api_workflow_at_excel_stage(
        client, owner_headers, project_id
    )
    monkeypatch.setattr(settings, "dxf2excel_pipeline_enabled", True)
    monkeypatch.setattr(workflows_api, "dispatch_committed_job", lambda _db, _job: None)
    executed = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json={"execution_kind": "dxf_to_excel", "batch_name": batch_name},
    )
    assert executed.status_code == 202, executed.text
    assert client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={"upload": ("stage1.xlsx", b"excel source", "application/octet-stream")},
    ).status_code == 201
    uploaded = client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={"upload": ("stage1-result.xlsx", b"excel result", "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    job_id = executed.json()["data"]["job"]["id"]
    assert conftest._test_session_factory is not None
    with conftest._test_session_factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        job.status = "succeeded"
        job.progress = 100
        db.add(
            AnalysisResult(
                job_id=job.id,
                result_type="extract_dxf_to_excel",
                result_file_id=uploaded.json()["data"]["id"],
                status="succeeded",
            )
        )
        db.commit()
    synced = client.get(f"/api/v1/workflows/{workflow_id}", headers=owner_headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["data"]["current_stage"] == "design_barrier"
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/stages/design_barrier/completion",
        headers=owner_headers,
    ).status_code == 200
    return workflow_id, uploaded.json()["data"]["id"]


def test_excel_final_execution_reuses_existing_pipeline(monkeypatch):
    from app.api.v1 import workflows_api
    from app.core.config import settings
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-final")
    project_id = _project(client, owner_headers)
    workflow_id, file_id = _api_workflow_at_excel_final(
        client, owner_headers, project_id, monkeypatch
    )
    dispatched: list[int] = []
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    monkeypatch.setattr(
        workflows_api,
        "dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
    )

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_final/executions",
        headers=owner_headers,
        json={"execution_kind": "excel_final", "file_id": file_id},
    )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["job"]["task_type"] == "process_excel_final"
    assert data["job"]["params_json"] == {"file_id": file_id}
    assert data["workflow"]["current_stage"] == "excel_final"
    assert dispatched == [data["job"]["id"]]


def test_excel_final_execution_rejects_non_excel_file(monkeypatch):
    from app.core.config import settings
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-final-ext")
    project_id = _project(client, owner_headers)
    workflow_id, _ = _api_workflow_at_excel_final(
        client, owner_headers, project_id, monkeypatch
    )
    uploaded = client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={"upload": ("drawing.dxf", b"0\nEOF\n", "image/vnd.dxf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_final/executions",
        headers=owner_headers,
        json={
            "execution_kind": "excel_final",
            "file_id": uploaded.json()["data"]["id"],
        },
    )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "NOT_EXCEL"


def test_placeholder_execution_exposes_complete_contract():
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-placeholder")
    project_id = _project(client, owner_headers)
    created = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "Placeholder contract",
            "workflow_type": "linux_production",
        },
    )
    workflow_id = created.json()["data"]["id"]
    uploaded = client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={"upload": ("source.dxf", b"0\nEOF\n", "image/vnd.dxf")},
    )
    file_id = uploaded.json()["data"]["id"]
    client.post(
        f"/api/v1/workflows/{workflow_id}/artifacts",
        headers=owner_headers,
        json={
            "stage_code": "source_intake",
            "artifact_type": "source_file",
            "file_id": file_id,
        },
    )
    client.post(f"/api/v1/workflows/{workflow_id}/start", headers=owner_headers)
    client.post(
        f"/api/v1/workflows/{workflow_id}/stages/source_intake/completion",
        headers=owner_headers,
    )

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/drawing_processing/executions",
        headers=owner_headers,
        json={"execution_kind": "drawing_processing"},
    )

    assert response.status_code == 501, response.text
    error = response.json()["error"]
    assert error["code"] == "WORKFLOW_STAGE_NOT_IMPLEMENTED"
    assert error["details"]["implementation_status"] == "placeholder"
    assert error["details"]["required_inputs"] == ["drawing_files"]
    assert error["details"]["artifact_types"] == [
        "processed_drawing",
        "validation_report",
    ]


def test_automated_stage_cannot_be_manually_completed(db):
    _, _, workflow = _production_workflow(db)
    source = _stored_file(db)
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=source.id,
    )
    workflow_service.complete_manual_stage(workflow, "source_intake")
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="drawing_processing",
        artifact_type="processed_drawing",
        file_id=source.id,
    )
    workflow_service.complete_manual_stage(workflow, "drawing_processing")

    with pytest.raises(AppHTTPException, match="execution endpoint"):
        workflow_service.complete_manual_stage(workflow, "excel_stage1")


def test_placeholder_handoff_requires_an_artifact(db):
    _, _, workflow = _production_workflow(db)
    source = _stored_file(db)
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=source.id,
    )
    workflow_service.complete_manual_stage(workflow, "source_intake")

    with pytest.raises(AppHTTPException, match="handoff artifact"):
        workflow_service.complete_manual_stage(workflow, "drawing_processing")


def test_cancelling_workflow_cancels_bound_active_job(monkeypatch):
    from app.api.v1 import workflows_api
    from app.core.config import settings
    from tests.test_workflow_api import _admin_headers, _client, _engineer_user, _project

    client = _client()
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, "prod-cancel")
    project_id = _project(client, owner_headers)
    workflow_id, batch_name = _api_workflow_at_excel_stage(
        client, owner_headers, project_id
    )
    monkeypatch.setattr(settings, "dxf2excel_pipeline_enabled", True)
    monkeypatch.setattr(workflows_api, "dispatch_committed_job", lambda _db, _job: None)
    executed = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json={"execution_kind": "dxf_to_excel", "batch_name": batch_name},
    )
    assert executed.status_code == 202, executed.text
    job_id = executed.json()["data"]["job"]["id"]

    cancelled = client.post(
        f"/api/v1/workflows/{workflow_id}/cancellation-requests",
        headers=owner_headers,
    )
    job = client.get(f"/api/v1/jobs/{job_id}", headers=owner_headers)

    assert cancelled.status_code == 200, cancelled.text
    assert job.status_code == 200, job.text
    assert job.json()["data"]["status"] == "cancelled"
