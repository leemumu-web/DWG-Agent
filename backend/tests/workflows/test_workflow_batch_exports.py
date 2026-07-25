from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.modules.cad_processing.interface import preview_batch_name
from app.modules.dxf_classification.interface import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.dxf_splitting.interface import DxfSplitItem, DxfSplitRun
from app.modules.files.interface import (
    FileTransfer,
    StoredFile,
    TransferSpec,
    prepare_transfer_in_transaction,
    settle_stream,
)
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.batch_exports import track_export_stream
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowBatchExport,
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRun,
)
from app.platform.storage.base import StorageError
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api
from tests.support.database import get_test_session_factory, open_test_session


def _register_object(
    db,
    storage: LocalFileStorage,
    *,
    owner_id: int,
    name: str,
    payload: bytes,
    batch_name: str | None = None,
) -> StoredFile:
    row = StoredFile(
        bucket="workflow-export-test",
        storage_key=f"objects/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=Path(name).suffix.lower(),
        content_type="application/octet-stream",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        batch_name=batch_name,
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


def _build_exportable_workflow(
    db,
    storage: LocalFileStorage,
    *,
    workflow_id: int,
) -> tuple[dict[str, StoredFile], list[StoredFile]]:
    workflow = db.get(WorkflowRun, workflow_id)
    assert workflow is not None
    owner = db.get(User, workflow.created_by)
    assert owner is not None

    payloads = {
        "classified_dxf": ("A_拆板前.dxf", b"classified dxf bytes"),
        "processed_dxf": ("A_正常拆板.dxf", b"processed dxf bytes"),
        "source_excel": ("工程数据.xlsx", b"source excel bytes"),
        "stage1_excel": ("工程数据_阶段一.xlsx", b"stage1 excel bytes"),
    }
    files = {
        category: _register_object(
            db,
            storage,
            owner_id=owner.id,
            name=name,
            payload=payload,
        )
        for category, (name, payload) in payloads.items()
    }

    batch = WorkflowInputBatch(
        workflow=workflow,
        project_id=workflow.project_id,
        created_by=owner.id,
        status="frozen",
        version=1,
        manifest_sha256="f" * 64,
    )
    db.add(batch)
    db.flush()
    source_excel = files["source_excel"]
    db.add(
        WorkflowInputItem(
            input_batch_id=batch.id,
            file_id=source_excel.id,
            role="source_excel",
            original_name=source_excel.original_name,
            normalized_stem=Path(source_excel.original_name).stem,
            status="validated",
            validation_json={"status": "passed"},
            validation_contract_version=1,
            validated_sha256=source_excel.sha256,
        )
    )

    classification_job = Job(
        project_id=workflow.project_id,
        created_by=owner.id,
        task_type="classify_steel_dxf",
        pipeline="steel_dxf_classifier",
        status="succeeded",
        attempt=1,
        progress=100,
        precision_level="normal",
        params_json={"workflow_id": workflow.id},
    )
    db.add(classification_job)
    db.flush()
    classification_run = DxfClassificationRun(
        workflow_run_id=workflow.id,
        project_id=workflow.project_id,
        job_id=classification_job.id,
        job_attempt=1,
        status="completed",
        classifier_version="1.2.0",
        project_name="export-test",
        input_manifest_sha256="c" * 64,
        input_count=1,
        classified_count=1,
        review_required_count=0,
        unreadable_count=0,
        type_counts_json={"BH": 1},
    )
    db.add(classification_run)
    db.flush()
    classified = files["classified_dxf"]
    classification_item = DxfClassificationItem(
        run_id=classification_run.id,
        source_file_id=classified.id,
        output_file_id=classified.id,
        source_name=classified.original_name,
        output_name=classified.original_name,
        output_directory="export-test_BH_dxf",
        disposition="classified",
        part_type="BH",
        type_source="catalog",
        group_key="type:BH",
        next_stage_eligible=True,
        diagnostics_json=[],
        evidence_json={},
    )
    db.add(classification_item)
    db.flush()

    split_job = Job(
        project_id=workflow.project_id,
        created_by=owner.id,
        task_type="split_steel_dxf",
        pipeline="steel_dxf_split",
        status="succeeded",
        attempt=1,
        progress=100,
        precision_level="normal",
        params_json={"workflow_id": workflow.id},
    )
    db.add(split_job)
    db.flush()
    split_run = DxfSplitRun(
        workflow_run_id=workflow.id,
        project_id=workflow.project_id,
        classification_run_id=classification_run.id,
        job_id=split_job.id,
        job_attempt=1,
        status="completed",
        splitter_version="1.5.2",
        input_manifest_sha256="d" * 64,
        input_count=1,
        processed_count=1,
        failed_count=0,
        auto_accepted_count=1,
        manual_review_count=0,
        source_contracts_json={"BH": "fixture"},
    )
    db.add(split_run)
    db.flush()
    processed = files["processed_dxf"]
    db.add(
        DxfSplitItem(
            run_id=split_run.id,
            classification_item_id=classification_item.id,
            source_file_id=classified.id,
            source_name=classified.original_name,
            classification_disposition="classified",
            classification_part_type="BH",
            type_resolution="classifier_confirmed",
            part_type="BH",
            family="BH",
            source_contract_id="fixture",
            automation_route="auto_accepted",
            disposition="auto_accepted",
            normal_dxf_file_id=processed.id,
            diagnostics_json=[],
            validation_json={"status": "passed"},
        )
    )

    stage1_job = Job(
        project_id=workflow.project_id,
        created_by=owner.id,
        task_type="process_excel_final",
        pipeline="excel_final",
        status="succeeded",
        attempt=1,
        progress=100,
        precision_level="normal",
        params_json={"workflow_id": workflow.id},
    )
    db.add(stage1_job)
    db.flush()
    stage1 = files["stage1_excel"]
    result = AnalysisResult(
        job_id=stage1_job.id,
        result_type="process_excel_final",
        result_file_id=stage1.id,
        status="succeeded",
    )
    db.add(result)
    db.flush()

    stages = {stage.stage_code: stage for stage in workflow.stages}
    stages["source_intake"].status = "succeeded"
    stages["dxf_classification"].status = "succeeded"
    stages["dxf_classification"].job_id = classification_job.id
    stages["dxf_classification"].job_attempt = classification_job.attempt
    stages["drawing_processing"].status = "succeeded"
    stages["drawing_processing"].job_id = split_job.id
    stages["drawing_processing"].job_attempt = split_job.attempt
    stages["excel_stage1"].status = "succeeded"
    stages["excel_stage1"].job_id = stage1_job.id
    stages["excel_stage1"].job_attempt = stage1_job.attempt
    stages["excel_stage2"].status = "waiting_input"
    workflow.current_stage = "excel_stage2"
    workflow.status = "waiting_input"

    for stage_code, artifact_type, file_id, result_id in (
        ("source_intake", "source_excel", source_excel.id, None),
        ("dxf_classification", "classified_dxf", classified.id, None),
        ("drawing_processing", "processed_dxf", processed.id, None),
        ("excel_stage1", "stage1_excel", stage1.id, result.id),
    ):
        db.add(
            WorkflowArtifact(
                workflow_run_id=workflow.id,
                stage_run_id=stages[stage_code].id,
                artifact_type=artifact_type,
                file_id=file_id,
                result_id=result_id,
                version=1,
                metadata_json={},
            )
        )

    previews = []
    for source in (classified, processed):
        preview = _register_object(
            db,
            storage,
            owner_id=owner.id,
            name=f"{source.id}.svg",
            payload=f"<svg>{source.id}</svg>".encode(),
            batch_name=preview_batch_name(source),
        )
        previews.append(preview)
    db.commit()
    return files, previews


def _setup(client, storage: LocalFileStorage):
    admin_headers = workflow_test_api.admin_headers(client)
    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=admin_headers,
        json={
            "code": f"EXPORT-{uuid4().hex[:6]}",
            "name": "分批导出测试项目",
        },
    )
    assert response.status_code == 201, response.text
    workflow_id = response.json()["data"]["workflow"]["id"]
    with open_test_session() as db:
        files, previews = _build_exportable_workflow(
            db,
            storage,
            workflow_id=workflow_id,
        )
        file_ids = {category: row.id for category, row in files.items()}
        preview_ids = [row.id for row in previews]
        object_locations = [(row.bucket, row.storage_key) for row in [*files.values(), *previews]]
    return admin_headers, workflow_id, file_ids, preview_ids, object_locations


