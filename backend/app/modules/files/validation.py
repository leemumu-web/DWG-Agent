"""File-name, upload metadata and DWG signature validation."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from app.platform.config.constants import ALLOWED_UPLOAD_EXTENSIONS
from app.platform.http.exceptions import AppHTTPException

ALLOWED_DWG_MIME_TYPES = {
    "application/acad",
    "application/autocad",
    "application/dwg",
    "application/octet-stream",
    "application/x-acad",
    "application/x-autocad",
    "application/x-dwg",
    "image/vnd.dwg",
    "application/vnd.dwg",
    "application/x-extension-dwg",
    "drawing/x-dwg",
    "image/x-dwg",
    "model/vnd.dwg",
}

_BINARY_FALLBACK_MIME = {
    "",
    "application/binary",
    "application/download",
    "application/x-binary",
    "application/x-msdownload",
    "binary/octet-stream",
}

SUPPORTED_DWG_HEADERS = {
    b"AC1012",
    b"AC1014",
    b"AC1015",
    b"AC1018",
    b"AC1021",
    b"AC1024",
    b"AC1027",
    b"AC1032",
}

MIN_DWG_SIZE_BYTES = 1024


def sanitize_filename(name: str) -> str:
    """Normalize a user filename and remove traversal/header injection vectors."""
    name = unicodedata.normalize("NFKC", name)
    for char in ("\x00", "/", "\\"):
        name = name.replace(char, "_")
    while ".." in name:
        name = name.replace("..", ".")

    safe: list[str] = []
    for char in name:
        codepoint = ord(char)
        allowed = (
            char in "._- @()+,"
            or char.isalnum()
            or codepoint > 127
        ) and codepoint >= 32
        safe.append(char if allowed else "_")
    clean = "".join(safe).lstrip(".-").rstrip(". ") or "unnamed"

    if len(clean) > 200:
        extension = clean.rsplit(".", 1)[-1] if "." in clean else ""
        basename = clean.rsplit(".", 1)[0] if "." in clean else clean
        clean = (
            f"{basename[: 200 - len(extension) - 1]}.{extension}"
            if extension
            else basename[:200]
        )
    return clean


def validate_upload_name(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise AppHTTPException(
            415,
            "FILE_TYPE_NOT_ALLOWED",
            "File type not allowed for upload.",
            {"allowed_extensions": sorted(ALLOWED_UPLOAD_EXTENSIONS)},
        )
    return extension


def validate_upload_mime(content_type: str | None) -> str | None:
    """Normalize MIME metadata; the DWG header remains the authoritative guard."""
    if not content_type:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in ALLOWED_DWG_MIME_TYPES or normalized in _BINARY_FALLBACK_MIME:
        return normalized
    return normalized


def validate_dwg_header(first_chunk: bytes) -> None:
    if len(first_chunk) < 6:
        raise AppHTTPException(415, "FILE_NOT_DWG", "File is too small to be a valid DWG.")
    if first_chunk[:6] not in SUPPORTED_DWG_HEADERS:
        raise AppHTTPException(415, "FILE_NOT_DWG", "File does not have a valid DWG header.")


__all__ = [
    "ALLOWED_DWG_MIME_TYPES",
    "MIN_DWG_SIZE_BYTES",
    "SUPPORTED_DWG_HEADERS",
    "sanitize_filename",
    "validate_dwg_header",
    "validate_upload_mime",
    "validate_upload_name",
]
