"""Field normalization — per-column value parsing and type conversion.

Converts raw cell text to typed Python values based on column position
and pattern matching.  Supports both 9-col (B7) and 10-col (SKG) schemas.

v2 additions:
- component_no field
- Expanded material grade patterns (Q345GJB-Z25, STUD, C, TS10.9)
- Fastener spec patterns (M 20 X 90, D30, NUT_M30)
- Unit label normalization (Kg → kg)
"""

from __future__ import annotations

import re
from typing import Any


def normalize_field(value: str, column_key: str) -> tuple[Any, float]:
    """Normalize a cell value to its typed Python representation.

    Args:
        value: Raw decoded cell text.
        column_key: One of the ALL_FIELD_KEYS.

    Returns:
        (normalized_value, confidence)
    """
    text = value.strip()
    if not text:
        # remark, component_no, area_m2, length_mm can be empty with high confidence
        if column_key in ("remark", "component_no"):
            return ("", 1.0)
        if column_key in ("area_m2", "length_mm"):
            return (None, 1.0)
        return (None, 0.0)

    handler = _NORMALIZERS.get(column_key, lambda v: (v.strip(), 1.0))
    return handler(text)


def normalize_row(
    row_cells: list[str],
    column_keys: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize all cells in a row.

    Returns (normalized_dict, warnings_list).
    """
    result: dict[str, Any] = {}
    warnings: list[str] = []

    for j, key in enumerate(column_keys):
        if j < len(row_cells):
            val, conf = normalize_field(row_cells[j], key)
            result[key] = val
            result[f"{key}_confidence"] = conf
            if conf < 0.5 and row_cells[j].strip():
                warnings.append(
                    f"Low confidence ({conf:.2f}) for {key}: {row_cells[j][:30]!r}"
                )

    return result, warnings


def validate_row_consistency(row: dict[str, Any]) -> list[str]:
    """Cross-field validation for a normalized row.

    Weight check is SKIPPED for:
    - fastener rows (no weights)
    - component_summary rows (totals, not per-part)
    """
    warnings: list[str] = []

    row_type = row.get("row_subtype", "data")
    if row_type in ("fastener_data", "component_summary"):
        return warnings  # skip weight validation

    qty = row.get("quantity")
    uw = row.get("unit_weight_kg")
    tw = row.get("total_weight_kg")

    if (
        qty is not None
        and uw is not None
        and tw is not None
        and isinstance(qty, (int, float))
        and isinstance(uw, (int, float))
        and isinstance(tw, (int, float))
        and tw > 0
    ):
        expected = qty * uw
        deviation = abs(expected - tw) / tw
        if deviation > 0.02:
            warnings.append(
                f"Weight mismatch: {qty} × {uw} = {expected:.2f} "
                f"vs total={tw:.2f} ({deviation:.1%})"
            )

    return warnings


# ---- Internal normalizers ----

def _parse_float(text: str) -> tuple[float | None, float]:
    """Extract float from text."""
    try:
        return (float(text), 1.0)
    except ValueError:
        m = re.search(r"[\d.]+", text)
        if m:
            return (float(m.group()), 0.7)
        return (None, 0.1)


def _parse_int(text: str) -> tuple[int | None, float]:
    """Extract int from text."""
    try:
        return (int(float(text)), 1.0)
    except ValueError:
        m = re.search(r"\d+", text)
        if m:
            return (int(m.group()), 0.7)
        return (None, 0.1)


def _parse_float_optional(text: str) -> tuple[float | None, float]:
    """Extract optional float."""
    if not text.strip():
        return (None, 1.0)
    return _parse_float(text)


def _normalize_material(text: str) -> tuple[str, float]:
    """Normalize material grade.

    Supports:
      Q355B, Q420B, Q345GJB, Q345GJB-Z25, Q345GJB-Z35
      STUD, C, TS10.9, TS8.8
    """
    t = text.strip().upper()
    # Q-grade steels
    if re.match(r"^Q\d{3}[A-Z]*(-Z\d{2})?$", t):
        return (t, 1.0)
    # Fastener grades
    if t in ("STUD", "C"):
        return (t, 1.0)
    if re.match(r"^TS\d+\.\d+$", t):
        return (t, 1.0)
    # Other recognizable
    if re.match(r"^[A-Z]{2,}.*$", t):
        return (t, 0.9)
    return (t, 0.7)


def _normalize_spec(text: str) -> tuple[str, float]:
    """Normalize specification, recognizing plate/box/fastener patterns."""
    t = text.strip()
    # PL, BOX, PIP, HN, HW patterns
    if re.match(r"^(PL|BOX|PIP|HN|HW|HM)\d", t, re.IGNORECASE):
        return (t, 1.0)
    # Fastener patterns: M 20 X 90, D30, NUT_M30
    if re.match(r"^M\s+\d+", t):
        return (t, 0.95)
    if re.match(r"^(D\d+|NUT_|STUD)", t, re.IGNORECASE):
        return (t, 0.95)
    return (t, 1.0)


def _normalize_component_no(text: str) -> tuple[str, float]:
    """Normalize component number."""
    t = text.strip()
    if re.match(r"^[A-Za-z0-9\-_\.]+$", t):
        return (t, 1.0)
    return (t, 0.8)


_NORMALIZERS: dict[str, Any] = {
    "component_no": _normalize_component_no,
    "part_no": lambda v: (v.strip(), 1.0),
    "spec": _normalize_spec,
    "length_mm": _parse_float_optional,
    "material": _normalize_material,
    "quantity": _parse_int,
    "unit_weight_kg": _parse_float,
    "total_weight_kg": _parse_float,
    "area_m2": _parse_float_optional,
    "remark": lambda v: (v.strip() if v.strip() else "", 1.0),
}
