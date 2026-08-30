"""Selective source-DXF exports for the standalone PL split stage."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

import jwt
from sqlalchemy.orm import Session

from app.modules.dxf_splitting.models import DxfSplitRun
from app.modules.files.interface import StorageZipMember, StoredFile
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now

PL_SELECTIVE_EXPORT_COOKIE_NAME = "dwg_pl_selective_export"
PL_SELECTIVE_EXPORT_CATEGORY_ORDER = ("failed_pl", "failed_xbox", "other")
PL_SELECTIVE_EXPORT_CATEGORY_DEFINITIONS = {
    "failed_pl": {"label": "未通过的 PL", "folder": "未通过的PL"},
    "failed_xbox": {
        "label": "未通过的 XBOX（预留）",
        "folder": "未通过的XBOX",
    },
    "other": {"label": "其他", "folder": "其他"},
}
TOKEN_TYPE = "pl_selective_export"


def _required_file(db: Session, file_id: int) -> StoredFile:
    stored = db.get(StoredFile, file_id)
    if (
        stored is None
        or stored.status != "available"
        or stored.file_ext.casefold() != ".dxf"
    ):
        raise AppHTTPException(
            409,
            "PL_SELECTIVE_EXPORT_FILE_MISSING",
            "选择导出的 PL 分类 DXF 不可用，未生成不完整压缩包。",
            {"file_id": file_id},
        )
    return stored


def pl_category_files(
    db: Session,
    run: DxfSplitRun,
) -> dict[str, list[tuple[int, StoredFile]]]:
    result: dict[str, list[tuple[int, StoredFile]]] = {
        category: [] for category in PL_SELECTIVE_EXPORT_CATEGORY_ORDER
    }
    for item in run.items:
        if item.automation_route != "manual_review":
            continue
        category = (
            "failed_pl"
            if item.classification_part_type == "PL" and item.family == "PL"
            else "other"
        )
        stored = _required_file(db, item.source_file_id)
        result[category].append((item.classification_item_id, stored))
    return result


def pl_export_preview(db: Session, run: DxfSplitRun) -> list[dict[str, Any]]:
    files = pl_category_files(db, run)
    return [
        {
            "key": category,
            "label": PL_SELECTIVE_EXPORT_CATEGORY_DEFINITIONS[category]["label"],
            "file_count": len(files[category]),
            "size_bytes": sum(stored.size_bytes for _, stored in files[category]),
            "available": bool(files[category]),
        }
        for category in PL_SELECTIVE_EXPORT_CATEGORY_ORDER
    ]


def pl_storage_members(
    db: Session,
    run: DxfSplitRun,
    categories: list[str],
) -> tuple[list[StorageZipMember], int]:
    files = pl_category_files(db, run)
    members: list[StorageZipMember] = []
    source_size_bytes = 0
    seen_paths: set[str] = set()
    for category in PL_SELECTIVE_EXPORT_CATEGORY_ORDER:
        if category not in categories:
            continue
        folder = PL_SELECTIVE_EXPORT_CATEGORY_DEFINITIONS[category]["folder"]
        for item_id, stored in files[category]:
            original_name = stored.original_name
            if (
                not original_name
                or original_name in {".", ".."}
                or "/" in original_name
                or "\\" in original_name
                or "\x00" in original_name
                or PurePosixPath(original_name).name != original_name
            ):
                raise AppHTTPException(
                    409,
                    "PL_SELECTIVE_EXPORT_FILENAME_INVALID",
                    "登记文件名无法安全写入导出压缩包。",
                    {"file_id": stored.id, "original_name": original_name},
                )
            archive_path = f"{folder}/{original_name}"
            if archive_path.casefold() in seen_paths:
                archive_path = f"{folder}/重复-{item_id}/{original_name}"
            seen_paths.add(archive_path.casefold())
            members.append(
                StorageZipMember(
                    bucket=stored.bucket,
                    storage_key=stored.storage_key,
                    archive_path=archive_path,
                )
            )
            source_size_bytes += stored.size_bytes
    if not members:
        raise AppHTTPException(
            409,
            "PL_SELECTIVE_EXPORT_EMPTY",
            "所选类别当前没有可下载的 DXF。",
        )
    return members, source_size_bytes


def pl_export_filename(workflow_id: int, run_id: int) -> str:
    return f"workflow-{workflow_id}-pl-split-run-{run_id}-selected-dxf.zip"


def pl_export_download_path(workflow_id: int, run_id: int, export_uid: str) -> str:
    return (
        f"/api/v1/workflows/{workflow_id}/pl-xbox-split/runs/{run_id}"
        f"/selective-exports/{export_uid}/download"
    )


def create_pl_download_token(
    *,
    workflow_id: int,
    run_id: int,
    categories: list[str],
    actor_user_id: int,
) -> tuple[str, str, datetime]:
    now = business_now()
    expires_at = now + timedelta(minutes=settings.workflow_batch_export_ttl_minutes)
    export_uid = str(uuid4())
    token = jwt.encode(
        {
            "type": TOKEN_TYPE,
            "jti": export_uid,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "actor_user_id": actor_user_id,
            "categories": categories,
            "iat": now.timestamp(),
            "exp": int(expires_at.timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return export_uid, token, expires_at


def require_pl_download_token(
    token: str | None,
    *,
    workflow_id: int,
    run_id: int,
    export_uid: str,
) -> tuple[list[str], int]:
    try:
        payload = jwt.decode(
            token or "",
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppHTTPException(
            410,
            "PL_SELECTIVE_EXPORT_TOKEN_EXPIRED",
            "本次 PL 导出凭据已过期，请重新创建导出。",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AppHTTPException(
            403,
            "PL_SELECTIVE_EXPORT_TOKEN_INVALID",
            "本次 PL 导出凭据无效。",
        ) from exc
    categories = payload.get("categories")
    actor_user_id = payload.get("actor_user_id")
    if (
        payload.get("type") != TOKEN_TYPE
        or payload.get("jti") != export_uid
        or payload.get("workflow_id") != workflow_id
        or payload.get("run_id") != run_id
        or not isinstance(actor_user_id, int)
        or actor_user_id <= 0
        or not isinstance(categories, list)
        or not categories
        or len(categories) != len(set(categories))
        or any(
            category not in PL_SELECTIVE_EXPORT_CATEGORY_ORDER
            for category in categories
        )
    ):
        raise AppHTTPException(
            403,
            "PL_SELECTIVE_EXPORT_TOKEN_INVALID",
            "本次 PL 导出凭据无效。",
        )
    return categories, actor_user_id


__all__ = [
    "PL_SELECTIVE_EXPORT_COOKIE_NAME",
    "create_pl_download_token",
    "pl_export_download_path",
    "pl_export_filename",
    "pl_export_preview",
    "pl_storage_members",
    "require_pl_download_token",
]
