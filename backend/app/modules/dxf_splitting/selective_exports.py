"""Selective Stage A3 source-DXF exports without server-side ZIP staging."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

import jwt
from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import DxfClassificationRun
from app.modules.dxf_splitting.models import DxfSplitRun
from app.modules.files.interface import StorageZipMember, StoredFile
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException

SELECTIVE_EXPORT_COOKIE_NAME = "dwg_drawing_selective_export"
SELECTIVE_EXPORT_CATEGORY_ORDER = ("failed_bh", "failed_box", "pl", "other")
SELECTIVE_EXPORT_CATEGORY_DEFINITIONS = {
    "failed_bh": {"label": "未通过的 BH", "folder": "未通过的BH"},
    "failed_box": {"label": "未通过的 BOX", "folder": "未通过的BOX"},
    "pl": {"label": "PL", "folder": "PL"},
    "other": {"label": "其他", "folder": "其他"},
}
TOKEN_TYPE = "drawing_selective_export"


def _required_file(db: Session, file_id: int) -> StoredFile:
    stored = db.get(StoredFile, file_id)
    if (
        stored is None
        or stored.status != "available"
        or stored.file_ext.casefold() != ".dxf"
    ):
        raise AppHTTPException(
            409,
            "DRAWING_SELECTIVE_EXPORT_FILE_MISSING",
            "选择导出的分类 DXF 不可用，未生成不完整压缩包。",
            {"file_id": file_id},
        )
    return stored


def category_files(
    db: Session,
    run: DxfSplitRun,
) -> dict[str, list[tuple[int, StoredFile]]]:
    """Resolve four disjoint UI categories from the current classification/split ledgers."""
    result: dict[str, list[tuple[int, StoredFile]]] = {
        category: [] for category in SELECTIVE_EXPORT_CATEGORY_ORDER
    }
    failed_classification_item_ids: set[int] = set()
    auto_accepted_classification_item_ids: set[int] = set()
    for item in run.items:
        if item.automation_route == "auto_accepted":
            auto_accepted_classification_item_ids.add(item.classification_item_id)
            continue
        if item.automation_route != "manual_review":
            continue
        category = {
            "BH": "failed_bh",
            "BOX": "failed_box",
        }.get(item.classification_part_type)
        if category is None:
            continue
        stored = _required_file(db, item.source_file_id)
        result[category].append((item.classification_item_id, stored))
        failed_classification_item_ids.add(item.classification_item_id)

    classification = db.get(DxfClassificationRun, run.classification_run_id)
    if classification is None or classification.workflow_run_id != run.workflow_run_id:
        raise AppHTTPException(
            409,
            "DRAWING_SELECTIVE_EXPORT_CLASSIFICATION_MISSING",
            "当前拆板批次对应的分类账本不可用。",
        )
    for item in classification.items:
        if (
            item.id in failed_classification_item_ids
            or item.id in auto_accepted_classification_item_ids
        ):
            continue
        if item.disposition == "classified" and item.part_type == "PL":
            category = "pl"
        else:
            category = "other"
        stored = _required_file(db, item.output_file_id)
        result[category].append((item.id, stored))
    return result


def export_preview(db: Session, run: DxfSplitRun) -> list[dict[str, Any]]:
    files = category_files(db, run)
    return [
        {
            "key": category,
            "label": SELECTIVE_EXPORT_CATEGORY_DEFINITIONS[category]["label"],
            "file_count": len(files[category]),
            "size_bytes": sum(stored.size_bytes for _, stored in files[category]),
            "available": bool(files[category]),
        }
        for category in SELECTIVE_EXPORT_CATEGORY_ORDER
    ]


def storage_members(
    db: Session,
    run: DxfSplitRun,
    categories: list[str],
) -> tuple[list[StorageZipMember], int]:
    files = category_files(db, run)
    members: list[StorageZipMember] = []
    source_size_bytes = 0
    seen_paths: set[str] = set()
    for category in SELECTIVE_EXPORT_CATEGORY_ORDER:
        if category not in categories:
            continue
        folder = SELECTIVE_EXPORT_CATEGORY_DEFINITIONS[category]["folder"]
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
                    "DRAWING_SELECTIVE_EXPORT_FILENAME_INVALID",
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
            "DRAWING_SELECTIVE_EXPORT_EMPTY",
            "所选类别当前没有可下载的 DXF。",
        )
    return members, source_size_bytes


def export_filename(workflow_id: int, run_id: int) -> str:
    return f"workflow-{workflow_id}-split-run-{run_id}-selected-dxf.zip"


def export_download_path(workflow_id: int, run_id: int, export_uid: str) -> str:
    return (
        f"/api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}"
        f"/selective-exports/{export_uid}/download"
    )


def create_download_token(
    *,
    workflow_id: int,
    run_id: int,
    categories: list[str],
    actor_user_id: int,
) -> tuple[str, str, datetime]:
    now = datetime.now(UTC)
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


def require_download_token(
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
            "DRAWING_SELECTIVE_EXPORT_TOKEN_EXPIRED",
            "本次导出凭据已过期，请重新创建导出。",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AppHTTPException(
            403,
            "DRAWING_SELECTIVE_EXPORT_TOKEN_INVALID",
            "本次导出凭据无效。",
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
        or any(category not in SELECTIVE_EXPORT_CATEGORY_ORDER for category in categories)
    ):
        raise AppHTTPException(
            403,
            "DRAWING_SELECTIVE_EXPORT_TOKEN_INVALID",
            "本次导出凭据无效。",
        )
    return categories, actor_user_id


__all__ = [
    "SELECTIVE_EXPORT_COOKIE_NAME",
    "create_download_token",
    "export_download_path",
    "export_filename",
    "export_preview",
    "require_download_token",
    "storage_members",
]
