"""Offline parsers for complete BOX production references.

This module is deliberately outside the production package.  It reads either a
frozen ZWCAD JSON snapshot or a paired split-after DXF and normalizes both into
one plate/opening model for the external acceptance oracle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from math import atan2, ceil, cos, hypot, sin
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.acis import api
from ezdxf.acis.const import Tags
from ezdxf.acis.entities import SabLoader
from shapely.geometry import Point, Polygon

from steel_dxf_split.box.dxf_io import decode_cad_text_transport, normalize_text

from .reference_snapshot import (
    CircleSnapshot,
    PolylineSnapshot,
    TextSnapshot,
    load_reference_snapshot,
)


Point2 = tuple[float, float]
Bounds2 = tuple[float, float, float, float]


def _bounds2(values: tuple[float, ...]) -> Bounds2:
    if len(values) != 4:
        raise ValueError(f"expected four 2D bounds values, got {len(values)}")
    return tuple(float(value) for value in values)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class ManualShape:
    entity_handle: str
    kind: str
    vertices: tuple[Point2, ...]
    area: float
    bounds: Bounds2
    centroid: Point2
    sampled_points: tuple[Point2, ...] = ()

    @classmethod
    def from_polygon(
        cls,
        *,
        entity_handle: str,
        kind: str,
        polygon: Polygon,
        vertices: tuple[Point2, ...] | None = None,
        sampled_points: tuple[Point2, ...] | None = None,
    ) -> ManualShape:
        normalized = polygon if polygon.is_valid else polygon.buffer(0)
        if normalized.is_empty or not isinstance(normalized, Polygon):
            raise ValueError(f"reference polygon {entity_handle} is invalid")
        exterior = tuple((float(x), float(y)) for x, y in normalized.exterior.coords[:-1])
        return cls(
            entity_handle=entity_handle,
            kind=kind,
            vertices=vertices or exterior,
            area=float(normalized.area),
            bounds=_bounds2(tuple(normalized.bounds)),
            centroid=(float(normalized.centroid.x), float(normalized.centroid.y)),
            sampled_points=sampled_points or exterior,
        )

    @property
    def polygon(self) -> Polygon:
        return Polygon(self.sampled_points or self.vertices)


@dataclass(frozen=True, slots=True)
class ManualOpening:
    entity_handle: str
    kind: str
    center: Point2
    radius: float | None = None
    shape: ManualShape | None = None
    source_bulges: tuple[float, ...] = ()

    @classmethod
    def circle(
        cls,
        *,
        entity_handle: str,
        center: Iterable[float],
        radius: float,
    ) -> ManualOpening:
        values = tuple(float(value) for value in center)
        if len(values) < 2 or radius <= 0:
            raise ValueError("manual circular opening is invalid")
        return cls(
            entity_handle=entity_handle,
            kind="CIRCLE",
            center=(values[0], values[1]),
            radius=float(radius),
        )

    @classmethod
    def polygon(
        cls,
        *,
        entity_handle: str,
        shape: ManualShape,
        source_bulges: tuple[float, ...] = (),
    ) -> ManualOpening:
        return cls(
            entity_handle=entity_handle,
            kind="POLYGON",
            center=shape.centroid,
            shape=shape,
            source_bulges=source_bulges,
        )

    @property
    def geometry(self):
        if self.kind == "CIRCLE":
            assert self.radius is not None
            return Point(self.center).buffer(self.radius, resolution=128)
        assert self.shape is not None
        return self.shape.polygon


@dataclass(frozen=True, slots=True)
class ManualPlate:
    label: str
    family: str
    side: str | None
    quantity: int
    label_position: Point2
    shape: ManualShape
    openings: tuple[ManualOpening, ...] = ()


@dataclass(frozen=True, slots=True)
class ManualReference:
    path: Path
    member_mark: str
    plates: tuple[ManualPlate, ...]
    evidence_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Label:
    role: str
    family: str
    side: str | None
    quantity: int
    position: Point2
    member_mark: str
    raw: str


_ROLE_RE = re.compile(
    r"(?:p=)?(?P<member>.+?)(?P<role>上腹|下腹|上翼|下翼|腹|翼)$"
)


def _label(value: str, position: Point2) -> _Label | None:
    normalized = normalize_text(decode_cad_text_transport(value))
    match = _ROLE_RE.fullmatch(normalized)
    if match is None:
        return None
    role = match.group("role")
    family = "web" if "腹" in role else "flange"
    side = "top" if role.startswith("上") else "bottom" if role.startswith("下") else None
    return _Label(
        role=role,
        family=family,
        side=side,
        quantity=2 if side is None else 1,
        position=position,
        member_mark=match.group("member"),
        raw=normalized,
    )


def _sample_bulge_segment(
    start: Point2,
    end: Point2,
    bulge: float,
    *,
    tolerance: float = 0.05,
) -> tuple[Point2, ...]:
    if abs(bulge) <= 1e-14:
        return (start,)
    chord = hypot(end[0] - start[0], end[1] - start[1])
    if chord <= 1e-14:
        return (start,)
    sweep = 4.0 * atan2(bulge, 1.0)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    left = (-(end[1] - start[1]) / chord, (end[0] - start[0]) / chord)
    center_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    center = (
        midpoint[0] + left[0] * center_offset,
        midpoint[1] + left[1] * center_offset,
    )
    radius = hypot(start[0] - center[0], start[1] - center[1])
    start_angle = atan2(start[1] - center[1], start[0] - center[0])
    steps = max(2, int(ceil(abs(sweep) * radius / max(tolerance, 0.01))))
    return tuple(
        (
            center[0] + radius * cos(start_angle + sweep * index / steps),
            center[1] + radius * sin(start_angle + sweep * index / steps),
        )
        for index in range(steps)
    )


def _polyline_shape(entity: PolylineSnapshot) -> ManualShape:
    vertices = tuple(zip(entity.coordinates[::2], entity.coordinates[1::2], strict=True))
    sampled = tuple(
        point
        for index, start in enumerate(vertices)
        for point in _sample_bulge_segment(
            start,
            vertices[(index + 1) % len(vertices)],
            entity.bulges[index],
        )
    )
    return ManualShape.from_polygon(
        entity_handle=entity.handle,
        kind="POLYLINE",
        polygon=Polygon(sampled),
        vertices=vertices,
        sampled_points=sampled,
    )


def _nearest_unique_shape(label: _Label, shapes: tuple[ManualShape, ...]) -> ManualShape:
    point = Point(label.position)
    covering = tuple(shape for shape in shapes if shape.polygon.buffer(0.5).covers(point))
    if len(covering) == 1:
        return covering[0]
    ranked = sorted((shape.polygon.distance(point), shape.entity_handle, shape) for shape in shapes)
    if not ranked or (len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) <= 1e-6):
        raise ValueError(f"reference label {label.raw!r} has no unique plate geometry")
    return ranked[0][2]


def _assign_reference(
    *,
    path: Path,
    expected_member_mark: str | None,
    labels: tuple[_Label, ...],
    plate_shapes: tuple[ManualShape, ...],
    openings: tuple[ManualOpening, ...],
    warnings: tuple[str, ...] = (),
) -> ManualReference:
    if not labels:
        raise ValueError(f"manual reference has no BOX plate labels: {path}")
    if len(labels) != len(plate_shapes):
        raise ValueError(
            f"manual reference plate geometry count mismatch: "
            f"{len(plate_shapes)} shapes for {len(labels)} labels"
        )
    member_mark = expected_member_mark or labels[0].member_mark
    assigned: list[ManualPlate] = []
    used_handles: set[str] = set()
    evidence_warnings = list(warnings)
    for item in labels:
        shape = _nearest_unique_shape(item, plate_shapes)
        if shape.entity_handle in used_handles:
            raise ValueError(f"multiple labels resolve to plate {shape.entity_handle}")
        used_handles.add(shape.entity_handle)
        plate_openings = tuple(
            opening
            for opening in openings
            if shape.polygon.buffer(0.5).covers(Point(opening.center))
        )
        if item.member_mark != member_mark:
            evidence_warnings.append(
                f"foreign member label {item.raw!r}; expected {member_mark!r}"
            )
        assigned.append(
            ManualPlate(
                label=item.role,
                family=item.family,
                side=item.side,
                quantity=item.quantity,
                label_position=item.position,
                shape=shape,
                openings=plate_openings,
            )
        )
    unassigned_openings = tuple(
        opening
        for opening in openings
        if not any(plate.shape.polygon.buffer(0.5).covers(Point(opening.center)) for plate in assigned)
    )
    if unassigned_openings:
        raise ValueError(
            "manual reference has openings outside every labelled plate: "
            + ", ".join(item.entity_handle for item in unassigned_openings)
        )
    return ManualReference(
        path=path,
        member_mark=member_mark,
        plates=tuple(assigned),
        evidence_warnings=tuple(dict.fromkeys(evidence_warnings)),
    )


def load_snapshot_reference(
    path: str | Path,
    *,
    expected_source_sha256: str,
    expected_member_mark: str | None = None,
) -> ManualReference:
    snapshot = load_reference_snapshot(path, expected_source_sha256=expected_source_sha256)
    shapes = tuple(
        _polyline_shape(entity)
        for entity in snapshot.entities
        if isinstance(entity, PolylineSnapshot)
    )
    opening_handles = {
        entity.handle
        for entity in snapshot.entities
        if isinstance(entity, PolylineSnapshot) and entity.layer == "CUT_HOLE"
    }
    plate_shapes = tuple(shape for shape in shapes if shape.entity_handle not in opening_handles)
    openings = tuple(
        ManualOpening.polygon(
            entity_handle=entity.handle,
            shape=next(shape for shape in shapes if shape.entity_handle == entity.handle),
            source_bulges=entity.bulges,
        )
        for entity in snapshot.entities
        if isinstance(entity, PolylineSnapshot) and entity.handle in opening_handles
    ) + tuple(
        ManualOpening.circle(
            entity_handle=entity.handle,
            center=entity.center,
            radius=entity.radius,
        )
        for entity in snapshot.entities
        if isinstance(entity, CircleSnapshot)
    )
    labels = tuple(
        parsed
        for entity in snapshot.entities
        if isinstance(entity, TextSnapshot)
        if (
            parsed := _label(
                entity.text,
                (entity.insertion_point[0], entity.insertion_point[1]),
            )
        )
        is not None
    )
    member_mark = expected_member_mark or snapshot.sample_id
    warnings = tuple(
        f"foreign member label {value!r}; expected {member_mark!r}"
        for value in snapshot.foreign_member_labels
        if _ROLE_RE.fullmatch(normalize_text(decode_cad_text_transport(value)))
    )
    return _assign_reference(
        path=snapshot.path,
        expected_member_mark=member_mark,
        labels=labels,
        plate_shapes=plate_shapes,
        openings=openings,
        warnings=warnings,
    )


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
        result.append((center, hypot(*major), float(ratio_match.group(1))))
    return tuple(result)


def _curve_aware_region_loop(
    region: Any,
    *,
    arc_step_mm: float = 0.1,
) -> tuple[tuple[Point2, ...], tuple[Point2, ...]]:
    if not isinstance(region.acis_data, bytes):
        raise TypeError("manual curve-aware REGION parser requires SAB data")
    loader = SabLoader(region.acis_data)
    loader.load_entities()
    raw_by_id = {id(record): record for record in loader.records}
    raw_for_entity = {id(entity): raw_by_id[raw_id] for raw_id, entity in loader.entities.items()}
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
        major_length = hypot(major[0], major[1])
        minor = (-normal[2] * major[1] * ratio, normal[2] * major[0] * ratio)
        sweep = float(end_param - start_param)
        steps = max(
            2,
            int(
                ceil(
                    abs(sweep)
                    * max(major_length, hypot(*minor))
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
        if hypot(curve_points[0][0] - start[0], curve_points[0][1] - start[1]) > 0.1:
            raise ValueError("manual ellipse parameters do not meet edge vertices")
        if hypot(
            center[0] + major[0] * cos(end_param) + minor[0] * sin(end_param) - end[0],
            center[1] + major[1] * cos(end_param) + minor[1] * sin(end_param) - end[1],
        ) > 0.1:
            raise ValueError("manual ellipse parameters do not meet edge vertices")
        sampled.extend(curve_points)
    return tuple(topology), tuple(sampled)


def _region_geometry(region: Any) -> tuple[ManualShape | None, ManualOpening | None]:
    body = api.load(region.acis_data)[0]
    coedges = tuple(_linked(body.lump.shell.face.loop.coedge, "next_coedge"))
    ellipses = _ellipse_records(region)
    circular = bool(ellipses) and all(
        hypot(ellipse[0][0] - ellipses[0][0][0], ellipse[0][1] - ellipses[0][0][1])
        <= 1e-6
        and abs(ellipse[1] - ellipses[0][1]) <= 1e-6
        and abs(ellipse[2] - 1.0) <= 1e-6
        for ellipse in ellipses
    )
    if circular and len(ellipses) == len(coedges):
        center, radius, _ = ellipses[0]
        return None, ManualOpening.circle(
            entity_handle=str(region.dxf.handle or ""),
            center=center,
            radius=radius,
        )
    topology, sampled = _curve_aware_region_loop(region)
    return (
        ManualShape.from_polygon(
            entity_handle=str(region.dxf.handle or ""),
            kind="REGION",
            polygon=Polygon(sampled),
            vertices=topology,
            sampled_points=sampled,
        ),
        None,
    )


def _lwpolyline_shape(entity: Any) -> ManualShape:
    points = tuple(
        (float(x), float(y), float(bulge))
        for x, y, _start_width, _end_width, bulge in entity.get_points("xyseb")
    )
    sampled = tuple(
        point
        for index, start in enumerate(points)
        for point in _sample_bulge_segment(
            (start[0], start[1]),
            (points[(index + 1) % len(points)][0], points[(index + 1) % len(points)][1]),
            start[2],
        )
    )
    return ManualShape.from_polygon(
        entity_handle=str(entity.dxf.handle or ""),
        kind="LWPOLYLINE",
        polygon=Polygon(sampled),
        vertices=tuple((x, y) for x, y, _ in points),
        sampled_points=sampled,
    )


def load_dxf_reference(
    path: str | Path,
    *,
    expected_member_mark: str | None = None,
) -> ManualReference:
    source = Path(path).resolve(strict=True)
    document = ezdxf.readfile(source)
    modelspace = document.modelspace()
    labels = tuple(
        parsed
        for entity in modelspace
        if entity.dxftype() in {"TEXT", "MTEXT"}
        if (
            parsed := _label(
                str(entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text),
                (float(entity.dxf.insert.x), float(entity.dxf.insert.y)),
            )
        )
        is not None
    )
    shapes: list[ManualShape] = []
    openings: list[ManualOpening] = []
    for entity in modelspace:
        if entity.dxftype() == "REGION":
            shape, opening = _region_geometry(entity)
            if shape is not None:
                shapes.append(shape)
            if opening is not None:
                openings.append(opening)
        elif entity.dxftype() == "LWPOLYLINE":
            shapes.append(_lwpolyline_shape(entity))
    if len(shapes) > len(labels):
        labelled_shapes: list[ManualShape] = []
        inner_shapes: list[ManualShape] = []
        for shape in shapes:
            if any(shape.polygon.buffer(0.5).covers(Point(item.position)) for item in labels):
                labelled_shapes.append(shape)
            else:
                inner_shapes.append(shape)
        shapes = labelled_shapes
        openings.extend(
            ManualOpening.polygon(entity_handle=shape.entity_handle, shape=shape)
            for shape in inner_shapes
        )
    return _assign_reference(
        path=source,
        expected_member_mark=expected_member_mark,
        labels=labels,
        plate_shapes=tuple(shapes),
        openings=tuple(openings),
    )
