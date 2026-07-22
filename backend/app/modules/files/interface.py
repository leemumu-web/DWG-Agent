"""Public file-registry boundary for other business modules."""

from pathlib import Path

from app.modules.files.access import (
    can_read_file,
    file_list_access_filter,
    file_project_ids,
    require_file_delete_access,
    require_file_read_access,
)
from app.modules.files.exports import (
    DOWNLOAD_URL_TTL_SECONDS,
    PreparedExport,
    ZipAvailabilityResolution,
    build_dxf_result_map,
    build_result_map,
    build_signed_download_url,
    build_zip,
    build_zip_to_path,
    download_headers,
    download_signature,
    preview_zip_availability,
    validate_download_signature,
)
from app.modules.files.lifecycle import soft_delete_file_in_transaction
from app.modules.files.models import FileTransfer, StorageScanFinding, StorageScanRun, StoredFile
from app.modules.files.registration import (
    get_local_file_path,
    save_bytes_as_file,
    save_path_as_file,
    save_upload_file,
)
from app.modules.files.schemas import (
    BatchBulkDeleteRequest,
    BatchBulkDeleteResult,
    BulkDeleteRequest,
    DownloadUrlRead,
    DxfPreviewBoundsRead,
    DxfPreviewRead,
    FileRead,
    ZipAvailabilityPreview,
    ZipDownloadRequest,
    ZipFormatAvailability,
    ZipUploadResult,
)
from app.modules.files.storage_transactions import (
    ACTIVE_TRANSFER_STATUSES,
    TERMINAL_TRANSFER_STATUSES,
    TransferSnapshot,
    TransferSpec,
    begin_transfer,
    complete_reused_transfer_in_transaction,
    complete_transfer_in_transaction,
    mark_transfer_in_progress,
    prepare_generated_file_transfer,
    prepare_transfer_in_transaction,
    register_pending_destructive_transfer,
    session_factory_for,
    settle_stream,
    settle_transfer,
)
from app.modules.files.validation import (
    ALLOWED_DWG_MIME_TYPES,
    MIN_DWG_SIZE_BYTES,
    SUPPORTED_DWG_HEADERS,
    sanitize_filename,
    validate_dwg_header,
    validate_dxf_structure,
    validate_upload_mime,
    validate_upload_name,
)
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import AbstractStorageBackend


def build_storage_path(bucket: str, storage_key: str) -> Path:
    return storage_factory.build_storage_path(bucket, storage_key)


def get_storage_backend() -> AbstractStorageBackend:
    return storage_factory.get_storage_backend()


def clear_storage_backend_cache() -> None:
    storage_factory.clear_storage_backend_cache()


def storage_health() -> dict[str, str]:
    return storage_factory.storage_health()


__all__ = [
    "ACTIVE_TRANSFER_STATUSES",
    "ALLOWED_DWG_MIME_TYPES",
    "BatchBulkDeleteRequest",
    "BatchBulkDeleteResult",
    "BulkDeleteRequest",
    "DownloadUrlRead",
    "DOWNLOAD_URL_TTL_SECONDS",
    "DxfPreviewBoundsRead",
    "DxfPreviewRead",
    "FileRead",
    "FileTransfer",
    "MIN_DWG_SIZE_BYTES",
    "PreparedExport",
    "SUPPORTED_DWG_HEADERS",
    "StoredFile",
    "StorageScanFinding",
    "StorageScanRun",
    "TERMINAL_TRANSFER_STATUSES",
    "TransferSnapshot",
    "TransferSpec",
    "ZipAvailabilityPreview",
    "ZipAvailabilityResolution",
    "ZipDownloadRequest",
    "ZipFormatAvailability",
    "ZipUploadResult",
    "begin_transfer",
    "build_dxf_result_map",
    "build_result_map",
    "build_signed_download_url",
    "build_storage_path",
    "build_zip",
    "build_zip_to_path",
    "can_read_file",
    "clear_storage_backend_cache",
    "complete_reused_transfer_in_transaction",
    "complete_transfer_in_transaction",
    "download_headers",
    "download_signature",
    "file_list_access_filter",
    "file_project_ids",
    "get_local_file_path",
    "get_storage_backend",
    "mark_transfer_in_progress",
    "prepare_generated_file_transfer",
    "prepare_transfer_in_transaction",
    "preview_zip_availability",
    "register_pending_destructive_transfer",
    "require_file_delete_access",
    "require_file_read_access",
    "sanitize_filename",
    "save_bytes_as_file",
    "save_path_as_file",
    "save_upload_file",
    "session_factory_for",
    "settle_stream",
    "settle_transfer",
    "soft_delete_file_in_transaction",
    "storage_health",
    "validate_download_signature",
    "validate_dwg_header",
    "validate_dxf_structure",
    "validate_upload_mime",
    "validate_upload_name",
]
