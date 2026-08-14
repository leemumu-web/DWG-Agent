"""文件登记（File Registry）对其他业务模块的公共边界。

调用契约（CONTEXT.md：Interface 必须文档化不变量、错误模式、顺序与配置）：

- 跨存储写序：登记助手（``save_upload_file`` / ``save_bytes_as_file`` /
  ``save_path_as_file``）先写字节到存储后端、再在 MySQL 登记行；DB 事务
  回滚时由 ``after_rollback`` 补偿删除孤儿存储对象。字节与登记从来不是
  一个 ACID 单元——调用方必须把流转账本（FileTransfer）视为结算记录。
- 流转账本生命周期：``FileTransfer`` 行按
  ``prepared → in_progress → succeeded | failed | cancelled |
  compensation_required`` 流转（见 ``ACTIVE_TRANSFER_STATUSES`` /
  ``TERMINAL_TRANSFER_STATUSES``）。``prepare_*_transfer`` /
  ``begin_transfer`` / ``settle_transfer`` / ``settle_stream`` 是唯一受控
  的变更助手；不要直接改 status 列。
- 破坏性流程：``soft_delete_file_in_transaction`` 只置
  ``StoredFile.status = deleted`` 并登记待执行的破坏性流转；物理对象由
  ``scripts/storage/reap.py`` 稍后回收——绝不内联删除存储对象。
- 幂等：上传/批量操作对重复请求键返回 409 ``*_DUPLICATE`` 类错误；客户端
  每次逻辑提交复用一个请求键，提交内容变化时重新生成。
- 访问控制：``require_file_*_access`` / ``can_read_file`` /
  ``file_list_access_filter`` 强制项目级 RBAC，所有跨模块的文件读/删必须
  经过它们；下载额外校验短时 HMAC 签名（``download_signature`` /
  ``validate_download_signature``）。
"""

from pathlib import Path

from app.modules.files.access import (
    can_read_file,
    file_list_access_filter,
    file_project_ids,
    require_file_delete_access,
    require_file_download_access,
    require_file_read_access,
)
from app.modules.files.exports import (
    DOWNLOAD_URL_TTL_SECONDS,
    PreparedExport,
    ZipAvailabilityResolution,
    build_dxf_result_map,
    build_registered_files_zip_to_path,
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
from app.modules.files.models import (
    FileTransfer,
    StorageScanFinding,
    StorageScanRun,
    StoredFile,
)
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
    prepare_destructive_transfer,
    prepare_generated_file_transfer,
    prepare_transfer_in_transaction,
    register_pending_destructive_transfer,
    session_factory_for,
    settle_stream,
    settle_transfer,
)
from app.modules.files.streaming_zip import StorageZipMember, iter_storage_zip
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
    "StorageZipMember",
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
    "build_registered_files_zip_to_path",
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
    "iter_storage_zip",
    "mark_transfer_in_progress",
    "prepare_generated_file_transfer",
    "prepare_destructive_transfer",
    "prepare_transfer_in_transaction",
    "preview_zip_availability",
    "register_pending_destructive_transfer",
    "require_file_delete_access",
    "require_file_download_access",
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
