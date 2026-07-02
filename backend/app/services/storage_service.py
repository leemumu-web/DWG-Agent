from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ALLOWED_UPLOAD_EXTENSIONS
from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile


def validate_upload_name(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise AppHTTPException(
            415,
            "FILE_TYPE_NOT_ALLOWED",
            "Only DWG files are allowed in this stage.",
            {"allowed_extensions": sorted(ALLOWED_UPLOAD_EXTENSIONS)},
        )
    return ext


def validate_dwg_header(first_chunk: bytes) -> None:
    """Validate that the file starts with the DWG magic bytes (AC + version digits).

    All DWG files begin with ``AC`` followed by four ASCII decimal digits
    (e.g. ``AC1032`` for AutoCAD 2018-2024 format).  This catches files that
    were renamed to ``.dwg`` but are actually a different format.
    """
    if len(first_chunk) < 6:
        raise AppHTTPException(415, "FILE_NOT_DWG", "File is too small to be a valid DWG.")
    header = first_chunk[:6]
    if not (header[:2] == b"AC" and header[2:6].isdigit()):
        raise AppHTTPException(415, "FILE_NOT_DWG", "File does not have a valid DWG header.")


def build_storage_path(bucket: str, storage_key: str) -> Path:
    root = settings.local_storage_root.resolve()
    path = (root / bucket / storage_key).resolve()
    if not str(path).startswith(str(root)):
        raise AppHTTPException(400, "INVALID_STORAGE_PATH", "Invalid storage path.")
    return path


async def save_upload_file(db: Session, upload: UploadFile, uploaded_by: int | None) -> StoredFile:
    original_name = upload.filename or "unnamed.dwg"
    file_ext = validate_upload_name(original_name)
    bucket = "dwg-original"
    storage_key = f"local/{uuid4().hex}{file_ext}"
    destination = build_storage_path(bucket, storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    max_size = settings.max_upload_size_mb * 1024 * 1024

    with destination.open("wb") as out:
        first = True
        while chunk := await upload.read(1024 * 1024):
            if first:
                validate_dwg_header(chunk)
                first = False
            size += len(chunk)
            if size > max_size:
                destination.unlink(missing_ok=True)
                raise AppHTTPException(413, "FILE_TOO_LARGE", "Uploaded file exceeds max size.")
            sha256.update(chunk)
            md5.update(chunk)
            out.write(chunk)

    content_type = upload.content_type or mimetypes.guess_type(original_name)[0]
    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=file_ext,
        content_type=content_type,
        size_bytes=size,
        sha256=sha256.hexdigest(),
        md5=md5.hexdigest(),
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def get_local_file_path(file: StoredFile) -> Path:
    return build_storage_path(file.bucket, file.storage_key)
