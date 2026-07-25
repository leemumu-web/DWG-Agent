from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from math import atan2, cos, degrees, radians, sin
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf.math import bulge_to_arc

from .bh_canonical import (
    canonical_primitive,
    canonical_sha256,
    canonical_sort_key,
    resolve_units,
)
from .bh_dialect import canonical_tekla_layer
from .bh_errors import BHDomainError
from .bh_frames import LocalFrame
from .bh_ir import BHDocumentIR, SemanticLayer, SourceViewRef, VisibilityClass
from .bh_source import (
    Affine2D,
    PrimitiveGeometry,
    SourceDocument,
    SourceEntity,
    primitive_geometry_points,
)
from .bh_topology import connected_source_components
from .geometry_types import BoundingBox, Point2D


class ViewRegionError(BHDomainError):
    diagnostic_code = "BH-VIEW-REGION-FAILED"


@dataclass(frozen=True, slots=True)
class NormalizedEntity:
    source_id: str
    container_id: str
    entity_type: str
    layer: str
    linetype: str
    semantic_role: SemanticLayer
    visibility: VisibilityClass
    geometry: PrimitiveGeometry | None
    text: str | None
    normalized_text: str | None
    text_height: float | None
    text_rotation: float | None
    bbox: BoundingBox | None
    source_order: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class ViewRegion:
    region_id: str
    entities: tuple[NormalizedEntity, ...]
    source_ids: tuple[str, ...]
    bbox: BoundingBox
    geometry_signature: str
    explicit_block: bool
    container_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionBuildResult:
    part_views: tuple[ViewRegion, ...]
    normalized_entities: tuple[NormalizedEntity, ...]
    global_entities: tuple[NormalizedEntity, ...]


def _local_point(frame: LocalFrame, value: tuple[float, float]) -> tuple[float, float]:
    point = frame.to_local_xy(*value)
    return point.x, point.y


def _angle(center: tuple[float, float], point: tuple[float, float]) -> float:
    return degrees(atan2(point[1] - center[1], point[0] - center[0])) % 360.0


def normalize_geometry(
    geometry: PrimitiveGeometry | None,
    frame: LocalFrame,
) -> PrimitiveGeometry | None:
    if geometry is None:
        return None
    coordinates = tuple(_local_point(frame, point) for point in geometry.coordinates)
    center = _local_point(frame, geometry.center) if geometry.center is not None else None
    bulges = geometry.bulges
    start_angle = geometry.start_angle
    end_angle = geometry.end_angle
    if geometry.kind == "ARC" and center is not None and len(coordinates) >= 2:
        start_point, end_point = coordinates[0], coordinates[1]
        if frame.reflected:
            start_point, end_point = end_point, start_point
            coordinates = (start_point, end_point, *coordinates[2:])
        start_angle = _angle(center, start_point)
        end_angle = _angle(center, end_point)
    if frame.reflected and geometry.kind in {"LWPOLYLINE", "POLYLINE"}:
        bulges = tuple(-value for value in bulges)
    return PrimitiveGeometry(
        kind=geometry.kind,
        coordinates=coordinates,
        center=center,
        radius=geometry.radius,
        start_angle=start_angle,
        end_angle=end_angle,
        closed=geometry.closed,
        bulges=bulges,
    )


def _geometry_points(geometry: PrimitiveGeometry | None) -> list[Point2D]:
    return primitive_geometry_points(geometry)


def _bbox(entities: Iterable[NormalizedEntity]) -> BoundingBox:
    points = [
        point
        for entity in entities
        for point in _geometry_points(entity.geometry)
    ]
    if not points:
        raise ViewRegionError("A view region requires geometric evidence.")
    return BoundingBox.from_points(points)


def _snap(value: float, grid: float) -> float:
    snapped = round(float(value) / grid) * grid
    return 0.0 if snapped == 0.0 else snapped


