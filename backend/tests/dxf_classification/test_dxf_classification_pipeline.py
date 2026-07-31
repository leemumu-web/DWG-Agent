from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.dxf_classification import execution as dxf_classification_service
from app.modules.dxf_classification import interface as classification_interface
from app.modules.dxf_classification.adapter import ClassificationError
from app.modules.dxf_classification.models import DxfClassificationItem, DxfClassificationRun
from app.modules.dxf_classification.persistence import (
    classification_request_id,
    reconcile_classification_run_for_terminal_job,
)
from app.modules.files.interface import (
    FileTransfer,
    StoredFile,
    get_storage_backend,
    save_bytes_as_file,
)
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.modules.operations.audit.models import AuditLog
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows import interface as workflow_service
from app.modules.workflows.interface import WorkflowInputBatch, WorkflowInputItem, WorkflowRun
from app.modules.workflows.schemas import WorkflowCreate


def _attach_source_artifacts(db, workflow: WorkflowRun, canonical_dxf: StoredFile) -> None:
    source_dwg = StoredFile(
        bucket="dxf-derived",
        storage_key=f"tests/{uuid4().hex}.dwg",
        original_name="A001.dwg",
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=2048,
        sha256=uuid4().hex + uuid4().hex,
        status="available",
    )
    source_excel = StoredFile(
        bucket="dxf-derived",
        storage_key=f"tests/{uuid4().hex}.xlsx",
        original_name="source.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=2048,
        sha256=uuid4().hex + uuid4().hex,
        status="available",
    )
    db.add_all([source_dwg, source_excel])
    db.flush()
    for artifact_type, stored in (
        ("source_dwg", source_dwg),
        ("source_excel", source_excel),
        ("canonical_dxf", canonical_dxf),
    ):
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type=artifact_type,
            file_id=stored.id,
        )


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
    _attach_source_artifacts(db, workflow, stored)
    workflow_service.complete_manual_stage(db, workflow, "source_intake")
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


