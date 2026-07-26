"""Workflow archives plus the single-file Excel stage result."""

from __future__ import annotations

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
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.modules.workflows.models import WorkflowArtifact, WorkflowRun, WorkflowStageRun
from app.platform.config.constants import TASK_EXCEL_FINAL
from app.platform.http.dependencies import get_db
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import StorageError, StorageObjectNotFound

router = APIRouter()
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
            metadata = artifact.metadata_json
            if (
                not isinstance(metadata, dict)
                or metadata.get("job_id") != artifact_stage.job_id
                or metadata.get("job_attempt") != artifact_stage.job_attempt
            ):
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
        "把指定普通生产阶段所有已登记 artifact 写入一个 ZIP；Excel 第一阶段必须改用"
        "唯一 .xlsx 结果下载入口。"
    ),
)
def download_workflow_stage_archive(
    workflow_id: int,
    stage_code: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if stage_code == "excel_stage1":
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_SINGLE_FILE_DOWNLOAD_REQUIRED",
            "Excel 第一阶段结果必须通过单文件下载入口获取，不能打包为阶段 ZIP。",
            {
                "download_path": (
                    f"/api/v1/workflows/{workflow_id}/stages/"
                    "excel_stage1/download-result"
                )
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


@router.get(
    "/{workflow_id}/stages/excel_stage1/download-result",
    summary="下载 Excel 第一阶段结果",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                XLSX_CONTENT_TYPE: {
                    "schema": {"type": "string", "format": "binary"},
                }
            }
        }
    },
    description="校验阶段、Job、AnalysisResult、文件登记和对象存储后，直接返回唯一 xlsx 文件。",
)
def download_excel_stage1_result(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    stage = next(
        (item for item in workflow.stages if item.stage_code == "excel_stage1"),
        None,
    )
    if stage is not None and stage.status != "succeeded" and stage.job_id is not None:
        sync_workflow_from_jobs(db, workflow)
        db.commit()
        workflow = load_workflow_detail(db, workflow_id)
        stage = next(
            (item for item in workflow.stages if item.stage_code == "excel_stage1"),
            None,
        )
    if (
        stage is None
        or stage.status != "succeeded"
        or stage.job_id is None
        or stage.job_attempt is None
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_NOT_READY",
            "Excel 第一阶段尚未形成可下载的正式结果。",
            {
                "stage_status": stage.status if stage is not None else None,
                "job_id": stage.job_id if stage is not None else None,
            },
        )
    job = db.get(Job, stage.job_id)
    if (
        job is None
        or job.project_id != workflow.project_id
        or job.task_type != TASK_EXCEL_FINAL
        or job.status != "succeeded"
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
    artifacts = list(
        db.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.workflow_run_id == workflow.id,
                WorkflowArtifact.stage_run_id == stage.id,
                WorkflowArtifact.artifact_type == "stage1_excel",
            )
        ).all()
    )
    if len(artifacts) != 1:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_CARDINALITY_INVALID",
            "Excel 第一阶段必须且只能登记一个正式结果文件。",
            {"artifact_count": len(artifacts)},
        )
    artifact = artifacts[0]
    result = db.get(AnalysisResult, artifact.result_id) if artifact.result_id else None
    metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    if (
        result is None
        or result.job_id != job.id
        or result.result_type != TASK_EXCEL_FINAL
        or result.status != "succeeded"
        or result.result_file_id != artifact.file_id
        or metadata.get("job_id") != job.id
        or metadata.get("job_attempt") != job.attempt
    ):
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_BINDING_INVALID",
            "Excel 产物、分析结果与阶段任务的来源链不一致。",
            {
                "artifact_id": artifact.id,
                "artifact_file_id": artifact.file_id,
                "result_id": artifact.result_id,
            },
        )
    stored = db.get(StoredFile, artifact.file_id) if artifact.file_id else None
    if stored is None or stored.status != "available":
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_FILE_UNAVAILABLE",
            "Excel 正式结果的文件登记不可用。",
            {"file_id": artifact.file_id},
        )
    if stored.file_ext.casefold() != ".xlsx":
        raise AppHTTPException(
            409,
            "EXCEL_STAGE_RESULT_FORMAT_INVALID",
            "Excel 正式结果必须是 xlsx 文件。",
            {"file_id": stored.id, "file_ext": stored.file_ext},
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
            operation="workflow_excel_result_download",
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
        action="workflow_excel_results.download",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "stage_code": "excel_stage1",
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
