from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.remnant_inventory.models import RemnantMaterial, RemnantMaterialAlias
from app.platform.http.exceptions import AppHTTPException


def normalize_material_token(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().upper().split())


def create_material(
    db: Session, *, code: str, family_code: str, actor_id: int | None
) -> RemnantMaterial:
    normalized_code = normalize_material_token(code)
    normalized_family = normalize_material_token(family_code)
    if not normalized_code or not normalized_family:
        raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "请填写完整的材质牌号和材质系列。")
    if db.scalar(select(RemnantMaterial.id).where(RemnantMaterial.code == normalized_code)):
        raise AppHTTPException(409, "REMNANT_MATERIAL_EXISTS", "该材质牌号已存在。")
    material = RemnantMaterial(
        code=normalized_code,
        family_code=normalized_family,
        enabled=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(material)
    db.flush()
    return material


def resolve_or_create_material(
    db: Session, *, code: str, actor_id: int | None
) -> tuple[RemnantMaterial, bool]:
    normalized = normalize_material_token(code)
    if not normalized:
        raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "请填写完整的材质牌号。")

    existing = db.scalar(select(RemnantMaterial).where(RemnantMaterial.code == normalized))
    if existing is not None:
        if not existing.enabled:
            raise AppHTTPException(
                409,
                "REMNANT_MATERIAL_DISABLED",
                "该材质已停用，请联系管理员重新启用。",
            )
        return existing, False

    material = RemnantMaterial(
        code=normalized,
        family_code=normalized,
        enabled=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    try:
        with db.begin_nested():
            db.add(material)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(RemnantMaterial)
            .where(RemnantMaterial.code == normalized)
            .with_for_update()
        )
        if existing is None:
            raise
        if not existing.enabled:
            raise AppHTTPException(
                409,
                "REMNANT_MATERIAL_DISABLED",
                "该材质已停用，请联系管理员重新启用。",
            ) from None
        return existing, False
    return material, True


def resolve_or_create_auto_material(
    db: Session, *, code: str, actor_id: int
) -> tuple[RemnantMaterial, bool, bool]:
    """自动导入路径的材质解析（自动建档即启用）。

    与工人确认路径 ``resolve_or_create_material``（对停用材质一律 409）
    不同：本函数仅供服务端自动导入流水线（actor 为批次创建者）使用，图纸中
    重新出现的停用材质按「自动建档即启用」策略处理，返回 reenabled 并写
    ``remnants.material.auto_enable`` 审计。两条路径的策略差异是刻意设计，
    防止自动导入被管理状态阻塞的同时，不让工人绕过管理停用。
    """
    normalized = normalize_material_token(code)
    if not normalized:
        raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "图纸中的材质牌号不完整。")

    existing = db.scalar(
        select(RemnantMaterial)
        .where(RemnantMaterial.code == normalized)
        .with_for_update()
    )
    if existing is None:
        existing = db.scalar(
            select(RemnantMaterial)
            .join(
                RemnantMaterialAlias,
                RemnantMaterialAlias.material_id == RemnantMaterial.id,
            )
            .where(RemnantMaterialAlias.normalized_alias == normalized)
            .with_for_update()
        )
    if existing is not None:
        reenabled = not existing.enabled
        if reenabled:
            existing.enabled = True
            existing.updated_by = actor_id
            db.flush()
        return existing, False, reenabled

    material = RemnantMaterial(
        code=normalized,
        family_code=normalized,
        enabled=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    try:
        with db.begin_nested():
            db.add(material)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(RemnantMaterial)
            .where(RemnantMaterial.code == normalized)
            .with_for_update()
        )
        if existing is None:
            raise
        reenabled = not existing.enabled
        if reenabled:
            existing.enabled = True
            existing.updated_by = actor_id
            db.flush()
        return existing, False, reenabled
    return material, True, False


def update_material(
    db: Session,
    material_id: int,
    *,
    family_code: str | None = None,
    enabled: bool | None = None,
    actor_id: int | None,
) -> RemnantMaterial:
    material = db.get(RemnantMaterial, material_id)
    if material is None:
        raise AppHTTPException(404, "REMNANT_MATERIAL_NOT_FOUND", "材质不存在或已被删除。")
    if family_code is not None:
        normalized = normalize_material_token(family_code)
        if not normalized:
            raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "请填写材质系列。")
        material.family_code = normalized
    if enabled is not None:
        material.enabled = enabled
    material.updated_by = actor_id
    db.flush()
    return material


def replace_aliases(
    db: Session,
    *,
    material: RemnantMaterial,
    aliases: Sequence[str],
    actor_id: int | None,
) -> list[RemnantMaterialAlias]:
    normalized_pairs = [(alias.strip(), normalize_material_token(alias)) for alias in aliases]
    normalized_pairs = [(raw, normalized) for raw, normalized in normalized_pairs if normalized]
    normalized_values = list(dict.fromkeys(normalized for _raw, normalized in normalized_pairs))
    if normalized_values:
        conflict = db.scalar(
            select(RemnantMaterialAlias).where(
                RemnantMaterialAlias.normalized_alias.in_(normalized_values),
                RemnantMaterialAlias.material_id != material.id,
            )
        )
        if conflict is not None:
            raise AppHTTPException(
                409,
                "REMNANT_MATERIAL_ALIAS_EXISTS",
                "该材质别名已归属其他材质。",
            )
    db.execute(delete(RemnantMaterialAlias).where(RemnantMaterialAlias.material_id == material.id))
    rows: list[RemnantMaterialAlias] = []
    seen: set[str] = set()
    for raw, normalized in normalized_pairs:
        if normalized in seen:
            continue
        seen.add(normalized)
        row = RemnantMaterialAlias(
            material_id=material.id,
            alias=raw,
            normalized_alias=normalized,
            created_by=actor_id,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def resolve_material_candidate(db: Session, raw: str) -> RemnantMaterial | None:
    normalized = normalize_material_token(raw)
    material = db.scalar(
        select(RemnantMaterial).where(
            RemnantMaterial.code == normalized,
            RemnantMaterial.enabled.is_(True),
        )
    )
    if material is not None:
        return material
    return db.scalar(
        select(RemnantMaterial)
        .join(RemnantMaterialAlias, RemnantMaterialAlias.material_id == RemnantMaterial.id)
        .where(
            RemnantMaterialAlias.normalized_alias == normalized,
            RemnantMaterial.enabled.is_(True),
        )
    )


def material_ids_for_search(
    db: Session, material_id: int, *, include_family: bool
) -> list[int]:
    selected = db.get(RemnantMaterial, material_id)
    if selected is None or not selected.enabled:
        raise AppHTTPException(404, "REMNANT_MATERIAL_NOT_FOUND", "材质不存在、已删除或已停用。")
    if not include_family:
        return [selected.id]
    return list(
        db.scalars(
            select(RemnantMaterial.id)
            .where(
                RemnantMaterial.family_code == selected.family_code,
                RemnantMaterial.enabled.is_(True),
            )
            .order_by(RemnantMaterial.code)
        ).all()
    )


def list_materials(db: Session, *, enabled_only: bool = True) -> list[RemnantMaterial]:
    query = select(RemnantMaterial)
    if enabled_only:
        query = query.where(RemnantMaterial.enabled.is_(True))
    return list(db.scalars(query.order_by(RemnantMaterial.code)).all())
