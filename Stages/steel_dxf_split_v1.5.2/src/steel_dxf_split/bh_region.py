from __future__ import annotations

from dataclasses import dataclass
import math
from threading import RLock

import ezdxf
from ezdxf.acis import api as acis
from ezdxf.acis import entities as acis_entities
from ezdxf.acis import sat as acis_sat
from ezdxf.entities.dxfclass import DXFClass
from ezdxf.entities import Region
from ezdxf.math import Vec3
from ezdxf.render import MeshBuilder

from .bh_models import BulgeContour, CircularCut


MAX_SAGITTA_MM = 0.002
MAX_ARC_STEP_DEGREES = 2.0
_POINT_TOLERANCE_MM = 1e-9
_SAT_EXPORT_LOCK = RLock()
_DETERMINISTIC_SAT_TIMESTAMP = "Thu Jan 01 00:00:00 1970"
_REGION_CLASS_NAME = "ACDBREGION"
_REGION_CPP_CLASS_NAME = "AcDbRegion"


@dataclass(frozen=True, slots=True)
class RegionBoundary:
    vertices: tuple[tuple[float, float], ...]
    face_count: int
    area_mm2: float


def _arc_segment_count(radius: float, sweep: float) -> int:
    if radius <= 0.0 or not math.isfinite(radius):
        raise ValueError("REGION arc radius must be positive and finite.")
    ratio = min(MAX_SAGITTA_MM / radius, 2.0)
    sagitta_step = 2.0 * math.acos(1.0 - ratio)
    step = min(math.radians(MAX_ARC_STEP_DEGREES), sagitta_step)
    if step <= 0.0 or not math.isfinite(step):
        raise ValueError("REGION arc tessellation step is invalid.")
    return max(1, math.ceil(abs(sweep) / step))


def _append_unique(
    points: list[tuple[float, float]], point: tuple[float, float]
) -> None:
    if points and max(
        abs(points[-1][0] - point[0]),
        abs(points[-1][1] - point[1]),
    ) <= _POINT_TOLERANCE_MM:
        return
    points.append(point)


def contour_vertices(contour: BulgeContour) -> tuple[tuple[float, float], ...]:
    if not contour.closed:
        raise ValueError("REGION output requires a closed contour.")
    points: list[tuple[float, float]] = []
    vertices = contour.vertices
    for index, vertex in enumerate(vertices):
        following = vertices[(index + 1) % len(vertices)]
        start = (float(vertex.x), float(vertex.y))
        end = (float(following.x), float(following.y))
        _append_unique(points, start)
        if abs(vertex.bulge) <= 1e-15:
            continue
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        chord = math.hypot(dx, dy)
        if chord <= _POINT_TOLERANCE_MM:
            raise ValueError("REGION bulge arc has coincident endpoints.")
        sweep = 4.0 * math.atan(float(vertex.bulge))
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
    if len(points) > 1 and max(
        abs(points[0][0] - points[-1][0]),
        abs(points[0][1] - points[-1][1]),
    ) <= _POINT_TOLERANCE_MM:
        points.pop()
    if len(points) < 3:
        raise ValueError("REGION contour requires at least three distinct points.")
    return tuple(points)


def circle_vertices(cut: CircularCut) -> tuple[tuple[float, float], ...]:
    count = _arc_segment_count(cut.radius, math.tau)
    return tuple(
        (
            cut.center.x + cut.radius * math.cos(math.tau * index / count),
            cut.center.y + cut.radius * math.sin(math.tau * index / count),
        )
        for index in range(count)
    )


def _add_region(
    document: ezdxf.document.Drawing,
    vertices: tuple[tuple[float, float], ...],
    *,
    layer: str,
) -> Region:
    _ensure_region_class(document)
    mesh = MeshBuilder()
    mesh.add_face((x, y, 0.0) for x, y in vertices)
    body = acis.body_from_mesh(mesh, precision=9)
    entity = document.modelspace().add_region(dxfattribs={"layer": layer})
    _export_r2007_sat_high_precision(entity, body)
    return entity


