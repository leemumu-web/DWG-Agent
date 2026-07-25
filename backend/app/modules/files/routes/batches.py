from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.access import (
    file_list_access_filter,
    require_file_delete_access,
    require_file_read_access,
)
from app.modules.files.exports import build_zip_to_path
from app.modules.files.lifecycle import soft_delete_file_in_transaction
from app.modules.files.models import StoredFile
from app.modules.files.schemas import BatchBulkDeleteRequest, BatchBulkDeleteResult
from app.modules.files.storage_transactions import (
    TransferSpec,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_stream,
)
from app.modules.files.validation import sanitize_filename
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import Job
from app.modules.jobs.interface import cancel_job as transition_job_to_cancelled
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import (
    JOB_PENDING,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_VALIDATING,
    JOB_WAITING_CAD_WORKER,
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
)
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import not_found

router = APIRouter()

@router.get("/batches")
def list_batches(
    request: Request,
    current_user: CurrentUser,
    file_ext: str = Query(
        "", description="Filter batches by file extension, e.g. '.dwg' or '.dxf'"
    ),
    standalone_only: bool = Query(
        False,
        description="Exclude batches whose files belong to the production workflow.",
    ),
    db: Session = Depends(get_db),
):
    """Query MySQL for distinct batch names, file counts and latest creation times."""
    from sqlalchemy import func as sa_func

    where_clauses = [
        StoredFile.batch_name.is_not(None),
        StoredFile.batch_name != "",
        StoredFile.status != "deleted",
    ]
    if file_ext.strip():
        where_clauses.append(StoredFile.file_ext == file_ext.strip())
    if standalone_only:
        from app.modules.workflows.interface import production_file_reference_exists

        where_clauses.append(~production_file_reference_exists(StoredFile.id))
    where_clauses.append(file_list_access_filter(current_user))

    rows = list(
        db.execute(
            select(
                StoredFile.batch_name,
                sa_func.count(StoredFile.id).label("file_count"),
                sa_func.max(StoredFile.created_at).label("latest_created_at"),
            )
            .where(*where_clauses)
            .group_by(StoredFile.batch_name)
            .order_by(sa_func.max(StoredFile.created_at).desc())
        ).all()
    )
    batches = [
        {
            "name": r.batch_name,
            "file_count": r.file_count,
            "latest_created_at": r.latest_created_at.isoformat(),
        }
        for r in rows
    ]
    return ok(batches, request.state.request_id)


@router.post("/batches/bulk-delete")
def bulk_delete_batches(
    payload: BatchBulkDeleteRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Atomically soft-delete one or more complete file batches."""
    batch_names = list(dict.fromkeys(payload.batch_names))
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name.in_(batch_names),
                StoredFile.status != "deleted",
            )
        ).all()
    )
    found_names = {stored.batch_name for stored in stored_list}
    if found_names != set(batch_names):
        raise not_found("Batch")

    for stored in stored_list:
        require_file_delete_access(db, current_user, stored)

    source_ids = [stored.id for stored in stored_list]
    active_jobs = list(
        db.scalars(
            select(Job).where(
                Job.task_type.in_((TASK_DWG_TO_DXF, TASK_DXF_TO_DWG)),
                Job.status.in_(
                    (
                        JOB_PENDING,
                        JOB_QUEUED,
                        JOB_RUNNING,
                        JOB_VALIDATING,
                        JOB_WAITING_CAD_WORKER,
                    )
                ),
                Job.params_json["file_id"].as_integer().in_(source_ids),
            )
        ).all()
    )

    try:
        for job in active_jobs:
            transition_job_to_cancelled(db, job)
        for stored in stored_list:
            soft_delete_file_in_transaction(
                db,
                stored,
                actor_user_id=current_user.id,
                request_id=request.state.request_id,
                batch_ref=stored.batch_name,
            )
            write_audit_log(
                db,
                actor_user_id=current_user.id,
                action="files.batch_delete",
                resource_type="file",
                resource_id=stored.id,
                after_json={"batch_name": stored.batch_name, "bulk": True},
                request=request,
            )
        result = BatchBulkDeleteResult(
            deleted_batch_count=len(batch_names),
            deleted_file_count=len(stored_list),
            cancelled_job_count=len(active_jobs),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result, request.state.request_id)


@router.delete("/batches/{batch_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(
    batch_name: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Soft-delete all non-deleted files in a batch.

    Uses a bulk UPDATE for efficiency on large batches. Access control is
    enforced per-file — if any file in the batch is not deletable by the
    current user the whole operation is rejected.
    """
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name == batch_name,
                StoredFile.status != "deleted",
            )
        ).all()
    )
    if not stored_list:
        raise not_found("Batch")

    # Verify access for every file before mutating
    for s in stored_list:
        require_file_delete_access(db, current_user, s)

    # Bulk soft-delete
    deleted_count = 0
    for s in stored_list:
        soft_delete_file_in_transaction(
            db,
            s,
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            batch_ref=batch_name,
        )
        deleted_count += 1
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="files.batch_delete",
            resource_type="file",
            resource_id=s.id,
            after_json={"batch_name": batch_name},
            request=request,
        )
    db.commit()
    return None


@router.get("/batches/{batch_name}/download-zip")
def download_batch_zip(
    batch_name: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Download all non-deleted files in a batch as a ZIP archive.

    Streams the zip directly — no intermediate storage. Only the original
    file format is included (not conversion results).
    """
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name == batch_name,
                StoredFile.status != "deleted",
            )
        ).all()
    )
    if not stored_list:
        raise not_found("Batch")

    for s in stored_list:
        require_file_read_access(db, current_user, s)

    file_ids = [s.id for s in stored_list]
    # Determine format from the batch's file extension
    first_ext = stored_list[0].file_ext.lstrip(".") if stored_list else "dxf"
    formats = [first_ext] if first_ext in ("dwg", "dxf") else ["dxf"]

    clean_name = sanitize_filename(batch_name)
    prepared = build_zip_to_path(db, file_ids, formats, clean_name)
    try:
        transfer = prepare_transfer_in_transaction(
            db,
            TransferSpec(
                direction="outbound",
                operation="download_zip",
                actor_user_id=current_user.id,
                request_id=request.state.request_id,
                idempotency_key=request.state.request_id,
                batch_ref=batch_name,
                original_name=prepared.filename,
                expected_bytes=prepared.size_bytes,
            ),
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="files.batch_download_zip",
            resource_type="file",
            resource_id=0,
            after_json={"batch_name": batch_name, "file_count": len(file_ids)},
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        prepared.path.unlink(missing_ok=True)
        raise

    def _stream_and_cleanup():
        try:
            with prepared.path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            prepared.path.unlink(missing_ok=True)

    encoded_filename = quote(f"{clean_name}.zip")
    return StreamingResponse(
        settle_stream(
            session_factory_for(db),
            transfer.transfer_uid,
            _stream_and_cleanup(),
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(prepared.size_bytes),
        },
    )
