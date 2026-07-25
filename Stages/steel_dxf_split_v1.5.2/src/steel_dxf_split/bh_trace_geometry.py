from __future__ import annotations

from collections.abc import Iterable
from math import cos, radians, sin
from typing import Any

from ezdxf.entities import DXFEntity
from ezdxf.path import make_path
from shapely.geometry import Polygon

from .bh_models import BulgeContour, CircularCut
from .bh_source import SourceEntity
from .bh_trace import TraceShape


def polygon_shape(
    shape_id: str,
    role: str,
    polygon: Polygon,
    source_ids: tuple[str, ...] = (),
) -> TraceShape:
    coordinates = tuple((float(x), float(y)) for x, y in polygon.exterior.coords)
    interiors = [
        [[float(x), float(y)] for x, y in ring.coords]
        for ring in polygon.interiors
    ]
    return TraceShape(
        shape_id,
        "polygon",
        role,
        coordinates,
        True,
        (),
        source_ids,
        {"interiors": interiors, "area_mm2": float(polygon.area)},
    )


def contour_shape(shape_id: str, role: str, contour: BulgeContour) -> TraceShape:
    return TraceShape(
        shape_id,
        "polyline",
        role,
        tuple((item.x, item.y) for item in contour.vertices),
        contour.closed,
        tuple(item.bulge for item in contour.vertices),
    )


def cut_shape(shape_id: str, role: str, cut: CircularCut) -> TraceShape:
    return TraceShape(
        shape_id,
        "circle",
        role,
        ((cut.center.x, cut.center.y),),
        True,
        properties={"radius": cut.radius},
    )


def polygon_shapes(
    prefix: str, role: str, polygons: Iterable[Polygon]
) -> tuple[TraceShape, ...]:
    return tuple(
        polygon_shape(f"{prefix}-{index:03d}", role, polygon)
        for index, polygon in enumerate(polygons, start=1)
    )


def cut_shapes(
    prefix: str, role: str, cuts: Iterable[CircularCut]
) -> tuple[TraceShape, ...]:
    return tuple(
        cut_shape(f"{prefix}-{index:03d}", role, cut)
        for index, cut in enumerate(cuts, start=1)
    )


def _entity_id(entity: DXFEntity, index: int) -> str:
    handle = getattr(entity.dxf, "handle", None)
    return str(handle or f"entity-{index:04d}")


def _entity_role(entity: DXFEntity) -> str:
    if entity.dxf.layer == "Bolt" and entity.dxftype() == "CIRCLE":
        return "physical_cut"
    if entity.dxf.layer == "Bolt":
        return "cut_helper"
    if str(getattr(entity.dxf, "linetype", "")).upper() == "XKITLINE04":
        return "part_hidden"
    return "part_visible"


def entity_shapes(
    prefix: str, entities: Iterable[DXFEntity]
) -> tuple[TraceShape, ...]:
    result: list[TraceShape] = []
    for index, entity in enumerate(entities, start=1):
        entity_type = entity.dxftype()
        shape_id = f"{prefix}-{_entity_id(entity, index)}"
        role = _entity_role(entity)
        source_ids = (_entity_id(entity, index),)
        if entity_type == "LINE":
            result.append(
                TraceShape(
                    shape_id,
                    "line",
                    role,
                    (
                        (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                        (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                    ),
                    source_ids=source_ids,
                )
            )
        elif entity_type == "CIRCLE":
            result.append(
                TraceShape(
                    shape_id,
                    "circle",
                    role,
                    ((float(entity.dxf.center.x), float(entity.dxf.center.y)),),
                    True,
                    source_ids=source_ids,
                    properties={"radius": float(entity.dxf.radius)},
                )
            )
        elif entity_type == "ARC":
            center_x = float(entity.dxf.center.x)
            center_y = float(entity.dxf.center.y)
            radius = float(entity.dxf.radius)
            start_angle = float(entity.dxf.start_angle)
            sweep = (float(entity.dxf.end_angle) - start_angle) % 360.0
            count = max(4, int(sweep / 5.0) + 1)
            coordinates = tuple(
                (
                    center_x + radius * cos(radians(start_angle + sweep * step / count)),
                    center_y + radius * sin(radians(start_angle + sweep * step / count)),
                )
                for step in range(count + 1)
            )
            properties: dict[str, Any] = {
                "center": [center_x, center_y],
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": float(entity.dxf.end_angle),
            }
            result.append(
                TraceShape(
                    shape_id,
                    "arc",
                    role,
                    coordinates,
                    source_ids=source_ids,
                    properties=properties,
                )
            )
        elif entity_type in {"LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"}:
            coordinates = tuple(
                (float(vertex.x), float(vertex.y))
                for vertex in make_path(entity).flattening(0.05)
            )
            if coordinates:
                result.append(
                    TraceShape(
                        shape_id,
                        "polyline",
                        role,
                        coordinates,
                        bool(getattr(entity, "closed", False)),
                        source_ids=source_ids,
                    )
                )
    return tuple(result)


def source_entity_shapes(
    entities: Iterable[SourceEntity],
) -> tuple[TraceShape, ...]:
    """Render SourceIR geometry without reconstructing mutable DXF entities."""

    roles = {
        "part_edge": "part_visible",
        "physical_cut": "physical_cut",
        "cut_helper": "cut_helper",
    }
    result: list[TraceShape] = []
    for entity in entities:
        geometry = entity.geometry
        if geometry is None:
            continue
        role = roles.get(entity.semantic_hint.role.value, "annotation")
        if entity.visibility.value == "hidden":
            role = "part_hidden"
        common = {
            "shape_id": f"source-{entity.source_id}",
            "role": role,
            "source_ids": (entity.source_id,),
        }
        if geometry.kind == "LINE" and len(geometry.coordinates) == 2:
            result.append(
                TraceShape(kind="line", coordinates=geometry.coordinates, **common)
            )
        elif geometry.kind == "CIRCLE" and geometry.center is not None:
            result.append(
                TraceShape(
                    kind="circle",
                    coordinates=(geometry.center,),
                    closed=True,
                    properties={"radius": geometry.radius},
                    **common,
                )
            )
        elif (
            geometry.kind == "ARC"
            and geometry.center is not None
            and geometry.radius is not None
            and geometry.start_angle is not None
            and geometry.end_angle is not None
        ):
            sweep = (geometry.end_angle - geometry.start_angle) % 360.0
            count = max(4, int(sweep / 5.0) + 1)
            coordinates = tuple(
                (
                    geometry.center[0]
                    + geometry.radius
                    * cos(radians(geometry.start_angle + sweep * step / count)),
                    geometry.center[1]
                    + geometry.radius
                    * sin(radians(geometry.start_angle + sweep * step / count)),
                )
                for step in range(count + 1)
            )
            result.append(
                TraceShape(
                    kind="arc",
                    coordinates=coordinates,
                    properties={
                        "center": geometry.center,
                        "radius": geometry.radius,
                        "start_angle": geometry.start_angle,
                        "end_angle": geometry.end_angle,
                    },
                    **common,
                )
            )
        elif geometry.kind in {"LWPOLYLINE", "POLYLINE"}:
            result.append(
                TraceShape(
                    kind="polyline",
                    coordinates=geometry.coordinates,
                    closed=geometry.closed,
                    bulges=geometry.bulges,
                    **common,
                )
            )
    return tuple(result)
