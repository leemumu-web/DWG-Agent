from __future__ import annotations

import pytest
from sqlalchemy import select

from app.modules.operations.audit.models import AuditLog
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows.lifecycle import create_workflow
from app.modules.workflows.models import WorkflowRun, WorkflowStageRun
from app.modules.workflows.routes.production_projects import (
    write_audit_log as real_write_audit_log,
)
from app.modules.workflows.schemas import WorkflowCreate
from app.platform.http.exceptions import AppHTTPException
from tests.support import workflow_api as workflow_test_api
from tests.support.database import open_test_session


def test_create_production_project_returns_project_and_started_complete_workflow():
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    owner_id, owner_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "production-project",
    )

    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json={
            "code": "P-API-001",
            "name": "一号厂房",
            "description": "完整生产流程",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["project"]["code"] == "P-API-001"
    assert data["project"]["name"] == "一号厂房"
    assert data["workflow"]["project_id"] == data["project"]["id"]
    assert data["workflow"]["name"] == "P-API-001 · 一号厂房"
    assert data["workflow"]["workflow_type"] == "linux_production"
    assert data["workflow"]["status"] == "waiting_input"
    assert data["workflow"]["current_stage"] == "source_intake"
    assert len(data["workflow"]["stages"]) == 12
    with open_test_session() as db:
        membership = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == data["project"]["id"],
                ProjectMember.user_id == owner_id,
            )
        )
        audits = db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action.in_(
                    ("projects.create", "workflows.create", "workflows.start")
                ),
                AuditLog.resource_id.in_(
                    (data["project"]["id"], data["workflow"]["id"])
                )
            )
            .order_by(AuditLog.id)
        ).all()
    assert membership is not None
    assert membership.project_role == "project_owner"
    assert [audit.action for audit in audits] == [
        "projects.create",
        "workflows.create",
        "workflows.start",
    ]


def test_legacy_workflow_route_cannot_add_second_production_flow():
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "production-unique",
    )
    created = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json={"code": "P-UNIQUE-001", "name": "唯一生产流程"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["data"]["project"]["id"]
    workflow_id = created.json()["data"]["workflow"]["id"]

    duplicate = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "不能创建的第二条流程",
            "workflow_type": "linux_production",
        },
    )

    assert duplicate.status_code == 409, duplicate.text
    error = duplicate.json()["error"]
    assert error["code"] == "PRODUCTION_WORKFLOW_ALREADY_EXISTS"
    assert error["details"] == {
        "project_id": project_id,
        "workflow_id": workflow_id,
    }


def test_atomic_creation_failure_leaves_no_project_or_workflow(monkeypatch):
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "production-rollback",
    )

    def fail_start(*_args, **_kwargs):
        raise AppHTTPException(409, "START_FAILED", "Injected start failure.")

    monkeypatch.setattr(
        "app.modules.workflows.production_projects.start_workflow",
        fail_start,
    )
    failed = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json={"code": "P-NO-PARTIAL", "name": "不能残留"},
    )

    assert failed.status_code == 409, failed.text
    projects = client.get(
        "/api/v1/workflows/projects",
        headers=owner_headers,
    )
    workflows = client.get(
        "/api/v1/workflows",
        headers=owner_headers,
    )
    assert projects.status_code == 200, projects.text
    assert workflows.status_code == 200, workflows.text
    assert all(item["code"] != "P-NO-PARTIAL" for item in projects.json()["data"])
    assert workflows.json()["pagination"]["total"] == 0


def test_audit_failure_rolls_back_project_membership_workflow_and_audit(monkeypatch):
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "production-audit-rollback",
    )

    audit_calls = 0

    def fail_second_audit(*args, **kwargs):
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls == 1:
            return real_write_audit_log(*args, **kwargs)
        raise RuntimeError("Injected audit failure.")

    monkeypatch.setattr(
        "app.modules.workflows.routes.production_projects.write_audit_log",
        fail_second_audit,
    )
    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json={"code": "P-AUDIT-ROLLBACK", "name": "审计失败必须回滚"},
    )

    assert response.status_code == 500
    with open_test_session() as db:
        project = db.scalar(select(Project).where(Project.code == "P-AUDIT-ROLLBACK"))
        membership_count = len(db.scalars(select(ProjectMember)).all())
        workflow_count = len(db.scalars(select(WorkflowRun)).all())
        stage_count = len(db.scalars(select(WorkflowStageRun)).all())
        production_audit_count = len(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(
                        ("projects.create", "workflows.create", "workflows.start")
                    )
                )
            ).all()
        )
    assert project is None
    assert membership_count == 0
    assert workflow_count == 0
    assert stage_count == 0
    assert production_audit_count == 0


def test_duplicate_project_code_returns_stable_conflict():
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "production-code-conflict",
    )
    payload = {"code": "P-CODE-001", "name": "项目编号唯一"}

    first = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json=payload,
    )
    duplicate = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json=payload,
    )

    assert first.status_code == 201, first.text
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROJECT_CODE_EXISTS"


def test_production_duplicate_check_uses_locking_current_read():
    project = Project(id=7, code="P-LOCK", name="锁定项目", status="active")
    existing = WorkflowRun(
        id=9,
        project_id=7,
        created_by=1,
        name="既有流程",
        workflow_type="linux_production",
        status="waiting_input",
        progress=0,
    )

    class RecordingSession:
        def __init__(self):
            self.statements = []

        def scalar(self, statement):
            self.statements.append(statement)
            return project if len(self.statements) == 1 else existing

    db = RecordingSession()
    with pytest.raises(AppHTTPException) as raised:
        create_workflow(
            db,  # type: ignore[arg-type]
            WorkflowCreate(
                project_id=project.id,
                name="竞争请求",
                workflow_type="linux_production",
            ),
            created_by=1,
        )

    assert raised.value.detail["code"] == "PRODUCTION_WORKFLOW_ALREADY_EXISTS"
    assert len(db.statements) == 2
    assert all(statement._for_update_arg is not None for statement in db.statements)


def test_workflow_list_can_select_only_production_projects():
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "production-list",
    )
    created = client.post(
        "/api/v1/workflows/production-projects",
        headers=owner_headers,
        json={"code": "P-LIST-001", "name": "生产项目列表"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["data"]["project"]["id"]
    compatibility = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "兼容交付流程",
            "workflow_type": "file_delivery",
        },
    )
    assert compatibility.status_code == 201, compatibility.text

    response = client.get(
        "/api/v1/workflows",
        headers=owner_headers,
        params={"workflow_type": "linux_production"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 1
    body = response.json()
    assert {workflow["workflow_type"] for workflow in body["data"]} == {
        "linux_production"
    }
    assert body["data"][0]["project_code"] == "P-LIST-001"
    assert body["data"][0]["project_name"] == "生产项目列表"
    assert body["summary"] == {
        "total": 1,
        "running": 0,
        "waiting": 1,
        "completed": 0,
    }

    second_compatibility = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "第二条兼容交付流程",
            "workflow_type": "file_delivery",
        },
    )
    assert second_compatibility.status_code == 201, second_compatibility.text
