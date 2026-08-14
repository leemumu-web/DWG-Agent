"""Authoritative material-family routing for D-series steel specifications.

Business rule (CONTEXT.md「五金手册材质路由」): D-series specs are routed by
material family to exactly one handbook category — HRB queries 螺纹钢
(``rebar``), while HPB/Q235B/Q355B query 圆钢 (``round_bar``). The routing
only decides the query category; it does NOT mean the handbook will hit.
Other materials must never borrow weights across categories.

Mirror contract: the backend adapter
(``app.modules.excel_processing.stage_adapter._normalize_lookup_request``)
and the frontend handbook validation replicate this same mapping; both are
kept in sync by cross-seam tests — change all three sides together.
"""

from __future__ import annotations

# Category keys correspond to HandbookCategory values consumed by the
# handbook repository (rebar=螺纹钢, round_bar=圆钢).
D_MATERIAL_CATEGORY_BY_PREFIX = {
    "HRB": "rebar",
    "HPB": "round_bar",
    "Q235B": "round_bar",
    "Q355B": "round_bar",
}


def normalize_material(material: object) -> str:
    """Normalize a material token: strip whitespace, uppercase."""
    return str(material or "").replace(" ", "").replace("　", "").upper()


def material_class(material: object) -> str | None:
    """Return the matching material-family prefix (key), or None.

    Prefix matching (``startswith``) so e.g. ``HRB400`` maps to ``HRB``.
    """
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
    """Route a D-series material to its unique handbook category, or None.

    None means the material family is unknown — the caller must NOT guess a
    category (no cross-category weight borrowing).
    """
    family = material_class(material)
    return D_MATERIAL_CATEGORY_BY_PREFIX.get(family) if family is not None else None


__all__ = [
    "D_MATERIAL_CATEGORY_BY_PREFIX",
    "d_series_category",
    "material_class",
    "normalize_material",
]