def _snap_geometry(
    geometry: PrimitiveGeometry | None,
    *,
    grid: float,
) -> PrimitiveGeometry | None:
    if geometry is None:
        return None
    coordinates = tuple(
        (_snap(x, grid), _snap(y, grid))
        for x, y in geometry.coordinates
    )
    center = (
        (_snap(geometry.center[0], grid), _snap(geometry.center[1], grid))
        if geometry.center is not None
        else None
    )
    radius = _snap(geometry.radius, grid) if geometry.radius is not None else None
    start_angle = geometry.start_angle
    end_angle = geometry.end_angle
    if geometry.kind == "ARC" and center is not None and len(coordinates) >= 2:
        start_angle = _angle(center, coordinates[0])
        end_angle = _angle(center, coordinates[1])
    return replace(
        geometry,
        coordinates=coordinates,
        center=center,
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
        bulges=tuple(round(value, 12) for value in geometry.bulges),
    )


def _normalize_entity(
    entity: SourceEntity,
    frame: LocalFrame,
    *,
    grid: float,
) -> NormalizedEntity:
    geometry = _snap_geometry(normalize_geometry(entity.geometry, frame), grid=grid)
    points = _geometry_points(geometry)
    text_rotation = entity.text_rotation
    if text_rotation is not None:
        angle = radians(text_rotation)
        direction = Point2D(cos(angle), sin(angle))
        local_x = direction.x * frame.longitudinal.x + direction.y * frame.longitudinal.y
        local_y = direction.x * frame.transverse.x + direction.y * frame.transverse.y
        text_rotation = degrees(atan2(local_y, local_x)) % 360.0
    return NormalizedEntity(
        source_id=entity.source_id,
        container_id=entity.container_id,
        entity_type=entity.entity_type,
        layer=entity.layer,
        linetype=entity.linetype,
        semantic_role=entity.semantic_hint.role,
        visibility=entity.visibility,
        geometry=geometry,
        text=entity.text,
        normalized_text=entity.normalized_text,
        text_height=entity.text_height,
        text_rotation=text_rotation,
        bbox=BoundingBox.from_points(points) if points else None,
        source_order=(
            entity.container_id,
            entity.path.inserts,
            entity.path.instance_indices,
            entity.path.entity_ordinal,
        ),
    )


def _normalized_entity_sort_key(entity: NormalizedEntity) -> str:
    return canonical_sort_key(
        {
            "type": entity.entity_type,
            "role": entity.semantic_role.value,
            "visibility": entity.visibility.value,
            "geometry": canonical_primitive(
                entity.geometry,
                scale_to_mm=1.0,
                grid_mm=1e-6,
            ),
            "text": entity.normalized_text,
        }
    )


def _curve_complexity(entity: SourceEntity) -> int:
    geometry = entity.geometry
    if geometry is None:
        return 0
    if entity.entity_type in {"LINE", "ARC", "CIRCLE"}:
        return 1
    if entity.entity_type in {"LWPOLYLINE", "POLYLINE"}:
        count = len(geometry.coordinates)
        return count if geometry.closed else max(0, count - 1)
    return 0


def _is_part(entity: SourceEntity) -> bool:
    return entity.semantic_hint.role == SemanticLayer.PART_EDGE and entity.geometry is not None


def _nested_view_key(
    entity: SourceEntity,
) -> tuple[tuple[str, ...], tuple[int, ...], tuple[Affine2D, ...]]:
    return (
        entity.path.inserts[1:],
        entity.path.instance_indices[1:],
        tuple(entity.transform_chain[1:]),
    )


def _explicit_groups(entities: tuple[SourceEntity, ...]) -> tuple[tuple[SourceEntity, ...], ...]:
    nested: dict[
        tuple[tuple[str, ...], tuple[int, ...], tuple[Affine2D, ...]],
        list[SourceEntity],
    ] = {}
    for entity in entities:
        nested.setdefault(_nested_view_key(entity), []).append(entity)
    viable = [
        tuple(items)
        for items in nested.values()
        if sum(_curve_complexity(item) for item in items if _is_part(item)) >= 4
    ]
    if len(viable) >= 2:
        return tuple(viable)
    if sum(_curve_complexity(item) for item in entities if _is_part(item)) >= 4:
        return (entities,)
    return ()