def _classified_output_file(db, name: str) -> StoredFile:
    stored = StoredFile(
        bucket="dxf-derived",
        storage_key=f"tests/bh-stage2/{uuid4().hex}.dxf",
        original_name=name,
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=128,
        sha256=hashlib.sha256(name.encode("utf-8")).hexdigest(),
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def _completed_classification_run_for_stage2(
    db,
    *,
    workflow_id: int,
    job_id: int,
    project_id: int,
    bh_count: int = 1,
    other_count: int = 0,
) -> DxfClassificationRun:
    job = db.get(Job, job_id)
    job.status = "succeeded"
    run = DxfClassificationRun(
        workflow_run_id=workflow_id,
        project_id=project_id,
        job_id=job_id,
        job_attempt=job.attempt,
        status="completed",
        classifier_version="1.2.0",
        project_name=f"stage2-workflow-{workflow_id}",
        input_manifest_sha256=hashlib.sha256(
            f"workflow:{workflow_id}".encode()
        ).hexdigest(),
        input_count=bh_count + other_count,
        classified_count=bh_count + other_count,
        review_required_count=0,
        unreadable_count=0,
        type_counts_json={
            **({"BH": bh_count} if bh_count else {}),
            **({"PX": other_count} if other_count else {}),
        },
    )
    db.add(run)
    db.flush()
    return run


def test_bh_stage2_batch_uses_only_the_exact_workflow_ledger_in_stable_order(
    db,
    tmp_path: Path,
) -> None:
    first_workflow_id, first_job_id, _first_source_id = _frozen_classification_job(
        db,
        tmp_path,
    )
    first_workflow = db.get(WorkflowRun, first_workflow_id)
    first_run = _completed_classification_run_for_stage2(
        db,
        workflow_id=first_workflow_id,
        job_id=first_job_id,
        project_id=first_workflow.project_id,
        bh_count=2,
        other_count=1,
    )
    first_outputs = [
        _classified_output_file(db, "BH-B_拆板前.dxf"),
        _classified_output_file(db, "BH-A_拆板前.dxf"),
        _classified_output_file(db, "PX-1_拆板前.dxf"),
    ]
    for index, (output, part_type) in enumerate(
        zip(first_outputs, ("BH", "BH", "PX"), strict=True),
    ):
        db.add(DxfClassificationItem(
            run=first_run,
            source_file_id=output.id,
            output_file_id=output.id,
            source_name=f"source-{index}.dxf",
            output_name=output.original_name,
            output_directory=f"type-{part_type}",
            disposition="classified",
            part_type=part_type,
            profile_raw=(
                "BH500*200*10*16" if part_type == "BH" else "PX300*150*8"
            ),
            profile_normalized=(
                "BH500*200*10*16" if part_type == "BH" else "PX300*150*8"
            ),
            type_source="catalog",
            group_key=f"type:{part_type}",
            next_stage_eligible=True,
            diagnostics_json=[],
            evidence_json={},
        ))

    other_workflow_id, other_job_id, _other_source_id = _frozen_classification_job(
        db,
        tmp_path,
    )
    other_workflow = db.get(WorkflowRun, other_workflow_id)
    other_run = _completed_classification_run_for_stage2(
        db,
        workflow_id=other_workflow_id,
        job_id=other_job_id,
        project_id=other_workflow.project_id,
    )
    other_output = _classified_output_file(db, "OTHER-BH_拆板前.dxf")
    db.add(DxfClassificationItem(
        run=other_run,
        source_file_id=other_output.id,
        output_file_id=other_output.id,
        source_name="other-source.dxf",
        output_name=other_output.original_name,
        output_directory="type-BH",
        disposition="classified",
        part_type="BH",
        profile_raw="BH600*300*12*20",
        profile_normalized="BH600*300*12*20",
        type_source="catalog",
        group_key="type:BH",
        next_stage_eligible=True,
        diagnostics_json=[],
        evidence_json={},
    ))
    first_run_id = first_run.id
    db.commit()

    select_statements: list[str] = []

    def count_selects(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", count_selects)
    try:
        batch = classification_interface.load_bh_stage2_classification_batch(
            db,
            first_workflow_id,
            expected_run_id=first_run_id,
        )
    finally:
        event.remove(db.bind, "before_cursor_execute", count_selects)

    assert batch.workflow_run_id == first_workflow_id
    assert batch.project_id == first_workflow.project_id
    assert batch.classification_run_id == first_run.id
    assert batch.classification_job_id == first_job_id
    assert batch.classification_job_attempt == 1
    assert batch.input_manifest_sha256 == first_run.input_manifest_sha256
    assert batch.bh_manifest_version == 1
    assert [item.input_name for item in batch.items] == [
        "BH-B_拆板前.dxf",
        "BH-A_拆板前.dxf",
    ]
    assert [item.input_file_id for item in batch.items] == [
        first_outputs[0].id,
        first_outputs[1].id,
    ]
    assert [item.source_file_id for item in batch.items] == [
        first_outputs[0].id,
        first_outputs[1].id,
    ]
    assert [item.input_sha256 for item in batch.items] == [
        first_outputs[0].sha256,
        first_outputs[1].sha256,
    ]
    manifest_payload = "".join(
        f"{item.classification_item_id}\0{item.input_file_id}\0{item.input_sha256}\0"
        f"{item.input_name}\0{item.profile_normalized}\n"
        for item in batch.items
    ).encode("utf-8")
    assert batch.bh_manifest_sha256 == hashlib.sha256(manifest_payload).hexdigest()
    assert other_output.id not in {item.input_file_id for item in batch.items}
    assert len(select_statements) == 2


def test_bh_stage2_manifest_changes_when_registered_object_digest_changes(
    db,
    tmp_path: Path,
) -> None:
    workflow_id, job_id, _source_id = _frozen_classification_job(db, tmp_path)
    workflow = db.get(WorkflowRun, workflow_id)
    run = _completed_classification_run_for_stage2(
        db,
        workflow_id=workflow_id,
        job_id=job_id,
        project_id=workflow.project_id,
    )
    output = _classified_output_file(db, "BH-DIGEST_拆板前.dxf")
    db.add(DxfClassificationItem(
        run=run,
        source_file_id=output.id,
        output_file_id=output.id,
        source_name="source.dxf",
        output_name=output.original_name,
        output_directory="type-BH",
        disposition="classified",
        part_type="BH",
        profile_raw="BH500*200*10*16",
        profile_normalized="BH500*200*10*16",
        type_source="catalog",
        group_key="type:BH",
        next_stage_eligible=True,
        diagnostics_json=[],
        evidence_json={},
    ))
    db.flush()
    first = classification_interface.load_bh_stage2_classification_batch(
        db, workflow_id
    )
    output.sha256 = "a" * 64
    db.flush()

    changed = classification_interface.load_bh_stage2_classification_batch(
        db, workflow_id
    )

    assert first.bh_manifest_sha256 != changed.bh_manifest_sha256


def test_bh_stage2_batch_rejects_declared_bh_count_drift(
    db,
    tmp_path: Path,
) -> None:
    workflow_id, job_id, _source_id = _frozen_classification_job(db, tmp_path)
    workflow = db.get(WorkflowRun, workflow_id)
    run = _completed_classification_run_for_stage2(
        db,
        workflow_id=workflow_id,
        job_id=job_id,
        project_id=workflow.project_id,
        bh_count=2,
    )
    output = _classified_output_file(db, "BH-COUNT_拆板前.dxf")
    db.add(DxfClassificationItem(
        run=run,
        source_file_id=output.id,
        output_file_id=output.id,
        source_name="source.dxf",
        output_name=output.original_name,
        output_directory="type-BH",
        disposition="classified",
        part_type="BH",
        profile_raw="BH500*200*10*16",
        profile_normalized="BH500*200*10*16",
        type_source="catalog",
        group_key="type:BH",
        next_stage_eligible=True,
        diagnostics_json=[],
        evidence_json={},
    ))
    db.flush()

    with pytest.raises(ClassificationError, match="BH 分类数量"):
        classification_interface.load_bh_stage2_classification_batch(
            db,
            workflow_id,
        )


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("stale_run", "已变化"),
        ("run_not_ready", "尚未形成正式输出"),
        ("not_eligible", "未获准"),
        ("missing_profile", "缺少规格"),
        ("deleted_file", "文件不可用"),
        ("wrong_extension", "文件不可用"),
    ],
)
def test_bh_stage2_batch_fails_closed_on_invalid_ledger_rows(
    db,
    tmp_path: Path,
    fault: str,
    message: str,
) -> None:
    workflow_id, job_id, _source_id = _frozen_classification_job(db, tmp_path)
    workflow = db.get(WorkflowRun, workflow_id)
    run = _completed_classification_run_for_stage2(
        db,
        workflow_id=workflow_id,
        job_id=job_id,
        project_id=workflow.project_id,
    )
    output = _classified_output_file(db, "BH-INVALID_拆板前.dxf")
    item = DxfClassificationItem(
        run=run,
        source_file_id=output.id,
        output_file_id=output.id,
        source_name="source.dxf",
        output_name=output.original_name,
        output_directory="type-BH",
        disposition="classified",
        part_type="BH",
        profile_raw="BH500*200*10*16",
        profile_normalized="BH500*200*10*16",
        type_source="catalog",
        group_key="type:BH",
        next_stage_eligible=True,
        diagnostics_json=[],
        evidence_json={},
    )
    db.add(item)
    db.flush()
    expected_run_id = run.id
    if fault == "stale_run":
        expected_run_id += 1000
    elif fault == "run_not_ready":
        run.status = "running"
    elif fault == "not_eligible":
        item.next_stage_eligible = False
    elif fault == "missing_profile":
        item.profile_normalized = None
    elif fault == "deleted_file":
        output.status = "deleted"
    elif fault == "wrong_extension":
        output.file_ext = ".dwg"
    db.commit()

    with pytest.raises(ClassificationError, match=message):
        classification_interface.load_bh_stage2_classification_batch(
            db,
            workflow_id,
            expected_run_id=expected_run_id,
        )


