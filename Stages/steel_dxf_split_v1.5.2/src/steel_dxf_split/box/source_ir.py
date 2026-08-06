from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ezdxf.entities.dxfentity import DXFEntity
from ezdxf.entities.insert import Insert

from .dxf_io import decode_cad_text_transport, load_document

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
HIDDEN_PROJECTION_LINETYPES = frozenset({"XKITLINE04", "DOT2"})


def is_hidden_projection_linetype(value: str) -> bool:
    """Normalize the two Tekla hidden-projection linetype dialects."""

    return value.upper() in HIDDEN_PROJECTION_LINETYPES


@dataclass(frozen=True, slots=True)
class SourceEntityIR:
    """One transformed DXF source fact with stable object-group lineage."""

    source_id: str
    group_id: str
    handle: str
    kind: str
    layer: str
    linetype: str
    start: Point2 | None = None
    end: Point2 | None = None
    center: Point2 | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    points: tuple[Point3, ...] = ()
    closed: bool = False
    text_raw: str | None = None
    text_decoded: str | None = None
    rotation: float | None = None
    major_axis: Point2 | None = None
    ratio: float | None = None
    extras: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ObjectGroupIR:
    """A top-level Tekla drawing object exported as one INSERT."""

    group_id: str
    insert_handle: str
    block_name: str
    insert_point: Point2
    rotation: float
    scale: Point3
    source_ids: tuple[str, ...]
    layers: tuple[str, ...]

    def contains_layer(self, layer: str) -> bool:
        folded = layer.casefold()
        return any(value.casefold() == folded for value in self.layers)


@dataclass(frozen=True, slots=True)
class SourceDocumentIR:
    """Immutable, source-only representation of one audited DXF document."""

    path: Path
    dxf_version: str
    units: int
    declared_codepage: str
    detected_encoding: str
    file_sha256: str
    geometry_fingerprint: str
    groups: tuple[ObjectGroupIR, ...]
    entities: tuple[SourceEntityIR, ...]

    def entities_by_layer(self, layer: str) -> tuple[SourceEntityIR, ...]:
        folded = layer.casefold()
        return tuple(
            entity for entity in self.entities if entity.layer.casefold() == folded
        )

    def groups_by_layer(self, layer: str) -> tuple[ObjectGroupIR, ...]:
        return tuple(group for group in self.groups if group.contains_layer(layer))

    def entities_for_group(self, group_id: str) -> tuple[SourceEntityIR, ...]:
        return tuple(entity for entity in self.entities if entity.group_id == group_id)


def _point2(value: Any) -> Point2:
    return (float(value.x), float(value.y))


def _point3(value: Any) -> Point3:
    return (float(value.x), float(value.y), float(value.z))


def _text_value(entity: DXFEntity) -> str | None:
    if entity.dxftype() == "MTEXT":
        return str(entity.text)  # type: ignore[attr-defined]
    if entity.dxftype() in {"TEXT", "ATTRIB", "ATTDEF"}:
        return str(entity.dxf.text)
    return None


def _entity_to_ir(
    entity: DXFEntity,
    *,
    source_id: str,
    group_id: str,
    original_handle: str,
) -> SourceEntityIR:
    kind = entity.dxftype()
    layer = str(getattr(entity.dxf, "layer", "0"))
    linetype = str(getattr(entity.dxf, "linetype", "BYLAYER"))
    values: dict[str, Any] = {}

    if kind == "LINE":
        values["start"] = _point2(entity.dxf.start)
        values["end"] = _point2(entity.dxf.end)
    elif kind == "ARC":
        values.update(
            center=_point2(entity.dxf.center),
            radius=float(entity.dxf.radius),
            start_angle=float(entity.dxf.start_angle),
            end_angle=float(entity.dxf.end_angle),
        )
    elif kind == "CIRCLE":
        values.update(
            center=_point2(entity.dxf.center),
            radius=float(entity.dxf.radius),
        )
    elif kind == "ELLIPSE":
        values.update(
            center=_point2(entity.dxf.center),
            major_axis=_point2(entity.dxf.major_axis),
            ratio=float(entity.dxf.ratio),
            start_angle=float(entity.dxf.start_param),
            end_angle=float(entity.dxf.end_param),
        )
    elif kind == "LWPOLYLINE":
        values["points"] = tuple(
            (float(x), float(y), float(bulge))
            for x, y, bulge in entity.get_points("xyb")  # type: ignore[attr-defined]
        )
        values["closed"] = bool(entity.closed)  # type: ignore[attr-defined]
    elif kind == "POLYLINE":
        values["points"] = tuple(
            (
                float(vertex.dxf.location.x),
                float(vertex.dxf.location.y),
                float(getattr(vertex.dxf, "bulge", 0.0)),
            )
            for vertex in entity.vertices  # type: ignore[attr-defined]
        )
        values["closed"] = bool(entity.is_closed)  # type: ignore[attr-defined]
    elif kind == "SPLINE":
        values["points"] = tuple(
            _point3(point)
            for point in entity.control_points  # type: ignore[attr-defined]
        )
        values["closed"] = bool(entity.closed)  # type: ignore[attr-defined]

    raw_text = _text_value(entity)
    if raw_text is not None:
        values["text_raw"] = raw_text
        values["text_decoded"] = decode_cad_text_transport(raw_text)
        insert = getattr(entity.dxf, "insert", None)
        if insert is not None:
            values["center"] = _point2(insert)
        values["rotation"] = float(getattr(entity.dxf, "rotation", 0.0))

    return SourceEntityIR(
        source_id=source_id,
        group_id=group_id,
        handle=original_handle,
        kind=kind,
        layer=layer,
        linetype=linetype,
        **values,
    )


