"""Stable read/consume boundary for the shared remnant inventory."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, user_role_codes
from app.modules.remnant_inventory.access import can_use_remnants
from app.modules.remnant_inventory.inventory import search_remnants
from app.modules.remnant_inventory.models import Remnant
from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN


def find_available_remnants(
    db: Session, *, material_id: int, thickness_mm: Decimal | str, include_family: bool = False
) -> list[Remnant]:
    return search_remnants(
        db,
        material_id=material_id,
        thickness_mm=thickness_mm,
        include_family=include_family,
        statuses=["available"],
        page=1,
        page_size=200,
    ).items


def remnant_file_access_decision(
    db: Session, *, file_id: int, actor: User, purpose: str
) -> bool | None:
    """Return a remnant-specific decision, or None when the file is unrelated.

    The purpose distinction prevents a worker's shared DXF preview permission
    from becoming permission to download the original drawing bytes.
    """
    rows = list(
        db.scalars(
            select(Remnant).where(
                or_(Remnant.source_file_id == file_id, Remnant.dxf_file_id == file_id)
            )
        ).all()
    )
    if not rows:
        return None
    if not can_use_remnants(actor):
        return False
    if purpose == "preview":
        return any(row.dxf_file_id == file_id for row in rows)
    if purpose == "download":
        is_admin = bool({ROLE_ADMIN, ROLE_SUPER_ADMIN} & user_role_codes(actor))
        return any(
            row.source_file_id == file_id
            and row.status == "reserved"
            and (row.reserved_by == actor.id or is_admin)
            for row in rows
        )
    return False


__all__ = ["Remnant", "find_available_remnants", "remnant_file_access_decision"]
