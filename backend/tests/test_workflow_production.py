from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.models.project import Project, ProjectMember
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
