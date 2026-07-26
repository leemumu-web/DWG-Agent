from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime
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
from app.modules.dxf_splitting.interface import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.files.interface import FileTransfer, StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRetentionExport,
    WorkflowRun,
    WorkflowStageRun,
)
from app.modules.workflows.retention import (
    build_retention_scope,
    create_retention_export,
    execute_retention_purge,
)
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api
from tests.support.database import get_test_session_factory, open_test_session


def _file(db, storage, owner_id: int, name: str, payload: bytes, *, batch_name=None):
    row = StoredFile(
        bucket="retention-test",
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


def _workflow(db, *, status: str = "succeeded"):
    user = User(
        username=f"retention-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Retention Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"RET-{uuid4().hex[:8]}",
        name="Retention Project",
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
    workflow = WorkflowRun(
        project_id=project.id,
        created_by=user.id,
        name="Retention Workflow",
        workflow_type="linux_production",
        status=status,
        progress=100 if status == "succeeded" else 0,
        finished_at=datetime.now(UTC) if status in {"succeeded", "failed", "cancelled"} else None,
    )
    db.add(workflow)
    db.flush()
    stage = WorkflowStageRun(
        workflow_run_id=workflow.id,
        stage_code="excel_stage1",
        name="Excel Stage 1",
        sequence=1,
        status="succeeded" if status == "succeeded" else "pending",
        progress=100 if status == "succeeded" else 0,
    )
    db.add(stage)
    db.flush()
    return user, project, workflow, stage


def _complete_scope(db, storage):
    user, project, workflow, stage = _workflow(db)
    source = _file(db, storage, user.id, "source.xlsx", b"source")
    derived = _file(db, storage, user.id, "source.dxf", b"derived")
    artifact = _file(db, storage, user.id, "part.xlsx", b"artifact")
    history = _file(db, storage, user.id, "old-part.xlsx", b"history")
    classified = _file(db, storage, user.id, "classified.dxf", b"classified")
    classification_report = _file(db, storage, user.id, "classification.json", b"class-report")
    classification_manifest = _file(db, storage, user.id, "classification-manifest.json", b"class-manifest")
    split_normal = _file(db, storage, user.id, "normal.dxf", b"normal")
    split_allowance = _file(db, storage, user.id, "allowance.dxf", b"allowance")
    split_candidate = _file(db, storage, user.id, "candidate.dxf", b"candidate")
    split_report = _file(db, storage, user.id, "split.json", b"split-report")
    split_ledger = _file(db, storage, user.id, "ledger.json", b"ledger")
    split_manifest = _file(db, storage, user.id, "split-manifest.json", b"split-manifest")
    validation_report = _file(db, storage, user.id, "validation.json", b"validation")

    batch = WorkflowInputBatch(
        workflow_run_id=workflow.id,
        project_id=project.id,
        created_by=user.id,
        status="frozen",
        version=1,
        manifest_sha256="a" * 64,
    )
    db.add(batch)
    db.flush()
    db.add(
        WorkflowInputItem(
            input_batch_id=batch.id,
            file_id=source.id,
            derived_dxf_file_id=derived.id,
            role="source_excel",
            original_name=source.original_name,
            normalized_stem="source",
            status="validated",
        )
    )

    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="process_excel_final",
        precision_level="normal",
        status="succeeded",
        attempt=2,
        params_json={"workflow_id": workflow.id},
    )
    db.add(job)
    db.flush()
    stage.job_id = job.id
    stage.job_attempt = 2
    db.add_all(
        [
            AnalysisResult(
                job_id=job.id,
                result_type="process_excel_final",
                result_file_id=history.id,
                status="succeeded",
            ),
            AnalysisResult(
                job_id=job.id,
                result_type="process_excel_final",
                result_file_id=artifact.id,
                status="succeeded",
            ),
            WorkflowArtifact(
                workflow_run_id=workflow.id,
                stage_run_id=stage.id,
                artifact_type="stage1_excel",
                file_id=artifact.id,
                version=1,
            ),
            # The derived input is also an artifact and must still appear once.
            WorkflowArtifact(
                workflow_run_id=workflow.id,
                stage_run_id=stage.id,
                artifact_type="derived_dxf",
                file_id=derived.id,
                version=1,
            ),
        ]
    )

    classification_job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="classify_steel_dxf",
        precision_level="normal",
        status="succeeded",
        attempt=1,
    )
    db.add(classification_job)
    db.flush()
    classification_run = DxfClassificationRun(
        workflow_run_id=workflow.id,
        project_id=project.id,
        job_id=classification_job.id,
        job_attempt=1,
        status="completed",
        classifier_version="1.2.0",
        project_name="retention",
        input_manifest_sha256="b" * 64,
        report_file_id=classification_report.id,
        manifest_file_id=classification_manifest.id,
    )
    db.add(classification_run)
    db.flush()
    classification_item = DxfClassificationItem(
        run_id=classification_run.id,
        source_file_id=derived.id,
        output_file_id=classified.id,
        source_name=derived.original_name,
        output_name=classified.original_name,
        output_directory="BH",
        disposition="classified",
        group_key="type:BH",
        next_stage_eligible=True,
    )
    db.add(classification_item)
    db.flush()

    split_job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="split_steel_dxf",
        precision_level="normal",
        status="succeeded",
        attempt=1,
    )
    db.add(split_job)
    db.flush()
    split_run = DxfSplitRun(
        workflow_run_id=workflow.id,
        project_id=project.id,
        classification_run_id=classification_run.id,
        job_id=split_job.id,
        job_attempt=1,
        status="completed",
        splitter_version="1.5.2",
        input_manifest_sha256="c" * 64,
        bh_split_ledger_file_id=split_ledger.id,
        split_manifest_file_id=split_manifest.id,
        validation_report_file_id=validation_report.id,
    )
    db.add(split_run)
    db.flush()
    split_item = DxfSplitItem(
        run_id=split_run.id,
        classification_item_id=classification_item.id,
        source_file_id=classified.id,
        source_name=classified.original_name,
        classification_disposition="classified",
        type_resolution="classifier_confirmed",
        part_type="BH",
        automation_route="auto_accepted",
        disposition="auto_accepted",
        normal_dxf_file_id=split_normal.id,
        weld_allowance_dxf_file_id=split_allowance.id,
        split_report_file_id=split_report.id,
        candidate_normal_dxf_file_id=split_candidate.id,
    )
    db.add(split_item)
    db.flush()
    db.add(
        DxfSplitReviewDecision(
            split_item_id=split_item.id,
            decision="accept_candidate",
            final_normal_dxf_file_id=split_candidate.id,
            final_weld_allowance_dxf_file_id=split_allowance.id,
            comment="accepted",
            decided_by=user.id,
            decided_at=datetime.now(UTC),
        )
    )
    preview = _file(
        db,
        storage,
        user.id,
        "classified.svg",
        b"<svg/>",
        batch_name=preview_batch_name(classified),
    )
    db.flush()
    expected = {
        row.id
        for row in (
            source,
            derived,
            artifact,
            history,
            classified,
            classification_report,
            classification_manifest,
            split_normal,
            split_allowance,
            split_candidate,
            split_report,
            split_ledger,
            split_manifest,
            validation_report,
        )
    }
    return user, project, workflow, expected, preview


