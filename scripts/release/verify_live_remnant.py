#!/usr/bin/env python3
"""Verify the protected remnant workflow against live MySQL and MinIO."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from uuid import uuid4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用受保护运行时验证余料库与真实 MySQL/MinIO 的完整交接，并自动清理测试数据。"
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="用于验收的便携 DXF 文件。",
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=Path("/app"),
        help="受保护后端运行根目录（默认：/app）。",
    )
    return parser.parse_args()


def _unique_dxf(source: bytes, marker: str) -> bytes:
    eof = b"  0\nEOF"
    offset = source.rfind(eof)
    if offset < 0:
        raise RuntimeError("DXF 验收样本缺少 EOF 标记。")
    comment = f"999\n{marker}\n".encode()
    return source[:offset] + comment + source[offset:]


def _cleanup(marker: str) -> None:
    from sqlalchemy import delete, or_, select

    from app.modules.files.models import FileTransfer, StoredFile
    from app.modules.jobs.models import Job, JobStep
    from app.modules.remnant_inventory.models import (
        Remnant,
        RemnantImportBatch,
        RemnantImportItem,
        RemnantMaterial,
        RemnantMaterialAlias,
        RemnantPart,
    )
    from app.platform.config.settings import settings
    from app.platform.database.session import SessionLocal
    from app.platform.storage.factory import get_storage_backend

    storage_locations: list[tuple[str, str]] = [
        (
            settings.minio_bucket_dxf_original,
            f"release-smoke/remnant/{marker}.dxf",
        )
    ]
    with SessionLocal() as db:
        files = list(
            db.scalars(select(StoredFile).where(StoredFile.batch_name == marker)).all()
        )
        file_ids = [row.id for row in files]
        storage_locations.extend((row.bucket, row.storage_key) for row in files)
        storage_locations = list(dict.fromkeys(storage_locations))

        items = (
            list(
                db.scalars(
                    select(RemnantImportItem).where(
                        RemnantImportItem.source_file_id.in_(file_ids)
                    )
                ).all()
            )
            if file_ids
            else []
        )
        item_ids = [row.id for row in items]
        batch_ids = list({row.batch_id for row in items})
        job_ids = list(
            {
                job_id
                for row in items
                for job_id in (row.conversion_job_id, row.parse_job_id)
                if job_id is not None
            }
        )
        remnant_ids = (
            list(
                db.scalars(
                    select(Remnant.id).where(Remnant.import_item_id.in_(item_ids))
                ).all()
            )
            if item_ids
            else []
        )

        if remnant_ids:
            db.execute(delete(RemnantPart).where(RemnantPart.remnant_id.in_(remnant_ids)))
            db.execute(delete(Remnant).where(Remnant.id.in_(remnant_ids)))
        if item_ids:
            db.execute(delete(RemnantImportItem).where(RemnantImportItem.id.in_(item_ids)))
        if batch_ids:
            db.execute(delete(RemnantImportBatch).where(RemnantImportBatch.id.in_(batch_ids)))
        if job_ids:
            db.execute(delete(JobStep).where(JobStep.job_id.in_(job_ids)))
            db.execute(delete(Job).where(Job.id.in_(job_ids)))
        db.execute(
            delete(FileTransfer).where(
                or_(
                    FileTransfer.request_id == marker,
                    FileTransfer.storage_key
                    == f"release-smoke/remnant/{marker}.dxf",
                    FileTransfer.file_id.in_(file_ids) if file_ids else False,
                )
            )
        )
        if file_ids:
            db.execute(delete(StoredFile).where(StoredFile.id.in_(file_ids)))

        material = db.scalar(
            select(RemnantMaterial).where(RemnantMaterial.code == marker)
        )
        if material is not None:
            db.execute(
                delete(RemnantMaterialAlias).where(
                    RemnantMaterialAlias.material_id == material.id
                )
            )
            db.delete(material)
        db.commit()

    storage = get_storage_backend()
    for bucket, storage_key in storage_locations:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                storage.delete_object(bucket, storage_key)
                last_error = None
                break
            except Exception as exc:  # cleanup must retry the exact test object
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError("MinIO 验收对象清理失败。") from last_error


def _verify(fixture: Path, marker: str) -> dict[str, object]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.modules.files.interface import (
        StoredFile,
        get_storage_backend,
        save_bytes_as_file,
    )
    from app.modules.identity.models.role import Role, user_roles
    from app.modules.identity.models.user import User
    from app.modules.jobs.models import Job
    from app.modules.remnant_inventory.execution import (
        prepare_import_execution,
        run_parse_item,
    )
    from app.modules.remnant_inventory.imports import (
        confirm_import_items,
        register_import_batch,
        update_import_item,
    )
    from app.modules.remnant_inventory.interface import (
        find_available_remnants,
        remnant_file_access_decision,
    )
    from app.modules.remnant_inventory.models import (
        Remnant,
        RemnantImportBatch,
        RemnantImportItem,
        RemnantMaterial,
    )
    from app.platform.config.constants import ACTIVE, ROLE_ADMIN, ROLE_SUPER_ADMIN
    from app.platform.config.settings import settings
    from app.platform.database.session import SessionLocal

    source = fixture.read_bytes()
    payload = _unique_dxf(source, marker)
    sha256 = hashlib.sha256(payload).hexdigest()
    storage_key = f"release-smoke/remnant/{marker}.dxf"

    with SessionLocal() as db:
        actor = db.scalar(
            select(User)
            .join(user_roles, user_roles.c.user_id == User.id)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(
                User.status == ACTIVE,
                User.deleted_at.is_(None),
                Role.code.in_((ROLE_SUPER_ADMIN, ROLE_ADMIN)),
            )
            .options(selectinload(User.roles))
            .order_by(User.id)
        )
        if actor is None:
            raise RuntimeError("没有可用于余料验收的有效管理员账号。")

        stored = save_bytes_as_file(
            db,
            bucket=settings.minio_bucket_dxf_original,
            storage_key=storage_key,
            original_name=f"{marker}.dxf",
            file_ext=".dxf",
            content_type="application/dxf",
            payload=payload,
            uploaded_by=actor.id,
            batch_name=marker,
            transfer_operation="release_smoke",
            request_id=marker,
        )
        batch = register_import_batch(
            db,
            actor_id=actor.id,
            source_files=[stored],
            import_mode="manual",
        )
        item = db.scalar(
            select(RemnantImportItem).where(
                RemnantImportItem.batch_id == batch.id
            )
        )
        dispatch = prepare_import_execution(db, batch.id, actor_id=actor.id)
        if (
            item is None
            or dispatch.convert_attempts
            or dispatch.parse_attempts != {item.id: item.attempt}
        ):
            raise RuntimeError("DXF 余料任务没有正确进入解析队列。")
        item_id, attempt = next(iter(dispatch.parse_attempts.items()))
        file_id = stored.id
        batch_id = batch.id
        actor_id = actor.id
        db.commit()

    run_parse_item(item_id, attempt)

    with SessionLocal() as db:
        actor = db.scalar(
            select(User)
            .where(User.id == actor_id)
            .options(selectinload(User.roles))
        )
        item = db.get(RemnantImportItem, item_id)
        batch = db.get(RemnantImportBatch, batch_id)
        if actor is None or item is None or batch is None:
            raise RuntimeError("余料解析后的数据库记录不完整。")
        if item.status != "pending_confirmation":
            raise RuntimeError(
                f"余料解析未进入待确认状态：{item.error_code or item.status}"
            )
        if not item.parser_version or not item.schema_version:
            raise RuntimeError("余料解析版本信息没有写回 MySQL。")
        parse_job = db.get(Job, item.parse_job_id)
        if parse_job is None or parse_job.status != "succeeded":
            raise RuntimeError("余料解析任务状态没有正确完成。")
        if batch.status != "awaiting_confirmation" or batch.pending_count != 1:
            raise RuntimeError("余料批次计数没有正确写回 MySQL。")

        material = RemnantMaterial(
            code=marker,
            family_code=marker,
            enabled=True,
            created_by=actor.id,
            updated_by=actor.id,
        )
        db.add(material)
        db.flush()
        update_import_item(
            db,
            item.id,
            actor=actor,
            thickness_mm="10",
            material_id=material.id,
            project_no=marker,
            parts=[f"{marker}-PART"],
        )
        confirmation = confirm_import_items(db, [item.id], actor=actor)
        if len(confirmation.confirmed) != 1 or confirmation.invalid:
            raise RuntimeError("余料确认没有生成唯一库存记录。")
        remnant_id = confirmation.confirmed[0].remnant_id
        db.commit()

    with SessionLocal() as db:
        actor = db.scalar(
            select(User)
            .where(User.id == actor_id)
            .options(selectinload(User.roles))
        )
        remnant = db.get(Remnant, remnant_id)
        if actor is None or remnant is None or remnant.status != "available":
            raise RuntimeError("余料记录没有以可用状态写入 MySQL。")
        available = find_available_remnants(
            db,
            material_id=remnant.material_id,
            thickness_mm="10",
        )
        if [row.id for row in available].count(remnant.id) != 1:
            raise RuntimeError("新余料无法通过标准库存接口检索。")
        if (
            remnant_file_access_decision(
                db,
                file_id=file_id,
                actor=actor,
                purpose="preview",
            )
            is not True
        ):
            raise RuntimeError("余料 DXF 预览权限没有与库存记录正确衔接。")

        storage = get_storage_backend()
        stored = db.get(StoredFile, file_id)
        if (
            stored is None
            or stored.bucket != settings.minio_bucket_dxf_original
            or stored.storage_key != storage_key
            or stored.size_bytes != len(payload)
            or stored.sha256 != sha256
        ):
            raise RuntimeError("余料文件元数据没有正确写入 MySQL。")
        info = storage.stat_object(
            settings.minio_bucket_dxf_original,
            storage_key,
        )
        downloaded = b"".join(
            storage.iter_file(settings.minio_bucket_dxf_original, storage_key)
        )
        if info.size_bytes != len(payload) or downloaded != payload:
            raise RuntimeError("MySQL 登记文件与 MinIO 实际对象不一致。")
        if hashlib.sha256(downloaded).hexdigest() != sha256:
            raise RuntimeError("MinIO 回读文件摘要不一致。")

    return {
        "database": "mysql",
        "storage": "minio",
        "parse_status": "succeeded",
        "inventory_status": "available",
        "preview_access": True,
        "bytes_verified": len(payload),
    }


def main() -> int:
    args = parse_args()
    fixture = args.fixture.resolve()
    if not fixture.is_file():
        print("余料验收 DXF 样本不存在。", file=sys.stderr)
        return 2
    app_root = args.app_root.resolve()
    if not (app_root / "app").is_dir():
        print("受保护后端运行根目录无效。", file=sys.stderr)
        return 2
    sys.path.insert(0, str(app_root))

    marker = f"RELEASE-SMOKE-{uuid4().hex.upper()}"
    failure: BaseException | None = None
    result: dict[str, object] | None = None
    try:
        result = _verify(fixture, marker)
    except BaseException as exc:
        failure = exc
    try:
        _cleanup(marker)
    except BaseException as cleanup_error:
        if failure is not None:
            raise RuntimeError("余料验收失败，且测试数据清理未完成。") from cleanup_error
        raise
    if failure is not None:
        raise failure
    print(json.dumps({"status": "pass", **(result or {})}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
