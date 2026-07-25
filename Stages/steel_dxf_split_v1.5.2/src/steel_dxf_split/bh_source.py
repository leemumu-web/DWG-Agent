from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from math import atan2, cos, degrees, radians, sin
from typing import Any, Iterable

import ezdxf
from ezdxf.entities import DXFEntity, Insert
from ezdxf.math import Matrix44, bulge_to_arc

from .bh_dialect import BHDialectProfile, DEFAULT_TEKLA_DIALECT, RoleHint
from .bh_ir import VisibilityClass
from .dxf_io import normalize_text
from .geometry_types import BoundingBox, Point2D


@dataclass(frozen=True, slots=True)
class Affine2D:
    """The 2D affine subset of an ezdxf row-vector Matrix44."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    @classmethod
    def from_matrix44(cls, matrix: Matrix44) -> "Affine2D":
        values = tuple(float(value) for value in matrix)
        return cls(
            a=values[0],
            b=values[1],
            c=values[4],
            d=values[5],
            tx=values[12],
            ty=values[13],
        )

    @property
    def determinant(self) -> float:
        return self.a * self.d - self.b * self.c

    @property
    def reflected(self) -> bool:
        return self.determinant < 0.0

    def transform_xy(self, x: float, y: float) -> Point2D:
        return Point2D(
            self.a * x + self.c * y + self.tx,
            self.b * x + self.d * y + self.ty,
        )

    def compose(self, child: "Affine2D") -> "Affine2D":
        return Affine2D(
            a=self.a * child.a + self.c * child.b,
            b=self.b * child.a + self.d * child.b,
            c=self.a * child.c + self.c * child.d,
            d=self.b * child.c + self.d * child.d,
            tx=self.a * child.tx + self.c * child.ty + self.tx,
            ty=self.b * child.tx + self.d * child.ty + self.ty,
        )

@dataclass(frozen=True, slots=True)
class EntityPath:
    layout: str
    inserts: tuple[str, ...]
    instance_indices: tuple[int, ...]
    entity_ordinal: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PrimitiveGeometry:
    kind: str
    coordinates: tuple[tuple[float, float], ...] = ()
    center: tuple[float, float] | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    closed: bool = False
    bulges: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceEntity:
    source_id: str
    path: EntityPath
    container_id: str
    entity_type: str
    layer: str
    linetype: str
    geometry: PrimitiveGeometry | None
    text: str | None
    normalized_text: str | None
    text_height: float | None
    text_rotation: float | None
    text_normal_z: float | None
    dimension_measurement: float | None
    semantic_hint: RoleHint
    visibility: VisibilityClass
    transform_chain: tuple[Affine2D, ...]
    bbox: BoundingBox | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path.to_dict(),
            "container_id": self.container_id,
            "entity_type": self.entity_type,
            "layer": self.layer,
            "linetype": self.linetype,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "text_height": self.text_height,
            "text_rotation": self.text_rotation,
            "text_normal_z": self.text_normal_z,
            "dimension_measurement": self.dimension_measurement,
            "semantic_hint": {
                "role": self.semantic_hint.role.value,
                "confidence": self.semantic_hint.confidence,
                "reason": self.semantic_hint.reason,
            },
            "visibility": self.visibility.value,
            "transform_chain": [asdict(item) for item in self.transform_chain],
            "bbox": asdict(self.bbox) if self.bbox else None,
        }


@dataclass(frozen=True, slots=True)
class SourceContainer:
    container_id: str
    explicit_block: bool
    source_ids: tuple[str, ...]
    top_insert_handle: str | None = None
    block_name: str | None = None
    instance_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceDocument:
    dxf_version: str
    encoding: str
    units: int
    entities: tuple[SourceEntity, ...]
    containers: tuple[SourceContainer, ...]
    audit_errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dxf_version": self.dxf_version,
            "encoding": self.encoding,
            "units": self.units,
            "entities": [item.to_dict() for item in self.entities],
            "containers": [item.to_dict() for item in self.containers],
            "audit_errors": list(self.audit_errors),
        }

    def entities_in(self, container_id: str) -> tuple[SourceEntity, ...]:
        return tuple(item for item in self.entities if item.container_id == container_id)


def top_container_id(insert: Insert, top_ordinal: int, instance_index: int) -> str:
    identity = insert.dxf.handle or f"#{top_ordinal}"
    return f"insert:{identity}:{instance_index}"


def _number(value: float) -> float:
    rounded = round(float(value), 12)
    return 0.0 if rounded == -0.0 else rounded


def _xy(value: Any) -> tuple[float, float]:
    return (_number(value.x), _number(value.y))


def _wcs_xy(entity: DXFEntity, value: Any) -> tuple[float, float]:
    ocs = entity.ocs() if hasattr(entity, "ocs") else None
    return _xy(ocs.to_wcs(value) if ocs is not None else value)


def _geometry(entity: DXFEntity) -> PrimitiveGeometry | None:
    kind = entity.dxftype()
    if kind == "LINE":
        return PrimitiveGeometry(kind, (_xy(entity.dxf.start), _xy(entity.dxf.end)))
    if kind == "CIRCLE":
        return PrimitiveGeometry(
            kind,
            center=_wcs_xy(entity, entity.dxf.center),
            radius=_number(entity.dxf.radius),
            closed=True,
        )
    if kind == "ARC":
        center = _wcs_xy(entity, entity.dxf.center)
        radius = _number(entity.dxf.radius)
        coordinates = tuple(
            _xy(point)
            for point in entity.vertices((entity.dxf.start_angle, entity.dxf.end_angle))
        )
        # A negative OCS normal reverses the apparent WCS winding. SourceIR
        # always stores arcs counter-clockwise in world XY.
        if float(entity.ocs().uz.z) < 0.0:
            coordinates = tuple(reversed(coordinates))
        start_angle = _number(
            degrees(atan2(coordinates[0][1] - center[1], coordinates[0][0] - center[0]))
            % 360.0
        )
        end_angle = _number(
            degrees(atan2(coordinates[1][1] - center[1], coordinates[1][0] - center[0]))
            % 360.0
        )
        return PrimitiveGeometry(
            kind,
            coordinates=coordinates,
            center=center,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
        )
    if kind == "LWPOLYLINE":
        values = tuple(entity.get_points("xyb"))
        reflected = float(entity.ocs().uz.z) < 0.0
        return PrimitiveGeometry(
            kind,
            coordinates=tuple(
                _wcs_xy(entity, (x, y, 0.0))
                for x, y, _ in values
            ),
            closed=bool(entity.closed),
            bulges=tuple(
                _number(-bulge if reflected else bulge)
                for _, _, bulge in values
            ),
        )
    if kind == "POLYLINE":
        vertices = tuple(entity.vertices)
        reflected = float(entity.ocs().uz.z) < 0.0
        return PrimitiveGeometry(
            kind,
            coordinates=tuple(
                _wcs_xy(entity, vertex.dxf.location)
                for vertex in vertices
            ),
            closed=bool(entity.is_closed),
            bulges=tuple(
                _number(
                    -float(getattr(vertex.dxf, "bulge", 0.0))
                    if reflected
                    else float(getattr(vertex.dxf, "bulge", 0.0))
                )
                for vertex in vertices
            ),
        )
    if kind in {"TEXT", "MTEXT"}:
        return PrimitiveGeometry(kind, coordinates=(_wcs_xy(entity, entity.dxf.insert),))
    if kind == "POINT":
        return PrimitiveGeometry(kind, coordinates=(_xy(entity.dxf.location),))
    if kind in {"SOLID", "TRACE", "3DFACE"}:
        coordinates = tuple(
            _xy(getattr(entity.dxf, name))
            for name in ("vtx0", "vtx1", "vtx2", "vtx3")
            if entity.dxf.is_supported(name)
        )
        return PrimitiveGeometry(kind, coordinates=coordinates, closed=True)
    if kind == "DIMENSION":
        coordinates = tuple(
            _xy(getattr(entity.dxf, name))
            for name in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")
            if entity.dxf.is_supported(name) and getattr(entity.dxf, name, None) is not None
        )
        return PrimitiveGeometry(kind, coordinates=coordinates)
    return None


def _entity_text(entity: DXFEntity) -> tuple[str | None, str | None]:
    if entity.dxftype() == "TEXT":
        raw = str(entity.dxf.text)
    elif entity.dxftype() == "MTEXT":
        raw = entity.plain_text()
    else:
        return None, None
    return raw, normalize_text(raw)


def _text_metrics(
    entity: DXFEntity,
) -> tuple[float | None, float | None, float | None]:
    if entity.dxftype() == "TEXT":
        height = _number(float(entity.dxf.height))
    elif entity.dxftype() == "MTEXT":
        height = _number(float(entity.dxf.char_height))
    else:
        return None, None, None
    rotation = radians(float(getattr(entity.dxf, "rotation", 0.0)))
    ocs = entity.ocs()
    origin = ocs.to_wcs((0.0, 0.0, 0.0))
    endpoint = ocs.to_wcs((cos(rotation), sin(rotation), 0.0))
    world_rotation = degrees(
        atan2(endpoint.y - origin.y, endpoint.x - origin.x)
    ) % 360.0
    return height, _number(world_rotation), _number(float(ocs.uz.z))


def _dimension_measurement(entity: DXFEntity) -> float | None:
    if entity.dxftype() != "DIMENSION":
        return None
    try:
        return _number(float(entity.get_measurement()))
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return None


def _angle_on_ccw_sweep(angle: float, start: float, end: float) -> bool:
    sweep = (end - start) % 360.0
    return (angle - start) % 360.0 <= sweep + 1e-9


def _arc_extrema_points(
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
) -> list[Point2D]:
    angles = [start_angle, end_angle]
    angles.extend(
        angle
        for angle in (0.0, 90.0, 180.0, 270.0)
        if _angle_on_ccw_sweep(angle, start_angle, end_angle)
    )
    return [
        Point2D(
            center[0] + radius * cos(radians(angle)),
            center[1] + radius * sin(radians(angle)),
        )
        for angle in angles
    ]


def primitive_geometry_points(
    geometry: PrimitiveGeometry | None,
) -> list[Point2D]:
    """Return exact envelope points for supported primitive geometry."""

    if geometry is None:
        return []
    points = [Point2D(x, y) for x, y in geometry.coordinates]
    if geometry.kind == "CIRCLE" and geometry.center is not None and geometry.radius is not None:
        x, y = geometry.center
        radius = geometry.radius
        points.extend(
            (Point2D(x - radius, y - radius), Point2D(x + radius, y + radius))
        )
    elif (
        geometry.kind == "ARC"
        and geometry.center is not None
        and geometry.radius is not None
        and geometry.start_angle is not None
        and geometry.end_angle is not None
    ):
        points.extend(
            _arc_extrema_points(
                geometry.center,
                geometry.radius,
                geometry.start_angle,
                geometry.end_angle,
            )
        )
    if geometry.kind in {"LWPOLYLINE", "POLYLINE"} and geometry.coordinates:
        edge_count = len(geometry.coordinates) if geometry.closed else len(geometry.coordinates) - 1
        for index in range(max(edge_count, 0)):
            bulge = geometry.bulges[index] if index < len(geometry.bulges) else 0.0
            if abs(bulge) <= 1e-12:
                continue
            start = geometry.coordinates[index]
            end = geometry.coordinates[(index + 1) % len(geometry.coordinates)]
            center, start_angle, end_angle, radius = bulge_to_arc(start, end, bulge)
            points.extend(
                _arc_extrema_points(
                    (float(center.x), float(center.y)),
                    float(radius),
                    degrees(start_angle),
                    degrees(end_angle),
                )
            )
    return points


def _bbox(geometry: PrimitiveGeometry | None) -> BoundingBox | None:
    if geometry is None:
        return None
    points = primitive_geometry_points(geometry)
    return BoundingBox.from_points(points) if points else None


def _source_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _make_source_entity(
    entity: DXFEntity,
    *,
    path: EntityPath,
    container_id: str,
    transform_chain: tuple[Affine2D, ...],
    dialect: BHDialectProfile,
) -> SourceEntity:
    kind = entity.dxftype()
    layer = str(getattr(entity.dxf, "layer", "0"))
    linetype = str(getattr(entity.dxf, "linetype", "BYLAYER"))
    geometry = _geometry(entity)
    text, normalized_text = _entity_text(entity)
    text_height, text_rotation, text_normal_z = _text_metrics(entity)
    dimension_measurement = _dimension_measurement(entity)
    hint = dialect.hint(layer, kind, linetype)
    identity_payload = {
        "path": path.to_dict(),
        "container_id": container_id,
        "type": kind,
        "layer": layer,
        "linetype": linetype,
        "geometry": geometry.to_dict() if geometry else None,
        "text": text,
        "text_height": text_height,
        "text_rotation": text_rotation,
        "text_normal_z": text_normal_z,
        "dimension_measurement": dimension_measurement,
    }
    return SourceEntity(
        source_id=_source_id(identity_payload),
        path=path,
        container_id=container_id,
        entity_type=kind,
        layer=layer,
        linetype=linetype,
        geometry=geometry,
        text=text,
        normalized_text=normalized_text,
        text_height=text_height,
        text_rotation=text_rotation,
        text_normal_z=text_normal_z,
        dimension_measurement=dimension_measurement,
        semantic_hint=hint,
        visibility=dialect.visibility(hint.role, linetype),
        transform_chain=transform_chain,
        bbox=_bbox(geometry),
    )


def _insert_instances(insert: Insert) -> tuple[Insert, ...]:
    return tuple(insert.multi_insert()) if insert.mcount > 1 else (insert,)


def _walk_insert(
    insert: Insert,
    *,
    container_id: str,
    insert_names: tuple[str, ...],
    instance_indices: tuple[int, ...],
    transform_chain: tuple[Affine2D, ...],
    flat_ordinal: list[int],
    dialect: BHDialectProfile,
) -> Iterable[SourceEntity]:
    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            nested = entity
            for nested_index, nested_instance in enumerate(_insert_instances(nested)):
                yield from _walk_insert(
                    nested_instance,
                    container_id=container_id,
                    insert_names=(*insert_names, str(nested.dxf.name)),
                    instance_indices=(*instance_indices, nested_index),
                    transform_chain=(
                        *transform_chain,
                        Affine2D.from_matrix44(nested_instance.matrix44()),
                    ),
                    flat_ordinal=flat_ordinal,
                    dialect=dialect,
                )
            continue
        path = EntityPath(
            layout="Model",
            inserts=insert_names,
            instance_indices=instance_indices,
            entity_ordinal=flat_ordinal[0],
        )
        flat_ordinal[0] += 1
        yield _make_source_entity(
            entity,
            path=path,
            container_id=container_id,
            transform_chain=transform_chain,
            dialect=dialect,
        )


def decode_source_document(
    doc: ezdxf.document.Drawing,
    dialect: BHDialectProfile = DEFAULT_TEKLA_DIALECT,
    *,
    audit: bool = False,
) -> SourceDocument:
    entities: list[SourceEntity] = []
    containers: list[SourceContainer] = []
    direct_ids: list[str] = []

    for top_ordinal, entity in enumerate(doc.modelspace()):
        if entity.dxftype() != "INSERT":
            path = EntityPath("Model", (), (), top_ordinal)
            source = _make_source_entity(
                entity,
                path=path,
                container_id="modelspace:direct",
                transform_chain=(),
                dialect=dialect,
            )
            entities.append(source)
            direct_ids.append(source.source_id)
            continue

        top_insert = entity
        for instance_index, instance in enumerate(_insert_instances(top_insert)):
            container_id = top_container_id(top_insert, top_ordinal, instance_index)
            current = tuple(
                _walk_insert(
                    instance,
                    container_id=container_id,
                    insert_names=(str(top_insert.dxf.name),),
                    instance_indices=(instance_index,),
                    transform_chain=(Affine2D.from_matrix44(instance.matrix44()),),
                    flat_ordinal=[0],
                    dialect=dialect,
                )
            )
            entities.extend(current)
            containers.append(
                SourceContainer(
                    container_id=container_id,
                    explicit_block=True,
                    source_ids=tuple(item.source_id for item in current),
                    top_insert_handle=top_insert.dxf.handle,
                    block_name=str(top_insert.dxf.name),
                    instance_index=instance_index,
                )
            )

    if direct_ids:
        containers.insert(
            0,
            SourceContainer(
                container_id="modelspace:direct",
                explicit_block=False,
                source_ids=tuple(direct_ids),
            ),
        )
    errors = tuple(str(item) for item in doc.audit().errors) if audit else ()
    return SourceDocument(
        dxf_version=doc.dxfversion,
        encoding=doc.encoding,
        units=int(doc.header.get("$INSUNITS", 0)),
        entities=tuple(entities),
        containers=tuple(containers),
        audit_errors=errors,
    )
