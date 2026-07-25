from __future__ import annotations

import hashlib
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

import openpyxl
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.modules.dxf_classification.interface import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.dxf_splitting.interface import DxfSplitItem, DxfSplitRun
from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows import interface as workflow_service
from app.modules.workflows.contracts import require_stage_outputs
from app.modules.workflows.intake import registration as workflow_input_registration
from app.modules.workflows.interface import WorkflowInputBatch, WorkflowInputItem, WorkflowRun
from app.modules.workflows.schemas import WorkflowCreate, WorkflowStageExecutionCreate
from app.modules.workflows.stage_execution import (
    StageExecutionPlan,
    dispatch_stage_execution,
    prepare_stage_execution,
)
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api
from tests.support.database import open_test_session


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
        "dxf_classification",
        "drawing_processing",
        "excel_stage1",
        "excel_stage2",
        "design_barrier",
        "cam_packaging",
        "windows_cam",
        "result_acceptance",
        "delivery_archive",
    ]
    assert workflow.config_json == {"definition_revision": 4}


def test_linux_production_template_exposes_honest_capabilities():
    templates = workflow_service.list_workflow_templates()
    production = next(item for item in templates if item.code == "linux_production")
    stages = {stage.code: stage for stage in production.stages}

    assert stages["source_intake"].execution_mode == "manual"
    assert stages["dxf_classification"].execution_mode == "automated"
    assert stages["dxf_classification"].implementation_status == "implemented"
    assert stages["dxf_classification"].execution_kind == "steel_dxf_classification"
    assert stages["dxf_classification"].artifact_types == [
        "classified_dxf",
        "classification_report",
        "classification_manifest",
    ]
    assert stages["excel_stage1"].implementation_status == "implemented"
    assert stages["excel_stage1"].execution_kind == "excel_stage1"
    assert stages["excel_stage1"].required_inputs == [
        "source_excel",
        "processed_dxf",
        "bh_split_ledger",
    ]
    assert stages["excel_stage1"].artifact_types == ["stage1_excel"]
    assert stages["excel_stage2"].execution_mode == "placeholder"
    assert stages["excel_stage2"].implementation_status == "placeholder"
    assert stages["excel_stage2"].execution_kind == "excel_stage2"
    assert stages["excel_stage2"].required_inputs == ["stage1_excel", "processed_dxf"]
    assert stages["excel_stage2"].artifact_types == ["stage2_excel"]
    assert stages["excel_stage2"].required_outputs == ["stage2_excel"]
    assert stages["design_barrier"].required_inputs == ["processed_dxf", "stage2_excel"]
    assert "stage2_excel" in stages["cam_packaging"].required_inputs
    assert "stage2_excel" in stages["delivery_archive"].required_inputs
    assert "excel_final" not in stages
    assert all(stage.execution_kind != "dxf_to_excel" for stage in production.stages)
    assert stages["drawing_processing"].implementation_status == "implemented"
    assert stages["drawing_processing"].execution_mode == "automated"
    assert stages["drawing_processing"].artifact_types == [
        "processed_dxf",
        "weld_allowance_dxf",
        "split_report",
        "weld_allowance_report",
        "validation_report",
        "bh_split_ledger",
        "split_manifest",
    ]
    assert stages["cam_packaging"].implementation_status == "placeholder"
    assert stages["windows_cam"].implementation_status == "external"
    assert stages["result_acceptance"].implementation_status == "placeholder"


def test_workflow_stage_execution_request_is_closed_and_parameter_free():
    assert WorkflowStageExecutionCreate(execution_kind="excel_stage1").model_dump() == {
        "execution_kind": "excel_stage1"
    }
    for extra in (
        {"file_id": 1},
        {"batch_name": "legacy"},
        {"unknown": "value"},
    ):
        with pytest.raises(ValidationError):
            WorkflowStageExecutionCreate(execution_kind="excel_stage1", **extra)


def test_excel_stage2_execution_is_rejected_without_creating_job(db):
    user, project = _owner_project(db)
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="Stage 2 placeholder",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    for stage in workflow.stages:
        if stage.stage_code == "excel_stage2":
            stage.status = "waiting_input"
        elif stage.sequence < 5:
            stage.status = "succeeded"
            stage.progress = 100
    workflow.current_stage = "excel_stage2"
    workflow.status = "waiting_input"
    job_count = db.query(Job).count()

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage2",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage2"),
            current_user=user,
        )

    assert caught.value.status_code == 501
    assert caught.value.detail["code"] == "WORKFLOW_STAGE_NOT_IMPLEMENTED"
    assert caught.value.detail["details"]["stage_code"] == "excel_stage2"
    assert db.query(Job).count() == job_count


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


@pytest.fixture(autouse=True)
def _serve_structural_dxf_for_registry_only_fixtures(monkeypatch):
    original = workflow_input_registration.read_verified_input_object

    def read(stored: StoredFile) -> bytes:
        if stored.bucket == "test-bucket" and stored.file_ext == ".dxf":
            return b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
        return original(stored)

    monkeypatch.setattr(
        workflow_input_registration,
        "read_verified_input_object",
        read,
    )


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


def _mark_input_batch_frozen(db, workflow: WorkflowRun) -> WorkflowInputBatch:
    batch = WorkflowInputBatch(
        workflow=workflow,
        project_id=workflow.project_id,
        created_by=workflow.created_by,
        status="frozen",
        version=1,
        manifest_sha256="f" * 64,
    )
    db.add(batch)
    db.flush()
    return batch


def _attach_source_intake_outputs(
    db,
    workflow: WorkflowRun,
    *,
    canonical_dxf: StoredFile | None = None,
    source_excel: StoredFile | None = None,
) -> dict[str, StoredFile]:
    outputs = {
        "source_dwg": _stored_file(db, name="source.dwg"),
        "source_excel": source_excel or _stored_file(db, name="source.xlsx"),
        "canonical_dxf": canonical_dxf or _stored_file(db, name="source.dxf"),
    }
    for artifact_type, stored in outputs.items():
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type=artifact_type,
            file_id=stored.id,
        )
    return outputs


