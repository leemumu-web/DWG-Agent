from __future__ import annotations

import hashlib
import mimetypes
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ALLOWED_UPLOAD_EXTENSIONS
from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.storage.base import AbstractStorageBackend, StorageConfigurationError, StorageError
from app.storage.local_storage import LocalFileStorage
from app.storage.minio_storage import MinioStorage
from app.utils.path_utils import ensure_within_root

ALLOWED_DWG_MIME_TYPES = {
    "application/acad",
    "application/autocad",
    "application/dwg",
    "application/octet-stream",
    "application/x-acad",
    "application/x-autocad",
    "application/x-dwg",
    "image/vnd.dwg",
    # Common browser fallbacks for .dwg (varies by OS / MIME database):
    "application/vnd.dwg",
    "application/x-extension-dwg",
    "drawing/x-dwg",
    "image/x-dwg",
    "model/vnd.dwg",
}

# MIME types that browsers send for unrecognised binary files — allow these
# through because the DWG header check (§10.3 ¶4) is the real security boundary.
_BINARY_FALLBACK_MIME = {
    "",
    "application/binary",
    "application/download",
    "application/x-binary",
    "application/x-msdownload",
    "binary/octet-stream",
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


def sanitize_filename(name: str) -> str:
    """Strip path traversal, control chars, and injection vectors from a filename.

    Keeps: alphanumerics, CJK characters, dots, dashes, underscores, spaces,
    and common safe punctuation.  Everything else is replaced with '_'.
    Leading dots and dashes are stripped (prevents hidden files on Unix).
    The result is never empty — falls back to 'unnamed'.

    Call this on every user-supplied filename before storing, serving, or
    embedding in headers / archive paths.
    """
    import unicodedata

    # 1. Normalise Unicode (NFKC) to collapse look-alike characters
    name = unicodedata.normalize("NFKC", name)

    # 2. Strip directory separators and null bytes
    for ch in ("\x00", "/", "\\"):
        name = name.replace(ch, "_")

    # 3. Collapse ".." to prevent traversal
    while ".." in name:
        name = name.replace("..", ".")

    # 4. Keep only safe characters
    safe: list[str] = []
    for ch in name:
        cp = ord(ch)
        if (
            ch in "._- @()+,"          # common safe punctuation
            or ch.isalnum()             # A-Z a-z 0-9 + Unicode alnum (covers CJK)
            or cp > 127                 # non-ASCII (CJK, Cyrillic, etc.)
        ) and cp >= 32:                 # no control chars
            safe.append(ch)
        else:
            safe.append("_")
    clean = "".join(safe)

    # 5. Strip leading dots/dashes (hidden files) and trailing dots/spaces (Windows)
    clean = clean.lstrip(".-").rstrip(". ")

    # 6. Fallback
    if not clean:
        clean = "unnamed"

    # 7. Truncate to reasonable max
    if len(clean) > 200:
        ext = clean.rsplit(".", 1)[-1] if "." in clean else ""
        base = clean.rsplit(".", 1)[0] if "." in clean else clean
        clean = base[: 200 - len(ext) - 1] + "." + ext if ext else base[:200]

    return clean


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
    """Validate the upload Content-Type, or pass through unknown types.

    MIME-type filtering is a first-pass hint; the DWG header check
    (AC1012–AC1032 bytes, §10.3 ¶4) is the real security boundary.
    Browsers/OSes report wildly different MIME for .dwg files, so
    we never block on MIME alone.
    """
    if not content_type:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in ALLOWED_DWG_MIME_TYPES or normalized in _BINARY_FALLBACK_MIME:
        return normalized
    # Pass-through for unknown MIME — DWG header check catches non-DWG files
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
    """Build a storage path and validate it stays inside the configured root (§6.2.6)."""
    root = settings.local_storage_root
    path = root / bucket / storage_key
    return ensure_within_root(root, path)


def get_storage_backend() -> AbstractStorageBackend:
    if settings.storage_backend == "local":
        return LocalFileStorage(settings.local_storage_root)
    if settings.storage_backend == "minio":
        try:
            return MinioStorage(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
            )
        except StorageConfigurationError as exc:
            raise AppHTTPException(
                500,
                "STORAGE_BACKEND_MISCONFIGURED",
                "Configured storage backend is not ready.",
            ) from exc
    raise AppHTTPException(
        500,
        "STORAGE_BACKEND_UNSUPPORTED",
        f"Unsupported storage backend: {settings.storage_backend}",
    )


async def save_upload_file(
    db: Session, upload: UploadFile, uploaded_by: int | None, batch_name: str | None = None
) -> StoredFile:
    original_name = sanitize_filename(upload.filename or "unnamed.dwg")
    file_ext = validate_upload_name(original_name)
    upload_content_type = validate_upload_mime(upload.content_type)
    bucket = (
        settings.minio_bucket_original
        if file_ext == ".dwg"
        else settings.minio_bucket_dxf_original
    )
    storage_key = f"uploads/{uuid4().hex}{file_ext}"
    storage = get_storage_backend()

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    max_size = settings.max_upload_size_mb * 1024 * 1024

    with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as tmp:
        try:
            first = True
            while chunk := await upload.read(1024 * 1024):
                if first:
                    if file_ext == ".dwg":
                        validate_dwg_header(chunk)
                    first = False
                size += len(chunk)
                if size > max_size:
                    raise AppHTTPException(413, "FILE_TOO_LARGE", "Uploaded file exceeds max size.")
                sha256.update(chunk)
                md5.update(chunk)
                tmp.write(chunk)
            if first:
                if file_ext == ".dwg":
                    validate_dwg_header(b"")
        except AppHTTPException:
            raise

        if file_ext == ".dwg" and size < MIN_DWG_SIZE_BYTES:
            raise AppHTTPException(
                415,
                "FILE_NOT_DWG",
                f"File too small ({size} bytes) — legitimate DWG files exceed {MIN_DWG_SIZE_BYTES} bytes.",
            )

        content_type = upload_content_type or mimetypes.guess_type(original_name)[0]
        try:
            storage.put_fileobj(
                bucket,
                storage_key,
                tmp,
                length=size,
                content_type=content_type,
            )
        except StorageError as exc:
            raise AppHTTPException(
                503,
                "STORAGE_WRITE_FAILED",
                "Failed to persist uploaded file.",
            ) from exc

    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=file_ext,
        content_type=content_type,
        size_bytes=size,
        sha256=sha256.hexdigest(),
        md5=md5.hexdigest(),
        batch_name=batch_name,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def save_bytes_as_file(
    db: Session,
    *,
    bucket: str,
    storage_key: str,
    original_name: str,
    file_ext: str,
    content_type: str,
    payload: bytes,
    uploaded_by: int | None,
    batch_name: str | None = None,
) -> StoredFile:
    storage = get_storage_backend()
    try:
        storage.put_fileobj(
            bucket,
            storage_key,
            BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_WRITE_FAILED",
            "Failed to persist generated file.",
        ) from exc

    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=file_ext,
        content_type=content_type,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        md5=hashlib.md5(payload).hexdigest(),
        batch_name=batch_name,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def get_local_file_path(file: StoredFile) -> Path:
    return build_storage_path(file.bucket, file.storage_key)
