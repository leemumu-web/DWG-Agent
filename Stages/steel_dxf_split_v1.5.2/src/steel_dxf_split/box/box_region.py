from __future__ import annotations

import math
from dataclasses import dataclass
from threading import RLock

import ezdxf
from ezdxf.acis import entities as acis_entities
from ezdxf.acis import sat as acis_sat
from ezdxf.acis.dxf import load_dxf
from ezdxf.acis.entities import Body, export_sat
from ezdxf.acis.mesh import body_from_mesh, mesh_from_body
from ezdxf.entities.acis import Region
from ezdxf.math import Vec3
from ezdxf.render.mesh import MeshBuilder

from .manufacturing_ir import CircularCutIR, ContourSegmentIR

MAX_SAGITTA_MM = 0.002
MAX_ARC_STEP_DEGREES = 2.0
_POINT_TOLERANCE_MM = 1e-9
_CLOSURE_TOLERANCE_MM = 1e-6
_SAT_EXPORT_LOCK = RLock()
_DETERMINISTIC_SAT_TIMESTAMP = "Thu Jan 01 00:00:00 1970"


@dataclass(frozen=True, slots=True)
class RegionBoundary:
    vertices: tuple[tuple[float, float], ...]
    face_count: int
    area_mm2: float


def _arc_segment_count(radius: float, sweep: float) -> int:
    if radius <= 0.0 or not math.isfinite(radius):
        raise ValueError("REGION arc radius must be positive and finite")
    ratio = min(MAX_SAGITTA_MM / radius, 2.0)
    sagitta_step = 2.0 * math.acos(1.0 - ratio)
    step = min(math.radians(MAX_ARC_STEP_DEGREES), sagitta_step)
    if step <= 0.0 or not math.isfinite(step):
        raise ValueError("REGION arc tessellation step is invalid")
    return max(1, math.ceil(abs(sweep) / step))


def _append_unique(
    points: list[tuple[float, float]],
    point: tuple[float, float],
) -> None:
    if not all(math.isfinite(value) for value in point):
        raise ValueError("REGION contour coordinates must be finite")
    if (
        points
        and max(
            abs(points[-1][0] - point[0]),
            abs(points[-1][1] - point[1]),
        )
        <= _POINT_TOLERANCE_MM
    ):
        return
    points.append(point)


def contour_vertices(
    segments: tuple[ContourSegmentIR, ...],
) -> tuple[tuple[float, float], ...]:
    if len(segments) < 3:
        raise ValueError("REGION contour requires at least three segments")
    for segment, following in zip(
        segments,
        (*segments[1:], segments[0]),
        strict=True,
    ):
        if (
            max(
                abs(segment.end[0] - following.start[0]),
                abs(segment.end[1] - following.start[1]),
            )
            > _CLOSURE_TOLERANCE_MM
        ):
            raise ValueError("Manufacturing IR contour is not end-to-start closed")

    points: list[tuple[float, float]] = []
    for segment in segments:
        start = (float(segment.start[0]), float(segment.start[1]))
        end = (float(segment.end[0]), float(segment.end[1]))
        _append_unique(points, start)
        if abs(segment.bulge) <= 1e-15:
            continue
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        chord = math.hypot(dx, dy)
        if chord <= _POINT_TOLERANCE_MM:
            raise ValueError("REGION bulge arc has coincident endpoints")
        sweep = 4.0 * math.atan(float(segment.bulge))
        radius = chord / (2.0 * math.sin(abs(sweep) / 2.0))
        offset = chord / (2.0 * math.tan(sweep / 2.0))
        center_x = (start[0] + end[0]) / 2.0 - dy / chord * offset
        center_y = (start[1] + end[1]) / 2.0 + dx / chord * offset
        start_angle = math.atan2(start[1] - center_y, start[0] - center_x)
        segment_count = _arc_segment_count(radius, sweep)
        for step_index in range(1, segment_count):
            angle = start_angle + sweep * step_index / segment_count
            _append_unique(
                points,
                (
                    center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle),
                ),
            )
        _append_unique(points, end)
    if (
        len(points) > 1
        and max(
            abs(points[0][0] - points[-1][0]),
            abs(points[0][1] - points[-1][1]),
        )
        <= _POINT_TOLERANCE_MM
    ):
        points.pop()
    if len(points) < 3:
        raise ValueError("REGION contour requires at least three distinct points")
    return tuple(points)


def circle_vertices(cut: CircularCutIR) -> tuple[tuple[float, float], ...]:
    if cut.radius_mm <= 0.0 or not math.isfinite(cut.radius_mm):
        raise ValueError("REGION circle radius must be positive and finite")
    if not all(math.isfinite(value) for value in cut.center):
        raise ValueError("REGION circle center must be finite")
    count = _arc_segment_count(cut.radius_mm, math.tau)
    return tuple(
        (
            cut.center[0] + cut.radius_mm * math.cos(math.tau * index / count),
            cut.center[1] + cut.radius_mm * math.sin(math.tau * index / count),
        )
        for index in range(count)
    )