def _complete_classification_fixture(
    db,
    workflow: WorkflowRun,
) -> DxfClassificationRun:
    """Advance the newly implemented automated classifier in downstream state-machine tests."""
    artifacts: dict[str, StoredFile] = {}
    for artifact_type, name in (
        ("classified_dxf", "classified.dxf"),
        ("classification_report", "classification-report.json"),
        ("classification_manifest", "classification-manifest.json"),
    ):
        stored = _stored_file(db, name=name)
        stored.uploaded_by = workflow.created_by
        artifacts[artifact_type] = stored
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="dxf_classification",
            artifact_type=artifact_type,
            file_id=stored.id,
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
        params_json={
            "workflow_id": workflow.id,
            "input_manifest_sha256": "f" * 64,
        },
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
        project_name=f"fixture-workflow-{workflow.id}",
        input_manifest_sha256="f" * 64,
        input_count=1,
        classified_count=1,
        review_required_count=0,
        unreadable_count=0,
        type_counts_json={"BH": 1},
        report_file_id=artifacts["classification_report"].id,
        manifest_file_id=artifacts["classification_manifest"].id,
    )
    db.add(run)
    db.flush()
    classified = artifacts["classified_dxf"]
    db.add(
        DxfClassificationItem(
            run=run,
            source_file_id=classified.id,
            output_file_id=classified.id,
            source_name=classified.original_name,
            output_name="fixture_拆板前.dxf",
            output_directory=f"{run.project_name}_BH_dxf",
            disposition="classified",
            part_type="BH",
            profile_raw="BH500*300*12*20",
            profile_normalized="BH500*300*12*20",
            type_source="catalog",
            group_key="type:BH",
            next_stage_eligible=True,
            diagnostics_json=[],
            evidence_json={},
        )
    )
    db.flush()
    stage = next(item for item in workflow.stages if item.stage_code == "dxf_classification")
    drawing = next(item for item in workflow.stages if item.stage_code == "drawing_processing")
    stage.job_id = job.id
    stage.job_attempt = job.attempt
    stage.status = "succeeded"
    stage.progress = 100
    drawing.status = "waiting_input"
    workflow_service.recompute_workflow(workflow)
    return run


def _advance_to_drawing_processing(db, workflow: WorkflowRun) -> None:
    _mark_input_batch_frozen(db, workflow)
    _attach_source_intake_outputs(db, workflow)
    workflow_service.complete_manual_stage(db, workflow, "source_intake")
    _complete_classification_fixture(db, workflow)


def _complete_drawing_processing_fixture(
    db,
    workflow: WorkflowRun,
    *,
    processed_dxf: StoredFile | None = None,
) -> StoredFile:
    processed = processed_dxf or _stored_file(db, name="processed.dxf")
    processed.uploaded_by = workflow.created_by
    allowance = _stored_file(db, name="fixture_余量增长.dxf")
    split_report = _stored_file(db, name="split-report.json")
    allowance_report = _stored_file(db, name="weld-allowance-report.json")
    validation_report = _stored_file(db, name="validation-report.json")
    ledger = _stored_file(db, name="BH拆板信息表.xlsx")
    manifest = _stored_file(db, name="split-manifest.json")
    for stored in (
        allowance,
        split_report,
        allowance_report,
        validation_report,
        ledger,
        manifest,
    ):
        stored.uploaded_by = workflow.created_by

    classification = db.scalar(
        select(DxfClassificationRun)
        .where(DxfClassificationRun.workflow_run_id == workflow.id)
        .order_by(DxfClassificationRun.id.desc())
    )
    assert classification is not None
    classification_item = classification.items[0]
    job = Job(
        project_id=workflow.project_id,
        created_by=workflow.created_by,
        task_type="split_steel_dxf",
        pipeline="steel_dxf_split",
        status="succeeded",
        attempt=1,
        progress=100,
        precision_level="normal",
        params_json={
            "workflow_id": workflow.id,
            "classification_run_id": classification.id,
            "input_manifest_sha256": classification.input_manifest_sha256,
        },
    )
    db.add(job)
    db.flush()
    run = DxfSplitRun(
        workflow_run_id=workflow.id,
        project_id=workflow.project_id,
        classification_run_id=classification.id,
        job_id=job.id,
        job_attempt=1,
        status="completed",
        splitter_version="1.5.2",
        cli_schema="DWG-AGENT-STEEL-DXF-SPLIT-CLI-1.0",
        validation_schema="DWG-AGENT-DXF-SPLIT-VALIDATION-1.0",
        input_manifest_sha256=classification.input_manifest_sha256,
        input_count=1,
        auto_accepted_count=1,
        manual_review_count=0,
        source_contracts_json={
            "BH": "project_tekla_bh_dxf_v1",
            "BOX": "project_tekla_box_dxf_v1",
        },
        bh_split_ledger_file_id=ledger.id,
        split_manifest_file_id=manifest.id,
        validation_report_file_id=validation_report.id,
    )
    db.add(run)
    db.flush()
    db.add(
        DxfSplitItem(
            run=run,
            classification_item_id=classification_item.id,
            source_file_id=classification_item.output_file_id,
            source_name=classification_item.output_name,
            classification_disposition=classification_item.disposition,
            classification_part_type=classification_item.part_type,
            type_resolution="classifier_confirmed",
            part_type="BH",
            profile_normalized=classification_item.profile_normalized,
            family="BH",
            source_contract_id="project_tekla_bh_dxf_v1",
            automation_route="auto_accepted",
            disposition="auto_accepted",
            normal_dxf_file_id=processed.id,
            weld_allowance_dxf_file_id=allowance.id,
            split_report_file_id=split_report.id,
            weld_allowance_report_file_id=allowance_report.id,
            diagnostics_json=[],
            validation_json={"status": "passed"},
        )
    )
    metadata = {"job_id": job.id, "job_attempt": job.attempt, "split_run_id": run.id}
    for artifact_type, stored in (
        ("processed_dxf", processed),
        ("weld_allowance_dxf", allowance),
        ("split_report", split_report),
        ("weld_allowance_report", allowance_report),
        ("validation_report", validation_report),
        ("bh_split_ledger", ledger),
        ("split_manifest", manifest),
    ):
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type=artifact_type,
            file_id=stored.id,
            metadata=metadata,
        )
    drawing = next(item for item in workflow.stages if item.stage_code == "drawing_processing")
    excel = next(item for item in workflow.stages if item.stage_code == "excel_stage1")
    drawing.job_id = job.id
    drawing.job_attempt = job.attempt
    drawing.status = "succeeded"
    drawing.progress = 100
    excel.status = "waiting_input"
    workflow_service.recompute_workflow(workflow)
    return processed


