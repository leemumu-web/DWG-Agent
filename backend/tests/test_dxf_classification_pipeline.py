from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap.seed import init_db
from app.main import app
from app.models.dxf_classification import DxfClassificationItem, DxfClassificationRun
from app.models.workflow import WorkflowRun
from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.modules.files.interface import StoredFile, get_storage_backend
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.modules.projects.interface import Project, ProjectMember
from app.schemas.workflow_schema import WorkflowCreate
from app.services import dxf_classification_service, workflow_service


def _frozen_classification_job(db, tmp_path: Path):
    user = User(
        username=f"classifier-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Classifier Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"CLS-{uuid4().hex[:6]}",
        name="Classification",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, project_role="project_owner"))
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Steel batch", workflow_type="linux_production"),
        created_by=user.id,
    )
    workflow_service.start_workflow(db, workflow)

    payload = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    storage = get_storage_backend()
    key = f"tests/{uuid4().hex}.dxf"
    storage.put_fileobj(
        "dxf-derived",
        key,
        __import__("io").BytesIO(payload),
        length=len(payload),
        content_type="application/dxf",
    )
    stored = StoredFile(
        bucket="dxf-derived",
        storage_key=key,
        original_name="A001.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        batch_name=f"workflow-input-{workflow.id}",
        uploaded_by=user.id,
        status="available",
    )
    db.add(stored)
    db.flush()
    batch = WorkflowInputBatch(
        workflow=workflow,
        project_id=project.id,
        created_by=user.id,
        status="frozen",
        version=1,
        manifest_sha256="f" * 64,
    )
    db.add(batch)
    db.flush()
    db.add(
        WorkflowInputItem(
            batch=batch,
            file_id=stored.id,
            role="source_dwg",
            original_name="A001.dwg",
            normalized_stem="a001",
            status="frozen",
            derived_dxf_file_id=stored.id,
        )
    )
    workflow_service.complete_manual_stage(workflow, "source_intake")
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="classify_steel_dxf",
        pipeline="steel_dxf_classifier",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json={"workflow_id": workflow.id},
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(db, workflow, stage_code="dxf_classification", job=job)
    db.commit()
    return workflow.id, job.id, stored.id


def test_classifier_run_persists_routed_dxf_reports_and_mysql_ledger(
    db, monkeypatch, tmp_path: Path
):
    from app.platform.config.settings import settings

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_root", tmp_path / "storage")
    workflow_id, job_id, source_file_id = _frozen_classification_job(db, tmp_path)

    def fake_cli(input_directory: Path):
        project_name = input_directory.name.removesuffix("_dxf")
        renamed = input_directory / "A001_拆板前.dxf"
        (input_directory / "A001.dxf").rename(renamed)
        route = f"{project_name}_BH_dxf"
        output_dir = input_directory.parent / route
        output_dir.mkdir()
        output = output_dir / renamed.name
        output.write_bytes(renamed.read_bytes())
        report = {
            "schema": "STEEL-DXF-CLASSIFICATION-1.1",
            "summary": {
                "project_name": project_name,
                "input_count": 1,
                "classified_count": 1,
                "review_required_count": 0,
                "unreadable_count": 0,
                "type_counts": {"BH": 1},
                "output_directories": [route],
                "elapsed_seconds": 0.01,
            },
            "results": [{
                "source_name": renamed.name,
                "disposition": "classified",
                "part_type": "BH",
                "diagnostics": [],
                "candidates": [],
                "source_metadata": {},
                "output_directory": route,
            }],
        }
        report_path = input_directory.parent / f"{project_name}_分类报告.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        manifest_path = input_directory.parent / f"{project_name}_分类清单.csv"
        manifest_path.write_text("文件名,处置\nA001_拆板前.dxf,classified\n", encoding="utf-8")
        return {"schema": "STEEL-DXF-CLI-1.1", "status": "completed", "exit_code": 0, "summary": report["summary"]}

    monkeypatch.setattr(dxf_classification_service, "_invoke_classifier", fake_cli)
    dxf_classification_service.run_dxf_classification(job_id, worker_name="test-classifier")

    db.expire_all()
    job = db.get(Job, job_id)
    run = db.scalar(select(DxfClassificationRun).where(DxfClassificationRun.job_id == job_id))
    item = db.scalar(select(DxfClassificationItem).where(DxfClassificationItem.run_id == run.id))
    assert job is not None and job.status == "succeeded"
    assert run is not None and run.status == "completed"
    assert run.input_manifest_sha256 == "f" * 64
    assert run.report_file_id is not None and run.manifest_file_id is not None
    assert item is not None and item.source_file_id == source_file_id
    assert item.disposition == "classified" and item.part_type == "BH"
    assert item.output_directory.endswith("_BH_dxf")
    output = db.get(StoredFile, item.output_file_id)
    assert output is not None
    assert output.original_name == "A001_拆板前.dxf"
    assert output.batch_name == item.output_directory
    assert output.sha256 == db.get(StoredFile, source_file_id).sha256
    assert get_storage_backend().object_exists(output.bucket, output.storage_key)
    workflow = db.get(WorkflowRun, workflow_id)
    workflow_service.sync_workflow_from_jobs(db, workflow)
    assert workflow.current_stage == "drawing_processing"
    classified_files = {
        artifact.file_id
        for artifact in workflow.artifacts
        if artifact.artifact_type == "classified_dxf"
    }
    assert classified_files == {output.id}


