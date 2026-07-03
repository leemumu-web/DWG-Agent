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

ALLOWED_DWG_MIME_TYPES = {
    "application/acad",
    "application/autocad",
    "application/dwg",
    "application/octet-stream",
    "application/x-acad",
    "application/x-autocad",
    "application/x-dwg",
    "image/vnd.dwg",
}

SUPPORTED_DWG_HEADERS = {
    b"AC1012",  # AutoCAD R13
    b"AC1014",  # AutoCAD R14
    b"AC1015",  # AutoCAD 2000/2000i/2002
    b"AC1018",  # AutoCAD 2004/2005/2006
    b"AC1021",  # AutoCAD 2007/2008/2009
    b"AC1024",  # AutoCAD 2010/2011/2012
    b"AC1027",  # AutoCAD 2013/2014/2015/2016/2017
    b"AC1032",  # AutoCAD 2018+
}


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


def validate_upload_mime(content_type: str | None) -> str | None:
    if not content_type:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized not in ALLOWED_DWG_MIME_TYPES:
        raise AppHTTPException(
            415,
            "FILE_MIME_NOT_ALLOWED",
            "Uploaded file MIME type is not allowed for DWG uploads.",
            {"allowed_mime_types": sorted(ALLOWED_DWG_MIME_TYPES)},
        )
    return normalized


# Minimum DWG file size in bytes — legitimate DWG files are never this small;
# the threshold prevents trivial DoS via header-only uploads.
MIN_DWG_SIZE_BYTES = 1024


def validate_dwg_header(first_chunk: bytes) -> None:
    """Validate that the file starts with a supported DWG version signature."""
    if len(first_chunk) < 6:
        raise AppHTTPException(415, "FILE_NOT_DWG", "File is too small to be a valid DWG.")
    header = first_chunk[:6]
    if header not in SUPPORTED_DWG_HEADERS:
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
    upload_content_type = validate_upload_mime(upload.content_type)
    bucket = "dwg-original"
    storage_key = f"local/{uuid4().hex}{file_ext}"
    destination = build_storage_path(bucket, storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    max_size = settings.max_upload_size_mb * 1024 * 1024

    try:
        with destination.open("wb") as out:
            first = True
            while chunk := await upload.read(1024 * 1024):
                if first:
                    validate_dwg_header(chunk)
                    first = False
                size += len(chunk)
                if size > max_size:
                    raise AppHTTPException(413, "FILE_TOO_LARGE", "Uploaded file exceeds max size.")
                sha256.update(chunk)
                md5.update(chunk)
                out.write(chunk)
            if first:
                validate_dwg_header(b"")
    except AppHTTPException:
        destination.unlink(missing_ok=True)
        raise

    if size < MIN_DWG_SIZE_BYTES:
        destination.unlink(missing_ok=True)
        raise AppHTTPException(
            415,
            "FILE_NOT_DWG",
            f"File too small ({size} bytes) — legitimate DWG files exceed {MIN_DWG_SIZE_BYTES} bytes.",
        )

    content_type = upload_content_type or mimetypes.guess_type(original_name)[0]
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