@dataclass(frozen=True, slots=True)
class _PartBand:
    entities: tuple[SourceEntity, ...]
    min_y: float
    max_y: float


def _component_band(
    entities: tuple[SourceEntity, ...],
    normalized_by_id: dict[str, NormalizedEntity],
) -> _PartBand:
    normalized = tuple(normalized_by_id[item.source_id] for item in entities)
    bbox = _bbox(normalized)
    return _PartBand(entities, bbox.min_y, bbox.max_y)


def _direct_groups(
    entities: tuple[SourceEntity, ...],
    normalized_by_id: dict[str, NormalizedEntity],
    *,
    band_tolerance: float,
) -> tuple[tuple[SourceEntity, ...], ...]:
    parts = tuple(item for item in entities if _is_part(item))
    components = connected_source_components(parts)
    bands = sorted(
        (
            _component_band(component.entities, normalized_by_id)
            for component in components
        ),
        key=lambda item: (item.min_y, item.max_y),
    )
    merged: list[_PartBand] = []
    for band in bands:
        if not merged or band.min_y > merged[-1].max_y + band_tolerance:
            merged.append(band)
            continue
        previous = merged[-1]
        merged[-1] = _PartBand(
            entities=(*previous.entities, *band.entities),
            min_y=min(previous.min_y, band.min_y),
            max_y=max(previous.max_y, band.max_y),
        )
    return tuple(
        band.entities
        for band in merged
        if sum(_curve_complexity(item) for item in band.entities) >= 4
    )


def _translated_geometry(
    geometry: PrimitiveGeometry | None,
    *,
    dx: float,
    dy: float,
) -> PrimitiveGeometry | None:
    if geometry is None:
        return None
    return replace(
        geometry,
        coordinates=tuple((x + dx, y + dy) for x, y in geometry.coordinates),
        center=(geometry.center[0] + dx, geometry.center[1] + dy)
        if geometry.center is not None
        else None,
    )


def _region_signature(
    part_entities: tuple[NormalizedEntity, ...],
    *,
    scale_to_mm: float,
    grid_mm: float,
) -> str:
    bbox = _bbox(part_entities)
    payload = [
        {
            "role": item.semantic_role.value,
            "geometry": canonical_primitive(
                _translated_geometry(
                    item.geometry,
                    dx=-bbox.min_x,
                    dy=-bbox.min_y,
                ),
                scale_to_mm=scale_to_mm,
                grid_mm=grid_mm,
            ),
        }
        for item in part_entities
    ]
    return canonical_sha256(sorted(payload, key=lambda item: canonical_sha256(item)))


def _build_region(
    source_group: tuple[SourceEntity, ...],
    normalized_by_id: dict[str, NormalizedEntity],
    *,
    explicit_block: bool,
    scale_to_mm: float,
    grid_mm: float,
) -> ViewRegion:
    entities = tuple(
        sorted(
            (normalized_by_id[item.source_id] for item in source_group),
            key=_normalized_entity_sort_key,
        )
    )
    part_entities = tuple(
        item for item in entities if item.semantic_role == SemanticLayer.PART_EDGE
    )
    bbox = _bbox(part_entities)
    signature = _region_signature(
        part_entities,
        scale_to_mm=scale_to_mm,
        grid_mm=grid_mm,
    )
    source_ids = tuple(item.source_id for item in entities)
    return ViewRegion(
        region_id="part-view:" + canonical_sha256({"sources": source_ids, "shape": signature})[:24],
        entities=entities,
        source_ids=source_ids,
        bbox=bbox,
        geometry_signature=signature,
        explicit_block=explicit_block,
        container_ids=tuple(sorted({item.container_id for item in entities})),
    )


