from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import openpyxl
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job, dispatch_committed_conversion_batch
from app.modules.projects.interface import Drawing, DrawingVersion, Project, ProjectMember
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.local import LocalFileStorage
from app.schemas.workflow_schema import WorkflowCreate
from app.services import workflow_input_service, workflow_service


def _workflow(db):
    user = User(
        username=f"input-owner-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Input Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"INPUT-{uuid4().hex[:6]}",
        name="Production Input",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, project_role="project_owner"))
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="Input freeze",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    db.flush()
    return user, project, workflow


def _file(db, name: str) -> StoredFile:
    stored = StoredFile(
        bucket="test",
        storage_key=f"inputs/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=f".{name.rsplit('.', 1)[-1].lower()}",
        content_type="application/octet-stream",
        size_bytes=2048,
        sha256=uuid4().hex + uuid4().hex,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def _stored_object(db, storage: LocalFileStorage, name: str, payload: bytes) -> StoredFile:
    bucket = "test-inputs"
    storage_key = f"inputs/{uuid4().hex}/{name}"
    storage.put_fileobj(bucket, storage_key, BytesIO(payload), length=len(payload))
    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=name,
        file_ext=f".{name.rsplit('.', 1)[-1].lower()}",
        content_type="application/octet-stream",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def _xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "构件编号"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_input_batch_model_has_one_batch_per_workflow_and_ordered_items(db):
    user, project, workflow = _workflow(db)
    first_dwg = _file(db, "B.dwg")
    second_dwg = _file(db, "A.dwg")
    excel = _file(db, "parts.xlsx")
    batch = WorkflowInputBatch(
        workflow_run_id=workflow.id,
        project_id=project.id,
        created_by=user.id,
        status="uploading",
        version=1,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            WorkflowInputItem(
                input_batch_id=batch.id,
                file_id=first_dwg.id,
                role="source_dwg",
                original_name=first_dwg.original_name,
                normalized_stem="b",
                status="uploaded",
            ),
            WorkflowInputItem(
                input_batch_id=batch.id,
                file_id=second_dwg.id,
                role="source_dwg",
                original_name=second_dwg.original_name,
                normalized_stem="a",
                status="uploaded",
            ),
            WorkflowInputItem(
                input_batch_id=batch.id,
                file_id=excel.id,
                role="source_excel",
                original_name=excel.original_name,
                normalized_stem="parts",
                status="uploaded",
            ),
        ]
    )
    db.flush()

    assert workflow.input_batch.id == batch.id
    assert [item.original_name for item in batch.items] == ["B.dwg", "A.dwg", "parts.xlsx"]

    db.add(
        WorkflowInputBatch(
            workflow_run_id=workflow.id,
            project_id=project.id,
            created_by=user.id,
            status="uploading",
            version=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_registers_multiple_real_dwgs_and_one_readable_excel(db, tmp_path, monkeypatch):
    user, _, workflow = _workflow(db)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(workflow_input_service, "get_storage_backend", lambda: storage)
    batch = workflow_input_service.create_input_batch(db, workflow, created_by=user.id)
    first = _stored_object(db, storage, " B  01.dwg", b"AC1027" + bytes(2048))
    second = _stored_object(db, storage, "A.dwg", b"AC1018" + bytes(2048))
    excel = _stored_object(db, storage, "parts.xlsx", _xlsx_bytes())

    first_item = workflow_input_service.register_input_file(db, batch, first)
    second_item = workflow_input_service.register_input_file(db, batch, second)
    excel_item = workflow_input_service.register_input_file(db, batch, excel)
    replay = workflow_input_service.register_input_file(db, batch, first)

    assert first_item.normalized_stem == "b 01"
    assert second_item.role == "source_dwg"
    assert excel_item.role == "source_excel"
    assert replay.id == first_item.id
    assert len(batch.items) == 3


def test_rejects_human_dxf_with_stable_error(db, tmp_path, monkeypatch):
    user, _, workflow = _workflow(db)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(workflow_input_service, "get_storage_backend", lambda: storage)
    batch = workflow_input_service.create_input_batch(db, workflow, created_by=user.id)
    dxf = _stored_object(db, storage, "manual.dxf", b"0\nSECTION\n0\nEOF\n")

    with pytest.raises(AppHTTPException) as error:
        workflow_input_service.register_input_file(db, batch, dxf)

    assert error.value.detail["code"] == "INPUT_DXF_NOT_ALLOWED"
    assert batch.items == []


def test_rejects_second_excel_without_changing_batch(db, tmp_path, monkeypatch):
    user, _, workflow = _workflow(db)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(workflow_input_service, "get_storage_backend", lambda: storage)
    batch = workflow_input_service.create_input_batch(db, workflow, created_by=user.id)
    first = _stored_object(db, storage, "parts.xlsx", _xlsx_bytes())
    second = _stored_object(db, storage, "other.xlsx", _xlsx_bytes())
    workflow_input_service.register_input_file(db, batch, first)

    with pytest.raises(AppHTTPException) as error:
        workflow_input_service.register_input_file(db, batch, second)

    assert error.value.detail["code"] == "INPUT_EXCEL_ALREADY_EXISTS"
    assert [item.file_id for item in batch.items] == [first.id]


def test_rejects_object_digest_mismatch(db, tmp_path, monkeypatch):
    user, _, workflow = _workflow(db)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(workflow_input_service, "get_storage_backend", lambda: storage)
    batch = workflow_input_service.create_input_batch(db, workflow, created_by=user.id)
    stored = _stored_object(db, storage, "source.dwg", b"AC1027" + bytes(2048))
    stored.sha256 = "0" * 64

    with pytest.raises(AppHTTPException) as error:
        workflow_input_service.register_input_file(db, batch, stored)

    assert error.value.detail["code"] == "INPUT_OBJECT_CHECKSUM_MISMATCH"


def test_rejects_fake_xlsx_container(db, tmp_path, monkeypatch):
    user, _, workflow = _workflow(db)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(workflow_input_service, "get_storage_backend", lambda: storage)
    batch = workflow_input_service.create_input_batch(db, workflow, created_by=user.id)
    fake = _stored_object(db, storage, "fake.xlsx", b"not an excel container")

    with pytest.raises(AppHTTPException) as error:
        workflow_input_service.register_input_file(db, batch, fake)

    assert error.value.detail["code"] == "INPUT_EXCEL_UNREADABLE"


def _registered_batch(db, tmp_path, monkeypatch, *, dwg_names=("A.dwg", "B.dwg")):
    user, project, workflow = _workflow(db)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(workflow_input_service, "get_storage_backend", lambda: storage)
    batch = workflow_input_service.create_input_batch(db, workflow, created_by=user.id)
    for index, name in enumerate(dwg_names):
        stored = _stored_object(
            db,
            storage,
            name,
            b"AC1027" + bytes([index + 1]) * 2048,
        )
        workflow_input_service.register_input_file(db, batch, stored)
    excel = _stored_object(db, storage, "parts.xlsx", _xlsx_bytes())
    workflow_input_service.register_input_file(db, batch, excel)
    return user, project, workflow, batch, storage


def test_conversion_jobs_are_project_bound_idempotent_and_retryable(db, tmp_path, monkeypatch):
    user, project, _, batch, _ = _registered_batch(db, tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_input_service.settings, "dxf_pipeline_enabled", True)

    first = workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)
    replay = workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)

    assert len(first.jobs) == 2
    assert first.dispatch == [(job.id, 1) for job in first.jobs]
    assert replay.dispatch == []
    assert [job.id for job in replay.jobs] == [job.id for job in first.jobs]
    assert all(job.project_id == project.id for job in first.jobs)
    assert all(job.task_type == "convert_dwg_to_dxf" for job in first.jobs)
    assert all(item.status == "converting" for item in batch.items if item.role == "source_dwg")

    first.jobs[0].status = "failed"
    db.flush()
    retried = workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)

    assert retried.jobs[0].attempt == 2
    assert retried.jobs[0].status == "queued"
    assert retried.dispatch == [(retried.jobs[0].id, 2)]


