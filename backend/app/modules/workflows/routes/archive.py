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
from app.platform.http.dependencies import get_db
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


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
    stage_by_id = {stage.id: stage for stage in workflow.stages}
    members: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    for artifact in sorted(
        workflow.artifacts,
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
        require_file_read_access(db, current_user, stored)
        stage = stage_by_id.get(artifact.stage_run_id)
        sequence = stage.sequence if stage is not None else 99
        stage_code = stage.stage_code if stage is not None else "workflow"
        original_name = sanitize_filename(stored.original_name)
        relative_path = (
            f"workflow-{workflow.id}/{sequence:02d}_{stage_code}/"
            f"{artifact.artifact_type}/{original_name}"
        )
        if relative_path.casefold() in seen_paths:
            relative_path = (
                f"workflow-{workflow.id}/{sequence:02d}_{stage_code}/"
                f"{artifact.artifact_type}/{stored.id}-{original_name}"
            )
        seen_paths.add(relative_path.casefold())
        members.append((stored.id, relative_path))

    if not members:
        raise AppHTTPException(
            409,
            "WORKFLOW_ARCHIVE_EMPTY",
            "The workflow has no downloadable production artifacts.",
        )
    archive_name = f"workflow-{workflow.id}"
    prepared = build_registered_files_zip_to_path(db, members, archive_name)
    try:
        transfer = prepare_transfer_in_transaction(
            db,
            TransferSpec(
                direction="outbound",
                operation="workflow_download_zip",
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
            action="workflow_archives.download",
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
