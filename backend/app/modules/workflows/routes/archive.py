"""Whole-workflow ZIP export; production artifacts never download one by one."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.modules.files.interface import (
    StoredFile,
    TransferSpec,
    build_registered_files_zip_to_path,
    prepare_transfer_in_transaction,
    require_file_read_access,
    sanitize_filename,
    session_factory_for,
    settle_stream,
)
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import AnalysisResult
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_member
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.models import WorkflowRun, WorkflowStageRun
from app.platform.http.dependencies import get_db
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


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
    artifacts = [
        artifact
        for artifact in workflow.artifacts
        if selected_stage is None or artifact.stage_run_id == selected_stage.id
    ]
    members: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    for artifact in sorted(
        artifacts,
        key=lambda value: (
            stage_by_id[value.stage_run_id].sequence
            if value.stage_run_id in stage_by_id
            else 999,
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
        require_file_read_access(db, current_user, stored)
        stage = stage_by_id.get(artifact.stage_run_id)
        sequence = stage.sequence if stage is not None else 99
        code = stage.stage_code if stage is not None else "workflow"
        original_name = sanitize_filename(stored.original_name)
        relative_path = (
            f"workflow-{workflow.id}/{sequence:02d}_{code}/"
            f"{artifact.artifact_type}/{original_name}"
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
) -> StreamingResponse:
    prepared = build_registered_files_zip_to_path(db, members, archive_name)
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
        "把当前所有已登记生产 artifact 按阶段和类型写入一个 ZIP；"
        "工作流不提供单个 artifact 下载。"
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
    description="把指定生产阶段所有已登记 artifact 写入一个 ZIP，不提供单文件下载。",
)
def download_workflow_stage_archive(
    workflow_id: int,
    stage_code: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
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