@pytest.mark.parametrize("inject_mysql_1020", [False, True])
def test_classifier_run_persists_routed_dxf_reports_and_mysql_ledger(
    db, monkeypatch, tmp_path: Path, inject_mysql_1020: bool
):
    from app.platform.config.settings import settings

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_root", tmp_path / "storage")
    workflow_id, job_id, source_file_id = _frozen_classification_job(db, tmp_path)
    workflow = db.get(WorkflowRun, workflow_id)
    assert workflow is not None
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="dxf_classification",
        artifact_type="classified_dxf",
        file_id=source_file_id,
        metadata={"job_id": job_id, "job_attempt": 0},
    )
    db.commit()
    persistence_events: list[tuple[str, str]] = []
    persist_output = dxf_classification_service._persist_output
    attach_artifact = dxf_classification_service.attach_artifact
    mysql_1020_injected = False

    def tracked_persist_output(*args, **kwargs):
        persistence_events.append(("persist", kwargs["relative_path"]))
        return persist_output(*args, **kwargs)

    def tracked_attach_artifact(*args, **kwargs):
        nonlocal mysql_1020_injected
        persistence_events.append(("attach", kwargs["artifact_type"]))
        if (
            inject_mysql_1020
            and kwargs["artifact_type"] == "classified_dxf"
            and not mysql_1020_injected
        ):
            mysql_1020_injected = True
            raise OperationalError(
                "INSERT INTO workflow_artifacts",
                {},
                Exception(1020, "Record has changed since last read"),
            )
        return attach_artifact(*args, **kwargs)

    def fake_cli(input_directory: Path):
        project_name = input_directory.name.removesuffix("_dxf")
        renamed = input_directory / "A001_拆板前.dxf"
        (input_directory / "A001.dxf").rename(renamed)
        route = f"{project_name}_PX_dxf"
        output_dir = input_directory.parent / route
        output_dir.mkdir()
        output = output_dir / renamed.name
        output.write_bytes(renamed.read_bytes())
        report = {
            "schema": "STEEL-DXF-CLASSIFICATION-1.2",
            "summary": {
                "project_name": project_name,
                "input_count": 1,
                "classified_count": 1,
                "review_required_count": 0,
                "unreadable_count": 0,
                "type_counts": {"PX": 1},
                "output_directories": [route],
                "elapsed_seconds": 0.01,
            },
            "results": [
                {
                    "source_name": renamed.name,
                    "disposition": "classified",
                    "part_type": "PX",
                    "profile_raw": "PX300*150*8",
                    "profile_normalized": "PX300*150*8",
                    "type_source": "catalog",
                    "group_key": "type:PX",
                    "next_stage_eligible": True,
                    "diagnostics": ["TITLE_PROFILE_PROVED"],
                    "candidates": [],
                    "source_metadata": {},
                    "output_directory": route,
                }
            ],
        }
        report_path = input_directory.parent / f"{project_name}_分类报告.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        manifest_path = input_directory.parent / f"{project_name}_分类清单.csv"
        manifest_path.write_text("文件名,处置\nA001_拆板前.dxf,classified\n", encoding="utf-8")
        return {
            "schema": "STEEL-DXF-CLI-1.2",
            "status": "completed",
            "exit_code": 0,
            "summary": report["summary"],
        }

    monkeypatch.setattr(dxf_classification_service, "_invoke_classifier", fake_cli)
    monkeypatch.setattr(
        dxf_classification_service,
        "_persist_output",
        tracked_persist_output,
    )
    monkeypatch.setattr(
        dxf_classification_service,
        "attach_artifact",
        tracked_attach_artifact,
    )
    dxf_classification_service.run_dxf_classification(job_id, worker_name="test-classifier")

    db.expire_all()
    job = db.get(Job, job_id)
    run = db.scalar(select(DxfClassificationRun).where(DxfClassificationRun.job_id == job_id))
    item = db.scalar(select(DxfClassificationItem).where(DxfClassificationItem.run_id == run.id))
    assert job is not None and job.status == "succeeded"
    assert mysql_1020_injected is inject_mysql_1020
    assert run is not None and run.status == "completed"
    assert run.input_manifest_sha256 == "f" * 64
    assert run.report_file_id is not None and run.manifest_file_id is not None
    assert item is not None and item.source_file_id == source_file_id
    assert item.disposition == "classified" and item.part_type == "PX"
    assert item.profile_raw == "PX300*150*8"
    assert item.profile_normalized == "PX300*150*8"
    assert item.type_source == "catalog"
    assert item.group_key == "type:PX"
    assert item.next_stage_eligible is True
    assert item.output_directory.endswith("_PX_dxf")
    output = db.get(StoredFile, item.output_file_id)
    assert output is not None
    assert output.original_name == "A001_拆板前.dxf"
    assert output.batch_name == item.output_directory
    assert output.sha256 == db.get(StoredFile, source_file_id).sha256
    assert get_storage_backend().object_exists(output.bucket, output.storage_key)
    last_file_persist = max(
        index for index, event in enumerate(persistence_events) if event[0] == "persist"
    )
    first_artifact_attach = min(
        index for index, event in enumerate(persistence_events) if event[0] == "attach"
    )
    assert last_file_persist < first_artifact_attach
    workflow = db.get(WorkflowRun, workflow_id)
    workflow_service.sync_workflow_from_jobs(db, workflow)
    # PX is not a BH/BOX splitter candidate: the split stage is an explicit
    # no-op and Excel is unlocked instead of leaving the workflow stuck.
    assert workflow.current_stage == "excel_stage1"
    classified_files = {
        artifact.file_id
        for artifact in workflow.artifacts
        if artifact.artifact_type == "classified_dxf"
    }
    assert classified_files == {output.id}

    payload = classification_interface.build_classification_run_read(db, run)
    assert [(group.group_key, group.label, group.count) for group in payload.groups] == [
        ("type:PX", "PX", 1)
    ]
    assert payload.groups[0].type_source == "catalog"
    assert payload.groups[0].warning_count == 0
    assert payload.groups[0].total_size_bytes == output.size_bytes

    detail = classification_interface.build_classification_group_page(
        db,
        run,
        group_key="type:PX",
        page=1,
        page_size=20,
    )
    assert detail.total == 1
    assert detail.items[0].output_name == "A001_拆板前.dxf"
    assert detail.items[0].profile_normalized == "PX300*150*8"
    assert detail.items[0].type_source == "catalog"
    assert detail.items[0].size_bytes == output.size_bytes
    assert not {
        "id",
        "file_id",
        "output_file_id",
        "bucket",
        "storage_key",
    } & detail.items[0].model_dump().keys()

    next_stage = classification_interface.list_next_stage_inputs(db, workflow_id)
    assert len(next_stage) == 1
    assert next_stage[0].drawing_id == item.drawing_id
    assert next_stage[0].part_type == "PX"
    assert next_stage[0].profile_normalized == "PX300*150*8"
    assert next_stage[0].type_source == "catalog"
    assert next_stage[0].source_file_id == source_file_id
    assert next_stage[0].output_file_id == output.id
    assert next_stage[0].classifier_version == "1.2.0"


