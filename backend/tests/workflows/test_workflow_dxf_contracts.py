from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.projects.interface import Project, ProjectMember
from app.modules.workflows import interface as workflow_service
from app.modules.workflows.contracts import require_stage_inputs, require_stage_outputs
from app.modules.workflows.schemas import WorkflowCreate
from app.platform.http.exceptions import AppHTTPException

EXPECTED_DRAWING_FLOW = {
    "source_intake": {
        "required_inputs": ["dwg_files", "excel_file"],
        "artifact_types": ["source_dwg", "source_excel", "canonical_dxf"],
        "required_outputs": ["source_dwg", "source_excel", "canonical_dxf"],
    },
    "dxf_classification": {
        "required_inputs": ["canonical_dxf"],
        "artifact_types": [
            "classified_dxf",
            "classification_report",
            "classification_manifest",
        ],
        "required_outputs": [
            "classified_dxf",
            "classification_report",
            "classification_manifest",
        ],
    },
    "drawing_processing": {
        "required_inputs": ["classified_dxf"],
        "artifact_types": [
            "processed_dxf",
            "weld_allowance_dxf",
            "split_report",
            "weld_allowance_report",
            "validation_report",
            "bh_split_ledger",
            "split_manifest",
        ],
        "required_outputs": [
            "processed_dxf",
            "weld_allowance_dxf",
            "split_report",
            "weld_allowance_report",
            "validation_report",
            "bh_split_ledger",
            "split_manifest",
        ],
    },
    "excel_stage1": {
        "required_inputs": [
            "source_excel",
            "processed_dxf",
            "bh_split_ledger",
        ],
        "artifact_types": ["stage1_excel"],
        "required_outputs": ["stage1_excel"],
    },
    "excel_stage2": {
        "required_inputs": ["stage1_excel", "processed_dxf"],
        "artifact_types": ["stage2_excel"],
        "required_outputs": ["stage2_excel"],
    },
    "design_barrier": {
        "required_inputs": ["processed_dxf", "stage2_excel"],
        "artifact_types": ["review_record"],
        "required_outputs": ["review_record"],
    },
    "cam_packaging": {
        "required_inputs": ["processed_dxf", "stage2_excel", "review_record"],
        "artifact_types": ["cam_input_dxf", "cam_package_manifest"],
        "required_outputs": ["cam_input_dxf", "cam_package_manifest"],
    },
    "windows_cam": {
        "required_inputs": ["cam_input_dxf", "cam_package_manifest"],
        "artifact_types": ["cam_output_dxf", "runner_diagnostics"],
        "required_outputs": ["cam_output_dxf"],
    },
    "result_acceptance": {
        "required_inputs": ["cam_output_dxf"],
        "artifact_types": ["accepted_dxf", "acceptance_report"],
        "required_outputs": ["accepted_dxf", "acceptance_report"],
    },
    "delivery_archive": {
        "required_inputs": ["accepted_dxf", "stage2_excel", "acceptance_report"],
        "artifact_types": ["delivery_dxf", "delivery_excel", "archive_manifest"],
        "required_outputs": ["delivery_dxf", "delivery_excel", "archive_manifest"],
    },
}


def _owner_project(db):
    user = User(
        username=f"dxf-contract-{uuid4().hex[:10]}",
        password_hash="not-used",
        real_name="DXF Contract Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"DXF-{uuid4().hex[:8]}",
        name="DXF canonical contract",
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
    db.flush()
    return user, project


def _production_workflow(db):
    user, project = _owner_project(db)
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="DXF canonical",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    db.flush()
    return user, project, workflow


def _stored_file(db, *, name: str, uploaded_by: int) -> StoredFile:
    extension = "." + name.rsplit(".", 1)[-1].lower()
    stored = StoredFile(
        bucket="workflow-contract-tests",
        storage_key=f"tests/{uuid4().hex}{extension}",
        original_name=name,
        file_ext=extension,
        content_type="application/octet-stream",
        size_bytes=32,
        sha256=uuid4().hex + uuid4().hex,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def _result(
    db,
    *,
    project_id: int,
    created_by: int,
    file_id: int,
) -> AnalysisResult:
    job = Job(
        project_id=project_id,
        created_by=created_by,
        task_type="drawing_processing",
        precision_level="normal",
        status="succeeded",
        progress=100,
        attempt=1,
    )
    db.add(job)
    db.flush()
    result = AnalysisResult(
        job_id=job.id,
        result_type="drawing_processing",
        result_file_id=file_id,
        status="succeeded",
    )
    db.add(result)
    db.flush()
    return result


def _set_current_stage(workflow, stage_code: str) -> None:
    target = next(stage for stage in workflow.stages if stage.stage_code == stage_code)
    for stage in workflow.stages:
        if stage.sequence < target.sequence:
            stage.status = "succeeded"
            stage.progress = 100
        elif stage.id == target.id:
            stage.status = "waiting_input"
            stage.progress = 0
        else:
            stage.status = "pending"
            stage.progress = 0
    workflow.current_stage = stage_code
    workflow.status = "waiting_input"


def test_linux_production_exposes_exact_dxf_canonical_contract():
    production = next(
        template
        for template in workflow_service.list_workflow_templates()
        if template.code == "linux_production"
    )

    actual = {
        stage.code: {
            "required_inputs": stage.required_inputs,
            "artifact_types": stage.artifact_types,
            "required_outputs": stage.required_outputs,
        }
        for stage in production.stages
    }

    assert actual == EXPECTED_DRAWING_FLOW


def test_linux_production_has_no_generic_drawing_artifacts():
    forbidden = {
        "source_file",
        "derived_dxf",
        "frozen_derived_dxf",
        "drawing_files",
        "processed_drawing",
        "processed_drawings",
        "cam_result",
        "delivery_file",
    }
    production = next(
        template
        for template in workflow_service.list_workflow_templates()
        if template.code == "linux_production"
    )

    published = {
        value
        for stage in production.stages
        for value in (
            *stage.required_inputs,
            *stage.artifact_types,
            *stage.required_outputs,
        )
    }

    assert published.isdisjoint(forbidden)


def test_new_linux_workflow_uses_definition_revision_four(db):
    user, project = _owner_project(db)

    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="DXF canonical",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )

    assert workflow.config_json == {"definition_revision": 4}


def test_dxf_artifact_rejects_excel_file(db):
    user, _, workflow = _production_workflow(db)
    excel_file = _stored_file(db, name="wrong.xlsx", uploaded_by=user.id)

    with pytest.raises(AppHTTPException) as caught:
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=excel_file.id,
        )

    assert caught.value.detail["code"] == "WORKFLOW_ARTIFACT_FORMAT_INVALID"


