"""Offline-only parser for manual BOX split references.

This module is deliberately outside ``src/steel_dxf_split/box``. Production code
must never import it or read the split-after reference corpus.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from math import ceil, cos, pi, sin
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.acis import api
from ezdxf.acis.const import Tags
from ezdxf.acis.entities import SabLoader
from shapely.geometry import Polygon

from steel_dxf_split.box.dxf_io import decode_cad_text_transport, normalize_text

Point2 = tuple[float, float]
Bounds2 = tuple[float, float, float, float]


def _bounds2(values: tuple[float, ...]) -> Bounds2:
    if len(values) != 4:
        raise ValueError(f"expected four 2D bounds values, got {len(values)}")
    return (
        float(values[0]),
        float(values[1]),
        float(values[2]),
        float(values[3]),
    )


@dataclass(frozen=True, slots=True)
class ManualShape:
    entity_handle: str
    kind: str
    vertices: tuple[Point2, ...]
    area: float
    bounds: tuple[float, float, float, float]
    centroid: Point2
    sampled_points: tuple[Point2, ...] = ()
    circle_center: Point2 | None = None
    circle_radius: float | None = None

    @property
    def polygon(self) -> Polygon:
        if self.kind == "CIRCLE":
            raise TypeError("a circular manual cut is not a plate polygon")
        return Polygon(self.sampled_points or self.vertices)


@dataclass(frozen=True, slots=True)
class ManualPlate:
    label: str
    family: str
    side: str | None
    quantity: int
    label_position: Point2
    shape: ManualShape


@dataclass(frozen=True, slots=True)
class ManualReference:
    path: Path
    member_mark: str
    plates: tuple[ManualPlate, ...]
    holes: tuple[ManualShape, ...]


def _linked(start: Any, next_attribute: str) -> Iterator[Any]:
    current = start
    seen: set[int] = set()
    while not current.is_none and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, next_attribute)


def _vertex_point(vertex: Any) -> Point2:
    location = vertex.point.location
    return (float(location.x), float(location.y))


def _ellipse_records(region: Any) -> tuple[tuple[Point2, float, float], ...]:
    lines = tuple(api.dump_sab_as_text(region.acis_data))
    result: list[tuple[Point2, float, float]] = []
    for index, line in enumerate(lines):
        if line.strip() != "ENTITY_TYPE = ellipse-curve":
            continue
        payload = "\n".join(lines[index + 1 : index + 12])
        locations = re.findall(
            r"(?:LOCATION_VEC|DIRECTION_VEC) = \("
            r"([-+0-9.eE]+), ([-+0-9.eE]+), ([-+0-9.eE]+)\)",
            payload,
        )
        ratio_match = re.search(r"DOUBLE = ([-+0-9.eE]+)", payload)
        if len(locations) < 3 or ratio_match is None:
            continue
        center = (float(locations[0][0]), float(locations[0][1]))
        major = (float(locations[2][0]), float(locations[2][1]))
        major_radius = (major[0] ** 2 + major[1] ** 2) ** 0.5
        result.append((center, major_radius, float(ratio_match.group(1))))
    return tuple(result)


def _curve_aware_region_loop(
    region: Any,
    *,
    arc_step_mm: float = 0.1,
) -> tuple[tuple[Point2, ...], tuple[Point2, ...]]:
    """Return ACIS topology vertices and a tessellated curve-aware boundary."""

    if not isinstance(region.acis_data, bytes):
        raise TypeError("manual curve-aware REGION parser requires SAB data")
    loader = SabLoader(region.acis_data)
    loader.load_entities()
    raw_by_id = {id(record): record for record in loader.records}
    raw_for_entity = {
        id(entity): raw_by_id[raw_id] for raw_id, entity in loader.entities.items()
    }
    body = loader.bodies()[0]
    coedges = tuple(_linked(body.lump.shell.face.loop.coedge, "next_coedge"))
    topology: list[Point2] = []
    sampled: list[Point2] = []
    for coedge in coedges:
        edge = coedge.edge
        start_vertex = edge.end_vertex if coedge.sense else edge.start_vertex
        end_vertex = edge.start_vertex if coedge.sense else edge.end_vertex
        start_param = edge.end_param if coedge.sense else edge.start_param
        end_param = edge.start_param if coedge.sense else edge.end_param
        start = _vertex_point(start_vertex)
        end = _vertex_point(end_vertex)
        topology.append(start)
        raw_curve = raw_for_entity[id(edge.curve)]
        if raw_curve.name != "ellipse-curve":
            sampled.append(start)
            continue
        vectors = [
            token.value
            for token in raw_curve.data
            if token.tag in {Tags.LOCATION_VEC, Tags.DIRECTION_VEC}
        ]
        ratios = [token.value for token in raw_curve.data if token.tag == Tags.DOUBLE]
        if len(vectors) < 3 or not ratios:
            raise ValueError("manual ellipse-curve has incomplete SAB parameters")
        center, normal, major = vectors[:3]
        ratio = float(ratios[0])
        major_length = (major[0] ** 2 + major[1] ** 2) ** 0.5
        minor = (
            -normal[2] * major[1] * ratio,
            normal[2] * major[0] * ratio,
        )
        minor_length = (minor[0] ** 2 + minor[1] ** 2) ** 0.5
        sweep = float(end_param - start_param)
        steps = max(
            2,
            int(
                ceil(
                    abs(sweep)
                    * max(major_length, minor_length)
                    / max(arc_step_mm, 0.01)
                )
            ),
        )
        curve_points = tuple(
            (
                float(
                    center[0]
                    + major[0] * cos(start_param + sweep * index / steps)
                    + minor[0] * sin(start_param + sweep * index / steps)
                ),
                float(
                    center[1]
                    + major[1] * cos(start_param + sweep * index / steps)
                    + minor[1] * sin(start_param + sweep * index / steps)
                ),
            )
            for index in range(steps)
        )
        if (curve_points[0][0] - start[0]) ** 2 + (
            curve_points[0][1] - start[1]
        ) ** 2 > 0.01 or (
            center[0] + major[0] * cos(end_param) + minor[0] * sin(end_param) - end[0]
        ) ** 2 + (
            center[1] + major[1] * cos(end_param) + minor[1] * sin(end_param) - end[1]
        ) ** 2 > 0.01:
            raise ValueError("manual ellipse parameters do not meet edge vertices")
        sampled.extend(curve_points)
    return tuple(topology), tuple(sampled)


def _region_shape(region: Any) -> ManualShape:
    body = api.load(region.acis_data)[0]
    face = body.lump.shell.face
    loop = face.loop
    coedges = tuple(_linked(loop.coedge, "next_coedge"))
    vertices = tuple(_vertex_point(coedge.edge.start_vertex) for coedge in coedges)
    ellipses = _ellipse_records(region)
    circular_ellipses = bool(ellipses) and all(
        abs(ellipse[0][0] - ellipses[0][0][0]) <= 1e-6
        and abs(ellipse[0][1] - ellipses[0][0][1]) <= 1e-6
        and abs(ellipse[1] - ellipses[0][1]) <= 1e-6
        and abs(ellipse[2] - 1.0) <= 1e-6
        for ellipse in ellipses
    )
    # ACIS builders represent a full circle either as one 2π edge or as two
    # semicircular edges sharing the same ellipse-curve definition.
    if circular_ellipses and len(ellipses) == len(coedges):
        center, radius, _ = ellipses[0]
        return ManualShape(
            entity_handle=str(region.dxf.handle or ""),
            kind="CIRCLE",
            vertices=(),
            area=pi * radius * radius,
            bounds=(
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            centroid=center,
            circle_center=center,
            circle_radius=radius,
        )
    if len(vertices) < 3:
        raise ValueError(f"manual REGION {region.dxf.handle} has no polygon loop")
    topology_vertices, sampled_points = _curve_aware_region_loop(region)
    polygon = Polygon(sampled_points)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return ManualShape(
        entity_handle=str(region.dxf.handle or ""),
        kind="REGION",
        vertices=topology_vertices,
        area=float(polygon.area),
        bounds=_bounds2(polygon.bounds),
        centroid=(float(polygon.centroid.x), float(polygon.centroid.y)),
        sampled_points=sampled_points,
    )


def _lwpolyline_shape(entity: Any) -> ManualShape:
    points = tuple((float(x), float(y)) for x, y in entity.get_points("xy"))
    if len(points) >= 2 and points[0] != points[-1]:
        points = (*points, points[0])
    polygon = Polygon(points)
    return ManualShape(
        entity_handle=str(entity.dxf.handle or ""),
        kind="LWPOLYLINE",
        vertices=points[:-1] if points and points[0] == points[-1] else points,
        area=float(polygon.area),
        bounds=_bounds2(polygon.bounds),
        centroid=(float(polygon.centroid.x), float(polygon.centroid.y)),
        sampled_points=points[:-1] if points and points[0] == points[-1] else points,
    )


def _text_and_position(entity: Any) -> tuple[str, Point2]:
    raw = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
    value = normalize_text(decode_cad_text_transport(str(raw)))
    insert = entity.dxf.insert
    return value, (float(insert.x), float(insert.y))


_ROLE_RE = re.compile(r"(?:p=)?(?P<member>.+?)(?P<role>上腹|下腹|上翼|下翼|腹|翼)$")


def load_manual_reference(path: str | Path) -> ManualReference:
    source = Path(path).resolve()
    # Manual references use both modern ANSI_936 and Unicode-era transports;
    # unlike the input preview's declared GB2312 quirk, ezdxf already detects
    # these files correctly.  An unconditional override corrupts Unicode text.
    document = ezdxf.readfile(source)
    modelspace = document.modelspace()
    labels: list[tuple[str, str, str | None, int, Point2, str]] = []
    for entity in modelspace:
        if entity.dxftype() not in {"TEXT", "MTEXT"}:
            continue
        text, position = _text_and_position(entity)
        match = _ROLE_RE.fullmatch(text)
        if match is None:
            continue
        role = match.group("role")
        family = "web" if "腹" in role else "flange"
        side = (
            "top"
            if role.startswith("上")
            else "bottom"
            if role.startswith("下")
            else None
        )
        quantity = 2 if side is None else 1
        labels.append((role, family, side, quantity, position, match.group("member")))
    if not labels:
        raise ValueError(f"manual reference has no BOX plate labels: {source}")
    member_marks = {label[5] for label in labels}
    if len(member_marks) != 1:
        raise ValueError(
            f"manual reference has conflicting member marks: {member_marks}"
        )

    shapes: list[ManualShape] = []
    for entity in modelspace:
        if entity.dxftype() == "REGION":
            shapes.append(_region_shape(entity))
        elif entity.dxftype() == "LWPOLYLINE":
            shapes.append(_lwpolyline_shape(entity))
    circles = tuple(shape for shape in shapes if shape.kind == "CIRCLE")
    polygon_shapes = tuple(shape for shape in shapes if shape.kind != "CIRCLE")
    if len(polygon_shapes) != len(labels):
        raise ValueError(
            f"manual reference plate geometry count mismatch: "
            f"{len(polygon_shapes)} shapes for {len(labels)} labels"
        )
    ordered_labels = sorted(labels, key=lambda label: label[4][1], reverse=True)
    ordered_shapes = sorted(
        polygon_shapes, key=lambda shape: shape.centroid[1], reverse=True
    )
    plates = tuple(
        ManualPlate(
            label=label[0],
            family=label[1],
            side=label[2],
            quantity=label[3],
            label_position=label[4],
            shape=shape,
        )
        for label, shape in zip(ordered_labels, ordered_shapes, strict=True)
    )
    return ManualReference(
        path=source,
        member_mark=next(iter(member_marks)),
        plates=plates,
        holes=circles,
    )
