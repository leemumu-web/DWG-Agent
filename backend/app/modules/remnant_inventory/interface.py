"""Stable read/consume boundary for the shared remnant inventory."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.remnant_inventory.inventory import search_remnants
from app.modules.remnant_inventory.models import Remnant


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


__all__ = ["Remnant", "find_available_remnants"]