def test_batch_export_streams_exact_names_then_requires_explicit_purge(
    monkeypatch,
    tmp_path,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, file_ids, preview_ids, object_locations = _setup(
            client,
            storage,
        )

        preview = client.get(
            f"/api/v1/workflows/{workflow_id}/batch-exports/preview",
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert [
            (item["key"], item["label"], item["file_count"])
            for item in preview.json()["data"]["categories"]
        ] == [
            ("classified_dxf", "原 DXF", 1),
            ("processed_dxf", "正常拆板 DXF", 1),
            ("source_excel", "原 Excel", 1),
            ("stage1_excel", "产出 Excel", 1),
        ]

        created = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports",
            headers=headers,
            json={
                "categories": [
                    "classified_dxf",
                    "processed_dxf",
                    "source_excel",
                    "stage1_excel",
                ]
            },
        )
        assert created.status_code == 201, created.text
        export = created.json()["data"]
        assert export["status"] == "prepared"
        assert export["file_count"] == 4
        assert export["download_url"]

        # The native-browser URL is capability-cookie authenticated; it does not
        # need to copy the bearer token into a query parameter or browser Blob.
        downloaded = client.get(export["download_url"])
        assert downloaded.status_code == 200, downloaded.text
        assert "content-length" not in downloaded.headers
        with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
            assert archive.namelist() == [
                "原DXF/A_拆板前.dxf",
                "正常拆板DXF/A_正常拆板.dxf",
                "原Excel/工程数据.xlsx",
                "产出Excel/工程数据_阶段一.xlsx",
            ]
            assert archive.read("原DXF/A_拆板前.dxf") == b"classified dxf bytes"
            assert archive.read("正常拆板DXF/A_正常拆板.dxf") == b"processed dxf bytes"
            assert archive.read("原Excel/工程数据.xlsx") == b"source excel bytes"
            assert archive.read("产出Excel/工程数据_阶段一.xlsx") == b"stage1 excel bytes"

        status = client.get(
            f"/api/v1/workflows/{workflow_id}/batch-exports/{export['export_uid']}",
            headers=headers,
        )
        assert status.status_code == 200, status.text
        assert status.json()["data"]["status"] == "downloaded"
        assert all(storage.object_exists(*location) for location in object_locations)

        purged = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports/{export['export_uid']}/purge",
            headers=headers,
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["data"]["status"] == "purged"
        assert purged.json()["data"]["purged_file_count"] == 6
        assert all(not storage.object_exists(*location) for location in object_locations)

        with open_test_session() as db:
            rows = list(
                db.scalars(
                    select(StoredFile).where(StoredFile.id.in_([*file_ids.values(), *preview_ids]))
                ).all()
            )
            assert len(rows) == 6
            assert all(row.status == "deleted" and row.purged_at is not None for row in rows)
            assert {row.original_name for row in rows if row.id in file_ids.values()} == {
                "A_拆板前.dxf",
                "A_正常拆板.dxf",
                "工程数据.xlsx",
                "工程数据_阶段一.xlsx",
            }
            assert (
                db.scalar(
                    select(WorkflowArtifact.id).where(
                        WorkflowArtifact.workflow_run_id == workflow_id,
                        WorkflowArtifact.file_id.in_(file_ids.values()),
                    )
                )
                is None
            )
            export_row = db.scalar(
                select(WorkflowBatchExport).where(
                    WorkflowBatchExport.export_uid == export["export_uid"]
                )
            )
            assert export_row is not None
            assert export_row.manifest_json == []
            assert export_row.token_digest is None
            assert (
                db.scalar(
                    select(FileTransfer.id).where(
                        FileTransfer.operation == "workflow_batch_export",
                        FileTransfer.batch_ref == export["export_uid"],
                        FileTransfer.status == "succeeded",
                    )
                )
                is not None
            )


def test_batch_export_purge_is_rejected_before_download(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _, _, object_locations = _setup(client, storage)
        created = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports",
            headers=headers,
            json={"categories": ["classified_dxf"]},
        )
        assert created.status_code == 201, created.text
        export_uid = created.json()["data"]["export_uid"]

        rejected = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports/{export_uid}/purge",
            headers=headers,
        )

        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "WORKFLOW_EXPORT_NOT_DOWNLOADED"
        assert all(storage.object_exists(*location) for location in object_locations)


