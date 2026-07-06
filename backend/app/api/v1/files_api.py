from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    get_db,
    get_project_membership,
    has_global_project_access,
)
from app.core.exceptions import AppHTTPException, forbidden, not_found
from app.core.validators import validate_sort_by
from app.models.drawing import Drawing, DrawingVersion
from app.models.file import StoredFile
from app.models.job import Job
from app.models.project import Project
from app.models.result import AnalysisResult
from app.schemas.common import ok, page_from_list
from app.schemas.file_schema import BulkDeleteRequest, FileRead, ZipDownloadRequest
from app.services.audit_service import write_audit_log
from app.services.file_service import (
    build_signed_download_url,
    build_zip,
    download_headers,
    validate_download_signature,
)
from app.services.storage_service import get_storage_backend, sanitize_filename, save_upload_file
from app.storage.base import StorageError, StorageObjectNotFound

router = APIRouter()


def _file_project_ids(db: Session, file_id: int, *, include_deleted: bool = False) -> set[int]:
    drawing_stmt = (
        select(Drawing.project_id)
        .join(DrawingVersion, DrawingVersion.drawing_id == Drawing.id)
        .join(Project, Project.id == Drawing.project_id)
        .where(
            DrawingVersion.file_id == file_id,
            Drawing.status != "deleted",
        )
    )
    if not include_deleted:
        drawing_stmt = drawing_stmt.where(Project.status != "deleted")
    drawing_project_ids = db.scalars(drawing_stmt).all()

    result_stmt = (
        select(Job.project_id)
        .join(AnalysisResult, AnalysisResult.job_id == Job.id)
        .join(Project, Project.id == Job.project_id)
        .where(
            AnalysisResult.result_file_id == file_id,
            Job.project_id.is_not(None),
        )
    )
    if not include_deleted:
        result_stmt = result_stmt.where(Project.status != "deleted")
    result_project_ids = db.scalars(result_stmt).all()

    return {
        project_id
        for project_id in (*drawing_project_ids, *result_project_ids)
        if project_id is not None
    }


def _can_read_file(db: Session, current_user: CurrentUser, stored: StoredFile) -> bool:
    active_project_ids = _file_project_ids(db, stored.id, include_deleted=False)

    # If the file is attached to projects but all of them have been soft-deleted,
    # treat it as inaccessible regardless of global role / uploader status.
    if not active_project_ids and _file_project_ids(db, stored.id, include_deleted=True):
        return False

    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return True
    return any(
        get_project_membership(db, current_user, project_id)
        for project_id in active_project_ids
    )


def _require_file_read_access(
    db: Session, current_user: CurrentUser, stored: StoredFile
) -> None:
    # If the file is attached only to soft-deleted projects, treat as not found
    # so that soft-deleting a project cascades to its file metadata (BUG-7).
    active_ids = _file_project_ids(db, stored.id, include_deleted=False)
    all_ids = _file_project_ids(db, stored.id, include_deleted=True)
    if not active_ids and all_ids:
        raise not_found("File")
    if not _can_read_file(db, current_user, stored):
        raise forbidden("File access is restricted.")


def _require_file_delete_access(
    db: Session, current_user: CurrentUser, stored: StoredFile
) -> None:
    # If all associated projects are soft-deleted, treat the file as not found (BUG-7).
    active_ids = _file_project_ids(db, stored.id, include_deleted=False)
    all_ids = _file_project_ids(db, stored.id, include_deleted=True)
    if not active_ids and all_ids:
        raise not_found("File")
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return
    raise forbidden("Only the uploader or an administrator can delete this file.")


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    batch_name: str = Query(""),
    db: Session = Depends(get_db),
):
    stored = await save_upload_file(
        db, upload, uploaded_by=current_user.id,
        batch_name=sanitize_filename(batch_name.strip()) if batch_name.strip() else None,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.upload",
        resource_type="file",
        resource_id=stored.id,
        after_json={"original_name": stored.original_name, "sha256": stored.sha256},
        request=request,
    )
    db.commit()
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.get("")
def list_files(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    batch_name: str = Query(""),
    db: Session = Depends(get_db),
):
    sort_column = validate_sort_by("files", sort_by)
    sort_dir_value = sort_dir.strip().lower()
    order_clause = getattr(StoredFile, sort_column)
    if sort_dir_value == "asc":
        order_clause = order_clause.asc()
    else:
        order_clause = order_clause.desc()
    stmt = select(StoredFile).where(StoredFile.status != "deleted")
    if batch_name.strip():
        stmt = stmt.where(StoredFile.batch_name == batch_name.strip())
    files = list(db.scalars(stmt.order_by(order_clause)).all())
    if not has_global_project_access(current_user):
        files = [stored for stored in files if _can_read_file(db, current_user, stored)]
    return page_from_list(
        [FileRead.model_validate(f) for f in files], page, page_size, request.state.request_id
    )