def build_view_regions(
    source: SourceDocument,
    frame: LocalFrame,
    *,
    topology_snap: float = 0.01,
    geometry_grid_mm: float = 0.001,
) -> RegionBuildResult:
    """Recover semantic plate views independently of INSERT/explode representation."""

    unit = resolve_units(source.units)
    if not unit.valid or unit.scale_to_mm is None:
        raise ViewRegionError(f"Cannot build physical regions for {unit.source}.")
    drawing_grid = geometry_grid_mm / unit.scale_to_mm
    normalized = tuple(
        _normalize_entity(item, frame, grid=drawing_grid)
        for item in source.entities
    )
    normalized_by_id = {item.source_id: item for item in normalized}
    containers = {item.container_id: item for item in source.containers}
    groups: list[tuple[tuple[SourceEntity, ...], bool]] = []
    for container_id, container in sorted(containers.items()):
        items = source.entities_in(container_id)
        if container.explicit_block:
            current = _explicit_groups(items)
        else:
            current = _direct_groups(
                items,
                normalized_by_id,
                band_tolerance=max(topology_snap, 1e-9),
            )
        groups.extend((group, container.explicit_block) for group in current)

    regions = tuple(
        sorted(
            (
                _build_region(
                    group,
                    normalized_by_id,
                    explicit_block=explicit,
                    scale_to_mm=unit.scale_to_mm,
                    grid_mm=geometry_grid_mm,
                )
                for group, explicit in groups
            ),
            key=lambda item: (
                round(item.bbox.min_y, 9),
                round(item.bbox.min_x, 9),
                item.geometry_signature,
            ),
        )
    )
    assigned = {source_id for region in regions for source_id in region.source_ids}
    global_entities = tuple(item for item in normalized if item.source_id not in assigned)
    return RegionBuildResult(regions, normalized, global_entities)


def _materialized_dxf_attributes(entity: NormalizedEntity) -> dict[str, object]:
    layer = canonical_tekla_layer(entity.semantic_role) or entity.layer or "0"
    attributes: dict[str, object] = {"layer": layer}
    if entity.linetype and entity.linetype.upper() != "BYLAYER":
        attributes["linetype"] = entity.linetype
    return attributes


def _add_materialized_polyline(
    layout,
    geometry: PrimitiveGeometry,
    attributes: dict[str, object],
) -> int:
    points = geometry.coordinates
    if len(points) < 2:
        return 0
    edge_count = len(points) if geometry.closed else len(points) - 1
    count = 0
    for index in range(edge_count):
        start = points[index]
        end = points[(index + 1) % len(points)]
        if start == end:
            continue
        bulge = geometry.bulges[index] if index < len(geometry.bulges) else 0.0
        if abs(bulge) <= 1e-12:
            layout.add_line(start, end, dxfattribs=attributes)
        else:
            center, start_angle, end_angle, radius = bulge_to_arc(start, end, bulge)
            layout.add_arc(
                center,
                radius,
                degrees(start_angle),
                degrees(end_angle),
                dxfattribs=attributes,
            )
        count += 1
    return count


def _add_materialized_entity(layout, entity: NormalizedEntity) -> int:
    geometry = entity.geometry
    if geometry is None:
        return 0
    attributes = _materialized_dxf_attributes(entity)
    kind = entity.entity_type
    if kind == "LINE" and len(geometry.coordinates) == 2:
        layout.add_line(geometry.coordinates[0], geometry.coordinates[1], dxfattribs=attributes)
    elif kind == "ARC" and geometry.center is not None and geometry.radius is not None:
        layout.add_arc(
            geometry.center,
            geometry.radius,
            geometry.start_angle or 0.0,
            geometry.end_angle or 0.0,
            dxfattribs=attributes,
        )
    elif kind == "CIRCLE" and geometry.center is not None and geometry.radius is not None:
        layout.add_circle(geometry.center, geometry.radius, dxfattribs=attributes)
    elif kind in {"LWPOLYLINE", "POLYLINE"}:
        return _add_materialized_polyline(layout, geometry, attributes)
    elif kind == "TEXT" and entity.text is not None and geometry.coordinates:
        attributes.update(
            {
                "insert": geometry.coordinates[0],
                "height": entity.text_height or 2.5,
                "rotation": entity.text_rotation or 0.0,
            }
        )
        layout.add_text(entity.text, dxfattribs=attributes)
    elif kind == "MTEXT" and entity.text is not None and geometry.coordinates:
        attributes.update(
            {
                "insert": geometry.coordinates[0],
                "char_height": entity.text_height or 2.5,
                "rotation": entity.text_rotation or 0.0,
            }
        )
        layout.add_mtext(entity.text, dxfattribs=attributes)
    elif kind == "POINT" and geometry.coordinates:
        layout.add_point(geometry.coordinates[0], dxfattribs=attributes)
    else:
        return 0
    return 1


