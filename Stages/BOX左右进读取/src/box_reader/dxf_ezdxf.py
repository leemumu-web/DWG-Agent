from __future__ import annotations

from math import atan2, cos, hypot, isfinite, pi, radians, sin
from pathlib import Path

from .model import DrawingData, Primitive, UnsupportedGeometry
from .units import insunits_info

_GEOMETRY_KINDS = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "POINT", "ELLIPSE"})
_TEXT_KINDS = frozenset({"TEXT", "MTEXT", "ATTRIB", "ATTDEF"})


def _bulge_arc_points(
    start: tuple[float, float],
    end: tuple[float, float],
    bulge: float,
) -> list[tuple[float, float]]:
    """Sample one LWPOLYLINE bulge arc, keeping exact horizontal extremes.

    Returns points on the arc between start and end.  The centre is derived
    from the bulge (chord-to-height ratio); the sweep is chosen so a 0-degree
    extreme on a half circle is exactly represented.
    """
    if abs(bulge) < 1e-12:
        return [start, end]
    chord_x, chord_y = end[0] - start[0], end[1] - start[1]
    chord = hypot(chord_x, chord_y)
    if chord <= 1e-12:
        return [start, end]
    radius = abs(chord * (1.0 + bulge * bulge) / (4.0 * bulge))
    mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    height = (bulge * chord) / 2.0
    # Direction from chord midpoint to arc centre
    perp = (-chord_y / chord, chord_x / chord)
    if bulge > 0:
        centre = (mid[0] - perp[0] * (radius - height), mid[1] - perp[1] * (radius - height))
        start_angle = atan2(start[1] - centre[1], start[0] - centre[0])
        end_angle = atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = end_angle - start_angle
        while sweep <= 0:
            sweep += 2 * pi
    else:
        centre = (mid[0] + perp[0] * (radius - height), mid[1] + perp[1] * (radius - height))
        start_angle = atan2(start[1] - centre[1], start[0] - centre[0])
        end_angle = atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = end_angle - start_angle
        while sweep >= 0:
            sweep -= 2 * pi
    # Include key angles that carry the horizontal extremes (0/180 degrees).
    key_angles: list[float] = []
    steps = max(4, int(abs(sweep) / (pi / 24)))
    for index in range(steps + 1):
        angle = start_angle + sweep * index / steps
        key_angles.append(angle)
    for key in (0.0, pi):
        if min(start_angle, end_angle) < key < max(start_angle, end_angle) or (
            sweep > 0 and start_angle < key < start_angle + sweep
        ) or (
            sweep < 0 and start_angle > key > start_angle + sweep
        ):
            key_angles.append(key)
    if key_angles:
        key_angles = sorted(key_angles)
        merged: list[float] = []
        for angle in key_angles:
            if not merged or abs(angle - merged[-1]) > 1e-9:
                merged.append(angle)
        key_angles = merged
    points = [(centre[0] + radius * cos(angle), centre[1] + radius * sin(angle)) for angle in key_angles]
    # Ensure endpoints are present exactly.
    points[0] = start
    points[-1] = end
    return points


def _arc_points(
    centre: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
) -> list[tuple[float, float]]:
    # DXF ARC angles are stored in degrees, not radians.  Sampling in the wrong
    # unit made shallow end-fillet arcs sweep more than a full turn and produce
    # phantom extremes at centre +/- radius, corrupting the view bounds.
    start_rad = radians(start_angle)
    end_rad = radians(end_angle)
    sweep = end_rad - start_rad
    if sweep == 0:
        return [(centre[0] + radius * cos(start_rad), centre[1] + radius * sin(start_rad))]
    steps = max(8, int(abs(sweep) / (pi / 24)))
    angles = [start_rad + sweep * index / steps for index in range(steps + 1)]
    lo, hi = sorted((start_rad, end_rad))
    for key in (0.0, pi):
        if lo < key < hi:
            angles.append(key)
    angles = sorted({round(angle, 12) for angle in angles})
    return [(centre[0] + radius * cos(angle), centre[1] + radius * sin(angle)) for angle in angles]


def _geometry_points(entity) -> list[tuple[float, float]] | None:
    kind = entity.dxftype()
    try:
        if kind == "LINE":
            return [entity.dxf.start, entity.dxf.end]
        if kind == "POINT":
            return [entity.dxf.location]
        if kind == "LWPOLYLINE":
            points: list[tuple[float, float]] = []
            previous = None
            for vertex in entity.get_points("xyb"):
                x, y, bulge = float(vertex[0]), float(vertex[1]), float(vertex[2])
                if not all(isfinite(value) for value in (x, y, bulge)):
                    raise ValueError("non-finite LWPOLYLINE vertex")
                point = (x, y)
                if previous is not None and abs(bulge) > 1e-12:
                    points.extend(_bulge_arc_points(previous, point, bulge))
                else:
                    points.append(point)
                previous = point
            return points
        if kind == "POLYLINE":
            points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            return points
        if kind == "ARC":
            return _arc_points(
                (entity.dxf.center.x, entity.dxf.center.y),
                entity.dxf.radius,
                entity.dxf.start_angle,
                entity.dxf.end_angle,
            )
        if kind == "CIRCLE":
            return _arc_points(
                (entity.dxf.center.x, entity.dxf.center.y),
                entity.dxf.radius,
                0.0,
                360.0,
            )
        if kind == "ELLIPSE":
            # Non-uniformly transformed arcs are not safely reducible.
            return None
    except Exception:
        return None
    return None


