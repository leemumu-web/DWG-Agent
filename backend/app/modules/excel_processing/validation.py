"""Canonical Excel input preflight for uploads and registered file objects."""

from __future__ import annotations

from fastapi import UploadFile

from app.modules.excel_processing.schemas import ExcelStage1Inspection
from app.modules.excel_processing.stage_adapter import (
    ExcelFinalInputError,
    ExcelFinalProcessError,
    ExcelFinalUnavailableError,
    inspect_excel_stage1_bytes,
)
from app.modules.files.interface import StoredFile, get_storage_backend
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageError, StorageObjectNotFound


def _maximum_input_bytes() -> int:
    return settings.max_upload_size_mb * 1024 * 1024


def _raise_http_failure(exc: ExcelFinalInputError) -> None:
    failure = exc.failure
    status_code = 409 if failure.code == "EXCEL_INPUT_OBJECT_CHANGED" else 422
    raise AppHTTPException(
        status_code,
        failure.code,
        failure.message,
        {"failure": failure.as_dict()},
    ) from exc


def _inspect_bytes(
    *,
    file_name: str,
    payload: bytes,
    expected_sha256: str | None = None,
) -> ExcelStage1Inspection:
    try:
        return inspect_excel_stage1_bytes(
            file_name=file_name,
            payload=payload,
            expected_sha256=expected_sha256,
        )
    except ExcelFinalInputError as exc:
        _raise_http_failure(exc)
    except ExcelFinalUnavailableError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE1_UNAVAILABLE",
            "Excel 第一阶段检查服务当前不可用。",
            {"action": "请稍后重试；如果问题持续，请联系管理员检查服务状态。"},
        ) from exc
    except ExcelFinalProcessError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE1_INTERNAL_ERROR",
            "Excel 第一阶段检查未能完成。",
            {"action": "请稍后重试；如果问题持续，请联系管理员并提供请求编号。"},
        ) from exc


async def preflight_excel_upload(upload: UploadFile) -> ExcelStage1Inspection:
    """Inspect an upload before the Files transfer saga creates durable state."""
    maximum = _maximum_input_bytes()
    payload = await upload.read(maximum + 1)
    await upload.seek(0)
    if len(payload) > maximum:
        raise AppHTTPException(
            413,
            "INPUT_OBJECT_TOO_LARGE",
            "上传文件超过系统允许的大小。",
            {"maximum_bytes": maximum},
        )
    return _inspect_bytes(
        file_name=upload.filename or "unnamed.xlsx",
        payload=payload,
    )


def preflight_stored_excel(stored: StoredFile) -> ExcelStage1Inspection:
    """Read, verify and inspect one Files-owned object before Job creation."""
    maximum = _maximum_input_bytes()
    if stored.size_bytes > maximum:
        raise AppHTTPException(
            413,
            "INPUT_OBJECT_TOO_LARGE",
            "已登记的 Excel 文件超过系统允许的大小。",
            {"maximum_bytes": maximum},
        )
    storage = get_storage_backend()
    payload = bytearray()
    try:
        for chunk in storage.iter_file(stored.bucket, stored.storage_key):
            payload.extend(chunk)
            if len(payload) > maximum:
                raise AppHTTPException(
                    413,
                    "INPUT_OBJECT_TOO_LARGE",
                    "已登记的 Excel 文件超过系统允许的大小。",
                    {"maximum_bytes": maximum},
                )
    except AppHTTPException:
        raise
    except StorageObjectNotFound as exc:
        raise AppHTTPException(
            409,
            "EXCEL_STAGE1_STORAGE_FAILED",
            "已登记的 Excel 文件在存储中不存在。",
            {"action": "请重新上传文件。"},
        ) from exc
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE1_STORAGE_FAILED",
            "暂时无法读取已登记的 Excel 文件。",
            {"action": "请稍后重试；如果问题持续，请联系管理员。"},
        ) from exc
    if len(payload) != stored.size_bytes:
        raise AppHTTPException(
            409,
            "EXCEL_INPUT_OBJECT_CHANGED",
            "Excel 文件大小与登记信息不一致。",
            {"action": "请重新上传文件后再处理。"},
        )
    return _inspect_bytes(
        file_name=stored.original_name,
        payload=bytes(payload),
        expected_sha256=stored.sha256,
    )


__all__ = ["preflight_excel_upload", "preflight_stored_excel"]