def _ensure_region_class(document: ezdxf.document.Drawing) -> None:
    """Register the CLASS record required by Windows CAD REGION readers.

    ezdxf serializes the entity itself but does not automatically add the
    ``ACDBREGION`` class.  AutoCAD-family readers can reject a drawing that
    references this ACIS entity without its class declaration.
    """

    if any(
        dxfclass.dxf.name == _REGION_CLASS_NAME
        and dxfclass.dxf.cpp_class_name == _REGION_CPP_CLASS_NAME
        for dxfclass in document.classes
    ):
        return
    dxfclass = DXFClass.new(doc=document)
    dxfclass.update_dxf_attribs(
        {
            "name": _REGION_CLASS_NAME,
            "cpp_class_name": _REGION_CPP_CLASS_NAME,
            "app_name": "ObjectDBX Classes",
            "flags": 499,
            "was_a_proxy": 0,
            "is_an_entity": 1,
        }
    )
    document.classes.register(dxfclass)


def _export_r2007_sat_high_precision(entity: Region, body: acis.Body) -> None:
    """Export SAT without ezdxf's six-significant-digit coordinate loss.

    ezdxf 1.4.4 exposes ``export_sat()`` publicly but does not expose its
    numeric precision.  Production coordinates above 10,000 mm can otherwise
    drift by hundredths of a millimetre.  The dependency is pinned and this
    narrow formatter replacement is process-locked and always restored.
    """

    if entity.doc is None or entity.doc.dxfversion != "AC1021":
        raise ValueError("BH REGION output requires deterministic R2007 SAT.")

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
        acis_sat.SatDataExporter.write_double = write_double
        acis_entities.Transform.write_common = write_transform
        try:
            sat_lines = acis.export_sat([body])
            header, separator, _ = sat_lines[1].rpartition("@24 ")
            if not separator:
                raise ValueError("Unexpected ACIS SAT header timestamp format.")
            sat_lines[1] = (
                header + separator + _DETERMINISTIC_SAT_TIMESTAMP
            )
            entity.sat = sat_lines
        finally:
            acis_sat.SatDataExporter.write_double = original_double
            acis_entities.Transform.write_common = original_transform


def add_contour_region(
    document: ezdxf.document.Drawing,
    contour: BulgeContour,
    *,
    layer: str,
) -> Region:
    return _add_region(document, contour_vertices(contour), layer=layer)


def add_circle_region(
    document: ezdxf.document.Drawing,
    cut: CircularCut,
    *,
    layer: str,
) -> Region:
    return _add_region(document, circle_vertices(cut), layer=layer)


def set_region_boundary(
    entity: Region,
    vertices: tuple[tuple[float, float], ...],
) -> None:
    """Replace one REGION face while preserving its DXF identity and XDATA."""

    if entity.doc is None:
        raise ValueError("REGION must belong to a document before replacement.")
    if len(vertices) < 3:
        raise ValueError("REGION replacement requires at least three vertices.")
    mesh = MeshBuilder()
    mesh.add_face((float(x), float(y), 0.0) for x, y in vertices)
    body = acis.body_from_mesh(mesh, precision=9)
    _export_r2007_sat_high_precision(entity, body)


def _signed_area(vertices: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(
            vertices, (*vertices[1:], vertices[0]), strict=True
        )
    )


def region_boundary(entity: Region) -> RegionBoundary:
    bodies = acis.load_dxf(entity)
    if len(bodies) != 1:
        raise ValueError("REGION must contain exactly one ACIS body.")
    meshes = acis.mesh_from_body(bodies[0], merge_lumps=True)
    if len(meshes) != 1:
        raise ValueError("REGION must contain exactly one connected mesh.")
    mesh = meshes[0]
    if len(mesh.faces) != 1:
        raise ValueError("REGION must contain exactly one planar face.")
    face = mesh.faces[0]
    vertices = tuple(
        (float(mesh.vertices[index].x), float(mesh.vertices[index].y))
        for index in face
    )
    if any(abs(float(mesh.vertices[index].z)) > _POINT_TOLERANCE_MM for index in face):
        raise ValueError("REGION face must lie on the manufacturing XY plane.")
    return RegionBoundary(
        vertices=vertices,
        face_count=len(mesh.faces),
        area_mm2=abs(_signed_area(vertices)),
    )