# ── batches ─────────────────────────────────────────────────────────────────
# NOTE: must be registered BEFORE /{file_id} to avoid route shadowing.


@router.get("/batches")
def list_batches(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Return all distinct batch names with file counts and latest created_at."""
    from sqlalchemy import func as sa_func

    rows = list(
        db.execute(
            select(
                StoredFile.batch_name,
                sa_func.count(StoredFile.id).label("file_count"),
                sa_func.max(StoredFile.created_at).label("latest_created_at"),
            )
            .where(
                StoredFile.batch_name.is_not(None),
                StoredFile.batch_name != "",
                StoredFile.status != "deleted",
            )
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


@router.get("/{file_id}")
def get_file(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_delete_access(db, current_user, stored)
    stored.status = "deleted"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.delete",
        resource_type="file",
        resource_id=stored.id,
        request=request,
    )
    db.commit()
    return None


@router.get("/{file_id}/download-url")
def get_download_url(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download_url",
        resource_type="file",
        resource_id=stored.id,
        request=request,
    )
    db.commit()
    return ok(build_signed_download_url(file_id), request.state.request_id)


@router.get("/{file_id}/download")
def download_file(
    file_id: int,
    request: Request,
    current_user: CurrentUser,
    expires: int | None = Query(default=None),
    signature: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)
    if expires is None or signature is None:
        raise AppHTTPException(
            403, "INVALID_DOWNLOAD_SIGNATURE", "Download URL signature is required."
        )
    validate_download_signature(file_id, expires, signature)
    storage = get_storage_backend()
    try:
        path = storage.local_path(stored.bucket, stored.storage_key)
        if path is not None:
            if not path.exists() or not path.is_file():
                raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
            response = FileResponse(
                path,
                media_type=stored.content_type or "application/octet-stream",
                filename=stored.original_name,
            )
        else:
            response = StreamingResponse(
                storage.iter_file(stored.bucket, stored.storage_key),
                media_type=stored.content_type or "application/octet-stream",
                headers=download_headers(stored.original_name),
            )
    except StorageObjectNotFound:
        raise not_found("StoredFileObject") from None
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_READ_FAILED",
            "Failed to read stored file object.",
        ) from exc
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download",
        resource_type="file",
        resource_id=stored.id,
        request=request,
    )
    db.commit()
    return response


# ── bulk operations ──────────────────────────────────────────────────────────


@router.post("/bulk-delete", status_code=status.HTTP_204_NO_CONTENT)
def bulk_delete_files(
    request: Request,
    payload: BulkDeleteRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if not payload.file_ids:
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ids must not be empty.")
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(payload.file_ids), StoredFile.status != "deleted"
            )
        ).all()
    )
    for s in stored_list:
        _require_file_delete_access(db, current_user, s)
        s.status = "deleted"
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="files.bulk_delete",
            resource_type="file",
            resource_id=s.id,
            request=request,
        )
    db.commit()
    return None


@router.post("/download-zip")
def download_zip_endpoint(
    request: Request,
    payload: ZipDownloadRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Build a zip archive with selected files' DWG and/or DXF versions,
    stream it directly, and clean up the temp file on completion."""
    import os
    import tempfile

    if not payload.file_ids:
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ids must not be empty.")
    if not payload.formats:
        raise AppHTTPException(
            422, "INVALID_PARAMS", "formats must not be empty — choose at least dwg or dxf."
        )

    # Verify access
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(payload.file_ids), StoredFile.status != "deleted"
            )
        ).all()
    )
    if len(stored_list) != len(payload.file_ids):
        raise not_found("File")
    for s in stored_list:
        _require_file_read_access(db, current_user, s)

    clean_name = sanitize_filename(payload.folder_name) or "图纸导出"
    zip_bytes, _ = build_zip(db, payload.file_ids, payload.formats, clean_name)

    # Write to a temp file so we can stream it and delete after download.
    # MinIO / local both work — the zip is ephemeral (temp file, not stored bucket).
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = tmp.name
    try:
        tmp.write(zip_bytes)
        tmp.close()
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    def _stream_and_cleanup():
        try:
            with open(tmp_path, "rb") as f:
                while chunk := f.read(1024 * 1024):  # 1 MiB chunks
                    yield chunk
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download_zip",
        resource_type="file",
        resource_id=0,
        after_json={
            "file_ids": payload.file_ids,
            "formats": payload.formats,
            "folder": clean_name,
        },
        request=request,
    )
    db.commit()

    encoded_filename = quote(f"{clean_name}.zip")
    return StreamingResponse(
        _stream_and_cleanup(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(len(zip_bytes)),
        },
    )