def _mark_api_input_batch_frozen(
    workflow_id: int,
    canonical_dxf_file_id: int | None = None,
) -> None:
    with open_test_session() as db:
        workflow = db.get(WorkflowRun, workflow_id)
        assert workflow is not None
        _mark_input_batch_frozen(db, workflow)
        canonical_dxf = (
            db.get(StoredFile, canonical_dxf_file_id) if canonical_dxf_file_id is not None else None
        )
        _attach_source_intake_outputs(
            db,
            workflow,
            canonical_dxf=canonical_dxf,
        )
        db.commit()


def _mark_api_input_batch_with_excel_frozen(
    workflow_id: int,
    file_id: int,
    canonical_dxf_file_id: int,
) -> None:
    with open_test_session() as db:
        workflow = db.get(WorkflowRun, workflow_id)
        stored = db.get(StoredFile, file_id)
        assert workflow is not None
        assert stored is not None
        batch = _mark_input_batch_frozen(db, workflow)
        db.add(
            WorkflowInputItem(
                batch=batch,
                file_id=stored.id,
                role="source_excel",
                original_name=stored.original_name,
                normalized_stem="source",
                status="frozen",
                validation_json={
                    "inspection": {
                        "protocol_version": 1,
                        "input_contract_version": 1,
                        "source_format": "standard_workbook",
                        "sheet_name": "原表",
                        "header_row": 1,
                        "part_count": 1,
                        "component_count": 1,
                    }
                },
                validation_contract_version=1,
                validated_sha256=stored.sha256,
            )
        )
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="source_excel",
            file_id=stored.id,
        )
        canonical_dxf = db.get(StoredFile, canonical_dxf_file_id)
        assert canonical_dxf is not None
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="source_dwg",
            file_id=_stored_file(db, name="source.dwg").id,
        )
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="canonical_dxf",
            file_id=canonical_dxf.id,
        )
        db.commit()


def _mark_api_classification_complete(workflow_id: int) -> None:
    with open_test_session() as db:
        workflow = db.get(WorkflowRun, workflow_id)
        assert workflow is not None
        _complete_classification_fixture(db, workflow)
        db.commit()


def _mark_api_drawing_processing_complete(workflow_id: int) -> None:
    with open_test_session() as db:
        workflow = db.get(WorkflowRun, workflow_id)
        assert workflow is not None
        _complete_drawing_processing_fixture(db, workflow)
        db.commit()


def _canonical_xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet.append(["构件编号", "零件号", "规格", "长度(mm)", "材质", "数量"])
    sheet.append(["C-1", None, "BH500*300*12*20", 1000, "Q355B", 1])
    sheet.append([None, "P-1", "PL10*200", 100, "Q355B", 1])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _component_only_xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "构件汇总"
    sheet.append(["构件编号", "规格", "长度(mm)", "材质", "数量"])
    sheet.append(["C-1", "BH500*300*12*20", 1000, "Q355B", 1])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _workflow_at_excel_stage_with_frozen_excel(
    db,
    tmp_path,
    monkeypatch,
    *,
    attach_source_artifact: bool = True,
):
    user, project, workflow = _production_workflow(db)
    for stage in workflow.stages:
        if stage.stage_code in {
            "source_intake",
            "dxf_classification",
            "drawing_processing",
        }:
            stage.status = "succeeded"
            stage.progress = 100
        elif stage.stage_code == "excel_stage1":
            stage.status = "waiting_input"
        else:
            stage.status = "pending"
    workflow.current_stage = "excel_stage1"
    workflow.status = "waiting_input"

    payload = _canonical_xlsx_bytes()
    storage = LocalFileStorage(tmp_path / "workflow-excel-storage")
    monkeypatch.setattr(workflow_input_registration, "get_storage_backend", lambda: storage)
    bucket = "workflow-excel"
    storage_key = f"inputs/{uuid4().hex}/source.xlsx"
    storage.put_fileobj(bucket, storage_key, BytesIO(payload), length=len(payload))
    source = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name="source.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        uploaded_by=user.id,
        status="available",
    )
    db.add(source)
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
    item = WorkflowInputItem(
        batch=batch,
        file_id=source.id,
        role="source_excel",
        original_name=source.original_name,
        normalized_stem="source",
        status="frozen",
        validation_json={
            "inspection": {
                "protocol_version": 1,
                "input_contract_version": 1,
                "source_format": "standard_workbook",
                "sheet_name": "原表",
                "header_row": 1,
                "part_count": 1,
                "component_count": 1,
            }
        },
        validation_contract_version=1,
        validated_sha256=source.sha256,
    )
    db.add(item)
    db.flush()
    if attach_source_artifact:
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="source_excel",
            file_id=source.id,
        )
    _complete_classification_fixture(db, workflow)
    _complete_drawing_processing_fixture(db, workflow)
    return user, project, workflow, batch, item, source, storage