def test_batch_export_purge_is_rejected_while_a_stage_is_running(
    monkeypatch,
    tmp_path,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _, _, object_locations = _setup(client, storage)
        created = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports",
            headers=headers,
            json={"categories": ["classified_dxf"]},
        )
        export = created.json()["data"]
        downloaded = client.get(export["download_url"])
        assert downloaded.status_code == 200, downloaded.text

        with open_test_session() as db:
            workflow = db.get(WorkflowRun, workflow_id)
            assert workflow is not None
            next(
                stage for stage in workflow.stages if stage.stage_code == "excel_stage2"
            ).status = "running"
            db.commit()

        rejected = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports/{export['export_uid']}/purge",
            headers=headers,
        )

        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "WORKFLOW_EXPORT_PURGE_ACTIVE_STAGE"
        assert all(storage.object_exists(*location) for location in object_locations)


def test_batch_export_rejects_name_conflicts_instead_of_renaming(
    monkeypatch,
    tmp_path,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _, _, _ = _setup(client, storage)

        with open_test_session() as db:
            workflow = db.get(WorkflowRun, workflow_id)
            assert workflow is not None
            run = db.scalar(
                select(DxfClassificationRun).where(
                    DxfClassificationRun.workflow_run_id == workflow_id
                )
            )
            assert run is not None
            duplicate = _register_object(
                db,
                storage,
                owner_id=workflow.created_by,
                name="A_拆板前.dxf",
                payload=b"second classified dxf",
            )
            db.add(
                DxfClassificationItem(
                    run_id=run.id,
                    source_file_id=duplicate.id,
                    output_file_id=duplicate.id,
                    source_name=duplicate.original_name,
                    output_name=duplicate.original_name,
                    output_directory="export-test_BH_dxf",
                    disposition="classified",
                    part_type="BH",
                    type_source="catalog",
                    group_key="type:BH",
                    next_stage_eligible=True,
                    diagnostics_json=[],
                    evidence_json={},
                )
            )
            location = (duplicate.bucket, duplicate.storage_key)
            db.commit()

        rejected = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports",
            headers=headers,
            json={"categories": ["classified_dxf"]},
        )

        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "WORKFLOW_EXPORT_FILENAME_CONFLICT"
        assert storage.object_exists(*location)


