from __future__ import annotations

from math import isfinite
from pathlib import Path

from .dxf_ascii import (
    _block_effective_boundary_layers,
    _blocks,
    _sections,
    _tags,
    raw_source_geometry_issues,
)
from .dxf_geometry import arc_points, polyline_points
from .model import DrawingData, Primitive, UnsupportedGeometry
from .units import insunits_info


def read_ezdxf(path: Path) -> DrawingData:
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as exc:
        raise RuntimeError("ezdxf is not installed; run `uv sync`") from exc

    try:
        doc, auditor = recover.readfile(path)
    except Exception as exc:  # ezdxf recovery can surface malformed entities as IndexError
        return DrawingData(
            path=path,
            primitives=[],
            texts=[],
            backend="ezdxf",
            fatal_messages=[f"DXF recovery incomplete: {exc}"],
        )

    primitives: list[Primitive] = []
    texts: list[Primitive] = []
    messages = [str(error) for error in auditor.errors]
    fatal_messages: list[str] = []
    raw_sections = _sections(_tags(path))
    raw_blocks = _blocks(raw_sections.get("BLOCKS", []))
    unsupported_geometry = raw_source_geometry_issues(path)
    try:
        insunits_code = int(doc.header.get("$INSUNITS", 0))
    except (TypeError, ValueError):
        insunits_code = None
    insunits_name, header_unit_to_mm = insunits_info(insunits_code)

    def effective_layer(entity, inherited_layer: str | None) -> str:
        raw_layer = entity.dxf.get("layer", "0")
        return inherited_layer if raw_layer == "0" and inherited_layer is not None else raw_layer

    def has_nondefault_ocs(entity, kind: str, handle: str) -> bool:
        extrusion = entity.dxf.get("extrusion", (0.0, 0.0, 1.0))
        if extrusion is None:
            ocs_vector = (0.0, 0.0, 1.0)
        else:
            ocs_vector = tuple(float(value) for value in extrusion)
            if len(ocs_vector) != 3:
                raise ValueError(f"expected 3 extrusion coordinates, got {ocs_vector!r}")
        if not all(isfinite(value) for value in ocs_vector):
            raise ValueError(f"non-finite OCS extrusion: {ocs_vector!r}")
        return any(
            abs(actual - expected) > 1e-12
            for actual, expected in zip(ocs_vector, (0.0, 0.0, 1.0), strict=True)
        )

    def insert_is_array(entity) -> bool:
        counts: list[int] = []
        for name in ("column_count", "row_count"):
            value = float(entity.dxf.get(name, 1))
            if not isfinite(value) or value < 1 or value != int(value):
                raise ValueError(f"invalid INSERT {name}: {value!r}")
            counts.append(int(value))
        return any(value > 1 for value in counts)

    def polyline_source_issue(entity, kind: str) -> str | None:
        flags_value = float(entity.dxf.get("flags", 0))
        if not isfinite(flags_value) or flags_value != int(flags_value):
            raise ValueError(f"invalid polyline flags: {flags_value!r}")
        flags = int(flags_value)
        if kind == "POLYLINE" and flags & ~0x81:
            return f"unsupported POLYLINE mode flags {flags}"
        widths: list[tuple[object, str]] = []
        if kind == "LWPOLYLINE":
            widths.append((entity.dxf.get("const_width", 0.0), "constant width"))
            points = list(entity.get_points("xyseb"))
            declared_count = int(entity.dxf.get("count", len(points)))
            if declared_count != len(points):
                raise ValueError(
                    f"LWPOLYLINE declared {declared_count} vertices but parsed {len(points)}"
                )
            for point in points:
                widths.extend(((point[2], "start width"), (point[3], "end width")))
        else:
            widths.extend(
                (
                    (entity.dxf.get("default_start_width", 0.0), "start width"),
                    (entity.dxf.get("default_end_width", 0.0), "end width"),
                )
            )
            for vertex in entity.vertices:
                widths.extend(
                    (
                        (vertex.dxf.get("start_width", 0.0), "start width"),
                        (vertex.dxf.get("end_width", 0.0), "end width"),
                    )
                )
        for value, label in widths:
            width = float(value)
            if not isfinite(width):
                raise ValueError(f"non-finite polyline {label}")
            if abs(width) > 1e-12:
                return f"non-zero polyline {label}"
        return None

    def record_unsupported_insert(
        entity,
        layer: str,
        source_block: str,
        reason: str,
    ) -> None:
        handle = entity.dxf.get("handle", "") or ""
        name = entity.dxf.get("name", "") or ""
        layers = _block_effective_boundary_layers(raw_blocks, name, layer)
        for effective_layer in sorted(layers):
            candidate = UnsupportedGeometry(
                kind="INSERT",
                layer=effective_layer,
                source_block=source_block,
                source_handle=handle,
                reason=reason,
            )
            if candidate not in unsupported_geometry:
                unsupported_geometry.append(candidate)

    def append_entity(entity, source_block: str, layer: str) -> None:
        kind = entity.dxftype()
        handle = entity.dxf.get("handle", "") or ""
        points: list[tuple[float, float]] = []
        text = ""
        if kind in {"LWPOLYLINE", "POLYLINE"}:
            try:
                source_issue = polyline_source_issue(entity, kind)
            except (AttributeError, TypeError, ValueError) as exc:
                fatal_messages.append(
                    f"DXF polyline parsing incomplete for {kind} {handle}: {exc}"
                )
                return
            if source_issue:
                unsupported_geometry.append(
                    UnsupportedGeometry(
                        kind=kind,
                        layer=layer,
                        source_block=source_block,
                        source_handle=handle,
                        reason=source_issue,
                    )
                )
                return
        if kind in {"ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}:
            try:
                nondefault_ocs = has_nondefault_ocs(entity, kind, handle)
            except (AttributeError, TypeError, ValueError) as exc:
                fatal_messages.append(
                    f"DXF OCS parsing incomplete for {kind} {handle}: {exc}"
                )
                return
            if nondefault_ocs:
                unsupported_geometry.append(
                    UnsupportedGeometry(
                        kind=kind,
                        layer=layer,
                        source_block=source_block,
                        source_handle=handle,
                        reason="non-default OCS extrusion",
                    )
                )
                return
        try:
            if kind == "LINE":
                points = [(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)]
            elif kind == "LWPOLYLINE":
                points = polyline_points(
                    [(point[0], point[1], point[4]) for point in entity.get_points("xyseb")],
                    closed=entity.closed,
                )
            elif kind == "POLYLINE":
                points = polyline_points(
                    [
                        (
                            vertex.dxf.location.x,
                            vertex.dxf.location.y,
                            vertex.dxf.get("bulge", 0.0),
                        )
                        for vertex in entity.vertices
                    ],
                    closed=entity.is_closed,
                )
            elif kind == "ARC":
                points = arc_points(entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius, entity.dxf.start_angle, entity.dxf.end_angle)
            elif kind == "CIRCLE":
                points = arc_points(entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius, 0.0, 360.0, 10.0)
            elif kind == "ELLIPSE":
                unsupported_geometry.append(
                    UnsupportedGeometry(
                        kind=kind,
                        layer=layer,
                        source_block=source_block,
                        source_handle=handle,
                        reason="unsupported ELLIPSE source geometry (possibly from non-uniform INSERT transform)",
                    )
                )
                return
            elif kind in {"TEXT", "ATTRIB", "ATTDEF"}:
                text = entity.dxf.text
                point = entity.dxf.get("insert", entity.dxf.get("align_point"))
                points = [(point.x, point.y)]
            elif kind == "MTEXT":
                text = entity.plain_text()
                points = [(entity.dxf.insert.x, entity.dxf.insert.y)]
            else:
                unsupported_geometry.append(
                    UnsupportedGeometry(
                        kind=kind,
                        layer=layer,
                        source_block=source_block,
                        source_handle=handle,
                        reason="no safe source-edge reader",
                    )
                )
                return
        except (AttributeError, ValueError, TypeError) as exc:
            messages.append(f"skip {kind} {handle}: {exc}")
            fatal_messages.append(f"DXF entity parsing incomplete for {kind} {handle}: {exc}")
            return
        if any(not isfinite(value) for point in points for value in point):
            reason = f"non-finite {kind} coordinate"
            messages.append(f"skip {kind} {handle}: {reason}")
            fatal_messages.append(f"DXF entity parsing incomplete for {kind} {handle}: {reason}")
            return
        primitive = Primitive(kind=kind, layer=layer, points=points, source_block=source_block, source_handle=handle, text=text)
        primitives.append(primitive)
        if text:
            texts.append(primitive)

    def walk(
        entity,
        path_parts: tuple[str, ...],
        depth: int = 0,
        inherited_layer: str | None = None,
    ) -> None:
        if depth > 20:
            messages.append(f"block recursion limit exceeded: {'/'.join(path_parts)}")
            if inherited_layer is not None:
                unsupported_geometry.append(
                    UnsupportedGeometry(
                        kind="INSERT",
                        layer=inherited_layer,
                        source_block="/".join(path_parts) or "MODELSPACE",
                        reason="block recursion limit exceeded before source geometry was expanded",
                    )
                )
            return
        layer = effective_layer(entity, inherited_layer)
        if entity.dxftype() == "INSERT":
            name = entity.dxf.name
            handle = entity.dxf.get("handle", "") or ""
            source_block = "/".join(path_parts) or "MODELSPACE"
            try:
                nondefault_ocs = has_nondefault_ocs(entity, "INSERT", handle)
            except (AttributeError, TypeError, ValueError) as exc:
                fatal_messages.append(
                    f"DXF OCS parsing incomplete for INSERT {handle}: {exc}"
                )
                return
            if nondefault_ocs:
                record_unsupported_insert(
                    entity,
                    layer,
                    source_block,
                    "non-default OCS extrusion on INSERT placement",
                )
                return
            try:
                is_array = insert_is_array(entity)
            except (TypeError, ValueError) as exc:
                fatal_messages.append(
                    f"DXF INSERT array parsing incomplete for {handle}: {exc}"
                )
                return
            if is_array:
                record_unsupported_insert(
                    entity,
                    layer,
                    source_block,
                    "MINSERT row/column array placement is not expanded",
                )
                return
            if name not in doc.blocks:
                messages.append(f"missing block definition: {name}")
                record_unsupported_insert(
                    entity,
                    layer,
                    source_block,
                    f"missing block definition: {name}",
                )
                return
            try:
                children = list(entity.virtual_entities())
            except Exception as exc:  # ezdxf raises different transform errors here
                messages.append(f"cannot expand INSERT {name}: {exc}")
                reason = str(exc)
                record_unsupported_insert(
                    entity,
                    layer,
                    source_block,
                    f"DXF INSERT expansion incomplete for {name}: {reason}",
                )
                return
            for child in children:
                walk(child, path_parts + (name,), depth + 1, layer)
            return
        append_entity(entity, "/".join(path_parts) or "MODELSPACE", layer)

    for entity in doc.modelspace():
        walk(entity, ())
    unique_unsupported: list[UnsupportedGeometry] = []
    seen_unsupported: set[tuple[str, str, str, str]] = set()
    for item in unsupported_geometry:
        identity = (item.kind, item.layer, item.source_block, item.source_handle)
        if identity in seen_unsupported:
            continue
        seen_unsupported.add(identity)
        unique_unsupported.append(item)
    return DrawingData(
        path=path, primitives=primitives, texts=texts, backend="ezdxf",
        audit_messages=messages, fatal_messages=fatal_messages,
        unsupported_geometry=unique_unsupported, insunits_code=insunits_code,
        insunits_name=insunits_name, header_unit_to_mm=header_unit_to_mm,
    )
