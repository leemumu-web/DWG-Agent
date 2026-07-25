from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from .bh_source import Affine2D, PrimitiveGeometry, SourceDocument


class UnsupportedDrawingUnits(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UnitResolution:
    scale_to_mm: float | None
    source: str
    valid: bool


_UNIT_SCALE_TO_MM = {
    1: 25.4,          # inches
    2: 304.8,         # feet
    3: 1_609_344.0,   # miles
    4: 1.0,           # millimetres
    5: 10.0,          # centimetres
    6: 1_000.0,       # metres
    7: 1_000_000.0,   # kilometres
    8: 0.0000254,     # microinches
    9: 0.0254,        # mils
    10: 914.4,        # yards
    11: 0.0000001,    # angstroms
    12: 0.000001,     # nanometres
    13: 0.001,        # microns
    14: 100.0,        # decimetres
    15: 10_000.0,     # decametres
    16: 100_000.0,    # hectometres
}


def resolve_units(insunits: int) -> UnitResolution:
    scale = _UNIT_SCALE_TO_MM.get(int(insunits))
    return UnitResolution(scale, f"INSUNITS={int(insunits)}", scale is not None)


def quantize(value: float, grid: float) -> int:
    if grid <= 0.0:
        raise ValueError("Canonical geometry grid must be positive.")
    return int(round(float(value) / grid))


def _canonical_float(value: float, digits: int = 12) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0.0 else rounded


def _quantized_point(
    point: tuple[float, float],
    *,
    scale_to_mm: float,
    grid_mm: float,
) -> tuple[int, int]:
    return (
        quantize(point[0] * scale_to_mm, grid_mm),
        quantize(point[1] * scale_to_mm, grid_mm),
    )


def _rotations(
    items: list[tuple[int, int, float]],
) -> Iterable[tuple[tuple[int, int, float], ...]]:
    for index in range(len(items)):
        yield tuple(items[index:] + items[:index])


def _canonical_closed_path(
    points: tuple[tuple[int, int], ...],
    bulges: tuple[float, ...],
) -> tuple[tuple[int, int, float], ...]:
    if not points:
        return ()
    normalized_bulges = tuple(
        _canonical_float(bulges[index]) if index < len(bulges) else 0.0
        for index in range(len(points))
    )
    forward = [
        (point[0], point[1], normalized_bulges[index])
        for index, point in enumerate(points)
    ]
    reversed_items: list[tuple[int, int, float]] = []
    count = len(forward)
    for new_index in range(count):
        old_index = (-new_index) % count
        previous_edge = (old_index - 1) % count
        x, y, _ = forward[old_index]
        reversed_items.append((x, y, _canonical_float(-forward[previous_edge][2])))
    return min((*_rotations(forward), *_rotations(reversed_items)))


def _canonical_open_path(
    points: tuple[tuple[int, int], ...],
    bulges: tuple[float, ...],
) -> tuple[tuple[int, int, float], ...]:
    normalized_bulges = tuple(
        _canonical_float(bulges[index]) if index < len(bulges) else 0.0
        for index in range(len(points))
    )
    forward = tuple(
        (point[0], point[1], normalized_bulges[index])
        for index, point in enumerate(points)
    )
    if len(points) < 2:
        return forward
    reversed_items = []
    for new_index, point in enumerate(reversed(points)):
        old_edge = len(points) - 2 - new_index
        bulge = -normalized_bulges[old_edge] if old_edge >= 0 else 0.0
        reversed_items.append((point[0], point[1], _canonical_float(bulge)))
    reversed_path = tuple(reversed_items)
    return min(forward, reversed_path)


def canonical_primitive(
    geometry: PrimitiveGeometry | None,
    *,
    scale_to_mm: float,
    grid_mm: float,
) -> dict[str, Any] | None:
    if geometry is None:
        return None
    points = tuple(
        _quantized_point(point, scale_to_mm=scale_to_mm, grid_mm=grid_mm)
        for point in geometry.coordinates
    )
    payload: dict[str, Any] = {"kind": geometry.kind, "closed": geometry.closed}
    if geometry.kind == "LINE" and len(points) == 2:
        payload["path"] = [list(point) for point in sorted(points)]
    elif geometry.kind in {"LWPOLYLINE", "POLYLINE"}:
        path = (
            _canonical_closed_path(points, geometry.bulges)
            if geometry.closed
            else _canonical_open_path(points, geometry.bulges)
        )
        payload["path"] = [list(item) for item in path]
    elif points:
        payload["path"] = [list(point) for point in points]
    if geometry.center is not None:
        payload["center"] = list(
            _quantized_point(
                geometry.center,
                scale_to_mm=scale_to_mm,
                grid_mm=grid_mm,
            )
        )
    if geometry.radius is not None:
        payload["radius"] = quantize(
            geometry.radius * scale_to_mm,
            grid_mm,
        )
    if geometry.start_angle is not None:
        payload["start_angle"] = _canonical_float(
            float(geometry.start_angle) % 360.0,
            digits=8,
        )
    if geometry.end_angle is not None:
        payload["end_angle"] = _canonical_float(
            float(geometry.end_angle) % 360.0,
            digits=8,
        )
    return payload


def _canonical_affine(
    affine: Affine2D,
    *,
    scale_to_mm: float,
    grid_mm: float,
) -> dict[str, int | float]:
    return {
        "a": _canonical_float(affine.a),
        "b": _canonical_float(affine.b),
        "c": _canonical_float(affine.c),
        "d": _canonical_float(affine.d),
        "tx": quantize(affine.tx * scale_to_mm, grid_mm),
        "ty": quantize(affine.ty * scale_to_mm, grid_mm),
    }


def canonical_sort_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_source_payload(
    source: SourceDocument,
    *,
    grid_mm: float,
) -> dict[str, object]:
    unit = resolve_units(source.units)
    if not unit.valid or unit.scale_to_mm is None:
        raise UnsupportedDrawingUnits(
            f"Unsupported or unspecified drawing units: {unit.source}."
        )
    entities = []
    for entity in source.entities:
        entities.append(
            {
                "type": entity.entity_type,
                "role": entity.semantic_hint.role.value,
                "role_reason": entity.semantic_hint.reason,
                "visibility": entity.visibility.value,
                "linetype": entity.linetype.strip().casefold(),
                "geometry": canonical_primitive(
                    entity.geometry,
                    scale_to_mm=unit.scale_to_mm,
                    grid_mm=grid_mm,
                ),
                "text": entity.normalized_text,
                "text_height": (
                    quantize(entity.text_height * unit.scale_to_mm, grid_mm)
                    if entity.text_height is not None
                    else None
                ),
                "text_rotation": (
                    _canonical_float(entity.text_rotation % 360.0)
                    if entity.text_rotation is not None
                    else None
                ),
                "text_normal_z": (
                    _canonical_float(entity.text_normal_z)
                    if entity.text_normal_z is not None
                    else None
                ),
                "dimension_measurement": (
                    quantize(
                        entity.dimension_measurement * unit.scale_to_mm,
                        grid_mm,
                    )
                    if entity.dimension_measurement is not None
                    else None
                ),
                "insert_names": list(entity.path.inserts),
                "instance_indices": list(entity.path.instance_indices),
                "transforms": [
                    _canonical_affine(
                        item,
                        scale_to_mm=unit.scale_to_mm,
                        grid_mm=grid_mm,
                    )
                    for item in entity.transform_chain
                ],
            }
        )
    return {
        "dxf_version": source.dxf_version,
        "units": "mm",
        "geometry_grid_mm": grid_mm,
        "entities": sorted(entities, key=canonical_sort_key),
        "audit_errors": sorted(source.audit_errors),
    }


def canonical_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_sort_key(payload).encode("utf-8")).hexdigest()