def _add_region(
    document: ezdxf.document.Drawing,
    vertices: tuple[tuple[float, float], ...],
    *,
    layer: str,
) -> Region:
    mesh = MeshBuilder()
    mesh.add_face((x, y, 0.0) for x, y in vertices)
    body = body_from_mesh(mesh, precision=9)
    entity = document.modelspace().add_region(dxfattribs={"layer": layer})
    _export_r2007_sat_high_precision(entity, body)
    return entity


def _export_r2007_sat_high_precision(entity: Region, body: Body) -> None:
    """Export deterministic R2007 SAT without six-digit coordinate loss."""

    if entity.doc is None or entity.doc.dxfversion != "AC1021":
        raise ValueError("BOX REGION output requires deterministic R2007 SAT")

    def write_double(exporter, value: float) -> None:  # type: ignore[no-untyped-def]
        exporter.data.append(format(value, ".17g"))

    def write_transform(transform, exporter) -> None:  # type: ignore[no-untyped-def]
        data: list[str] = []
        for row in transform.matrix.rows():
            data.extend(format(row[index], ".17g") for index in range(3))
        direction = transform.matrix.transform_direction(Vec3(1.0, 0.0, 0.0))
        data.append(format(round(direction.magnitude, 6), ".17g"))
        data.append(
            "rotate"
            if not direction.normalize().isclose(Vec3(1.0, 0.0, 0.0))
            else "no_rotate"
        )
        data.extend(("no_reflect", "no_shear"))
        exporter.write_transform(data)

    with _SAT_EXPORT_LOCK:
        original_double = acis_sat.SatDataExporter.write_double
        original_transform = acis_entities.Transform.write_common
        acis_sat.SatDataExporter.write_double = write_double  # type: ignore[method-assign,assignment]
        acis_entities.Transform.write_common = write_transform  # type: ignore[method-assign,assignment]
        try:
            sat_lines = export_sat([body])
            header, separator, _ = sat_lines[1].rpartition("@24 ")
            if not separator:
                raise ValueError("Unexpected ACIS SAT header timestamp format")
            sat_lines[1] = header + separator + _DETERMINISTIC_SAT_TIMESTAMP
            entity.sat = sat_lines
        finally:
            acis_sat.SatDataExporter.write_double = original_double  # type: ignore[method-assign]
            acis_entities.Transform.write_common = original_transform  # type: ignore[method-assign]


def add_contour_region(
    document: ezdxf.document.Drawing,
    segments: tuple[ContourSegmentIR, ...],
    *,
    layer: str,
) -> Region:
    return _add_region(document, contour_vertices(segments), layer=layer)


def add_circle_region(
    document: ezdxf.document.Drawing,
    cut: CircularCutIR,
    *,
    layer: str,
) -> Region:
    return _add_region(document, circle_vertices(cut), layer=layer)


def set_region_boundary(
    entity: Region,
    vertices: tuple[tuple[float, float], ...],
) -> None:
    """Replace one REGION face while preserving its handle, layer, and XDATA."""

    if entity.doc is None:
        raise ValueError("REGION must belong to a document before replacement")
    if len(vertices) < 3:
        raise ValueError("REGION replacement requires at least three vertices")
    mesh = MeshBuilder()
    mesh.add_face((float(x), float(y), 0.0) for x, y in vertices)
    body = body_from_mesh(mesh, precision=9)
    _export_r2007_sat_high_precision(entity, body)


def _signed_area(vertices: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(
            vertices,
            (*vertices[1:], vertices[0]),
            strict=True,
        )
    )


def region_boundary(entity: Region) -> RegionBoundary:
    bodies = load_dxf(entity)
    if len(bodies) != 1:
        raise ValueError("REGION must contain exactly one ACIS body")
    meshes = mesh_from_body(bodies[0], merge_lumps=True)
    if len(meshes) != 1:
        raise ValueError("REGION must contain exactly one connected mesh")
    mesh = meshes[0]
    if len(mesh.faces) != 1:
        raise ValueError("REGION must contain exactly one planar face")
    face = mesh.faces[0]
    vertices = tuple(
        (float(mesh.vertices[index].x), float(mesh.vertices[index].y)) for index in face
    )
    if any(abs(float(mesh.vertices[index].z)) > _POINT_TOLERANCE_MM for index in face):
        raise ValueError("REGION face must lie on the manufacturing XY plane")
    return RegionBoundary(
        vertices=vertices,
        face_count=len(mesh.faces),
        area_mm2=abs(_signed_area(vertices)),
    )