def test_conversion_feature_gate_is_enforced(db, tmp_path, monkeypatch):
    user, _, _, batch, _ = _registered_batch(db, tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_input_service.settings, "dxf_pipeline_enabled", False)

    with pytest.raises(AppHTTPException) as error:
        workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)

    assert error.value.detail["code"] == "DXF_PIPELINE_DISABLED"
    assert db.query(Job).count() == 0


def test_batch_dispatch_failure_marks_queued_attempt_retryable(db, monkeypatch):
    user, project, _ = _workflow(db)
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="convert_dwg_to_dxf",
        precision_level="normal",
        pipeline="dxf",
        status="queued",
        attempt=1,
        priority=0,
        progress=0,
        params_json={"file_id": 1},
    )
    db.add(job)
    db.commit()

    def fail_dispatch(_serialized):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.modules.cad_processing.tasks.convert_dwg_to_dxf_batch_task.delay",
        fail_dispatch,
    )
    with pytest.raises(AppHTTPException) as error:
        dispatch_committed_conversion_batch(
            task_type="convert_dwg_to_dxf", jobs=[(job.id, job.attempt)]
        )

    db.expire_all()
    failed = db.get(Job, job.id)
    assert error.value.detail["code"] == "JOB_ENQUEUE_FAILED"
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "JOB_ENQUEUE_FAILED"


def test_sync_pairs_only_successful_server_derived_dxf(db, tmp_path, monkeypatch):
    user, _, _, batch, storage = _registered_batch(
        db, tmp_path, monkeypatch, dwg_names=("Assembly 01.dwg",)
    )
    monkeypatch.setattr(workflow_input_service.settings, "dxf_pipeline_enabled", True)
    plan = workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)
    job = plan.jobs[0]
    derived = _stored_object(
        db,
        storage,
        "Assembly 01.dxf",
        b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n",
    )
    db.add(
        AnalysisResult(
            job_id=job.id,
            result_type="convert_dwg_to_dxf",
            result_file_id=derived.id,
            status="succeeded",
        )
    )
    job.status = "succeeded"
    job.progress = 100
    db.flush()

    workflow_input_service.sync_input_batch(db, batch)

    dwg_item = next(item for item in batch.items if item.role == "source_dwg")
    assert dwg_item.status == "paired"
    assert dwg_item.derived_dxf_file_id == derived.id
    assert batch.status == "ready_to_freeze"