def test_classifier_naming_contract_uses_project_code_and_workflow_id():
    assert dxf_classification_service.classifier_project_name("PRJ_01", 42) == "PRJ_01-workflow-42"


def test_workflow_execution_api_creates_idempotent_classifier_job(db, monkeypatch):
    from app.api.v1 import workflows_api
    from app.platform.config.settings import settings

    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": f"API-{uuid4().hex[:6]}", "name": "Classifier API"},
    ).json()["data"]
    created = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "project_id": project["id"],
            "name": "Classifier API run",
            "workflow_type": "linux_production",
        },
    ).json()["data"]
    client.post(f"/api/v1/workflows/{created['id']}/start", headers=headers)

    workflow = db.get(WorkflowRun, created["id"])
    assert workflow is not None
    source = StoredFile(
        bucket="dxf-derived",
        storage_key=f"api/{uuid4().hex}.dxf",
        original_name="api-source.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=8,
        sha256="a" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    batch = WorkflowInputBatch(
        workflow=workflow,
        project_id=workflow.project_id,
        created_by=workflow.created_by,
        status="frozen",
        version=1,
        manifest_sha256="b" * 64,
    )
    db.add(batch)
    db.flush()
    db.add(
        WorkflowInputItem(
            batch=batch,
            file_id=source.id,
            role="source_dwg",
            original_name="api-source.dwg",
            normalized_stem="api-source",
            status="frozen",
            derived_dxf_file_id=source.id,
        )
    )
    workflow_service.complete_manual_stage(workflow, "source_intake")
    db.commit()

    monkeypatch.setattr(settings, "dxf_classification_pipeline_enabled", True)
    dispatched: list[tuple[int, int]] = []
    monkeypatch.setattr(
        workflows_api,
        "dispatch_committed_job",
        lambda _db, job: dispatched.append((job.id, job.attempt)) or "task-1",
    )
    payload = {"execution_kind": "steel_dxf_classification"}
    first = client.post(
        f"/api/v1/workflows/{workflow.id}/stages/dxf_classification/executions",
        headers=headers,
        json=payload,
    )
    second = client.post(
        f"/api/v1/workflows/{workflow.id}/stages/dxf_classification/executions",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert first_data["job"]["task_type"] == "classify_steel_dxf"
    assert first_data["job"]["pipeline"] == "steel_dxf_classifier"
    assert first_data["job"]["params_json"] == {
        "workflow_id": workflow.id,
        "input_manifest_sha256": "b" * 64,
    }
    assert second_data["job"]["id"] == first_data["job"]["id"]
    assert second_data["reused"] is True
    assert dispatched == [(first_data["job"]["id"], 1)]
    empty = client.get(
        f"/api/v1/workflows/{workflow.id}/dxf-classification", headers=headers
    )
    assert empty.status_code == 200 and empty.json()["data"] is None