def test_classifier_naming_contract_uses_project_code_and_workflow_id():
    assert dxf_classification_service.classifier_project_name("PRJ_01", 42) == "PRJ_01-workflow-42"


def test_classification_transfer_request_id_is_bounded_stable_and_path_sensitive():
    long_path = (
        "DXFREAL-1784893851-workflow-5_待确认_dxf/"
        "15C-114 - 板零件图_拆板前.dxf"
    )

    request_id = classification_request_id(
        job_id=1652,
        attempt=1,
        relative_path=long_path,
    )

    assert request_id == classification_request_id(
        job_id=1652,
        attempt=1,
        relative_path=long_path,
    )
    assert len(request_id) <= 64
    assert request_id.startswith("dxf-classification:")
    assert request_id != classification_request_id(
        job_id=1652,
        attempt=1,
        relative_path=f"{long_path}.other",
    )


def test_classification_scratch_uses_configured_persistent_work_root(
    tmp_path: Path, monkeypatch
):
    work_root = tmp_path / "classification-work"
    monkeypatch.setattr(
        dxf_classification_service.settings,
        "dxf_classification_work_root",
        work_root,
    )

    with dxf_classification_service.classification_temporary_directory(
        job_id=268, attempt=6
    ) as raw_root:
        scratch = Path(raw_root)
        assert scratch.parent == work_root
        assert scratch.is_dir()

    assert not scratch.exists()


