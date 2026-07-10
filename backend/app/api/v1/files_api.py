from __future__ import annotations

import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.api.deps import (
    CurrentUser,
    get_db,
    get_project_membership,
    has_global_project_access,
)
from app.core.constants import ALLOWED_UPLOAD_EXTENSIONS
from app.core.exceptions import AppHTTPException, forbidden, not_found
from app.core.validators import validate_sort_by
from app.db.pagination import paginate_scalars
from app.models.drawing import Drawing, DrawingVersion
from app.models.file import StoredFile
from app.models.job import Job
from app.models.project import Project, ProjectMember
from app.models.result import AnalysisResult
from app.schemas.common import ok
from app.schemas.common import page as page_response
from app.schemas.file_schema import BulkDeleteRequest, FileRead, ZipDownloadRequest, ZipUploadResult
from app.services.audit_service import write_audit_log
from app.services.file_service import (
    build_signed_download_url,
    build_zip,
    download_headers,
    validate_download_signature,
)
from app.services.storage_service import (
    get_storage_backend,
    sanitize_filename,
    save_bytes_as_file,
    save_upload_file,
    validate_dwg_header,
    validate_upload_name,
)
from app.storage.base import StorageError, StorageObjectNotFound

router = APIRouter()

logger = logging.getLogger(__name__)


def _file_project_association_exists(
    *,
    active_only: bool,
    member_user_id: int | None = None,
) -> ColumnElement[bool]:
    """Build a correlated project-association predicate for the outer file row."""
    drawing_conditions = [
        DrawingVersion.file_id == StoredFile.id,
        Drawing.status != "deleted",
    ]
    result_conditions = [
        AnalysisResult.result_file_id == StoredFile.id,
        Job.project_id.is_not(None),
    ]
    if active_only:
        drawing_conditions.append(Project.status != "deleted")
        result_conditions.append(Project.status != "deleted")

    drawing_stmt = (
        select(1)
        .select_from(DrawingVersion)
        .join(Drawing, Drawing.id == DrawingVersion.drawing_id)
        .join(Project, Project.id == Drawing.project_id)
    )
    result_stmt = (
        select(1)
        .select_from(AnalysisResult)
        .join(Job, Job.id == AnalysisResult.job_id)
        .join(Project, Project.id == Job.project_id)
    )
    if member_user_id is not None:
        drawing_stmt = drawing_stmt.join(
            ProjectMember, ProjectMember.project_id == Project.id
        )
        result_stmt = result_stmt.join(ProjectMember, ProjectMember.project_id == Project.id)
        drawing_conditions.append(ProjectMember.user_id == member_user_id)
        result_conditions.append(ProjectMember.user_id == member_user_id)

    return or_(
        exists(drawing_stmt.where(*drawing_conditions)),
        exists(result_stmt.where(*result_conditions)),
    )


def _file_list_access_filter(current_user: CurrentUser) -> ColumnElement[bool]:
    """Mirror single-file access rules as one SQL predicate for list endpoints."""
    any_project = _file_project_association_exists(active_only=False)
    active_project = _file_project_association_exists(active_only=True)
    not_orphaned_by_project_deletion = or_(~any_project, active_project)

    if has_global_project_access(current_user):
        return not_orphaned_by_project_deletion

    active_membership = _file_project_association_exists(
        active_only=True,
        member_user_id=current_user.id,
    )
    return and_(
        not_orphaned_by_project_deletion,
        or_(StoredFile.uploaded_by == current_user.id, active_membership),
    )


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


