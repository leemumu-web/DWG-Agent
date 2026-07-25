from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf.entities import DXFEntity

from .bh_dialect import BHDialectProfile, DEFAULT_TEKLA_DIALECT
from .bh_geometry import arc_points
from .bh_ir import (
    BHDocumentIR,
    BlockIR,
    EntityAtom,
    InsertTransform,
    SemanticLayer,
    SourceRef,
    TextAtom,
)
from .geometry_types import BoundingBox, Point2D
from .dxf_io import normalize_text, recursive_virtual_entities


def _semantic_layer(
    entity: DXFEntity,
    dialect: BHDialectProfile = DEFAULT_TEKLA_DIALECT,
) -> SemanticLayer:
    layer = str(entity.dxf.layer)
    kind = entity.dxftype()
    linetype = str(getattr(entity.dxf, "linetype", "BYLAYER"))
    return dialect.hint(layer, kind, linetype).role


def _entity_points(entity: DXFEntity) -> list[Point2D]:
    kind = entity.dxftype()
    if kind == "LINE":
        return [
            Point2D(float(entity.dxf.start.x), float(entity.dxf.start.y)),
            Point2D(float(entity.dxf.end.x), float(entity.dxf.end.y)),
        ]
    if kind == "ARC":
        return [Point2D(x, y) for x, y in arc_points(entity, max_angle_step=5.0)]
    if kind == "CIRCLE":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        return [
            Point2D(float(center.x) - radius, float(center.y) - radius),
            Point2D(float(center.x) + radius, float(center.y) + radius),
        ]
    if kind in {"TEXT", "MTEXT", "POINT"}:
        position = entity.dxf.insert if kind in {"TEXT", "MTEXT"} else entity.dxf.location
        return [Point2D(float(position.x), float(position.y))]
    if kind == "LWPOLYLINE":
        return [Point2D(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
    return []


def _bbox(points: Iterable[Point2D]) -> BoundingBox | None:
    values = list(points)
    return BoundingBox.from_points(values) if values else None


def _merge_bboxes(items: Iterable[BoundingBox | None]) -> BoundingBox | None:
    boxes = [item for item in items if item is not None]
    if not boxes:
        return None
    return BoundingBox(
        min(item.min_x for item in boxes),
        min(item.min_y for item in boxes),
        max(item.max_x for item in boxes),
        max(item.max_y for item in boxes),
    )


def build_bh_document_ir(
    doc: ezdxf.document.Drawing,
    *,
    source_path: Path | None = None,
    audit: bool = False,
    dialect: BHDialectProfile = DEFAULT_TEKLA_DIALECT,
) -> BHDocumentIR:
    blocks: list[BlockIR] = []
    for insert in doc.modelspace().query("INSERT"):
        expanded = list(recursive_virtual_entities(insert))
        atoms: list[EntityAtom] = []
        texts: list[TextAtom] = []
        layer_counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        for ordinal, entity in enumerate(expanded):
            kind = entity.dxftype()
            layer = str(entity.dxf.layer)
            linetype = str(getattr(entity.dxf, "linetype", "BYLAYER"))
            source = SourceRef(
                insert_handle=insert.dxf.handle or "",
                block_name=insert.dxf.name,
                entity_ordinal=ordinal,
                entity_handle=entity.dxf.handle,
                entity_type=kind,
                layer=layer,
                linetype=linetype,
                source_id="",
            )
            semantic = _semantic_layer(entity, dialect)
            atom = EntityAtom(
                entity=entity,
                source=source,
                semantic_layer=semantic,
                visibility=dialect.visibility(semantic, linetype),
                bbox=_bbox(_entity_points(entity)),
            )
            atoms.append(atom)
            layer_counts[layer] += 1
            type_counts[kind] += 1
            if kind == "TEXT":
                raw = str(entity.dxf.text)
                normalized = normalize_text(raw)
                if normalized:
                    point = entity.dxf.insert
                    texts.append(
                        TextAtom(
                            raw=raw,
                            normalized=normalized,
                            position=Point2D(float(point.x), float(point.y)),
                            height=float(entity.dxf.height),
                            rotation=float(getattr(entity.dxf, "rotation", 0.0)),
                            source=source,
                        )
                    )
            elif kind == "MTEXT":
                raw = entity.plain_text()
                normalized = normalize_text(raw)
                if normalized:
                    point = entity.dxf.insert
                    texts.append(
                        TextAtom(
                            raw=raw,
                            normalized=normalized,
                            position=Point2D(float(point.x), float(point.y)),
                            height=float(entity.dxf.char_height),
                            rotation=0.0,
                            source=source,
                        )
                    )
        blocks.append(
            BlockIR(
                insert=insert,
                handle=insert.dxf.handle or "",
                name=insert.dxf.name,
                transform=InsertTransform(
                    insert_x=float(insert.dxf.insert.x),
                    insert_y=float(insert.dxf.insert.y),
                    insert_z=float(insert.dxf.insert.z),
                    xscale=float(getattr(insert.dxf, "xscale", 1.0)),
                    yscale=float(getattr(insert.dxf, "yscale", 1.0)),
                    zscale=float(getattr(insert.dxf, "zscale", 1.0)),
                    rotation_deg=float(getattr(insert.dxf, "rotation", 0.0)),
                ),
                entities=atoms,
                texts=texts,
                bbox=_merge_bboxes(item.bbox for item in atoms),
                layer_counts=dict(layer_counts),
                type_counts=dict(type_counts),
            )
        )

    direct_counts = Counter(entity.dxftype() for entity in doc.modelspace())
    audit_error_count = len(doc.audit().errors) if audit else 0
    return BHDocumentIR(
        source_path=source_path,
        dxf_version=doc.dxfversion,
        encoding=doc.encoding,
        units=int(doc.header.get("$INSUNITS", 0)),
        blocks=blocks,
        direct_entity_counts=dict(direct_counts),
        audit_error_count=audit_error_count,
    )
