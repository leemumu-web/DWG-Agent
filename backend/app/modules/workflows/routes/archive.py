"""Workflow archives plus the single-file Excel stage result."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.dxf_splitting.interface import get_dxf_split_outcome
from app.modules.files.interface import (
    StoredFile,
    TransferSpec,
    build_registered_files_zip_to_path,
    prepare_transfer_in_transaction,
    sanitize_filename,
    session_factory_for,
    settle_stream,
)
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_member
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.contracts import (
    _current_attempt_artifacts,
    require_stage_outputs,
)
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.modules.workflows.models import WorkflowArtifact, WorkflowRun, WorkflowStageRun
from app.platform.config.constants import TASK_EXCEL_FINAL, TASK_EXCEL_STAGE2
from app.platform.http.dependencies import get_db
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import StorageError, StorageObjectNotFound

router = APIRouter()
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class ExcelResultDownload:
    stage_code: str
    stage_label: str
    task_type: str
    artifact_type: str
    operation: str
    audit_action: str
    allow_failed_diagnostic: bool = False


_EXCEL_STAGE1_RESULT = ExcelResultDownload(
    stage_code="excel_stage1",
    stage_label="Excel 第一阶段",
    task_type=TASK_EXCEL_FINAL,
    artifact_type="stage1_excel",
    operation="workflow_excel_result_download",
    audit_action="workflow_excel_results.download",
)
_EXCEL_STAGE2_RESULT = ExcelResultDownload(
    stage_code="excel_stage2",
    stage_label="Excel 第二阶段",
    task_type=TASK_EXCEL_STAGE2,
    artifact_type="stage2_excel",
    operation="workflow_excel_stage2_result_download",
    audit_action="workflow_excel_stage2_results.download",
)
_EXCEL_STAGE2_READER_RESULT = ExcelResultDownload(
    stage_code="excel_stage2",
    stage_label="BH 左右进读取",
    task_type=TASK_EXCEL_STAGE2,
    artifact_type="bh_setback_excel",
    operation="workflow_excel_stage2_reader_download",
    audit_action="workflow_excel_stage2_reader_results.download",
    allow_failed_diagnostic=True,
)
_EXCEL_STAGE2_BOX_READER_RESULT = ExcelResultDownload(
    stage_code="excel_stage2",
    stage_label="BOX 左右进读取",
    task_type=TASK_EXCEL_STAGE2,
    artifact_type="box_setback_excel",
    operation="workflow_excel_stage2_box_reader_download",
    audit_action="workflow_excel_stage2_box_reader_results.download",
    allow_failed_diagnostic=True,
)


def _collect_archive_members(
    db: Session,
    current_user: CurrentUser,
    workflow: WorkflowRun,
    *,
    stage_code: str | None = None,
) -> tuple[list[tuple[int, str]], WorkflowStageRun | None]:
    stage_by_id = {stage.id: stage for stage in workflow.stages}
    selected_stage = next(
        (stage for stage in workflow.stages if stage.stage_code == stage_code),
        None,
    )
    if stage_code is not None and selected_stage is None:
        return [], None
    exportable_drawing_attempts: dict[int, bool] = {}
    for stage in workflow.stages:
        if stage.stage_code != "drawing_processing" or stage.job_id is None:
            continue
        job = db.get(Job, stage.job_id)
        outcome = (
            get_dxf_split_outcome(
                db,
                job_id=stage.job_id,
                attempt=stage.job_attempt,
            )
            if stage.job_attempt is not None
            else None
        )
        exportable_drawing_attempts[stage.id] = bool(
            job is not None
            and job.project_id == workflow.project_id
            and job.status == "succeeded"
            and outcome in {"completed", "completed_with_review"}
        )
    artifacts = []
    current_attempt_artifact_ids = {
        artifact.id for stage in workflow.stages for artifact in _current_attempt_artifacts(stage)
    }
    final_split_manifest_stage_ids = {
        artifact.stage_run_id
        for artifact in workflow.artifacts
        if artifact.artifact_type == "split_manifest"
        and isinstance(artifact.metadata_json, dict)
        and artifact.metadata_json.get("final_review") is True
    }
    for artifact in workflow.artifacts:
        if selected_stage is not None and artifact.stage_run_id != selected_stage.id:
            continue
        artifact_stage = stage_by_id.get(artifact.stage_run_id)
        if (
            artifact_stage is not None
            and artifact_stage.job_id is not None
            and artifact.id not in current_attempt_artifact_ids
        ):
            continue
        if (
            artifact.artifact_type == "split_manifest"
            and artifact.stage_run_id in final_split_manifest_stage_ids
            and (
                not isinstance(artifact.metadata_json, dict)
                or artifact.metadata_json.get("final_review") is not True
            )
        ):
            continue
        if artifact_stage is not None and artifact_stage.stage_code == "drawing_processing":
            if not exportable_drawing_attempts.get(artifact_stage.id, False):
                continue
        artifacts.append(artifact)
    members: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    for artifact in sorted(
        artifacts,
        key=lambda value: (
            stage_by_id[value.stage_run_id].sequence if value.stage_run_id in stage_by_id else 999,
            value.id,
        ),
    ):
        file_id = artifact.file_id
        if file_id is None and artifact.result_id is not None:
            result = db.get(AnalysisResult, artifact.result_id)
            file_id = result.result_file_id if result is not None else None
        stored = db.get(StoredFile, file_id) if file_id is not None else None
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "WORKFLOW_ARCHIVE_ARTIFACT_MISSING",
                "A registered workflow artifact is unavailable.",
                {"artifact_id": artifact.id, "file_id": file_id},
            )
        stage = stage_by_id.get(artifact.stage_run_id)
        if stage is not None and stage.stage_code == "drawing_processing":
            job = db.get(Job, stage.job_id) if stage.job_id is not None else None
            if job is None or job.project_id != workflow.project_id:
                raise AppHTTPException(
                    409,
                    "WORKFLOW_ARCHIVE_JOB_MISMATCH",
                    "拆板产物没有绑定当前项目的正式 Job。",
                    {"artifact_id": artifact.id, "job_id": stage.job_id},
                )
        sequence = stage.sequence if stage is not None else 99
        code = stage.stage_code if stage is not None else "workflow"
        original_name = sanitize_filename(stored.original_name)
        relative_path = (
            f"workflow-{workflow.id}/{sequence:02d}_{code}/{artifact.artifact_type}/{original_name}"
        )
        if relative_path.casefold() in seen_paths:
            relative_path = (
                f"workflow-{workflow.id}/{sequence:02d}_{code}/"
                f"{artifact.artifact_type}/{stored.id}-{original_name}"
            )
        seen_paths.add(relative_path.casefold())
        members.append((stored.id, relative_path))
    return members, selected_stage


def stream_registered_workflow_archive(
    db: Session,
    request: Request,
    current_user: CurrentUser,
    workflow: WorkflowRun,
    members: list[tuple[int, str]],
    archive_name: str,
    *,
    operation: str,
    audit_action: str,
    inline_members: dict[str, bytes] | None = None,
) -> StreamingResponse:
    prepared = build_registered_files_zip_to_path(
        db,
        members,
        archive_name,
        inline_members=inline_members,
    )
    try:
        transfer = prepare_transfer_in_transaction(
            db,
            TransferSpec(
                direction="outbound",
                operation=operation,
                actor_user_id=current_user.id,
                request_id=request.state.request_id,
                idempotency_key=request.state.request_id,
                batch_ref=archive_name,
                original_name=prepared.filename,
                expected_bytes=prepared.size_bytes,
            ),
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action=audit_action,
            resource_type="workflow",
            resource_id=workflow.id,
            after_json={
                "file_ids": list(prepared.included_file_ids),
                "artifact_count": len(members),
                "inline_member_count": len(inline_members or {}),
            },
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        prepared.path.unlink(missing_ok=True)
        raise

    def stream_and_cleanup():
        try:
            with prepared.path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    yield chunk
        finally:
            prepared.path.unlink(missing_ok=True)

    encoded_filename = quote(prepared.filename)
    return StreamingResponse(
        settle_stream(
            session_factory_for(db),
            transfer.transfer_uid,
            stream_and_cleanup(),
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(prepared.size_bytes),
        },
    )


@router.get(
    "/{workflow_id}/download-archive",
    summary="下载完整工作流压缩包",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/zip": {
                    "schema": {"type": "string", "format": "binary"},
                }
            }
        }
    },
    description=(
        "把当前所有已登记生产 artifact 按阶段和类型写入一个 ZIP；工作流不提供单个 artifact 下载。"
    ),
)
def download_workflow_archive(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    members, _ = _collect_archive_members(db, current_user, workflow)

    if not members:
        raise AppHTTPException(
            409,
            "WORKFLOW_ARCHIVE_EMPTY",
            "The workflow has no downloadable production artifacts.",
        )
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}",
        operation="workflow_download_zip",
        audit_action="workflow_archives.download",
    )


@router.get(
    "/{workflow_id}/stages/{stage_code}/download-archive",
    summary="下载阶段结果压缩包",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/zip": {
                    "schema": {"type": "string", "format": "binary"},
                }
            }
        }
    },
    description=(
        "把指定普通生产阶段所有已登记 artifact 写入一个 ZIP；两个 Excel 处理阶段必须"
        "改用各自的单文件 .xlsx 下载入口。"
    ),
)
def download_workflow_stage_archive(
    workflow_id: int,
    stage_code: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if stage_code in {"excel_stage1", "excel_stage2"}:
        download_paths = (
            [(f"/api/v1/workflows/{workflow_id}/stages/excel_stage1/download-result")]
            if stage_code == "excel_stage1"
            else [
                (f"/api/v1/workflows/{workflow_id}/stages/excel_stage2/download-reader-result"),
                (f"/api/v1/workflows/{workflow_id}/stages/excel_stage2/download-result"),
            ]
        )
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_SINGLE_FILE_DOWNLOAD_REQUIRED",
            "Excel 处理结果必须通过对应的单文件下载入口获取，不能打包为阶段 ZIP。",
            {
                "download_path": download_paths[0],
                "download_paths": download_paths,
            },
        )
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    members, stage = _collect_archive_members(
        db,
        current_user,
        workflow,
        stage_code=stage_code,
    )
    if stage is None:
        raise AppHTTPException(
            404,
            "WORKFLOW_STAGE_UNKNOWN",
            "Workflow stage was not found.",
        )
    if not members:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_ARCHIVE_EMPTY",
            "The workflow stage has no downloadable production artifacts.",
        )
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-{stage.sequence:02d}_{stage.stage_code}",
        operation="workflow_stage_download_zip",
        audit_action="workflow_stage_archives.download",
    )


def _sync_and_get_excel_stage(
    db: Session,
    workflow: WorkflowRun,
    *,
    stage_code: str,
) -> tuple[WorkflowRun, WorkflowStageRun | None]:
    stage = next(
        (item for item in workflow.stages if item.stage_code == stage_code),
        None,
    )
    if stage is not None and stage.job_id is not None:
        job = db.get(Job, stage.job_id)
        if (
            job is not None
            and job.attempt == stage.job_attempt
            and (
                stage.status != job.status
                or (job.status == "succeeded" and not _current_attempt_artifacts(stage))
            )
        ):
            sync_workflow_from_jobs(db, workflow)
            db.commit()
            workflow = load_workflow_detail(db, workflow.id)
            stage = next(
                (item for item in workflow.stages if item.stage_code == stage_code),
                None,
            )
    return workflow, stage


def _matching_current_attempt_results(
    db: Session,
    job: Job,
    *,
    artifact_type: str,
) -> list[AnalysisResult]:
    results = list(
        db.scalars(
            select(AnalysisResult).where(
                AnalysisResult.job_id == job.id,
                AnalysisResult.status == "succeeded",
            )
        ).all()
    )
    return [
        result
        for result in results
        if result.result_type in {TASK_EXCEL_FINAL, TASK_EXCEL_STAGE2}
        and isinstance(result.result_json, dict)
        and result.result_json.get("job_attempt") == job.attempt
        and result.result_json.get("workflow_artifact_type") == artifact_type
    ]


def _resolve_excel_result(
    db: Session,
    workflow: WorkflowRun,
    *,
    spec: ExcelResultDownload,
) -> tuple[WorkflowRun, WorkflowStageRun, Job, AnalysisResult, StoredFile]:
    workflow, stage = _sync_and_get_excel_stage(
        db,
        workflow,
        stage_code=spec.stage_code,
    )
    if stage is None or stage.job_id is None or stage.job_attempt is None:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_NOT_READY",
            f"{spec.stage_label}尚未形成可下载的正式结果。",
            {
                "stage_status": stage.status if stage is not None else None,
                "job_id": stage.job_id if stage is not None else None,
            },
        )
    job = db.get(Job, stage.job_id)
    if (
        job is None
        or job.project_id != workflow.project_id
        or job.task_type != spec.task_type
        or job.attempt != stage.job_attempt
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_BINDING_INVALID",
            "Excel 阶段绑定的任务与当前项目或 attempt 不一致。",
            {
                "stage_job_id": stage.job_id,
                "stage_job_attempt": stage.job_attempt,
            },
        )

    result: AnalysisResult | None = None
    artifact: WorkflowArtifact | None = None
    if job.status == "succeeded" and stage.status == "succeeded":
        if spec.stage_code == "excel_stage2":
            require_stage_outputs(workflow, spec.stage_code)
        artifacts = [
            item
            for item in _current_attempt_artifacts(stage)
            if item.artifact_type == spec.artifact_type
        ]
        if len(artifacts) != 1:
            raise AppHTTPException(
                409,
                "EXCEL_STAGE_RESULT_CARDINALITY_INVALID",
                f"{spec.stage_label}必须且只能登记一个当前批次结果文件。",
                {"artifact_count": len(artifacts)},
            )
        artifact = artifacts[0]
        result = (
            db.get(AnalysisResult, artifact.result_id) if artifact.result_id is not None else None
        )
    elif spec.allow_failed_diagnostic and job.status == "failed":
        candidates = [
            item
            for item in _matching_current_attempt_results(
                db,
                job,
                artifact_type=spec.artifact_type,
            )
            if item.result_type == spec.task_type
            and item.result_json.get("diagnostic_only") is True
        ]
        if len(candidates) > 1:
            raise AppHTTPException(
                409,
                "EXCEL_STAGE_RESULT_CARDINALITY_INVALID",
                f"{spec.stage_label}存在多个当前批次诊断文件，不能自动选择。",
                {"result_count": len(candidates)},
            )
        result = candidates[0] if candidates else None
    if result is None:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_NOT_READY",
            f"{spec.stage_label}尚未形成可下载的当前批次结果。",
            {
                "stage_status": stage.status,
                "job_status": job.status,
                "job_id": job.id,
                "job_attempt": job.attempt,
            },
        )

    result_json = result.result_json if isinstance(result.result_json, dict) else {}
    artifact_metadata = (
        artifact.metadata_json
        if artifact is not None and isinstance(artifact.metadata_json, dict)
        else {}
    )
    artifact_file_id = artifact.file_id if artifact is not None else result.result_file_id
    if (
        result.job_id != job.id
        or result.result_type != spec.task_type
        or result.status != "succeeded"
        or result.result_file_id != artifact_file_id
        or result_json.get("job_attempt") != job.attempt
        or result_json.get("workflow_artifact_type") != spec.artifact_type
        or (
            artifact is not None
            and (
                artifact_metadata.get("job_id") != job.id
                or artifact_metadata.get("job_attempt") != job.attempt
            )
        )
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_BINDING_INVALID",
            "Excel 产物、分析结果与阶段任务的来源链不一致。",
            {
                "artifact_id": artifact.id if artifact is not None else None,
                "artifact_file_id": artifact_file_id,
                "result_id": result.id,
            },
        )
    if spec.stage_code == "excel_stage2":
        stage2_status = result_json.get("stage2_status")
        if artifact is not None and stage2_status not in {
            "complete",
            "partial",
            "noop",
        }:
            raise AppHTTPException(
                409,
                "EXCEL_STAGE_RESULT_BINDING_INVALID",
                "Excel 第二阶段结果状态与正式阶段不一致。",
                {"stage2_status": stage2_status, "result_id": result.id},
            )

    stored = db.get(StoredFile, artifact_file_id) if artifact_file_id else None
    if stored is None or stored.status != "available":
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_FILE_UNAVAILABLE",
            f"{spec.stage_label}结果的文件登记不可用。",
            {"file_id": artifact_file_id},
        )
    if stored.file_ext.casefold() != ".xlsx":
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_FORMAT_INVALID",
            f"{spec.stage_label}结果必须是 xlsx 文件。",
            {"file_id": stored.id, "file_ext": stored.file_ext},
        )
    return workflow, stage, job, result, stored


def _stream_excel_result(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session,
    *,
    spec: ExcelResultDownload,
) -> StreamingResponse:
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    workflow, stage, job, _, stored = _resolve_excel_result(
        db,
        workflow,
        spec=spec,
    )
    storage = storage_factory.get_storage_backend()
    try:
        object_info = storage.stat_object(stored.bucket, stored.storage_key)
    except StorageObjectNotFound:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_OBJECT_MISSING",
            "Excel 正式结果的存储对象不存在，请重新执行本阶段。",
            {"file_id": stored.id},
        ) from None
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE_RESULT_STORAGE_FAILED",
            "Excel 正式结果的对象存储暂时不可读。",
            {"file_id": stored.id},
        ) from exc
    if object_info.size_bytes != stored.size_bytes:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_OBJECT_CHANGED",
            "Excel 正式结果的对象大小与登记记录不一致。",
            {
                "file_id": stored.id,
                "registered_size": stored.size_bytes,
                "object_size": object_info.size_bytes,
            },
        )
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="outbound",
            operation=spec.operation,
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            idempotency_key=request.state.request_id,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            expected_bytes=object_info.size_bytes,
        ),
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=spec.audit_action,
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "stage_code": stage.stage_code,
            "artifact_type": spec.artifact_type,
            "job_id": job.id,
            "job_attempt": job.attempt,
            "file_id": stored.id,
        },
        request=request,
    )
    db.commit()
    encoded_filename = quote(sanitize_filename(stored.original_name))
    return StreamingResponse(
        settle_stream(
            session_factory_for(db),
            transfer.transfer_uid,
            storage.iter_file(stored.bucket, stored.storage_key),
        ),
        media_type=XLSX_CONTENT_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(object_info.size_bytes),
        },
    )


_XLSX_RESPONSE = {
    200: {
        "content": {
            XLSX_CONTENT_TYPE: {
                "schema": {"type": "string", "format": "binary"},
            }
        }
    }
}


@router.get(
    "/{workflow_id}/stages/excel_stage1/download-result",
    summary="下载 Excel 第一阶段结果",
    response_class=StreamingResponse,
    responses=_XLSX_RESPONSE,
    description="校验当前批次完整来源链和对象存储后，直接返回唯一 xlsx 文件。",
)
def download_excel_stage1_result(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    return _stream_excel_result(
        workflow_id,
        request,
        current_user,
        db,
        spec=_EXCEL_STAGE1_RESULT,
    )


@router.get(
    "/{workflow_id}/stages/excel_stage2/download-result",
    summary="下载 Excel 第二阶段处理结果",
    response_class=StreamingResponse,
    responses=_XLSX_RESPONSE,
    description="只返回当前批次正式登记的 BH 左右进深化处理 xlsx。",
)
def download_excel_stage2_result(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    return _stream_excel_result(
        workflow_id,
        request,
        current_user,
        db,
        spec=_EXCEL_STAGE2_RESULT,
    )


@router.get(
    "/{workflow_id}/stages/excel_stage2/download-box-reader-result",
    summary="下载 BOX 左右进读取结果",
    response_class=StreamingResponse,
    responses=_XLSX_RESPONSE,
    description=(
        "成功批次返回当前 BOX 读取表；读取阻断时仅返回当前 attempt 的诊断表，绝不回退到旧批次。"
    ),
)
def download_excel_stage2_box_reader_result(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    return _stream_excel_result(
        workflow_id,
        request,
        current_user,
        db,
        spec=_EXCEL_STAGE2_BOX_READER_RESULT,
    )


@router.get(
    "/{workflow_id}/stages/excel_stage2/download-reader-result",
    summary="下载 BH 左右进读取结果",
    response_class=StreamingResponse,
    responses=_XLSX_RESPONSE,
    description=(
        "成功批次返回当前读取表；读取阻断时仅返回当前 attempt 的诊断表，绝不回退到旧批次。"
    ),
)
def download_excel_stage2_reader_result(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    return _stream_excel_result(
        workflow_id,
        request,
        current_user,
        db,
        spec=_EXCEL_STAGE2_READER_RESULT,
    )