def _iter_insert_entities_with_lineage(
    insert: Insert,
    *,
    group_id: str,
    source_path: tuple[str, ...],
) -> Iterator[SourceEntityIR]:
    """Pair transformed virtual entities with stable definition handles."""

    block = insert.block()
    if block is None:
        raise ValueError(f"INSERT {insert.dxf.name!r} has no block definition")
    original_entities = tuple(block)
    virtual_entities = tuple(insert.virtual_entities())
    if len(original_entities) != len(virtual_entities):
        raise ValueError(
            f"INSERT {insert.dxf.name!r} could not preserve entity lineage: "
            f"{len(original_entities)} source entities != "
            f"{len(virtual_entities)} virtual entities"
        )
    for index, (original, virtual) in enumerate(
        zip(original_entities, virtual_entities, strict=True)
    ):
        original_handle = str(getattr(original.dxf, "handle", "") or index)
        nested_path = (*source_path, original_handle)
        source_id = "/".join(nested_path)
        if virtual.dxftype() == "INSERT":
            assert isinstance(virtual, Insert)
            yield from _iter_insert_entities_with_lineage(
                virtual,
                group_id=group_id,
                source_path=nested_path,
            )
            continue
        yield _entity_to_ir(
            virtual,
            source_id=source_id,
            group_id=group_id,
            original_handle=original_handle,
        )


def _canonical_entity_payload(entity: SourceEntityIR) -> dict[str, Any]:
    payload = asdict(entity)
    payload.pop("source_id")
    payload.pop("handle")
    return payload


def geometry_fingerprint(entities: Iterable[SourceEntityIR]) -> str:
    """Hash source semantics independently of DXF enumeration order."""

    records = [
        json.dumps(
            _canonical_entity_payload(entity),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for entity in entities
    ]
    canonical = "\n".join(sorted(records)).encode("utf-8")
    return sha256(canonical).hexdigest()


def build_source_ir(path: str | Path) -> SourceDocumentIR:
    """Decode an audited DXF into immutable source facts and object groups."""

    source_path = Path(path).resolve()
    document = load_document(source_path)
    entities: list[SourceEntityIR] = []
    groups: list[ObjectGroupIR] = []

    for index, entity in enumerate(document.modelspace()):
        handle = str(getattr(entity.dxf, "handle", "") or index)
        if entity.dxftype() != "INSERT":
            group_id = "modelspace"
            source_id = f"modelspace/{handle}"
            entities.append(
                _entity_to_ir(
                    entity,
                    source_id=source_id,
                    group_id=group_id,
                    original_handle=handle,
                )
            )
            continue

        assert isinstance(entity, Insert)
        insert = entity
        group_id = f"insert:{handle}"
        group_entities = tuple(
            _iter_insert_entities_with_lineage(
                insert,
                group_id=group_id,
                source_path=(group_id,),
            )
        )
        entities.extend(group_entities)
        insert_point = _point2(insert.dxf.insert)
        groups.append(
            ObjectGroupIR(
                group_id=group_id,
                insert_handle=handle,
                block_name=str(insert.dxf.name),
                insert_point=insert_point,
                rotation=float(getattr(insert.dxf, "rotation", 0.0)),
                scale=(
                    float(getattr(insert.dxf, "xscale", 1.0)),
                    float(getattr(insert.dxf, "yscale", 1.0)),
                    float(getattr(insert.dxf, "zscale", 1.0)),
                ),
                source_ids=tuple(value.source_id for value in group_entities),
                layers=tuple(sorted({value.layer for value in group_entities})),
            )
        )

    materialized = tuple(entities)
    return SourceDocumentIR(
        path=source_path,
        dxf_version=document.dxfversion,
        units=int(document.header.get("$INSUNITS", 0)),
        declared_codepage=str(document.header.get("$DWGCODEPAGE", "")),
        detected_encoding=str(document.encoding),
        file_sha256=sha256(source_path.read_bytes()).hexdigest(),
        geometry_fingerprint=geometry_fingerprint(materialized),
        groups=tuple(groups),
        entities=materialized,
    )