def test_interrupted_export_marks_failure_and_retains_every_object(
    monkeypatch,
    tmp_path,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers, workflow_id, _, _, object_locations = _setup(client, storage)
        created = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports",
            headers=headers,
            json={"categories": ["classified_dxf"]},
        )
        assert created.status_code == 201, created.text
        export_uid = created.json()["data"]["export_uid"]

        with open_test_session() as db:
            row = db.scalar(
                select(WorkflowBatchExport).where(WorkflowBatchExport.export_uid == export_uid)
            )
            assert row is not None
            row.status = "downloading"
            transfer = prepare_transfer_in_transaction(
                db,
                TransferSpec(
                    direction="outbound",
                    operation="workflow_batch_export",
                    actor_user_id=row.created_by,
                    request_id="interrupted-export",
                    idempotency_key="interrupted-export",
                    batch_ref=row.export_uid,
                    original_name="interrupted.zip",
                ),
            )
            db.commit()

        def interrupted_chunks():
            yield b"partial zip"
            raise StorageError("connection interrupted")

        factory = get_test_session_factory()
        with pytest.raises(StorageError):
            list(
                track_export_stream(
                    factory,
                    export_uid,
                    settle_stream(
                        factory,
                        transfer.transfer_uid,
                        interrupted_chunks(),
                    ),
                )
            )

        with open_test_session() as db:
            row = db.scalar(
                select(WorkflowBatchExport).where(WorkflowBatchExport.export_uid == export_uid)
            )
            transfer_row = db.scalar(
                select(FileTransfer).where(FileTransfer.transfer_uid == transfer.transfer_uid)
            )
            assert row is not None and row.status == "download_failed"
            assert transfer_row is not None and transfer_row.status == "failed"

        rejected = client.post(
            f"/api/v1/workflows/{workflow_id}/batch-exports/{export_uid}/purge",
            headers=headers,
        )
        assert rejected.status_code == 409, rejected.text
        assert all(storage.object_exists(*location) for location in object_locations)