def materialize_lowering_ir(
    source: SourceDocument,
    regions: RegionBuildResult,
    frame: LocalFrame,
    *,
    source_path: Path | None = None,
) -> BHDocumentIR:
    """Lower canonical member-local regions to the geometry-kernel adapter."""

    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = source.units
    normalized_by_id = {
        entity.source_id: _normalize_entity(entity, frame, grid=1e-6)
        for entity in source.entities
    }
    grouped: list[
        tuple[str, tuple[NormalizedEntity, ...], SourceViewRef | None]
    ] = [
        (
            f"BH_VIEW_{index:03d}",
            tuple(
                sorted(
                    (normalized_by_id[source_id] for source_id in region.source_ids),
                    key=lambda item: item.source_order,
                )
            ),
            SourceViewRef(
                region_id=region.region_id,
                geometry_signature=region.geometry_signature,
                source_ids=region.source_ids,
                container_ids=region.container_ids,
                explicit_block=region.explicit_block,
            ),
        )
        for index, region in enumerate(regions.part_views, start=1)
    ]
    auxiliaries: dict[str, list[NormalizedEntity]] = defaultdict(list)
    for entity in regions.global_entities:
        auxiliaries[entity.container_id].append(normalized_by_id[entity.source_id])
    grouped.extend(
        (
            f"BH_AUX_{index:03d}",
            tuple(sorted(items, key=lambda item: item.source_order)),
            None,
        )
        for index, (_, items) in enumerate(sorted(auxiliaries.items()), start=1)
    )

    emitted_groups: list[
        tuple[tuple[NormalizedEntity, ...], SourceViewRef | None]
    ] = []
    for name, entities, source_view in grouped:
        block = doc.blocks.new(name)
        emitted = []
        for entity in entities:
            layer = canonical_tekla_layer(entity.semantic_role) or entity.layer or "0"
            if layer not in doc.layers:
                doc.layers.add(layer)
            emitted.extend([entity] * _add_materialized_entity(block, entity))
        if not emitted:
            doc.blocks.delete_block(name, safe=False)
            continue
        doc.modelspace().add_blockref(name, (0.0, 0.0))
        emitted_groups.append((tuple(emitted), source_view))

    # Imported locally so bh_frontend remains the single lowering-IR decoder.
    from .bh_frontend import build_bh_document_ir

    ir = build_bh_document_ir(doc, source_path=source_path)
    if len(ir.blocks) != len(emitted_groups):
        raise ViewRegionError("Lowering block materialization lost a source group.")
    for block, (emitted, source_view) in zip(ir.blocks, emitted_groups, strict=True):
        if len(block.entities) != len(emitted):
            raise ViewRegionError("Lowering entity materialization changed entity count.")
        source_by_ordinal = {}
        for atom, normalized in zip(block.entities, emitted, strict=True):
            atom.source = replace(atom.source, source_id=normalized.source_id)
            source_by_ordinal[atom.source.entity_ordinal] = atom.source
        block.texts = [
            replace(text, source=source_by_ordinal[text.source.entity_ordinal])
            for text in block.texts
        ]
        block.source_view = source_view
    ir.dxf_version = source.dxf_version
    ir.encoding = source.encoding
    ir.units = source.units
    ir.audit_error_count = len(source.audit_errors)
    return ir