def test_cancelled_job_closes_running_classification_projection(db, tmp_path: Path):
    workflow_id, job_id, _ = _frozen_classification_job(db, tmp_path)
    job = db.get(Job, job_id)
    workflow = db.get(WorkflowRun, workflow_id)
    project = db.get(Project, workflow.project_id)
    assert job is not None and workflow is not None and project is not None
    run = DxfClassificationRun(
        workflow_run_id=workflow.id,
        project_id=project.id,
        job_id=job.id,
        job_attempt=job.attempt,
        status="running",
        classifier_version="1.2.0",
        project_name="test-workflow",
        input_manifest_sha256="a" * 64,
        input_count=1,
    )
    db.add(run)
    db.commit()

    job.status = "cancelled"
    db.commit()

    assert reconcile_classification_run_for_terminal_job(
        db, job_id=job.id, attempt=job.attempt
    )
    db.commit()
    db.refresh(run)
    assert run.status == "cancelled"
    assert run.error_code == "CANCELLED_BY_ADMIN"
    assert run.finished_at is not None


def test_classification_openapi_exposes_paginated_group_details():
    path = (
        "/api/v1/workflows/{workflow_id}/dxf-classification/"
        "groups/{group_key}"
    )

    assert path in app.openapi()["paths"]
    assert "get" in app.openapi()["paths"][path]