def test_excel_stage1_resolves_frozen_source_excel_internally(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, project, workflow, batch, _, source, _ = _workflow_at_excel_stage_with_frozen_excel(
        db, tmp_path, monkeypatch
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)

    plan = prepare_stage_execution(
        db,
        workflow,
        stage_code="excel_stage1",
        payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
        current_user=user,
    )

    assert plan.job.task_type == "process_excel_final"
    assert plan.job.project_id == project.id
    params = plan.job.params_json
    assert params is not None
    assert params["file_id"] == source.id
    assert params["workflow_id"] == workflow.id
    assert params["input_manifest_sha256"] == batch.manifest_sha256
    handoff = params["dxf_split_handoff"]
    assert handoff["workflow_id"] == workflow.id
    assert handoff["job_attempt"] == 1
    assert handoff["bh_split_ledger_file_id"]
    assert len(handoff["drawings"]) == 1
    assert handoff["drawings"][0]["normal_dxf_file_id"]
    assert handoff["drawings"][0]["weld_allowance_dxf_file_id"]


def test_excel_stage1_rejects_missing_frozen_source_artifact(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow, _, _, _, _ = _workflow_at_excel_stage_with_frozen_excel(
        db,
        tmp_path,
        monkeypatch,
        attach_source_artifact=False,
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage1",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
            current_user=user,
        )

    assert caught.value.detail["code"] == "WORKFLOW_STAGE_INPUT_INCOMPLETE"
    assert caught.value.detail["details"]["missing_inputs"] == ["source_excel"]


def test_excel_stage1_rejects_source_changed_after_freeze(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow, _, item, source, storage = _workflow_at_excel_stage_with_frozen_excel(
        db, tmp_path, monkeypatch
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    changed = _canonical_xlsx_bytes() + b"changed"
    storage.put_fileobj(
        source.bucket,
        source.storage_key,
        BytesIO(changed),
        length=len(changed),
    )
    source.size_bytes = len(changed)
    source.sha256 = hashlib.sha256(changed).hexdigest()
    db.flush()

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage1",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
            current_user=user,
        )

    assert caught.value.detail["code"] == "EXCEL_INPUT_OBJECT_CHANGED"
    assert caught.value.detail["details"]["failure"]["meta"]["expected_sha256"] == (
        item.validated_sha256
    )


def test_excel_stage1_rejects_duplicate_frozen_source_items(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow, batch, item, source, storage = _workflow_at_excel_stage_with_frozen_excel(
        db, tmp_path, monkeypatch
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    duplicate_key = f"inputs/{uuid4().hex}/duplicate.xlsx"
    duplicate_payload = _canonical_xlsx_bytes()
    storage.put_fileobj(
        source.bucket,
        duplicate_key,
        BytesIO(duplicate_payload),
        length=len(duplicate_payload),
    )
    duplicate = StoredFile(
        bucket=source.bucket,
        storage_key=duplicate_key,
        original_name="duplicate.xlsx",
        file_ext=".xlsx",
        content_type=source.content_type,
        size_bytes=len(duplicate_payload),
        sha256=hashlib.sha256(duplicate_payload).hexdigest(),
        uploaded_by=user.id,
        status="available",
    )
    db.add(duplicate)
    db.flush()
    db.add(
        WorkflowInputItem(
            batch=batch,
            file_id=duplicate.id,
            role="source_excel",
            original_name=duplicate.original_name,
            normalized_stem="duplicate",
            status="frozen",
            validation_json=item.validation_json,
            validation_contract_version=1,
            validated_sha256=duplicate.sha256,
        )
    )
    db.flush()

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage1",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
            current_user=user,
        )

    assert caught.value.detail["code"] == "WORKFLOW_SOURCE_EXCEL_COUNT_INVALID"
    assert caught.value.detail["details"]["excel_count"] == 2


def test_excel_stage1_rejects_deleted_frozen_source(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow, _, _, source, _ = _workflow_at_excel_stage_with_frozen_excel(
        db, tmp_path, monkeypatch
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    source.status = "deleted"
    db.flush()

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage1",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
            current_user=user,
        )

    assert caught.value.detail["code"] == "WORKFLOW_SOURCE_EXCEL_FILE_MISSING"


def test_excel_stage1_rejects_inaccessible_frozen_source(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow, _, _, source, _ = _workflow_at_excel_stage_with_frozen_excel(
        db, tmp_path, monkeypatch
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    stranger = User(
        username=f"other-uploader-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Other Uploader",
        status="active",
    )
    db.add(stranger)
    db.flush()
    source.uploaded_by = stranger.id
    db.flush()

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage1",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
            current_user=user,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "FORBIDDEN"


def test_excel_stage1_returns_specific_invalid_table_failure(
    db,
    tmp_path,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow, _, item, source, storage = _workflow_at_excel_stage_with_frozen_excel(
        db, tmp_path, monkeypatch
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    invalid = _component_only_xlsx_bytes()
    storage.put_fileobj(
        source.bucket,
        source.storage_key,
        BytesIO(invalid),
        length=len(invalid),
    )
    invalid_sha256 = hashlib.sha256(invalid).hexdigest()
    source.size_bytes = len(invalid)
    source.sha256 = invalid_sha256
    item.validated_sha256 = invalid_sha256
    db.flush()

    with pytest.raises(AppHTTPException) as caught:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="excel_stage1",
            payload=WorkflowStageExecutionCreate(execution_kind="excel_stage1"),
            current_user=user,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "EXCEL_INPUT_COMPONENT_ONLY"
    assert caught.value.detail["details"]["failure"]["action"]


def test_source_intake_requires_the_dedicated_batch_freeze(db):
    _, _, workflow = _production_workflow(db)

    with pytest.raises(AppHTTPException) as error:
        workflow_service.complete_manual_stage(db, workflow, "source_intake")
    assert error.value.detail["code"] == "WORKFLOW_INPUT_BATCH_NOT_FROZEN"

    _mark_input_batch_frozen(db, workflow)
    _attach_source_intake_outputs(db, workflow)
    workflow_service.complete_manual_stage(db, workflow, "source_intake")

    assert workflow.current_stage == "dxf_classification"


def test_repeated_file_binding_is_idempotent(db):
    _, _, workflow = _production_workflow(db)
    stored = _stored_file(db)

    first = workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="canonical_dxf",
        file_id=stored.id,
    )
    second = workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="canonical_dxf",
        file_id=stored.id,
    )

    assert second.id == first.id
    assert len(workflow.artifacts) == 1


def test_artifact_api_reuses_files_and_is_idempotent():
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client, admin_headers, "prod-artifact"
    )
    project_id = workflow_test_api.create_project(client, owner_headers)
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
        "artifact_type": "source_excel",
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
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(client, admin_headers, "prod-owner")
    _, stranger_headers = workflow_test_api.create_engineer_user(
        client, admin_headers, "prod-stranger"
    )
    project_id = workflow_test_api.create_project(client, owner_headers)
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
        json={"stage_code": "source_intake", "artifact_type": "source_excel", "file_id": 1},
    )

    assert response.status_code == 403, response.text


def _api_workflow_at_excel_stage(client, owner_headers, project_id: int):
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
        files={"upload": ("drawing.dxf", b"0\nSECTION\n2\nHEADER\n0\nEOF\n", "image/vnd.dxf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["data"]["id"]
    excel = client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={
            "upload": (
                "source.xlsx",
                _canonical_xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert excel.status_code == 201, excel.text
    excel_file_id = excel.json()["data"]["id"]
    assert (
        client.post(
            f"/api/v1/workflows/{workflow_id}/artifacts",
            headers=owner_headers,
            json={
                "stage_code": "source_intake",
                "artifact_type": "canonical_dxf",
                "file_id": file_id,
            },
        ).status_code
        == 201
    )
    assert (
        client.post(f"/api/v1/workflows/{workflow_id}/start", headers=owner_headers).status_code
        == 200
    )
    _mark_api_input_batch_with_excel_frozen(
        workflow_id,
        excel_file_id,
        file_id,
    )
    assert (
        client.post(
            f"/api/v1/workflows/{workflow_id}/stages/source_intake/completion",
            headers=owner_headers,
        ).status_code
        == 200
    )
    _mark_api_classification_complete(workflow_id)
    _mark_api_drawing_processing_complete(workflow_id)
    return workflow_id, excel_file_id


def test_excel_stage1_execution_creates_binds_and_reuses_real_job(monkeypatch):
    from app.modules.workflows.routes import execution as workflows_api
    from app.platform.config.settings import settings

    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(client, admin_headers, "prod-exec")
    project_id = workflow_test_api.create_project(client, owner_headers)
    workflow_id, excel_file_id = _api_workflow_at_excel_stage(client, owner_headers, project_id)
    dispatched: list[int] = []
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    monkeypatch.setattr(
        workflows_api,
        "dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
        raising=False,
    )
    payload = {"execution_kind": "excel_stage1"}

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
    assert first_data["job"]["task_type"] == "process_excel_final"
    assert first_data["job"]["params_json"]["file_id"] == excel_file_id
    assert first_data["job"]["params_json"]["workflow_id"] == workflow_id
    assert len(first_data["job"]["params_json"]["input_manifest_sha256"]) == 64
    assert first_data["job"]["project_id"] == project_id
    assert first_data["workflow"]["current_stage"] == "excel_stage1"
    assert first_data["workflow"]["status"] == "running"
    assert second_data["job"]["id"] == first_data["job"]["id"]
    assert second_data["reused"] is True
    assert dispatched == [first_data["job"]["id"]]


def test_excel_stage1_preflight_reuses_the_execution_gate(monkeypatch):
    from app.platform.config.settings import settings

    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client, admin_headers, "prod-preflight"
    )
    project_id = workflow_test_api.create_project(client, owner_headers)
    workflow_id, excel_file_id = _api_workflow_at_excel_stage(
        client, owner_headers, project_id
    )
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)

    response = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/preflight",
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ready"] is True
    assert data["source_file_id"] == excel_file_id
    assert data["source_file_name"] == "source.xlsx"
    assert data["input_contract_version"] == 1
    assert data["split_run_id"] > 0
    assert data["official_pair_count"] == 1
    assert [check["code"] for check in data["checks"]] == [
        "input_batch_frozen",
        "source_excel_unique",
        "source_object_verified",
        "excel_contract_verified",
        "split_handoff_verified",
    ]


def test_excel_stage1_result_download_streams_one_xlsx_not_zip(monkeypatch):
    from app.platform.config.settings import settings

    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client, admin_headers, "prod-excel-download"
    )
    project_id = workflow_test_api.create_project(client, owner_headers)
    workflow_id, _ = _api_workflow_at_excel_stage(client, owner_headers, project_id)
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    payload = _canonical_xlsx_bytes()
    uploaded = client.post(
        "/api/v1/files",
        headers=owner_headers,
        files={
            "upload": (
                "最终整理结果.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    output_file_id = uploaded.json()["data"]["id"]

    with open_test_session() as db:
        workflow = db.get(WorkflowRun, workflow_id)
        assert workflow is not None
        job = Job(
            project_id=workflow.project_id,
            created_by=workflow.created_by,
            task_type="process_excel_final",
            pipeline="excel_final",
            status="succeeded",
            attempt=1,
            progress=100,
            precision_level="normal",
            params_json={"workflow_id": workflow.id},
        )
        db.add(job)
        db.flush()
        workflow_service.bind_stage_job(
            db,
            workflow,
            stage_code="excel_stage1",
            job=job,
        )
        result = AnalysisResult(
            job_id=job.id,
            result_type="process_excel_final",
            result_file_id=output_file_id,
            status="succeeded",
        )
        db.add(result)
        db.flush()
        workflow_service.sync_workflow_from_jobs(db, workflow)
        db.commit()

    response = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/download-result",
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    assert response.content == payload
    assert response.content[:2] == b"PK"
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "最终整理结果.xlsx" in unquote(response.headers["content-disposition"])
    assert ".zip" not in response.headers["content-disposition"]

    with open_test_session() as db:
        stored = db.get(StoredFile, output_file_id)
        assert stored is not None
        stored.file_ext = ".zip"
        db.commit()

    invalid_format = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/download-result",
        headers=owner_headers,
    )
    assert invalid_format.status_code == 409, invalid_format.text
    assert invalid_format.json()["error"]["code"] == "EXCEL_STAGE_RESULT_FORMAT_INVALID"

    zip_bypass = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/download-archive",
        headers=owner_headers,
    )
    assert zip_bypass.status_code == 409, zip_bypass.text
    assert (
        zip_bypass.json()["error"]["code"]
        == "EXCEL_STAGE_SINGLE_FILE_DOWNLOAD_REQUIRED"
    )


def test_excel_stage1_execution_honors_pipeline_feature_gate(monkeypatch):
    from app.platform.config.settings import settings

    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(client, admin_headers, "prod-gate")
    project_id = workflow_test_api.create_project(client, owner_headers)
    workflow_id, _ = _api_workflow_at_excel_stage(client, owner_headers, project_id)
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", False)

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json={"execution_kind": "excel_stage1"},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "EXCEL_STAGE1_PIPELINE_DISABLED"


def test_failed_automated_stage_can_retry_through_workflow_execution(monkeypatch):
    from app.modules.jobs.interface import Job
    from app.modules.workflows.routes import execution as workflows_api
    from app.platform.config.settings import settings

    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(client, admin_headers, "prod-retry")
    project_id = workflow_test_api.create_project(client, owner_headers)
    workflow_id, _ = _api_workflow_at_excel_stage(client, owner_headers, project_id)
    dispatched: list[tuple[int, int]] = []
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    monkeypatch.setattr(
        workflows_api,
        "dispatch_committed_job",
        lambda _db, job: dispatched.append((job.id, job.attempt)),
    )
    payload = {"execution_kind": "excel_stage1"}
    first = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json=payload,
    )
    assert first.status_code == 202, first.text
    job_id = first.json()["data"]["job"]["id"]
    with open_test_session() as db:
        job = db.get(Job, job_id)
        assert job is not None
        job.status = "failed"
        job.error_code = "FIXTURE_FAILURE"
        job.error_message = "retry me"
        db.commit()
    failed = client.get(f"/api/v1/workflows/{workflow_id}", headers=owner_headers)
    assert failed.json()["data"]["status"] == "failed"

    retried = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json=payload,
    )

    assert retried.status_code == 202, retried.text
    data = retried.json()["data"]
    assert data["job"]["id"] == job_id
    assert data["job"]["attempt"] == 2
    assert data["job"]["status"] == "queued"
    assert data["workflow"]["status"] == "running"
    assert data["workflow"]["stages"][3]["job_attempt"] == 2
    assert data["retried"] is True
    assert dispatched == [(job_id, 1), (job_id, 2)]


def test_successful_job_sync_attaches_result_once_and_advances(db):
    _, project, workflow = _production_workflow(db)
    source = _stored_file(db)
    _advance_to_drawing_processing(db, workflow)
    _complete_drawing_processing_fixture(db, workflow, processed_dxf=source)
    job = Job(
        project_id=project.id,
        task_type="process_excel_final",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(db, workflow, stage_code="excel_stage1", job=job)
    output = _stored_file(db, name="stage1.xlsx")
    result = AnalysisResult(
        job_id=job.id,
        result_type="process_excel_final",
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
    assert workflow.current_stage == "excel_stage2"
    assert workflow.status == "waiting_input"
    assert len(artifacts) == 1
    assert artifacts[0].artifact_type == "stage1_excel"
    assert artifacts[0].file_id == output.id
    assert artifacts[0].result_id == result.id


def test_cancelled_bound_job_stays_on_its_recoverable_workflow_stage(db):
    _, project, workflow = _production_workflow(db)
    source = _stored_file(db)
    _advance_to_drawing_processing(db, workflow)
    _complete_drawing_processing_fixture(db, workflow, processed_dxf=source)
    job = Job(
        project_id=project.id,
        task_type="process_excel_final",
        pipeline="excel_final",
        status="cancelled",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(db, workflow, stage_code="excel_stage1", job=job)

    workflow_service.sync_workflow_from_jobs(db, workflow)

    assert workflow.status == "failed"
    assert workflow.current_stage == "excel_stage1"
    assert workflow.error_code == "WORKFLOW_STAGE_CANCELLED"


def test_drawing_processing_execution_honors_disabled_feature_gate(monkeypatch):
    from app.platform.config.settings import settings

    monkeypatch.setattr(settings, "dxf_split_pipeline_enabled", False)
    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(
        client, admin_headers, "prod-placeholder"
    )
    project_id = workflow_test_api.create_project(client, owner_headers)
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
        files={
            "upload": (
                "source.dxf",
                b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n",
                "image/vnd.dxf",
            )
        },
    )
    file_id = uploaded.json()["data"]["id"]
    client.post(f"/api/v1/workflows/{workflow_id}/start", headers=owner_headers)
    _mark_api_input_batch_frozen(workflow_id, file_id)
    client.post(
        f"/api/v1/workflows/{workflow_id}/stages/source_intake/completion",
        headers=owner_headers,
    )
    _mark_api_classification_complete(workflow_id)

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/drawing_processing/executions",
        headers=owner_headers,
        json={"execution_kind": "drawing_processing"},
    )

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "DXF_SPLIT_PIPELINE_DISABLED"


def test_drawing_processing_ignores_unresolved_types_when_bh_input_exists(
    db,
    monkeypatch,
):
    from app.platform.config.settings import settings

    user, _, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)
    classification = db.scalar(
        select(DxfClassificationRun).where(DxfClassificationRun.workflow_run_id == workflow.id)
    )
    assert classification is not None
    classification.status = "completed_with_review"
    classification.review_required_count = 1
    db.flush()
    monkeypatch.setattr(settings, "dxf_split_pipeline_enabled", True)

    plan = prepare_stage_execution(
        db,
        workflow,
        stage_code="drawing_processing",
        payload=WorkflowStageExecutionCreate(execution_kind="drawing_processing"),
        current_user=user,
    )

    assert plan.job.task_type == "split_steel_dxf"
    assert plan.job.params_json["classification_run_id"] == classification.id


def test_project_engineer_can_launch_split_from_another_members_classification(
    db,
    monkeypatch,
):
    from app.platform.config.settings import settings

    _, project, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)
    engineer = User(
        username=f"split-launcher-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Split Project Engineer",
        status="active",
    )
    db.add(engineer)
    db.flush()
    db.add(
        ProjectMember(
            project_id=project.id,
            user_id=engineer.id,
            project_role="project_engineer",
        )
    )
    db.flush()
    monkeypatch.setattr(settings, "dxf_split_pipeline_enabled", True)

    plan = prepare_stage_execution(
        db,
        workflow,
        stage_code="drawing_processing",
        payload=WorkflowStageExecutionCreate(execution_kind="drawing_processing"),
        current_user=engineer,
    )

    assert plan.job.project_id == project.id
    assert plan.job.created_by == engineer.id
    assert plan.job.task_type == "split_steel_dxf"


def test_bound_split_job_is_reused_across_project_members(
    db,
    monkeypatch,
):
    from app.platform.config.settings import settings

    owner, project, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)
    engineer = User(
        username=f"split-reuser-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Split Project Engineer",
        status="active",
    )
    db.add(engineer)
    db.flush()
    db.add(
        ProjectMember(
            project_id=project.id,
            user_id=engineer.id,
            project_role="project_engineer",
        )
    )
    db.flush()
    monkeypatch.setattr(settings, "dxf_split_pipeline_enabled", True)
    payload = WorkflowStageExecutionCreate(execution_kind="drawing_processing")

    first = prepare_stage_execution(
        db,
        workflow,
        stage_code="drawing_processing",
        payload=payload,
        current_user=owner,
    )
    second = prepare_stage_execution(
        db,
        workflow,
        stage_code="drawing_processing",
        payload=payload,
        current_user=engineer,
    )

    split_jobs = db.scalars(
        select(Job).where(
            Job.project_id == project.id,
            Job.task_type == "split_steel_dxf",
        )
    ).all()
    assert second.job.id == first.job.id
    assert second.job.created_by == owner.id
    assert second.reused is True
    assert len(split_jobs) == 1


def test_drawing_processing_dispatch_does_not_retry_enqueue_failure(
    db,
    monkeypatch,
):
    user, project, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)
    classification = db.scalar(
        select(DxfClassificationRun).where(DxfClassificationRun.workflow_run_id == workflow.id)
    )
    assert classification is not None
    job = Job(
        project_id=project.id,
        created_by=workflow.created_by,
        task_type="split_steel_dxf",
        pipeline="steel_dxf_split",
        status="queued",
        attempt=1,
        progress=0,
        precision_level="normal",
        params_json={
            "workflow_id": workflow.id,
            "classification_run_id": classification.id,
            "input_manifest_sha256": classification.input_manifest_sha256,
        },
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(
        db,
        workflow,
        stage_code="drawing_processing",
        job=job,
    )
    db.commit()
    attempts: list[int] = []

    def flaky_dispatch(session, dispatched_job):
        attempts.append(dispatched_job.attempt)
        dispatched_job.status = "failed"
        dispatched_job.error_code = "JOB_ENQUEUE_FAILED"
        dispatched_job.error_message = "fixture broker failure"
        session.commit()
        raise AppHTTPException(
            503,
            "JOB_ENQUEUE_FAILED",
            "fixture broker failure",
        )

    with pytest.raises(AppHTTPException) as dispatch_error:
        dispatch_stage_execution(
            db,
            workflow,
            StageExecutionPlan(job=job, reused=False, retried=False),
            dispatcher=flaky_dispatch,
        )
    assert dispatch_error.value.detail["code"] == "JOB_ENQUEUE_FAILED"

    db.expire_all()
    current_job = db.get(Job, job.id)
    drawing_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "drawing_processing"
    )
    assert attempts == [1]
    assert current_job is not None and current_job.status == "failed"
    assert current_job.attempt == 1
    assert drawing_stage.job_id == job.id
    assert drawing_stage.job_attempt == 1

    from app.platform.config.settings import settings

    monkeypatch.setattr(settings, "dxf_split_pipeline_enabled", True)
    current_job.status = "failed"
    db.commit()
    with pytest.raises(AppHTTPException) as exc_info:
        prepare_stage_execution(
            db,
            workflow,
            stage_code="drawing_processing",
            payload=WorkflowStageExecutionCreate(execution_kind="drawing_processing"),
            current_user=user,
        )
    assert exc_info.value.detail["code"] == "DXF_SPLIT_ATTEMPTS_EXHAUSTED"


def test_partial_split_outcome_with_deliverables_advances_to_excel(db):
    _, _, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)
    _complete_drawing_processing_fixture(db, workflow)
    run = db.scalar(
        select(DxfSplitRun).where(DxfSplitRun.workflow_run_id == workflow.id)
    )
    assert run is not None
    run.status = "completed_with_review"
    run.input_count = 2
    run.processed_count = 2
    run.auto_accepted_count = 1
    run.manual_review_count = 1
    drawing_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "drawing_processing"
    )
    excel_stage = next(
        stage for stage in workflow.stages if stage.stage_code == "excel_stage1"
    )
    drawing_stage.status = "running"
    excel_stage.status = "pending"
    workflow.current_stage = "drawing_processing"
    workflow.status = "running"
    db.flush()

    workflow_service.sync_workflow_from_jobs(db, workflow)

    assert drawing_stage.status == "succeeded"
    assert drawing_stage.output_json == {
        "job_id": run.job_id,
        "job_attempt": 1,
        "split_status": "completed_with_review",
    }
    assert excel_stage.status == "waiting_input"
    assert workflow.current_stage == "excel_stage1"
    assert workflow.status == "waiting_input"