def test_artifact_rejects_result_file_mismatch(db):
    user, project, workflow = _production_workflow(db)
    dxf_file = _stored_file(db, name="drawing.dxf", uploaded_by=user.id)
    other_dxf_file = _stored_file(db, name="other.dxf", uploaded_by=user.id)
    result = _result(
        db,
        project_id=project.id,
        created_by=user.id,
        file_id=other_dxf_file.id,
    )

    with pytest.raises(AppHTTPException) as caught:
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=dxf_file.id,
            result_id=result.id,
        )

    assert caught.value.detail["code"] == "WORKFLOW_ARTIFACT_RESULT_FILE_MISMATCH"


def test_artifact_rejects_cross_project_result(db):
    user, _, workflow = _production_workflow(db)
    foreign_user, foreign_project = _owner_project(db)
    dxf_file = _stored_file(db, name="drawing.dxf", uploaded_by=user.id)
    foreign_result = _result(
        db,
        project_id=foreign_project.id,
        created_by=foreign_user.id,
        file_id=dxf_file.id,
    )

    with pytest.raises(AppHTTPException) as caught:
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="drawing_processing",
            artifact_type="processed_dxf",
            file_id=dxf_file.id,
            result_id=foreign_result.id,
        )

    assert caught.value.detail["code"] == "WORKFLOW_ARTIFACT_PROJECT_MISMATCH"


def test_result_only_artifact_keeps_production_zip_download_boundary(db):
    user, project, workflow = _production_workflow(db)
    result_file = _stored_file(db, name="processed.dxf", uploaded_by=user.id)
    result = _result(
        db,
        project_id=project.id,
        created_by=user.id,
        file_id=result_file.id,
    )
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="drawing_processing",
        artifact_type="processed_dxf",
        result_id=result.id,
    )

    assert workflow_service.find_production_file_workflow_id(db, result_file.id) == workflow.id


def test_drawing_processing_requires_classified_dxf(db):
    _, _, workflow = _production_workflow(db)
    _set_current_stage(workflow, "drawing_processing")

    with pytest.raises(AppHTTPException) as caught:
        require_stage_inputs(workflow, "drawing_processing")

    assert caught.value.detail["code"] == "WORKFLOW_STAGE_INPUT_INCOMPLETE"
    assert caught.value.detail["details"]["missing_inputs"] == ["classified_dxf"]
    assert caught.value.detail["message"] == (
        "当前阶段缺少必需的上游产物：classified_dxf。"
        "请返回前序阶段补齐后重新检查。"
    )


def test_drawing_processing_requires_all_current_attempt_outputs(db):
    user, _, workflow = _production_workflow(db)
    _set_current_stage(workflow, "drawing_processing")
    classified_dxf = _stored_file(
        db,
        name="classified.dxf",
        uploaded_by=user.id,
    )
    processed_dxf = _stored_file(
        db,
        name="processed.dxf",
        uploaded_by=user.id,
    )
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="dxf_classification",
        artifact_type="classified_dxf",
        file_id=classified_dxf.id,
    )
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="drawing_processing",
        artifact_type="processed_dxf",
        file_id=processed_dxf.id,
        metadata={"job_id": None, "job_attempt": None},
    )

    with pytest.raises(AppHTTPException) as caught:
        require_stage_outputs(workflow, "drawing_processing")

    assert caught.value.detail["code"] == "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
    assert caught.value.detail["details"]["missing_outputs"] == [
        "weld_allowance_dxf",
        "split_report",
        "weld_allowance_report",
        "validation_report",
        "bh_split_ledger",
        "split_manifest",
    ]


def test_succeeded_job_without_required_output_fails_workflow_stage(db):
    user, project, workflow = _production_workflow(db)
    _set_current_stage(workflow, "excel_stage1")
    job = Job(
        project_id=project.id,
        created_by=user.id,
        task_type="process_excel_final",
        precision_level="normal",
        status="succeeded",
        progress=100,
        attempt=1,
    )
    db.add(job)
    db.flush()
    workflow_service.bind_stage_job(
        db,
        workflow,
        stage_code="excel_stage1",
        job=job,
    )
    job.status = "succeeded"
    job.progress = 100
    db.flush()

    workflow_service.sync_workflow_from_jobs(db, workflow)

    stage = next(value for value in workflow.stages if value.stage_code == "excel_stage1")
    assert stage.status == "failed"
    assert stage.error_code == "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
    assert workflow.current_stage == "excel_stage1"
