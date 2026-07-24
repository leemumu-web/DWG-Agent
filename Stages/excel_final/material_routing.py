"""Authoritative material-family routing for D-series steel specifications."""

from __future__ import annotations

D_MATERIAL_CATEGORY_BY_PREFIX = {
    "HRB": "rebar",
    "HPB": "round_bar",
    "Q235B": "round_bar",
    "Q355B": "round_bar",
}


def normalize_material(material: object) -> str:
    return str(material or "").replace(" ", "").replace("　", "").upper()


def material_class(material: object) -> str | None:
    normalized = normalize_material(material)
    return next(
        (
            prefix
            for prefix in D_MATERIAL_CATEGORY_BY_PREFIX
            if normalized.startswith(prefix)
        ),
        None,
    )


def d_series_category(material: object) -> str | None:
    family = material_class(material)
    return D_MATERIAL_CATEGORY_BY_PREFIX.get(family) if family is not None else None


__all__ = [
    "D_MATERIAL_CATEGORY_BY_PREFIX",
    "d_series_category",
    "material_class",
    "normalize_material",
]