def test_sync_reports_derived_name_mismatch(db, tmp_path, monkeypatch):
    user, _, _, batch, storage = _registered_batch(
        db, tmp_path, monkeypatch, dwg_names=("source.dwg",)
    )
    monkeypatch.setattr(workflow_input_service.settings, "dxf_pipeline_enabled", True)
    job = workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id).jobs[0]
    derived = _stored_object(db, storage, "other.dxf", b"0\nEOF\n")
    db.add(
        AnalysisResult(
            job_id=job.id,
            result_type="convert_dwg_to_dxf",
            result_file_id=derived.id,
            status="succeeded",
        )
    )
    job.status = "succeeded"
    db.flush()

    workflow_input_service.sync_input_batch(db, batch)

    item = next(item for item in batch.items if item.role == "source_dwg")
    assert item.status == "conversion_failed"
    assert item.error_code == "INPUT_DXF_NAME_MISMATCH"
    assert batch.status == "needs_attention"


def _ready_batch(db, tmp_path, monkeypatch, *, dwg_names=("A.dwg", "B.dwg")):
    user, project, workflow, batch, storage = _registered_batch(
        db, tmp_path, monkeypatch, dwg_names=dwg_names
    )
    monkeypatch.setattr(workflow_input_service.settings, "dxf_pipeline_enabled", True)
    plan = workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)
    for item, job in zip(
        [item for item in batch.items if item.role == "source_dwg"],
        plan.jobs,
        strict=True,
    ):
        derived_name = f"{item.original_name.rsplit('.', 1)[0]}.dxf"
        derived = _stored_object(
            db,
            storage,
            derived_name,
            b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n",
        )
        db.add(
            AnalysisResult(
                job_id=job.id,
                result_type="convert_dwg_to_dxf",
                result_file_id=derived.id,
                status="succeeded",
            )
        )
        job.status = "succeeded"
        job.progress = 100
    db.flush()
    workflow_input_service.sync_input_batch(db, batch)
    workflow_service.start_workflow(db, workflow)
    return user, project, workflow, batch


def test_freeze_creates_drawings_manifest_artifacts_and_completes_source_intake(
    db, tmp_path, monkeypatch
):
    _, project, workflow, batch = _ready_batch(db, tmp_path, monkeypatch)

    frozen = workflow_input_service.freeze_input_batch(db, batch)
    first_manifest = frozen.manifest_sha256
    replay = workflow_input_service.freeze_input_batch(db, batch)

    drawings = list(db.query(Drawing).filter(Drawing.project_id == project.id).all())
    versions = list(db.query(DrawingVersion).all())
    assert frozen.status == "frozen"
    assert frozen.frozen_at is not None
    assert first_manifest is not None and len(first_manifest) == 64
    assert replay.manifest_sha256 == first_manifest
    assert len(drawings) == 2
    assert len(versions) == 2
    assert all(item.status == "frozen" for item in batch.items)
    assert all(item.drawing_id is not None for item in batch.items if item.role == "source_dwg")
    assert workflow.current_stage == "dxf_classification"
    assert {artifact.artifact_type for artifact in workflow.artifacts} == {
        "source_file",
        "source_excel",
        "derived_dxf",
    }


def test_freeze_rejects_duplicate_normalized_dwg_names(db, tmp_path, monkeypatch):
    user, _, workflow, batch, _ = _registered_batch(
        db, tmp_path, monkeypatch, dwg_names=("A.dwg", "Ａ.DWG")
    )
    workflow_service.start_workflow(db, workflow)
    monkeypatch.setattr(workflow_input_service.settings, "dxf_pipeline_enabled", True)
    workflow_input_service.prepare_input_conversions(db, batch, created_by=user.id)

    with pytest.raises(AppHTTPException) as error:
        workflow_input_service.freeze_input_batch(db, batch)

    assert error.value.detail["code"] == "INPUT_DWG_NAME_CONFLICT"
    assert db.query(Drawing).count() == 0


def test_source_intake_cannot_be_manually_completed_before_batch_freeze(db, tmp_path, monkeypatch):
    _, _, workflow, batch, _ = _registered_batch(db, tmp_path, monkeypatch, dwg_names=("A.dwg",))
    workflow_service.start_workflow(db, workflow)
    source = next(item for item in batch.items if item.role == "source_dwg")
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_file",
        file_id=source.file_id,
    )

    with pytest.raises(AppHTTPException) as error:
        workflow_service.complete_manual_stage(workflow, "source_intake")

    assert error.value.detail["code"] == "WORKFLOW_INPUT_BATCH_NOT_FROZEN"