def test_classification_downloads_category_and_all_dxf_without_audit_files(
    db, monkeypatch, tmp_path: Path
):
    from app.platform.config.settings import settings

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_root", tmp_path / "storage")
    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    project = client.post(
        "/api/v1/workflows/projects",
        headers=headers,
        json={"code": f"ZIP-{uuid4().hex[:6]}", "name": "Classifier ZIP"},
    ).json()["data"]
    created = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "project_id": project["id"],
            "name": "Classifier ZIP run",
            "workflow_type": "linux_production",
        },
    ).json()["data"]

    dxf_payload = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
    with db:
        workflow = db.get(WorkflowRun, created["id"])
        assert workflow is not None
        job = Job(
            project_id=workflow.project_id,
            created_by=workflow.created_by,
            task_type="classify_steel_dxf",
            pipeline="steel_dxf_classifier",
            status="succeeded",
            attempt=1,
            progress=100,
            precision_level="normal",
        )
        db.add(job)
        db.flush()
        run = DxfClassificationRun(
            workflow_run_id=workflow.id,
            project_id=workflow.project_id,
            job_id=job.id,
            job_attempt=1,
            status="completed_with_review",
            classifier_version="1.2.0",
            report_schema="STEEL-DXF-CLASSIFICATION-1.2",
            cli_schema="STEEL-DXF-CLI-1.2",
            project_name=f"{project['code']}-workflow-{workflow.id}",
            input_manifest_sha256="c" * 64,
            input_count=2,
            classified_count=1,
            review_required_count=1,
            unreadable_count=0,
            type_counts_json={"PX": 1},
        )
        db.add(run)
        db.flush()
        for index, semantic in enumerate(
            (
                {
                    "name": "px_拆板前.dxf",
                    "directory": f"{run.project_name}_PX_dxf",
                    "disposition": "classified",
                    "part_type": "PX",
                    "profile": "PX300*150*8",
                    "type_source": "catalog",
                    "group_key": "type:PX",
                    "eligible": True,
                },
                {
                    "name": "review_拆板前.dxf",
                    "directory": f"{run.project_name}_待确认_dxf",
                    "disposition": "review_required",
                    "part_type": None,
                    "profile": None,
                    "type_source": None,
                    "group_key": "status:review_required",
                    "eligible": False,
                },
            ),
            start=1,
        ):
            source = save_bytes_as_file(
                db,
                bucket="dxf-derived",
                storage_key=f"tests/classification-zip/source-{uuid4().hex}.dxf",
                original_name=f"source-{index}.dxf",
                file_ext=".dxf",
                content_type="application/dxf",
                payload=dxf_payload,
                uploaded_by=workflow.created_by,
                batch_name=f"classification-{run.id}",
            )
            output = save_bytes_as_file(
                db,
                bucket="dxf-derived",
                storage_key=f"tests/classification-zip/output-{uuid4().hex}.dxf",
                original_name=semantic["name"],
                file_ext=".dxf",
                content_type="application/dxf",
                payload=dxf_payload,
                uploaded_by=workflow.created_by,
                batch_name=semantic["directory"],
            )
            db.add(
                DxfClassificationItem(
                    run=run,
                    source_file_id=source.id,
                    output_file_id=output.id,
                    source_name=source.original_name,
                    output_name=semantic["name"],
                    output_directory=semantic["directory"],
                    disposition=semantic["disposition"],
                    part_type=semantic["part_type"],
                    profile_raw=semantic["profile"],
                    profile_normalized=semantic["profile"],
                    type_source=semantic["type_source"],
                    group_key=semantic["group_key"],
                    next_stage_eligible=semantic["eligible"],
                    diagnostics_json=(
                        ["TITLE_PROFILE_PROVED"]
                        if semantic["eligible"]
                        else ["TITLE_VALUE_MISSING"]
                    ),
                    evidence_json={},
                )
            )
        report = save_bytes_as_file(
            db,
            bucket="reports",
            storage_key=f"tests/classification-zip/{uuid4().hex}.json",
            original_name="分类报告.json",
            file_ext=".json",
            content_type="application/json",
            payload=b'{"schema":"STEEL-DXF-CLASSIFICATION-1.2"}',
            uploaded_by=workflow.created_by,
            batch_name=f"classification-{run.id}",
        )
        manifest = save_bytes_as_file(
            db,
            bucket="reports",
            storage_key=f"tests/classification-zip/{uuid4().hex}.csv",
            original_name="分类清单.csv",
            file_ext=".csv",
            content_type="text/csv",
            payload=b"name,status\n",
            uploaded_by=workflow.created_by,
            batch_name=f"classification-{run.id}",
        )
        run.report_file_id = report.id
        run.manifest_file_id = manifest.id
        db.commit()

    category = client.get(
        f"/api/v1/workflows/{created['id']}/dxf-classification/"
        "groups/type:PX/download-archive",
        headers=headers,
    )
    assert category.status_code == 200, category.text
    with zipfile.ZipFile(BytesIO(category.content)) as archive:
        category_names = archive.namelist()
        assert len(category_names) == 1
        assert category_names[0].endswith("/PX/px_拆板前.dxf")

    complete = client.get(
        f"/api/v1/workflows/{created['id']}/dxf-classification/download-archive",
        headers=headers,
    )
    assert complete.status_code == 200, complete.text
    with zipfile.ZipFile(BytesIO(complete.content)) as archive:
        names = archive.namelist()
        assert len(names) == 2
        assert any(name.endswith("/PX/px_拆板前.dxf") for name in names)
        assert any(name.endswith("/待确认/review_拆板前.dxf") for name in names)
        assert all(name.lower().endswith(".dxf") for name in names)
        assert not any(name.lower().endswith((".json", ".csv", ".dwg")) for name in names)

    missing = client.get(
        f"/api/v1/workflows/{created['id']}/dxf-classification/"
        "groups/type:NOT-FOUND/download-archive",
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CLASSIFICATION_GROUP_NOT_FOUND"

    db.expire_all()
    transfers = db.scalars(
        select(FileTransfer).where(
            FileTransfer.operation.in_(
                ("dxf_class_group_zip", "dxf_class_all_zip")
            )
        )
    ).all()
    assert {transfer.operation for transfer in transfers} == {
        "dxf_class_group_zip",
        "dxf_class_all_zip",
    }
    assert all(transfer.status == "succeeded" for transfer in transfers)
    audits = db.scalars(
        select(AuditLog).where(
            AuditLog.action.in_(
                (
                    "dxf_classification_groups.download",
                    "dxf_classification_archives.download",
                )
            )
        )
    ).all()
    assert len(audits) == 2

    paths = app.openapi()["paths"]
    assert (
        "/api/v1/workflows/{workflow_id}/dxf-classification/"
        "groups/{group_key}/download-archive"
    ) in paths
    assert (
        "/api/v1/workflows/{workflow_id}/dxf-classification/download-archive"
    ) in paths


def test_workflow_execution_api_creates_idempotent_classifier_job(db, monkeypatch):
    from app.modules.workflows.routes import execution as workflows_api
    from app.platform.config.settings import settings

    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    project = client.post(
        "/api/v1/workflows/projects",
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
    _attach_source_artifacts(db, workflow, source)
    workflow_service.complete_manual_stage(db, workflow, "source_intake")
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
    empty = client.get(f"/api/v1/workflows/{workflow.id}/dxf-classification", headers=headers)
    assert empty.status_code == 200 and empty.json()["data"] is None
