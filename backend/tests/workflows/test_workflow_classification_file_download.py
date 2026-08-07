"""HTTP integration tests for single-file DXF classification download."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import select

from app.modules.dxf_classification.interface import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.files.interface import FileTransfer, StoredFile
from app.modules.jobs.interface import Job
from app.modules.workflows.models import WorkflowRun
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api
from tests.support.database import open_test_session


def _register_object(db, storage, *, owner_id, name, payload):
    row = StoredFile(
        bucket="classification-file-test",
        storage_key=f"objects/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=Path(name).suffix.lower(),
        content_type="application/octet-stream",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        uploaded_by=owner_id,
        status="available",
    )
    db.add(row)
    db.flush()
    storage.put_fileobj(
        row.bucket,
        row.storage_key,
        BytesIO(payload),
        length=len(payload),
        content_type=row.content_type,
    )
    return row


def _seed_run(
    db,
    storage,
    *,
    workflow,
    group_key="type:BH",
    output_name="A_拆板前.dxf",
    output_payload=b"dxf bytes",
):
    output = _register_object(
        db,
        storage,
        owner_id=workflow.created_by,
        name=output_name,
        payload=output_payload,
    )
    job = Job(
        project_id=workflow.project_id,
        created_by=workflow.created_by,
        task_type="classify_steel_dxf",
        pipeline="steel_dxf_classifier",
        status="succeeded",
        attempt=1,
        progress=100,
        precision_level="normal",
        params_json={},
    )
    db.add(job)
    db.flush()
    run = DxfClassificationRun(
        workflow_run_id=workflow.id,
        project_id=workflow.project_id,
        job_id=job.id,
        job_attempt=1,
        status="completed",
        classifier_version="1.2.0",
        report_schema="STEEL-DXF-CLASSIFICATION-1.2",
        cli_schema="STEEL-DXF-CLI-1.2",
        project_name="fixture-project",
        input_manifest_sha256="f" * 64,
        input_count=1,
        classified_count=1,
        review_required_count=0,
        unreadable_count=0,
        type_counts_json={"BH": 1},
    )
    db.add(run)
    db.flush()
    db.add(DxfClassificationItem(
        run=run,
        source_file_id=output.id,
        output_file_id=output.id,
        source_name=output.original_name,
        output_name=output.original_name,
        output_directory="fixture_BH_dxf",
        disposition="classified",
        part_type="BH",
        profile_raw="BH500*300*12*20",
        profile_normalized="BH500*300*12*20",
        type_source="catalog",
        group_key=group_key,
        next_stage_eligible=True,
        diagnostics_json=[],
        evidence_json={},
    ))
    db.flush()
    return run, output


def _setup(client, storage, *, seed=True):
    admin_headers = workflow_test_api.admin_headers(client)
    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=admin_headers,
        json={"code": f"CLS-{uuid4().hex[:6]}", "name": "单文件下载测试项目"},
    )
    assert response.status_code == 201, response.text
    workflow_id = response.json()["data"]["workflow"]["id"]
    output_id = None
    if seed:
        with open_test_session() as db:
            workflow = db.get(WorkflowRun, workflow_id)
            assert workflow is not None
            _, output = _seed_run(db, storage, workflow=workflow)
            output_id = output.id
            db.commit()
    return admin_headers, workflow_id, output_id


def _single_file_url(workflow_id, group_key="type:BH", output_name="A_拆板前.dxf"):
    return (
        f"/api/v1/workflows/{workflow_id}/dxf-classification/groups/"
        f"{quote(group_key, safe='')}/files/{quote(output_name, safe='')}/download"
    )


def test_single_file_download_streams_exact_bytes_with_headers(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, output_id = _setup(client, storage)
        response = client.get(_single_file_url(workflow_id), headers=headers)
        assert response.status_code == 200, response.text
        assert response.content == b"dxf bytes"
        assert "attachment" in response.headers["content-disposition"]
        assert "A_%E6%8B%86%E6%9D%BF%E5%89%8D.dxf" in response.headers["content-disposition"]
        with open_test_session() as db:
            transfer = db.scalar(
                select(FileTransfer).where(
                    FileTransfer.operation == "dxf_class_single_file",
                    FileTransfer.file_id == output_id,
                )
            )
            assert transfer is not None and transfer.status == "succeeded"


def test_single_file_download_404_when_run_missing(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _ = _setup(client, storage, seed=False)
        response = client.get(_single_file_url(workflow_id), headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLASSIFICATION_RUN_NOT_FOUND"


def test_single_file_download_404_when_item_not_matching(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _ = _setup(client, storage)
        response = client.get(
            _single_file_url(workflow_id, output_name="UNKNOWN.dxf"),
            headers=headers,
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLASSIFICATION_FILE_NOT_FOUND"


def test_single_file_download_409_when_output_is_not_dxf(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _ = _setup(client, storage)
        with open_test_session() as db:
            workflow = db.get(WorkflowRun, workflow_id)
            assert workflow is not None
            _seed_run(
                db,
                storage,
                workflow=workflow,
                output_name="A_拆板前.dwg",
            )
            db.commit()
        response = client.get(
            _single_file_url(workflow_id, output_name="A_拆板前.dwg"),
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CLASSIFICATION_OUTPUT_MISSING"


def test_single_file_download_403_for_non_member(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        admin_headers, workflow_id, _ = _setup(client, storage)
        _, engineer_headers = workflow_test_api.create_engineer_user(
            client,
            admin_headers,
        )
        response = client.get(_single_file_url(workflow_id), headers=engineer_headers)
        assert response.status_code == 403