def read_ezdxf(path: Path) -> DrawingData:
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as exc:
        raise RuntimeError("ezdxf is not installed; run `uv sync`") from exc

    try:
        doc, auditor = recover.readfile(path)
    except Exception as exc:
        return DrawingData(
            path=path,
            primitives=[],
            texts=[],
            backend="ezdxf",
            fatal_messages=[f"DXF recovery incomplete: {exc}"],
        )

    primitives: list[Primitive] = []
    texts: list[Primitive] = []
    unsupported: list[UnsupportedGeometry] = []
    messages = [str(error) for error in auditor.errors]

    try:
        insunits_code = int(doc.header.get("$INSUNITS", 0))
    except (TypeError, ValueError):
        insunits_code = None
    insunits_name, header_unit_to_mm = insunits_info(insunits_code)

    def transform_point(point: tuple[float, float], insert, rotation: float, scale: tuple[float, float]) -> tuple[float, float]:
        x, y = point[0] * scale[0], point[1] * scale[1]
        if abs(rotation) > 1e-12:
            cos_r, sin_r = cos(rotation), sin(rotation)
            x, y = x * cos_r - y * sin_r, x * sin_r + y * cos_r
        return (insert[0] + x, insert[1] + y)

    def resolve_block(block_name: str):
        try:
            return doc.blocks.get(block_name)
        except Exception:
            return None

    def walk_block(block_name: str, transform, inherited_layer: str | None, depth: int = 0) -> None:
        """Expand one block definition into primitives, applying INSERT transform."""
        block = resolve_block(block_name)
        if block is None:
            return
        if depth > 8:
            return
        for entity in block:
            kind = entity.dxftype()
            layer = entity.dxf.get("layer", "0")
            effective_layer = inherited_layer if layer == "0" and inherited_layer else layer
            if kind == "INSERT":
                insert_point = (float(entity.dxf.insert.x), float(entity.dxf.insert.y))
                rotation = float(entity.dxf.get("rotation", 0.0))
                if not isfinite(rotation):
                    rotation = 0.0
                xscale = float(entity.dxf.get("xscale", 1.0))
                yscale = float(entity.dxf.get("yscale", 1.0))
                if not all(isfinite(value) for value in (xscale, yscale)) or xscale <= 1e-12 or yscale <= 1e-12:
                    unsupported.append(UnsupportedGeometry(
                        kind=kind, layer=effective_layer,
                        source_block=block_name,
                        reason="invalid or non-finite INSERT scale",
                    ))
                    continue
                nested_insert = (insert_point[0] + transform[0], insert_point[1] + transform[1])
                nested_scale = (transform[2] * xscale, transform[3] * yscale)
                nested_rotation = transform[4] + rotation
                walk_block(entity.dxf.name, (nested_insert[0], nested_insert[1], nested_scale[0], nested_scale[1], nested_rotation), effective_layer, depth + 1)
                continue
            handle = entity.dxf.get("handle", "")
            if kind in _TEXT_KINDS:
                try:
                    if kind == "MTEXT":
                        text = entity.plain_text()
                        insert_point = (float(entity.dxf.insert.x), float(entity.dxf.insert.y))
                    else:
                        text = entity.dxf.text
                        insert_point = (float(entity.dxf.insert.x), float(entity.dxf.insert.y))
                except Exception:
                    continue
                point = transform_point(insert_point, transform[0:2], transform[4], (transform[2], transform[3]))
                primitive = Primitive(kind="TEXT", layer=effective_layer, points=[point], source_block=block_name, source_handle=handle, text=text)
                primitives.append(primitive)
                texts.append(primitive)
                continue
            if kind not in _GEOMETRY_KINDS:
                if kind in ("HATCH", "SPLINE"):
                    unsupported.append(UnsupportedGeometry(
                        kind=kind, layer=effective_layer, source_block=block_name,
                        reason="not reduced to source edges by this reader",
                    ))
                continue
            points = _geometry_points(entity)
            if points is None:
                unsupported.append(UnsupportedGeometry(
                    kind=kind, layer=effective_layer, source_block=block_name,
                    reason="geometry not reduced to source edges",
                ))
                continue
            if not points:
                continue
            transformed = [transform_point(point, transform[0:2], transform[4], (transform[2], transform[3])) for point in points]
            primitive = Primitive(kind=kind, layer=effective_layer, points=transformed, source_block=block_name, source_handle=handle)
            primitives.append(primitive)

    for insert in doc.modelspace():
        if insert.dxftype() != "INSERT":
            continue
        block_name = insert.dxf.name
        insert_point = (float(insert.dxf.insert.x), float(insert.dxf.insert.y))
        rotation = float(insert.dxf.get("rotation", 0.0))
        if not isfinite(rotation):
            rotation = 0.0
        xscale = float(insert.dxf.get("xscale", 1.0))
        yscale = float(insert.dxf.get("yscale", 1.0))
        if not all(isfinite(value) for value in (xscale, yscale)) or xscale <= 1e-12 or yscale <= 1e-12:
            unsupported.append(UnsupportedGeometry(
                kind="INSERT", layer="0", source_block=block_name,
                reason="invalid or non-finite INSERT scale",
            ))
            continue
        walk_block(
            block_name,
            (insert_point[0], insert_point[1], xscale, yscale, rotation),
            None,
        )

    return DrawingData(
        path=path,
        primitives=primitives,
        texts=texts,
        backend="ezdxf",
        audit_messages=messages,
        unsupported_geometry=unsupported,
        insunits_code=insunits_code,
        insunits_name=insunits_name,
        header_unit_to_mm=header_unit_to_mm,
    )
