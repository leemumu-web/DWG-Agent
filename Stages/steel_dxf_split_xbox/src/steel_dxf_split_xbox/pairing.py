from __future__ import annotations

from pathlib import Path

import ezdxf

# Weld-allowance extension tiers pinned by the certified 20-pair corpus:
# length in millimetres (upper bound inclusive) -> longitudinal extension.
ALLOWANCE_TIERS: tuple[tuple[int, int], ...] = (
    (2000, 0),
    (5000, 5),
    (10000, 10),
    (15000, 15),
)


def allowance_increment(length_mm: float) -> int:
    """Weld-allowance extension for a plate length; >15000mm maps to +20mm."""

    for bound, increment in ALLOWANCE_TIERS:
        if length_mm <= bound:
            return increment
    return 20


def _plate_bounds(path: Path) -> list[tuple[float, float]]:
    document = ezdxf.readfile(path)
    plates: list[tuple[float, float]] = []
    for entity in document.modelspace():
        if entity.dxftype() != "LWPOLYLINE":
            continue
        points = list(entity.get_points("xy"))
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        plates.append((max(xs) - min(xs), max(ys) - min(ys)))
    return plates


def verify_paired_geometry(
    normal_dxf: Path,
    allowance_dxf: Path,
    *,
    tolerance_mm: float = 0.01,
) -> dict[str, object]:
    """Self-check one normal/allowance pair against the tier table.

    The pair must contain the same number of plates; every plate keeps its
    width and grows its length by exactly the tier increment of the normal
    length. Returns a dict proof; raises ValueError on any violation.
    """

    normal_plates = _plate_bounds(normal_dxf)
    allowance_plates = _plate_bounds(allowance_dxf)
    if len(normal_plates) != len(allowance_plates):
        raise ValueError(
            "weld-allowance pair plate count mismatch: "
            f"{len(normal_plates)} != {len(allowance_plates)}"
        )
    checks: list[dict[str, object]] = []
    for index, ((normal_w, normal_h), (allow_w, allow_h)) in enumerate(
        zip(sorted(normal_plates), sorted(allowance_plates))
    ):
        expected = float(allowance_increment(normal_w))
        actual = allow_w - normal_w
        if abs(actual - expected) > tolerance_mm:
            raise ValueError(
                "weld-allowance extension mismatch on plate "
                f"{index}: expected +{expected}mm, got +{actual:.2f}mm"
            )
        if abs(allow_h - normal_h) > tolerance_mm:
            raise ValueError(
                "weld-allowance width changed on plate "
                f"{index}: {normal_h} -> {allow_h}"
            )
        checks.append(
            {
                "plate_index": index,
                "normal_length_mm": round(normal_w, 3),
                "width_mm": round(normal_h, 3),
                "expected_extension_mm": expected,
                "actual_extension_mm": round(actual, 3),
            }
        )
    return {"ok": True, "plates": checks}


__all__ = [
    "ALLOWANCE_TIERS",
    "allowance_increment",
    "verify_paired_geometry",
]