def test_retention_scope_collects_every_relationship_and_deduplicates(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    _user, _project, workflow, expected, preview = _complete_scope(db, storage)

    scope = build_retention_scope(db, workflow)

    assert {item["file_id"] for item in scope.manifest} == expected
    assert len(scope.manifest) == len(expected)
    assert scope.preview_file_ids == (preview.id,)
    assert scope.preview_cache_count == 1
    assert scope.source_size_bytes == sum(item["size_bytes"] for item in scope.manifest)
    assert scope.reclaimable_size_bytes == scope.source_size_bytes + preview.size_bytes
    assert scope.blockers == ()
    assert scope.manifest_sha256 == hashlib.sha256(scope.manifest_bytes).hexdigest()
    source_entry = next(item for item in scope.manifest if item["original_name"] == "source.xlsx")
    assert source_entry["archive_path"].startswith("输入/source_excel/")
    artifact_entry = next(item for item in scope.manifest if item["original_name"] == "part.xlsx")
    assert artifact_entry["archive_path"].startswith("阶段产物/excel_stage1/stage1_excel/")
    history_entry = next(item for item in scope.manifest if item["original_name"] == "old-part.xlsx")
    assert history_entry["archive_path"].startswith("其他结果/")


@pytest.mark.parametrize("status", ["draft", "waiting_input", "running", "waiting_review"])
def test_retention_scope_blocks_non_terminal_workflows(db, tmp_path, status):
    _user, _project, workflow, _stage = _workflow(db, status=status)

    scope = build_retention_scope(db, workflow)

    assert "WORKFLOW_RETENTION_NOT_TERMINAL" in {item["code"] for item in scope.blockers}


def test_retention_scope_blocks_active_stage_and_job_even_if_status_drifted(db, tmp_path):
    user, project, workflow, stage = _workflow(db)
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="process_excel_final",
        precision_level="normal",
        status="running",
        attempt=1,
    )
    db.add(job)
    db.flush()
    stage.status = "running"
    stage.job_id = job.id
    stage.job_attempt = 1

    scope = build_retention_scope(db, workflow)

    codes = {item["code"] for item in scope.blockers}
    assert "WORKFLOW_RETENTION_ACTIVE_STAGE" in codes
    assert "WORKFLOW_RETENTION_ACTIVE_JOB" in codes


