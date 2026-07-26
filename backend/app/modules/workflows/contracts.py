"""Canonical artifact and stage-lineage invariants for production workflows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, validate_dxf_structure
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.intake import registration
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.templates import get_stage_capability
from app.platform.config.constants import EXCEL_FILE_EXTENSIONS
from app.platform.http.exceptions import AppHTTPException

DXF_ARTIFACT_TYPES = frozenset(
    {
        "canonical_dxf",
        "classified_dxf",
        "processed_dxf",
        "weld_allowance_dxf",
        "cam_input_dxf",
        "cam_output_dxf",
        "accepted_dxf",
        "delivery_dxf",
    }
)
DWG_ARTIFACT_TYPES = frozenset({"source_dwg"})
EXCEL_ARTIFACT_TYPES = frozenset(
    {"source_excel", "stage1_excel", "delivery_excel", "bh_split_ledger"}
)


def _expected_extensions(artifact_type: str) -> set[str] | None:
    if artifact_type in DXF_ARTIFACT_TYPES:
        return {".dxf"}
    if artifact_type in DWG_ARTIFACT_TYPES:
        return {".dwg"}
    if artifact_type in EXCEL_ARTIFACT_TYPES:
        return set(EXCEL_FILE_EXTENSIONS)
    return None


def validate_artifact_reference(
    db: Session,
    workflow: WorkflowRun,
    *,
    artifact_type: str,
    file_id: int | None,
    result_id: int | None,
) -> None:
    result: AnalysisResult | None = None
    if result_id is not None:
        result = db.get(AnalysisResult, result_id)
        if result is None:
            raise AppHTTPException(404, "RESULT_NOT_FOUND", "Result not found.")
        job = db.get(Job, result.job_id)
        if job is None:
            raise AppHTTPException(404, "JOB_NOT_FOUND", "Job not found.")
        if (
            workflow.workflow_type == "linux_production"
            and job.project_id != workflow.project_id
        ):
            raise AppHTTPException(
                409,
                "WORKFLOW_ARTIFACT_PROJECT_MISMATCH",
                "The result belongs to another project.",
            )
        if file_id is not None and result.result_file_id != file_id:
            raise AppHTTPException(
                409,
                "WORKFLOW_ARTIFACT_RESULT_FILE_MISMATCH",
                "The result and file references do not describe the same artifact.",
            )

    effective_file_id = file_id
    if effective_file_id is None and result is not None:
        effective_file_id = result.result_file_id
    expected = _expected_extensions(artifact_type)
    if expected is None:
        return
    stored = (
        db.get(StoredFile, effective_file_id)
        if effective_file_id is not None
        else None
    )
    if (
        stored is None
        or stored.status == "deleted"
        or (stored.file_ext or "").lower() not in expected
    ):
        raise AppHTTPException(
            422,
            "WORKFLOW_ARTIFACT_FORMAT_INVALID",
            "The workflow artifact does not have the required file format.",
            {
                "artifact_type": artifact_type,
                "file_id": effective_file_id,
                "expected_extensions": sorted(expected),
            },
        )


def _artifact_types_before_stage(
    workflow: WorkflowRun,
    stage_code: str,
) -> set[str]:
    stage = next(
        value for value in workflow.stages if value.stage_code == stage_code
    )
    prior_stage_ids = {
        value.id for value in workflow.stages if value.sequence < stage.sequence
    }
    return {
        artifact.artifact_type
        for artifact in workflow.artifacts
        if artifact.stage_run_id in prior_stage_ids
    }


def require_stage_inputs(workflow: WorkflowRun, stage_code: str) -> None:
    capability = get_stage_capability(workflow, stage_code)
    if stage_code == "source_intake":
        return
    available = _artifact_types_before_stage(workflow, stage_code)
    required_inputs = capability.required_inputs
    if stage_code == "excel_stage1":
        drawing_stage = next(
            (
                stage
                for stage in workflow.stages
                if stage.stage_code == "drawing_processing"
            ),
            None,
        )
        if (
            drawing_stage is not None
            and drawing_stage.status == "skipped"
            and isinstance(drawing_stage.output_json, dict)
            and drawing_stage.output_json.get("reason") == "no_split_candidates"
        ):
            required_inputs = ["source_excel"]
    missing = [
        value for value in required_inputs if value not in available
    ]
    if missing:
        missing_text = "、".join(missing)
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_INPUT_INCOMPLETE",
            f"当前阶段缺少必需的上游产物：{missing_text}。"
            "请返回前序阶段补齐后重新检查。",
            {"stage_code": stage_code, "missing_inputs": missing},
        )


def require_stage_outputs(workflow: WorkflowRun, stage_code: str) -> None:
    capability = get_stage_capability(workflow, stage_code)
    stage = next(
        value for value in workflow.stages if value.stage_code == stage_code
    )
    artifacts = stage.artifacts
    if stage_code == "drawing_processing":
        artifacts = [
            artifact
            for artifact in artifacts
            if isinstance(artifact.metadata_json, dict)
            and artifact.metadata_json.get("job_id") == stage.job_id
            and artifact.metadata_json.get("job_attempt") == stage.job_attempt
        ]
    available = {artifact.artifact_type for artifact in artifacts}
    missing = [
        value for value in capability.required_outputs if value not in available
    ]
    if missing:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_OUTPUT_INCOMPLETE",
            "The workflow stage has not produced every required artifact.",
            {"stage_code": stage_code, "missing_outputs": missing},
        )


def verify_required_dxf_objects(
    db: Session,
    workflow: WorkflowRun,
    stage_code: str,
) -> None:
    capability = get_stage_capability(workflow, stage_code)
    required_dxf = set(capability.required_outputs) & DXF_ARTIFACT_TYPES
    if not required_dxf:
        return
    stage = next(
        value for value in workflow.stages if value.stage_code == stage_code
    )
    artifacts = stage.artifacts
    if stage_code == "drawing_processing":
        artifacts = [
            artifact
            for artifact in artifacts
            if isinstance(artifact.metadata_json, dict)
            and artifact.metadata_json.get("job_id") == stage.job_id
            and artifact.metadata_json.get("job_attempt") == stage.job_attempt
        ]
    for artifact in artifacts:
        if artifact.artifact_type not in required_dxf:
            continue
        file_id = artifact.file_id
        if file_id is None and artifact.result_id is not None:
            result = db.get(AnalysisResult, artifact.result_id)
            file_id = result.result_file_id if result is not None else None
        stored = db.get(StoredFile, file_id) if file_id is not None else None
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "WORKFLOW_ARTIFACT_FILE_MISSING",
                "A required workflow DXF is unavailable.",
                {"artifact_id": artifact.id, "file_id": file_id},
            )
        payload = registration.read_verified_input_object(stored)
        try:
            validate_dxf_structure(payload)
        except AppHTTPException as exc:
            raise AppHTTPException(
                422,
                "WORKFLOW_ARTIFACT_FORMAT_INVALID",
                "A required workflow drawing is not a readable DXF.",
                {
                    "artifact_id": artifact.id,
                    "file_id": stored.id,
                    "artifact_type": artifact.artifact_type,
                },
            ) from exc