def test_automated_stage_cannot_be_manually_completed(db):
    _, _, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)

    with pytest.raises(AppHTTPException, match="execution endpoint"):
        workflow_service.complete_manual_stage(db, workflow, "drawing_processing")


def test_drawing_processing_contract_requires_all_current_attempt_outputs(db):
    _, _, workflow = _production_workflow(db)
    _advance_to_drawing_processing(db, workflow)

    with pytest.raises(AppHTTPException) as caught:
        require_stage_outputs(workflow, "drawing_processing")

    assert caught.value.detail["code"] == "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
    assert caught.value.detail["details"]["missing_outputs"] == [
        "processed_dxf",
        "weld_allowance_dxf",
        "split_report",
        "weld_allowance_report",
        "validation_report",
        "bh_split_ledger",
        "split_manifest",
    ]


def test_linux_stage_rejects_artifact_type_outside_declared_contract(db):
    _, _, workflow = _production_workflow(db)
    source = _stored_file(db)

    with pytest.raises(AppHTTPException, match="not declared") as error:
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="unrelated_file",
            file_id=source.id,
        )

    assert error.value.detail["code"] == "WORKFLOW_ARTIFACT_TYPE_INVALID"


def test_cancelling_workflow_cancels_bound_active_job(monkeypatch):
    from app.modules.workflows.routes import execution as workflows_api
    from app.platform.config.settings import settings

    client = workflow_test_api.client()
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(client, admin_headers, "prod-cancel")
    project_id = workflow_test_api.create_project(client, owner_headers)
    workflow_id, _ = _api_workflow_at_excel_stage(client, owner_headers, project_id)
    monkeypatch.setattr(settings, "excel_final_pipeline_enabled", True)
    monkeypatch.setattr(workflows_api, "dispatch_committed_job", lambda _db, _job: None)
    executed = client.post(
        f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/executions",
        headers=owner_headers,
        json={"execution_kind": "excel_stage1"},
    )
    assert executed.status_code == 202, executed.text
    job_id = executed.json()["data"]["job"]["id"]

    cancelled = client.post(
        f"/api/v1/workflows/{workflow_id}/cancellation-requests",
        headers=owner_headers,
    )
    job = client.get(f"/api/v1/workflows/jobs/{job_id}", headers=owner_headers)

    assert cancelled.status_code == 200, cancelled.text
    assert job.status_code == 200, job.text
    assert job.json()["data"]["status"] == "cancelled"