def test_retention_scope_blocks_files_shared_with_another_workflow(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    user, _project, workflow, expected, _preview = _complete_scope(db, storage)
    shared_file_id = min(expected)
    _other_user, other_project, other, _stage = _workflow(db)
    batch = WorkflowInputBatch(
        workflow_run_id=other.id,
        project_id=other_project.id,
        created_by=other.created_by,
        status="frozen",
        version=1,
        manifest_sha256="d" * 64,
    )
    db.add(batch)
    db.flush()
    shared = db.get(StoredFile, shared_file_id)
    assert shared is not None
    db.add(
        WorkflowInputItem(
            input_batch_id=batch.id,
            file_id=shared.id,
            role="source_excel",
            original_name=shared.original_name,
            normalized_stem="shared",
            status="validated",
        )
    )
    db.flush()

    scope = build_retention_scope(db, workflow)

    conflict = next(
        item for item in scope.blockers if item["code"] == "WORKFLOW_RETENTION_SHARED_FILES"
    )
    assert conflict["details"]["shared_file_count"] == 1
    assert conflict["details"]["file_ids"] == [shared_file_id]


def test_create_retention_export_checks_registered_object_size(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    user, _project, workflow, expected, _preview = _complete_scope(db, storage)
    damaged = db.get(StoredFile, min(expected))
    assert damaged is not None
    path = storage.local_path(damaged.bucket, damaged.storage_key)
    assert path is not None
    path.write_bytes(b"damaged-size")

    with pytest.raises(AppHTTPException) as caught:
        create_retention_export(
            db,
            workflow,
            actor_user_id=user.id,
            storage=storage,
        )

    assert caught.value.detail["code"] == "WORKFLOW_RETENTION_OBJECT_MISMATCH"


def test_create_retention_export_persists_stable_manifest_and_token(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    user, _project, workflow, expected, _preview = _complete_scope(db, storage)

    row, token = create_retention_export(
        db,
        workflow,
        actor_user_id=user.id,
        storage=storage,
    )

    assert row.status == "prepared"
    assert row.file_count == len(expected)
    assert row.manifest_sha256 == hashlib.sha256(
        build_retention_scope(db, workflow).manifest_bytes
    ).hexdigest()
    assert row.token_digest == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_expires_at > datetime.now(UTC)


def _create_and_download_retention(client, headers, storage):
    with open_test_session() as db:
        _user, _project, workflow, expected, preview = _complete_scope(db, storage)
        workflow_id = workflow.id
        preview_id = preview.id
        db.commit()

    created = client.post(
        f"/api/v1/workflows/{workflow_id}/retention-exports",
        headers=headers,
    )
    assert created.status_code == 201, created.text
    export = created.json()["data"]
    downloaded = client.get(export["download_url"])
    assert downloaded.status_code == 200, downloaded.text
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        assert len(archive.namelist()) == len(expected)
    return workflow_id, export, expected, preview_id


def test_retention_enqueue_failure_keeps_every_object(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers = workflow_test_api.admin_headers(client)
        workflow_id, export, expected, preview_id = _create_and_download_retention(
            client,
            headers,
            storage,
        )

        def _fail_enqueue(*_args, **_kwargs):
            raise RuntimeError("maintenance queue unavailable")

        monkeypatch.setattr(
            "app.modules.workflows.retention_tasks.purge_workflow_retention_task.apply_async",
            _fail_enqueue,
        )
        response = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports/"
            f"{export['export_uid']}/purge",
            headers=headers,
            json={"confirmation": f"DELETE WORKFLOW {workflow_id}"},
        )

        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "WORKFLOW_RETENTION_ENQUEUE_FAILED"
        with open_test_session() as db:
            row = db.scalar(
                select(WorkflowRetentionExport).where(
                    WorkflowRetentionExport.export_uid == export["export_uid"]
                )
            )
            assert row is not None
            assert row.status == "purge_failed"
            assert row.error_code == "WORKFLOW_RETENTION_ENQUEUE_FAILED"
            files = list(
                db.scalars(
                    select(StoredFile).where(StoredFile.id.in_([*expected, preview_id]))
                ).all()
            )
            assert all(item.status == "available" and item.purged_at is None for item in files)
            assert all(storage.object_exists(item.bucket, item.storage_key) for item in files)


def test_retention_partial_delete_is_compensatable_and_retryable(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers = workflow_test_api.admin_headers(client)
        workflow_id, export, expected, preview_id = _create_and_download_retention(
            client,
            headers,
            storage,
        )

        class _Queued:
            id = "retention-partial-delete"

        monkeypatch.setattr(
            "app.modules.workflows.retention_tasks.purge_workflow_retention_task.apply_async",
            lambda **_kwargs: _Queued(),
        )
        queued = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports/"
            f"{export['export_uid']}/purge",
            headers=headers,
            json={"confirmation": f"DELETE WORKFLOW {workflow_id}"},
        )
        assert queued.status_code == 202, queued.text

    class _FailOnSecondDelete:
        def __init__(self, delegate):
            self.delegate = delegate
            self.delete_count = 0

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def delete_object(self, bucket, storage_key):
            self.delete_count += 1
            if self.delete_count == 2:
                raise RuntimeError("injected partial deletion")
            self.delegate.delete_object(bucket, storage_key)

    with pytest.raises(AppHTTPException) as caught:
        execute_retention_purge(
            export["export_uid"],
            factory=get_test_session_factory(),
            storage=_FailOnSecondDelete(storage),
        )
    assert caught.value.detail["code"] == "WORKFLOW_RETENTION_PURGE_FAILED"

    with open_test_session() as db:
        row = db.scalar(
            select(WorkflowRetentionExport).where(
                WorkflowRetentionExport.export_uid == export["export_uid"]
            )
        )
        assert row is not None
        assert row.status == "purge_failed"
        assert row.error_code == "WORKFLOW_RETENTION_PURGE_PARTIAL"
        transfer = db.scalar(
            select(FileTransfer)
            .where(
                FileTransfer.operation == "workflow_retention_purge",
                FileTransfer.batch_ref == export["export_uid"],
            )
            .order_by(FileTransfer.id.desc())
        )
        assert transfer is not None
        assert transfer.status == "compensation_required"
        assert 0 < transfer.transferred_bytes < transfer.expected_bytes
        files = list(
            db.scalars(
                select(StoredFile).where(StoredFile.id.in_([*expected, preview_id]))
            ).all()
        )
        assert all(item.status == "available" and item.purged_at is None for item in files)

    with workflow_test_api.client() as client:
        headers = workflow_test_api.admin_headers(client)
        retry_queued = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports/"
            f"{export['export_uid']}/purge",
            headers=headers,
            json={"confirmation": f"DELETE WORKFLOW {workflow_id}"},
        )
        assert retry_queued.status_code == 202, retry_queued.text

    retried = execute_retention_purge(
        export["export_uid"],
        factory=get_test_session_factory(),
        storage=storage,
    )
    assert retried["status"] == "purged"
    assert retried["purged_file_count"] == len(expected) + 1


def test_retention_api_downloads_complete_backup_then_queues_async_purge(
    monkeypatch,
    tmp_path,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    with workflow_test_api.client() as client:
        headers = workflow_test_api.admin_headers(client)
        with open_test_session() as db:
            _user, _project, workflow, expected, preview = _complete_scope(db, storage)
            workflow_id = workflow.id
            preview_id = preview.id
            db.commit()

        preview_response = client.get(
            f"/api/v1/workflows/{workflow_id}/retention-preview",
            headers=headers,
        )
        assert preview_response.status_code == 200, preview_response.text
        preview_data = preview_response.json()["data"]
        assert preview_data["blocked"] is False
        assert preview_data["file_count"] == len(expected)
        assert preview_data["preview_cache_count"] == 1

        created = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports",
            headers=headers,
        )
        assert created.status_code == 201, created.text
        export = created.json()["data"]
        assert export["status"] == "prepared"
        assert export["download_url"]

        latest = client.get(
            f"/api/v1/workflows/{workflow_id}/retention-exports/latest",
            headers=headers,
        )
        assert latest.status_code == 200, latest.text
        assert latest.json()["data"]["export_uid"] == export["export_uid"]

        early = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports/"
            f"{export['export_uid']}/purge",
            headers=headers,
            json={"confirmation": f"DELETE WORKFLOW {workflow_id}"},
        )
        assert early.status_code == 409, early.text
        assert early.json()["error"]["code"] == "WORKFLOW_RETENTION_NOT_DOWNLOADED"

        downloaded = client.get(export["download_url"])
        assert downloaded.status_code == 200, downloaded.text
        with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
            assert len(archive.namelist()) == len(expected)
            assert any(name.startswith("输入/source_excel/") for name in archive.namelist())
            assert any(name.startswith("阶段产物/excel_stage1/") for name in archive.namelist())
            assert any(name.startswith("其他结果/") for name in archive.namelist())

        wrong = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports/"
            f"{export['export_uid']}/purge",
            headers=headers,
            json={"confirmation": "DELETE"},
        )
        assert wrong.status_code == 409, wrong.text
        assert wrong.json()["error"]["code"] == "WORKFLOW_RETENTION_CONFIRMATION_INVALID"

        class _Queued:
            id = "retention-task-1"

        monkeypatch.setattr(
            "app.modules.workflows.retention_tasks.purge_workflow_retention_task.apply_async",
            lambda **_kwargs: _Queued(),
        )
        queued = client.post(
            f"/api/v1/workflows/{workflow_id}/retention-exports/"
            f"{export['export_uid']}/purge",
            headers=headers,
            json={"confirmation": f"DELETE WORKFLOW {workflow_id}"},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["data"]["status"] == "purge_queued"
        assert queued.json()["data"]["task_id"] == "retention-task-1"

        result = execute_retention_purge(
            export["export_uid"],
            factory=get_test_session_factory(),
            storage=storage,
        )
        assert result["status"] == "purged"
        assert result["purged_file_count"] == len(expected) + 1

        status_response = client.get(
            f"/api/v1/workflows/{workflow_id}/retention-exports/{export['export_uid']}",
            headers=headers,
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["data"]["status"] == "purged"

        with open_test_session() as db:
            assert db.get(WorkflowRun, workflow_id) is not None
            assert db.scalar(
                select(WorkflowInputItem.id)
                .join(WorkflowInputBatch)
                .where(WorkflowInputBatch.workflow_run_id == workflow_id)
            ) is not None
            rows = list(
                db.scalars(
                    select(StoredFile).where(StoredFile.id.in_([*expected, preview_id]))
                ).all()
            )
            assert len(rows) == len(expected) + 1
            assert all(row.status == "deleted" and row.purged_at for row in rows)
            assert db.scalar(
                select(WorkflowArtifact.id).where(
                    WorkflowArtifact.workflow_run_id == workflow_id
                )
            ) is None