@router.post("/upload-zip", status_code=status.HTTP_201_CREATED)
async def upload_zip(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    file_ext: str = Query(".dwg", description="只提取此扩展名的文件 (.dwg 或 .dxf)"),
    db: Session = Depends(get_db),
):
    """Upload a .zip archive, extract matching files, and auto-create StoredFile records.

    The ZIP filename (minus .zip) becomes the batch_name for all extracted files.
    Only files matching ``file_ext`` are imported; others are counted as skipped.
    """
    import io
    import zipfile

    from app.core.config import settings

    # ── validate the upload is a .zip ──────────────────────────────────────
    zip_original = sanitize_filename(upload.filename or "unnamed.zip")
    if not zip_original.lower().endswith(".zip"):
        raise AppHTTPException(
            415, "FILE_TYPE_NOT_ALLOWED",
            "Only .zip archives are accepted by this endpoint.",
        )
    validate_upload_name(zip_original)  # 校验 .zip 在 ALLOWED_UPLOAD_EXTENSIONS 白名单内
    if not file_ext.strip():
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ext query parameter is required.")
    target_ext = file_ext.strip().lower()
    if target_ext not in (".dwg", ".dxf"):
        raise AppHTTPException(
            422, "INVALID_PARAMS",
            "file_ext must be .dwg or .dxf.",
        )
    if target_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise AppHTTPException(
            422, "INVALID_PARAMS",
            f"Target extension {target_ext} is not allowed.",
        )

    # Derive batch_name from the ZIP filename
    batch_name = sanitize_filename(zip_original[: -len(".zip")] if zip_original.lower().endswith(".zip") else zip_original.rsplit(".", 1)[0])
    if not batch_name or batch_name == "unnamed":
        batch_name = f"导入_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    # ── buffer the upload ──────────────────────────────────────────────────
    max_upload = settings.max_upload_size_mb * 1024 * 1024
    zip_bytes_total = 0
    with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as tmp:
        while chunk := await upload.read(1024 * 1024):
            zip_bytes_total += len(chunk)
            if zip_bytes_total > max_upload:
                raise AppHTTPException(
                    413, "FILE_TOO_LARGE",
                    f"ZIP archive exceeds max upload size ({settings.max_upload_size_mb} MB).",
                )
            tmp.write(chunk)
        tmp.seek(0)

        # ── extract ────────────────────────────────────────────────────────
        try:
            zf = zipfile.ZipFile(tmp, "r")
        except (zipfile.BadZipFile, io.UnsupportedOperation) as e:
            raise AppHTTPException(
                415, "FILE_NOT_ZIP",
                "The uploaded file is not a valid ZIP archive.",
            ) from e

        bad_names = zf.testzip()
        if bad_names is not None:
            raise AppHTTPException(
                415, "ZIP_CORRUPTED",
                f"ZIP archive contains corrupted entry: {bad_names}",
            )

        extracted: list[StoredFile] = []
        skipped = 0
        total_extracted_bytes = 0
        max_extract = settings.max_zip_extract_mb * 1024 * 1024
        entry_count = 0

        for info in zf.infolist():
            if info.is_dir():
                continue
            entry_count += 1
            if entry_count > settings.max_zip_entry_count:
                raise AppHTTPException(
                    413, "ZIP_TOO_MANY_FILES",
                    f"ZIP archive contains more than {settings.max_zip_entry_count} files.",
                )

            # Path traversal defence: sanitize + strip any directory components
            entry_name = sanitize_filename(info.filename.rsplit("/", 1)[-1] if "/" in info.filename else info.filename)
            entry_ext = Path(entry_name).suffix.lower()
            if entry_ext != target_ext:
                skipped += 1
                continue

            # Read entry
            entry_bytes = zf.read(info)
            total_extracted_bytes += len(entry_bytes)
            if total_extracted_bytes > max_extract:
                raise AppHTTPException(
                    413, "ZIP_TOO_LARGE",
                    f"Extracted content exceeds {settings.max_zip_extract_mb} MB limit.",
                )

            # DWG header validation
            if entry_ext == ".dwg":
                try:
                    validate_dwg_header(entry_bytes[:6])
                except AppHTTPException:
                    skipped += 1
                    continue

            # Persist via save_bytes_as_file (reuses existing storage logic)
            content_type = mimetypes.guess_type(entry_name)[0]
            bucket = (
                settings.minio_bucket_original
                if entry_ext == ".dwg"
                else settings.minio_bucket_dxf_original
            )
            storage_key = f"uploads/{uuid4().hex}{entry_ext}"
            stored = save_bytes_as_file(
                db,
                bucket=bucket,
                storage_key=storage_key,
                original_name=entry_name,
                file_ext=entry_ext,
                content_type=content_type or "application/octet-stream",
                payload=entry_bytes,
                uploaded_by=current_user.id,
                batch_name=batch_name,
            )
            extracted.append(stored)

    if not extracted and skipped == 0:
        raise AppHTTPException(
            422, "ZIP_EMPTY",
            "The ZIP archive contains no files.",
        )

    # ── audit log ──────────────────────────────────────────────────────────
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.upload_zip",
        resource_type="file",
        resource_id=0,
        after_json={
            "batch_name": batch_name,
            "target_ext": target_ext,
            "success_count": len(extracted),
            "skipped_count": skipped,
            "zip_original": zip_original,
            "zip_size": zip_bytes_total,
        },
        request=request,
    )
    db.commit()

    return ok(
        ZipUploadResult(
            batch_name=batch_name,
            files=[FileRead.model_validate(f) for f in extracted],
            success_count=len(extracted),
            skipped_count=skipped,
        ).model_dump(),
        request.state.request_id,
    )


