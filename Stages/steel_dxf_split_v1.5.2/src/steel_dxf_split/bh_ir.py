from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ezdxf.entities import DXFEntity, Insert

from .geometry_types import BoundingBox, Point2D

class SemanticLayer(str, Enum):
    PART_EDGE = "part_edge"
    PHYSICAL_CUT = "physical_cut"
    CUT_HELPER = "cut_helper"
    PART_MARK = "part_mark"
    BOLT_MARK = "bolt_mark"
    DIMENSION = "dimension"
    SECTION = "section"
    DRAWING_SHEET = "drawing_sheet"
    OTHER = "other"
    UNKNOWN = "unknown"


class VisibilityClass(str, Enum):
    PHYSICAL = "physical"
    HIDDEN = "hidden"
    CENTER = "center"
    ANNOTATION = "annotation"
    AUXILIARY = "auxiliary"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InsertTransform:
    insert_x: float
    insert_y: float
    insert_z: float
    xscale: float
    yscale: float
    zscale: float
    rotation_deg: float

    @property
    def is_identity(self) -> bool:
        return (
            abs(self.insert_x) <= 1e-12
            and abs(self.insert_y) <= 1e-12
            and abs(self.insert_z) <= 1e-12
            and abs(self.xscale - 1.0) <= 1e-12
            and abs(self.yscale - 1.0) <= 1e-12
            and abs(self.zscale - 1.0) <= 1e-12
            and abs(self.rotation_deg) <= 1e-12
        )


@dataclass(frozen=True, slots=True)
class SourceRef:
    insert_handle: str
    block_name: str
    entity_ordinal: int
    entity_handle: str | None
    entity_type: str
    layer: str
    linetype: str
    source_id: str = ""

    @property
    def stable_id(self) -> str:
        if self.source_id:
            return self.source_id
        handle = self.entity_handle or f"#{self.entity_ordinal}"
        return f"{self.insert_handle}:{self.block_name}:{handle}"


@dataclass(frozen=True, slots=True)
class TextAtom:
    raw: str
    normalized: str
    position: Point2D
    height: float
    rotation: float
    source: SourceRef


@dataclass(slots=True)
class EntityAtom:
    entity: DXFEntity
    source: SourceRef
    semantic_layer: SemanticLayer
    visibility: VisibilityClass
    bbox: BoundingBox | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": asdict(self.source),
            "semantic_layer": self.semantic_layer.value,
            "visibility": self.visibility.value,
            "bbox": asdict(self.bbox) if self.bbox else None,
        }


@dataclass(frozen=True, slots=True)
class SourceViewRef:
    """Compile-local identity and canonical shape of one drawing projection.

    Compatibility block names and INSERT handles are lowering artifacts.  This
    reference preserves the canonical front-end view identity across that seam
    so annotations, hypotheses and manufacturing features can share ownership
    within one compilation.  ``geometry_signature`` is representation and
    affine invariant; ``region_id`` also distinguishes repeated identical
    views and therefore is intentionally not a cross-document fingerprint.
    """

    region_id: str
    geometry_signature: str
    source_ids: tuple[str, ...]
    container_ids: tuple[str, ...]
    explicit_block: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BlockIR:
    insert: Insert
    handle: str
    name: str
    transform: InsertTransform
    entities: list[EntityAtom]
    texts: list[TextAtom]
    bbox: BoundingBox | None
    layer_counts: dict[str, int]
    type_counts: dict[str, int]
    source_view: SourceViewRef | None = None

    def entities_on(self, semantic_layer: SemanticLayer) -> list[EntityAtom]:
        return [item for item in self.entities if item.semantic_layer == semantic_layer]

    def to_summary(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "name": self.name,
            "transform": asdict(self.transform),
            "transform_is_identity": self.transform.is_identity,
            "bbox": asdict(self.bbox) if self.bbox else None,
            "layer_counts": dict(self.layer_counts),
            "type_counts": dict(self.type_counts),
            "text_count": len(self.texts),
            "source_view": self.source_view.to_dict() if self.source_view else None,
        }


@dataclass(slots=True)
class BHDocumentIR:
    source_path: Path | None
    dxf_version: str
    encoding: str
    units: int
    blocks: list[BlockIR]
    direct_entity_counts: dict[str, int]
    audit_error_count: int

    @property
    def texts(self) -> list[TextAtom]:
        return [text for block in self.blocks for text in block.texts]

    @property
    def entities(self) -> list[EntityAtom]:
        return [entity for block in self.blocks for entity in block.entities]

    def block_by_handle(self, handle: str) -> BlockIR:
        for block in self.blocks:
            if block.handle == handle:
                return block
        raise KeyError(handle)

    def to_summary(self) -> dict[str, Any]:
        semantic_counts: dict[str, int] = {item.value: 0 for item in SemanticLayer}
        visibility_counts: dict[str, int] = {item.value: 0 for item in VisibilityClass}
        for entity in self.entities:
            semantic_counts[entity.semantic_layer.value] = semantic_counts.get(entity.semantic_layer.value, 0) + 1
            visibility_counts[entity.visibility.value] = visibility_counts.get(entity.visibility.value, 0) + 1
        return {
            "source_path": str(self.source_path) if self.source_path else None,
            "dxf_version": self.dxf_version,
            "encoding": self.encoding,
            "units": self.units,
            "block_count": len(self.blocks),
            "text_count": len(self.texts),
            "entity_count": len(self.entities),
            "direct_entity_counts": dict(self.direct_entity_counts),
            "semantic_counts": semantic_counts,
            "visibility_counts": visibility_counts,
            "audit_error_count": self.audit_error_count,
        }