def test_linux_production_can_reach_delivery_with_real_jobs_and_handoffs(db):
    """Exercise the complete ten-stage server-side state machine."""
    _, project, workflow = _production_workflow(db)

    def bind_and_complete(
        stage_code: str,
        outputs: tuple[tuple[str, str | StoredFile], ...],
    ):
        for artifact_type, value in outputs:
            stored = value if isinstance(value, StoredFile) else _stored_file(db, name=value)
            workflow_service.attach_artifact(
                db,
                workflow,
                stage_code=stage_code,
                artifact_type=artifact_type,
                file_id=stored.id,
            )
        workflow_service.complete_manual_stage(db, workflow, stage_code)

    def finish_job(stage_code: str, task_type: str, artifact_name: str) -> StoredFile:
        job = Job(
            project_id=project.id,
            task_type=task_type,
            pipeline=task_type,
            status="queued",
            attempt=1,
            progress=0,
            precision_level="normal",
            params_json={},
        )
        db.add(job)
        db.flush()
        workflow_service.bind_stage_job(db, workflow, stage_code=stage_code, job=job)
        output = _stored_file(db, name=artifact_name)
        db.add(
            AnalysisResult(
                job_id=job.id,
                result_type=task_type,
                result_file_id=output.id,
                status="succeeded",
            )
        )
        job.status = "succeeded"
        job.progress = 100
        db.flush()
        workflow_service.sync_workflow_from_jobs(db, workflow)
        return output

    _advance_to_drawing_processing(db, workflow)
    _complete_drawing_processing_fixture(db, workflow)
    finish_job("excel_stage1", "process_excel_final", "stage1.xlsx")
    stage2 = _stored_file(db, name="stage2.xlsx")
    bind_and_complete(
        "excel_stage2",
        (("stage2_excel", stage2),),
    )
    bind_and_complete(
        "design_barrier",
        (("review_record", "review-record.json"),),
    )
    bind_and_complete(
        "cam_packaging",
        (
            ("cam_input_dxf", "cam-input.dxf"),
            ("cam_package_manifest", "cam-package-manifest.json"),
        ),
    )
    bind_and_complete(
        "windows_cam",
        (("cam_output_dxf", "cam-output.dxf"),),
    )
    bind_and_complete(
        "result_acceptance",
        (
            ("accepted_dxf", "accepted.dxf"),
            ("acceptance_report", "acceptance-report.json"),
        ),
    )
    bind_and_complete(
        "delivery_archive",
        (
            ("delivery_dxf", "delivery.dxf"),
            ("delivery_excel", stage2),
            ("archive_manifest", "archive-manifest.json"),
        ),
    )

    assert workflow.status == "succeeded"
    assert workflow.progress == 100
    assert workflow.current_stage == "delivery_archive"
    assert [stage.status for stage in workflow.stages] == ["succeeded"] * 10
    assert {artifact.artifact_type for artifact in workflow.artifacts} == {
        "source_dwg",
        "source_excel",
        "canonical_dxf",
        "classified_dxf",
        "classification_report",
        "classification_manifest",
        "processed_dxf",
        "weld_allowance_dxf",
        "split_report",
        "weld_allowance_report",
        "validation_report",
        "bh_split_ledger",
        "split_manifest",
        "stage1_excel",
        "stage2_excel",
        "review_record",
        "cam_input_dxf",
        "cam_package_manifest",
        "cam_output_dxf",
        "accepted_dxf",
        "acceptance_report",
        "delivery_dxf",
        "delivery_excel",
        "archive_manifest",
    }