@router.get("")
def list_files(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    batch_name: str = Query(""),
    file_ext: str = Query("", description="Filter by file extension, e.g. '.dwg' or '.dxf'"),
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
    if file_ext.strip():
        stmt = stmt.where(StoredFile.file_ext == file_ext.strip())
    tie_breaker = StoredFile.id.asc() if sort_dir_value == "asc" else StoredFile.id.desc()
    stmt = stmt.where(_file_list_access_filter(current_user)).order_by(
        order_clause, tie_breaker
    )
    files, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    return page_response(
        [FileRead.model_validate(f) for f in files],
        page,
        page_size,
        total,
        request.state.request_id,
    )


# ── batches ─────────────────────────────────────────────────────────────────
# NOTE: must be registered BEFORE /{file_id} to avoid route shadowing.


@router.get("/batches")
def list_batches(
    request: Request,
    current_user: CurrentUser,
    file_ext: str = Query("", description="Filter batches by file extension, e.g. '.dwg' or '.dxf'"),
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
    where_clauses.append(_file_list_access_filter(current_user))

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
        _require_file_delete_access(db, current_user, s)

    # Bulk soft-delete
    deleted_count = 0
    for s in stored_list:
        s.status = "deleted"
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
    import os
    import tempfile

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
        _require_file_read_access(db, current_user, s)

    file_ids = [s.id for s in stored_list]
    # Determine format from the batch's file extension
    first_ext = stored_list[0].file_ext.lstrip(".") if stored_list else "dxf"
    formats = [first_ext] if first_ext in ("dwg", "dxf") else ["dxf"]

    clean_name = sanitize_filename(batch_name)
    zip_bytes, _ = build_zip(db, file_ids, formats, clean_name)

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
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

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

    encoded_filename = quote(f"{clean_name}.zip")
    return StreamingResponse(
        _stream_and_cleanup(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(len(zip_bytes)),
        },
    )


def _column_letter(index: int) -> str:
    """Convert 0-based column index to Excel column letter(s): 0→A, 25→Z, 26→AA."""
    letters: list[str] = []
    n = index
    while True:
        n, rem = divmod(n, 26)
        letters.append(chr(ord("A") + rem))
        if n == 0:
            break
        n -= 1
    return "".join(reversed(letters))


@router.get("/{file_id}/excel-preview")
def get_excel_preview(
    file_id: int,
    request: Request,
    current_user: CurrentUser,
    sheet: str = Query("", description="Sheet name to preview (empty = first sheet)"),
    db: Session = Depends(get_db),
):
    """Read an Excel file from authoritative storage and return preview JSON."""
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)

    if not stored.file_ext or stored.file_ext.lower() not in (".xlsx", ".xls"):
        raise AppHTTPException(
            415,
            "NOT_EXCEL",
            "Only .xlsx / .xls files can be previewed.",
        )

    # Read Excel bytes from storage
    storage = get_storage_backend()
    try:
        local_path = storage.local_path(stored.bucket, stored.storage_key)
        if local_path is not None:
            if not local_path.exists():
                raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
            excel_bytes = local_path.read_bytes()
        else:
            chunks: list[bytes] = []
            for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                chunks.append(chunk)
            excel_bytes = b"".join(chunks)
    except StorageObjectNotFound:
        raise not_found("StoredFileObject") from None
    except StorageError as exc:
        raise AppHTTPException(
            503, "STORAGE_READ_FAILED", "Failed to read stored file object."
        ) from exc

    # Parse with openpyxl
    try:
        import io

        import openpyxl
    except ImportError as exc:
        raise AppHTTPException(
            503,
            "OPENPYXL_UNAVAILABLE",
            "openpyxl is not installed — cannot preview Excel files.",
        ) from exc

    try:
        wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise AppHTTPException(
            415,
            "EXCEL_PARSE_ERROR",
            f"Failed to parse Excel file: {exc}",
        ) from exc

    sheet_names = wb.sheetnames
    if not sheet_names:
        raise AppHTTPException(415, "EXCEL_EMPTY", "Excel file has no sheets.")

    target_sheet = sheet.strip() if sheet.strip() else sheet_names[0]
    if target_sheet not in sheet_names:
        raise AppHTTPException(
            422,
            "SHEET_NOT_FOUND",
            f"Sheet '{target_sheet}' not found. Available: {', '.join(sheet_names)}",
        )

    ws = wb[target_sheet]
    rows_iter = ws.iter_rows(values_only=True)

    # First row is always the header, remaining rows are data.
    # Simple and predictable — no heuristic scoring that can accidentally
    # discard real data rows as "metadata".
    try:
        headers_raw: tuple[object, ...] = next(rows_iter)
    except StopIteration:
        headers_raw = ()

    # Build clean, unique headers. Empty cells become "Col A", "Col B", ...
    # Duplicates get a numeric suffix ("Name", "Name_2", "Name_3").
    seen: dict[str, int] = {}
    headers: list[str] = []
    for idx, h in enumerate(headers_raw or []):
        col_letter = _column_letter(idx)
        base = str(h).strip() if h is not None and str(h).strip() else f"Col {col_letter}"
        if base in seen:
            seen[base] += 1
            headers.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 0
            headers.append(base)

    # If there are more data columns than headers, pad with column letters
    # (openpyxl iter_rows may return rows wider than the header row)
    _max_data_cols = 0

    data_rows: list[dict[str, object]] = []

    def _extract_row(row: tuple[object, ...]) -> dict[str, object]:
        """Convert an openpyxl row tuple into a column-keyed dict."""
        nonlocal _max_data_cols
        if len(row) > _max_data_cols:
            _max_data_cols = len(row)
        row_dict: dict[str, object] = {}
        for idx, val in enumerate(row):
            while idx >= len(headers):
                headers.append(f"Col {_column_letter(len(headers))}")
            col_name = headers[idx]
            if val is None:
                row_dict[col_name] = None
            elif isinstance(val, (int, float)):
                row_dict[col_name] = val
            else:
                row_dict[col_name] = str(val)
        return row_dict

    for row in rows_iter:
        data_rows.append(_extract_row(row))

    wb.close()

    result: dict = {
        "file": stored.original_name,
        "file_id": file_id,
        "sheets": sheet_names,
        "sheet": target_sheet,
        "headers": headers,
        "rows": data_rows,
        "total_rows": len(data_rows),
    }

    return ok(result, request.state.request_id)


@router.get("/{file_id}/dxf-preview")
def get_dxf_preview(  # ← sync def — FastAPI runs CPU-bound rendering in thread pool
    file_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Read a DXF file from storage, render to PNG, and return preview metadata.

    The PNG is cached in storage under ``previews/{file_id}_{sha256[:8]}.png``
    so subsequent requests for the same file content skip re-rendering.

    Returns ``{file_id, file_name, preview_url, entity_counts, total_entities,
    layers, layer_colors, bounds, cached}``.
    """
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)

    if not stored.file_ext or stored.file_ext.lower() != ".dxf":
        raise AppHTTPException(
            415, "NOT_DXF",
            "Only .dxf files can be previewed with this endpoint.",
        )

    # Read DXF bytes from storage
    storage = get_storage_backend()
    try:
        local_path = storage.local_path(stored.bucket, stored.storage_key)
        if local_path is not None:
            if not local_path.exists():
                raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
            dxf_bytes = local_path.read_bytes()
        else:
            chunks: list[bytes] = []
            for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                chunks.append(chunk)
            dxf_bytes = b"".join(chunks)
    except StorageObjectNotFound:
        raise not_found("StoredFileObject") from None
    except StorageError as exc:
        raise AppHTTPException(
            503, "STORAGE_READ_FAILED", "Failed to read stored file object."
        ) from exc

    # Check ezdxf import (server startup should have it, but guard for safety)
    try:
        from app.services.dxf_preview_service import preview_dxf  # noqa: F811
    except ImportError as exc:
        raise AppHTTPException(
            503, "EZDXF_UNAVAILABLE",
            "ezdxf is not installed — cannot preview DXF files.",
        ) from exc

    try:
        result = preview_dxf(
            file_id=stored.id,
            original_name=stored.original_name,
            sha256=stored.sha256 or "",
            dxf_bytes=dxf_bytes,
            storage=storage,
        )
    except AppHTTPException:
        raise
    except Exception as exc:
        logger.warning("DXF preview failed for file_id=%s: %s", file_id, exc)
        raise AppHTTPException(
            415, "DXF_PREVIEW_FAILED",
            "DXF 文件预览失败，请确认文件格式正确且未损坏。",
        ) from exc

    return ok(result, request.state.request_id)


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
