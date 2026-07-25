from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from .bh_associations import DrawingEdgeKind, DrawingGraph, DrawingNodeKind
from .bh_dimensions import displayed_dimension_tolerance
from .bh_ir import BHDocumentIR, SemanticLayer
from .bh_models import BHAssembly, BHMetadata, BHPlate
from .dxf_io import normalize_text
from .geometry_types import BoundingBox

_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)?$")
_CHAIN_RE = re.compile(r"^(?P<count>\d+)\s*[xX×*]\s*(?P<pitch>\d+(?:\.\d+)?)$")
_BOLT_MARK_RE = re.compile(
    r"^\s*(?P<count>[1-9]\d*)\s*"
    r"(?:个|pcs?|holes?)?\s*"
    r"(?:[-–—:：]\s*)?"
    r"(?:Φ|Ø|⌀|∅|M)\s*"
    r"(?P<diameter>\d+(?:\.\d+)?)\s*"
    r"(?:孔|holes?)?\s*$",
    re.IGNORECASE,
)
_TEKLA_BOLT_GROUP_RE = re.compile(
    r"^\s*(?P<count>[1-9]\d*)\s*[*xX×]\s*"
    r"(?:D|Φ|Ø|⌀|∅|M)\s*(?P<diameter>\d+(?:\.\d+)?)\s*"
    r"\(\s*\d+(?:\.\d+)?\s*[xX×]\s*\d+(?:\.\d+)?\s*\)\s*$",
    re.IGNORECASE,
)
_PART_RE = re.compile(r"(?i)^[a-z0-9]+(?:-[a-z0-9]+)+$")


def parse_bolt_mark_text(value: str) -> tuple[int, float] | None:
    """Parse a hole/bolt mark only when its diameter semantics are explicit.

    CAD transport escapes are decoded first.  A generic separator such as
    ``-`` or ``x`` never establishes that the second number is a diameter.
    """

    normalized = normalize_text(value)
    if "\\" in normalized or "%%" in normalized:
        return None
    match = _BOLT_MARK_RE.fullmatch(normalized)
    if match is None:
        # Tekla's ``18*D22(22x35)`` denotes an 18-hole group with D22 as
        # nominal diameter; the parenthesized tuple describes the non-circular
        # opening envelope. Requiring the complete tuple keeps arbitrary ``16x5`` text
        # from being interpreted as a hole diameter.
        match = _TEKLA_BOLT_GROUP_RE.fullmatch(normalized)
        if match is None:
            return None
    return int(match.group("count")), float(match.group("diameter"))


def _bolt_mark_parse_failure_reason(value: str) -> str:
    normalized = normalize_text(value)
    if "\\" in normalized or "%%" in normalized:
        return "unresolved_cad_text"
    return "missing_explicit_diameter_semantic"


@dataclass(frozen=True, slots=True)
class DimensionObservation:
    text: str
    value: float | None
    chain_count: int | None
    chain_pitch: float | None
    orientation: str
    block_name: str
    block_handle: str
    bbox: BoundingBox | None
    source_ids: tuple[str, ...] = ()
    association_edge_ids: tuple[str, ...] = ()
    target_node_id: str | None = None
    target_region_id: str | None = None
    scope: str = "untyped"
    property_type: str = "unresolved"
    target_count: int = 1
    anchor_source_ids: tuple[str, ...] = ()
    anchor_axis_values_mm: tuple[float, ...] = ()
    anchor_residual_mm: float | None = None
    anchor_tolerance_mm: float | None = None
    anchor_count: int = 0
    residual_mm: float | None = None
    strength: str = "candidate"


@dataclass(frozen=True, slots=True)
class BoltMarkObservation:
    raw_text: str
    count: int
    diameter: float
    block_name: str
    block_handle: str
    source_ids: tuple[str, ...] = ()
    association_edge_ids: tuple[str, ...] = ()
    target_node_id: str | None = None
    target_source_ids: tuple[str, ...] = ()
    target_region_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnresolvedBoltMarkObservation:
    raw_text: str
    reason: str
    block_name: str
    block_handle: str
    source_ids: tuple[str, ...] = ()
    association_edge_ids: tuple[str, ...] = ()
    target_node_id: str | None = None
    target_source_ids: tuple[str, ...] = ()
    target_region_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PartMarkObservation:
    text: str
    block_name: str
    block_handle: str
    source_ids: tuple[str, ...] = ()
    association_edge_ids: tuple[str, ...] = ()
    target_node_id: str | None = None
    target_region_id: str | None = None


@dataclass(frozen=True, slots=True)
class SectionMarkObservation:
    texts: tuple[str, ...]
    block_name: str
    block_handle: str
    source_ids: tuple[str, ...] = ()
    association_edge_ids: tuple[str, ...] = ()
    target_node_id: str | None = None
    target_region_id: str | None = None


@dataclass(slots=True)
class AnnotationModel:
    dimensions: list[DimensionObservation] = field(default_factory=list)
    bolt_marks: list[BoltMarkObservation] = field(default_factory=list)
    unresolved_bolt_marks: list[UnresolvedBoltMarkObservation] = field(
        default_factory=list
    )
    part_marks: list[PartMarkObservation] = field(default_factory=list)
    section_marks: list[SectionMarkObservation] = field(default_factory=list)
    section_block_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": [asdict(item) for item in self.dimensions],
            "bolt_marks": [asdict(item) for item in self.bolt_marks],
            "unresolved_bolt_marks": [
                asdict(item) for item in self.unresolved_bolt_marks
            ],
            "part_marks": [asdict(item) for item in self.part_marks],
            "section_marks": [asdict(item) for item in self.section_marks],
            "section_block_count": self.section_block_count,
        }


def _block_orientation(block) -> str:
    line_boxes = [
        atom.bbox
        for atom in block.entities
        if atom.semantic_layer == SemanticLayer.DIMENSION
        and atom.entity.dxftype() == "LINE"
        and atom.bbox is not None
    ]
    if not line_boxes:
        return "unknown"
    horizontal = sum(box.width >= box.height for box in line_boxes)
    vertical = len(line_boxes) - horizontal
    if horizontal > vertical:
        return "horizontal"
    if vertical > horizontal:
        return "vertical"
    return "mixed"


def _extract_graph_annotation_model(graph: DrawingGraph) -> AnnotationModel:
    model = AnnotationModel()
    nodes_by_id = {node.node_id: node for node in graph.nodes}

    def target_region_id(edge) -> str | None:
        target = nodes_by_id.get(edge.target)
        if target is None or target.kind != DrawingNodeKind.VIEW_REGION:
            return None
        value = target.attributes.get("region_id")
        return str(value) if value else None

    measurement_edges = graph.edges_of(DrawingEdgeKind.MEASURES)
    label_edges = graph.edges_of(DrawingEdgeKind.LABELS)
    projection_edges = graph.edges_of(DrawingEdgeKind.PROJECTS_TO)
    alignment_edges = graph.edges_of(DrawingEdgeKind.ALIGNED_WITH)
    for node in graph.nodes_of(DrawingNodeKind.DIMENSION):
        edges = [edge for edge in measurement_edges if edge.source == node.node_id]
        if not edges:
            continue
        value = node.attributes.get("value_mm")
        text = str(node.attributes.get("text") or value or "")
        anchors = [edge for edge in alignment_edges if edge.source == node.node_id]
        for edge in edges:
            model.dimensions.append(
                DimensionObservation(
                    text=text,
                    value=float(value) if value is not None else None,
                    chain_count=(
                        int(node.attributes["chain_interval_count"])
                        if node.attributes.get("chain_interval_count") is not None
                        else None
                    ),
                    chain_pitch=(
                        float(node.attributes["chain_pitch_mm"])
                        if node.attributes.get("chain_pitch_mm") is not None
                        else None
                    ),
                    orientation=str(
                        edge.attributes.get(
                            "orientation",
                            node.attributes.get("orientation", "unknown"),
                        )
                    ),
                    block_name="drawing_graph",
                    block_handle=node.node_id,
                    bbox=None,
                    source_ids=node.source_ids,
                    association_edge_ids=(edge.edge_id,),
                    target_node_id=edge.target,
                    target_region_id=target_region_id(edge),
                    scope=str(
                        edge.attributes.get(
                            "scope",
                            node.attributes.get("scope", "untyped"),
                        )
                    ),
                    property_type=str(
                        edge.attributes.get(
                            "property_type",
                            node.attributes.get("property_type", "unresolved"),
                        )
                    ),
                    target_count=len(edges),
                    anchor_source_ids=tuple(
                        sorted(
                            {
                                source_id
                                for anchor in anchors
                                for source_id in nodes_by_id[anchor.target].source_ids
                            }
                        )
                    ),
                    anchor_axis_values_mm=tuple(
                        sorted(
                            {
                                float(anchor.attributes["anchor_axis_mm"])
                                for anchor in anchors
                                if anchor.attributes.get("anchor_axis_mm")
                                is not None
                            }
                        )
                    ),
                    anchor_residual_mm=(
                        min(anchor.residual_mm for anchor in anchors)
                        if anchors
                        else None
                    ),
                    anchor_tolerance_mm=(
                        max(
                            float(
                                anchor.attributes.get(
                                    "maximum_origin_gap_mm",
                                    displayed_dimension_tolerance(
                                        _chain_pitch_text_from_values(
                                            text,
                                            node.attributes.get("chain_pitch_mm"),
                                        )
                                    ),
                                )
                            )
                            for anchor in anchors
                        )
                        if anchors
                        else None
                    ),
                    anchor_count=len(anchors),
                    residual_mm=edge.residual_mm,
                    strength=edge.strength.value,
                )
            )
    for node in graph.nodes_of(DrawingNodeKind.BOLT_MARK):
        text = str(node.attributes.get("text") or "")
        edges = [edge for edge in label_edges if edge.source == node.node_id]
        target_regions = tuple(
            sorted(
                {
                    str(region_id)
                    for label_edge in edges
                    for projection_edge in projection_edges
                    if projection_edge.source == label_edge.target
                    if (
                        region_id := nodes_by_id[
                            projection_edge.target
                        ].attributes.get("region_id")
                    )
                }
            )
        )
        target_node_id = edges[0].target if edges else None
        target_source_ids = (
            nodes_by_id[target_node_id].source_ids
            if target_node_id is not None and target_node_id in nodes_by_id
            else ()
        )
        parsed = parse_bolt_mark_text(text)
        if parsed is None:
            if text.strip():
                model.unresolved_bolt_marks.append(
                    UnresolvedBoltMarkObservation(
                        raw_text=text,
                        reason=_bolt_mark_parse_failure_reason(text),
                        block_name="drawing_graph",
                        block_handle=node.node_id,
                        source_ids=node.source_ids,
                        association_edge_ids=tuple(edge.edge_id for edge in edges),
                        target_node_id=target_node_id,
                        target_source_ids=target_source_ids,
                        target_region_ids=target_regions,
                    )
                )
            continue
        count, diameter = parsed
        model.bolt_marks.append(
            BoltMarkObservation(
                raw_text=text,
                count=count,
                diameter=diameter,
                block_name="drawing_graph",
                block_handle=node.node_id,
                source_ids=node.source_ids,
                association_edge_ids=tuple(edge.edge_id for edge in edges),
                target_node_id=target_node_id,
                target_source_ids=target_source_ids,
                target_region_ids=target_regions,
            )
        )
    for node in graph.nodes_of(DrawingNodeKind.PART_MARK):
        text = str(node.attributes.get("text") or "")
        if _PART_RE.fullmatch(text):
            edges = [edge for edge in label_edges if edge.source == node.node_id]
            model.part_marks.append(
                PartMarkObservation(
                    text,
                    "drawing_graph",
                    node.node_id,
                    source_ids=node.source_ids,
                    association_edge_ids=tuple(edge.edge_id for edge in edges),
                    target_node_id=edges[0].target if edges else None,
                    target_region_id=target_region_id(edges[0]) if edges else None,
                )
            )
    for node in graph.nodes_of(DrawingNodeKind.SECTION_SYMBOL):
        edges = [edge for edge in label_edges if edge.source == node.node_id]
        model.section_marks.append(
            SectionMarkObservation(
                texts=tuple(map(str, node.attributes.get("texts", ()))),
                block_name="drawing_graph",
                block_handle=node.node_id,
                source_ids=node.source_ids,
                association_edge_ids=tuple(edge.edge_id for edge in edges),
                target_node_id=edges[0].target if len(edges) == 1 else None,
                target_region_id=(
                    target_region_id(edges[0]) if len(edges) == 1 else None
                ),
            )
        )
    model.section_block_count = len(graph.nodes_of(DrawingNodeKind.SECTION_SYMBOL))
    return model


def extract_annotation_model(ir: BHDocumentIR | DrawingGraph) -> AnnotationModel:
    if isinstance(ir, DrawingGraph):
        return _extract_graph_annotation_model(ir)
    model = AnnotationModel()
    for block in ir.blocks:
        semantic_roles = {item.semantic_layer for item in block.entities}
        if SemanticLayer.DIMENSION in semantic_roles:
            orientation = _block_orientation(block)
            for text in block.texts:
                numeric = _NUMERIC_RE.fullmatch(text.normalized)
                chain = _CHAIN_RE.fullmatch(text.normalized)
                if not numeric and not chain:
                    continue
                model.dimensions.append(
                    DimensionObservation(
                        text=text.normalized,
                        value=float(text.normalized) if numeric else None,
                        chain_count=int(chain.group("count")) if chain else None,
                        chain_pitch=float(chain.group("pitch")) if chain else None,
                        orientation=orientation,
                        block_name=block.name,
                        block_handle=block.handle,
                        bbox=block.bbox,
                    )
                )
        if SemanticLayer.BOLT_MARK in semantic_roles:
            for text in block.texts:
                parsed = parse_bolt_mark_text(text.normalized)
                if not parsed:
                    if text.normalized.strip():
                        model.unresolved_bolt_marks.append(
                            UnresolvedBoltMarkObservation(
                                raw_text=text.normalized,
                                reason=_bolt_mark_parse_failure_reason(
                                    text.normalized
                                ),
                                block_name=block.name,
                                block_handle=block.handle,
                            )
                        )
                    continue
                count, diameter = parsed
                model.bolt_marks.append(
                    BoltMarkObservation(
                        raw_text=text.normalized,
                        count=count,
                        diameter=diameter,
                        block_name=block.name,
                        block_handle=block.handle,
                    )
                )
        if SemanticLayer.PART_MARK in semantic_roles:
            for text in block.texts:
                if _PART_RE.fullmatch(text.normalized):
                    model.part_marks.append(
                        PartMarkObservation(text.normalized, block.name, block.handle)
                    )
        if SemanticLayer.SECTION in semantic_roles:
            model.section_block_count += 1
    return model


def _chain_pitch_text(observation: DimensionObservation) -> str:
    match = _CHAIN_RE.fullmatch(observation.text)
    if match:
        return match.group("pitch")
    return str(observation.chain_pitch or "")


def _chain_pitch_text_from_values(text: str, pitch: object) -> str:
    match = _CHAIN_RE.fullmatch(text)
    return match.group("pitch") if match else str(pitch or "")


def _collapsed_axis_values(values: list[float], tolerance: float) -> list[float]:
    collapsed: list[float] = []
    for value in sorted(values):
        if not collapsed or abs(value - collapsed[-1]) > tolerance:
            collapsed.append(value)
    return collapsed


def _pitch_chain_supported(
    assembly: BHAssembly,
    observation: DimensionObservation,
) -> bool | None:
    if (
        observation.chain_count is None
        or observation.chain_count <= 0
        or observation.chain_pitch is None
        or observation.chain_pitch <= 0.0
        or observation.orientation not in {"horizontal", "vertical"}
        or observation.target_node_id is None
        or observation.scope != "pitch_chain"
        or observation.target_count != 1
        or not observation.association_edge_ids
    ):
        return None
    if (
        observation.anchor_count < 1
        or not observation.anchor_source_ids
        or observation.anchor_residual_mm is None
        or observation.anchor_tolerance_mm is None
        or observation.anchor_residual_mm
        > observation.anchor_tolerance_mm
    ):
        return None
    tolerance = displayed_dimension_tolerance(_chain_pitch_text(observation))
    coordinate = "x" if observation.orientation == "horizontal" else "y"
    anchor_matches: list[tuple[BHPlate, list[int]]] = []
    for plate in assembly.plates:
        if (
            observation.target_region_id is None
            or plate.provenance.get("source_region_id")
            != observation.target_region_id
        ):
            continue
        source_rows = plate.provenance.get("circular_cut_source_ids", [])
        if not isinstance(source_rows, list):
            continue
        indexes = []
        for index, source_ids in enumerate(source_rows):
            if index >= len(plate.circular_cuts):
                continue
            if set(map(str, source_ids)).intersection(observation.anchor_source_ids):
                indexes.append(index)
        if indexes:
            anchor_matches.append((plate, indexes))
    if not anchor_matches:
        # No match means a uniquely anchored source hole was lost during
        # lowering; this is a real source/manufacturing conflict.
        return False
    for plate, anchor_indexes in anchor_matches:
        values = _collapsed_axis_values(
            [getattr(cut.center, coordinate) for cut in plate.circular_cuts],
            tolerance,
        )
        anchor_values = _collapsed_axis_values(
            [
                getattr(plate.circular_cuts[index].center, coordinate)
                for index in anchor_indexes
            ],
            tolerance,
        )
        if len(anchor_values) != 1:
            continue
        for direction in (-1.0, 1.0):
            current = anchor_values[0]
            used = {
                index
                for index, value in enumerate(values)
                if abs(value - current) <= tolerance
            }
            matched = True
            for _ in range(observation.chain_count):
                candidates = [
                    (
                        abs((value - current) - direction * observation.chain_pitch),
                        index,
                        value,
                    )
                    for index, value in enumerate(values)
                    if index not in used
                    and abs(
                        (value - current) - direction * observation.chain_pitch
                    )
                    <= tolerance
                ]
                if not candidates:
                    matched = False
                    break
                _, index, current = min(candidates)
                used.add(index)
            if matched:
                return True
    return False


def annotation_consistency(
    metadata: BHMetadata,
    assembly: BHAssembly,
    model: AnnotationModel,
) -> dict[str, Any]:
    selected_region_ids = {
        str(region_id)
        for plate in assembly.plates
        if (region_id := plate.provenance.get("source_region_id"))
    }
    selected_dimensions = [
        item
        for item in model.dimensions
        if not selected_region_ids or item.target_region_id in selected_region_ids
    ]
    ignored_dimensions = [
        item for item in model.dimensions if item not in selected_dimensions
    ]
    dimension_values = [item.value for item in selected_dimensions if item.value is not None]
    ordinary_dimensions = [
        item
        for item in selected_dimensions
        if item.value is not None
        and item.scope == "view_extent"
        and item.target_count == 1
        and item.target_node_id is not None
        and item.strength in {"explicit", "geometric"}
        and item.residual_mm is not None
        and item.residual_mm <= displayed_dimension_tolerance(item.text)
    ]
    nominal_dimensions = [
        item
        for item in ordinary_dimensions
        if item.property_type == "longitudinal_extent"
    ]
    web_region_id = str(
        assembly.web_plate.provenance.get("source_region_id") or ""
    )
    profile_height_dimensions = [
        item
        for item in ordinary_dimensions
        if item.property_type == "transverse_envelope"
        and bool(web_region_id)
        and item.target_region_id == web_region_id
    ]
    pitch_chains = [
        item
        for item in selected_dimensions
        if item.chain_count is not None and item.chain_pitch is not None
    ]
    pitch_chain_support = [
        _pitch_chain_supported(assembly, item)
        for item in pitch_chains
    ]
    resolved_pitch_support = [
        item for item in pitch_chain_support if item is not None
    ]
    nominal_length_seen = any(
        abs(float(item.value) - metadata.nominal_length)
        <= displayed_dimension_tolerance(item.text)
        for item in nominal_dimensions
    )
    profile_height_seen = any(
        abs(float(item.value) - metadata.profile.max_height)
        <= displayed_dimension_tolerance(item.text)
        for item in profile_height_dimensions
    )
    actual_diameters = {
        round(cut.radius * 2.0, 3)
        for plate in assembly.plates
        for cut in plate.circular_cuts
    }
    for plate in assembly.plates:
        inner_source_rows = plate.provenance.get("inner_contour_source_ids", [])
        inner_diameters = plate.provenance.get(
            "inner_contour_nominal_diameters_mm", []
        )
        if not isinstance(inner_source_rows, list) or not isinstance(inner_diameters, list):
            continue
        actual_diameters.update(
            round(float(diameter), 3)
            for sources, diameter in zip(inner_source_rows, inner_diameters)
            if sources and diameter is not None
        )
    cut_diameters_by_source: dict[str, float] = {}
    for plate in assembly.plates:
        source_rows = plate.provenance.get("circular_cut_source_ids", [])
        for index, cut in enumerate(plate.circular_cuts):
            if not isinstance(source_rows, list) or index >= len(source_rows):
                continue
            for source_id in source_rows[index]:
                cut_diameters_by_source[str(source_id)] = round(
                    cut.radius * 2.0,
                    3,
                )
        inner_source_rows = plate.provenance.get("inner_contour_source_ids", [])
        inner_diameters = plate.provenance.get(
            "inner_contour_nominal_diameters_mm", []
        )
        for index, _ in enumerate(plate.inner_contours):
            if (
                not isinstance(inner_source_rows, list)
                or not isinstance(inner_diameters, list)
                or index >= len(inner_source_rows)
                or index >= len(inner_diameters)
                or inner_diameters[index] is None
            ):
                continue
            diameter = round(float(inner_diameters[index]), 3)
            for source_id in inner_source_rows[index]:
                cut_diameters_by_source[str(source_id)] = diameter
    selected_bolt_marks = [
        item
        for item in model.bolt_marks
        if not selected_region_ids
        or bool(selected_region_ids.intersection(item.target_region_ids))
    ]
    ignored_bolt_marks = [
        item for item in model.bolt_marks if item not in selected_bolt_marks
    ]
    selected_unresolved_bolt_marks = [
        item
        for item in model.unresolved_bolt_marks
        if not selected_region_ids
        or bool(selected_region_ids.intersection(item.target_region_ids))
    ]
    ignored_unresolved_bolt_marks = [
        item
        for item in model.unresolved_bolt_marks
        if item not in selected_unresolved_bolt_marks
    ]
    bolt_mark_diameters = {
        round(item.diameter, 3) for item in selected_bolt_marks
    }
    bolt_mark_diameters_supported = all(
        any(
            source_id in cut_diameters_by_source
            and abs(cut_diameters_by_source[source_id] - item.diameter) <= 0.001
            for source_id in item.target_source_ids
        )
        for item in selected_bolt_marks
    )
    marked_count = sum(item.count for item in selected_bolt_marks)
    actual_count = sum(len(plate.circular_cuts) for plate in assembly.plates)
    actual_count += sum(
        1
        for plate in assembly.plates
        for sources, diameter in zip(
            plate.provenance.get("inner_contour_source_ids", []),
            plate.provenance.get("inner_contour_nominal_diameters_mm", []),
        )
        if sources and diameter is not None
    )
    # A drawing may annotate only one view or only flange holes, so marked count
    # is supporting evidence rather than a strict equality constraint.
    bolt_mark_count_plausible = (
        marked_count <= actual_count if selected_bolt_marks else True
    )
    selected_part_marks = [
        item
        for item in model.part_marks
        if not selected_region_ids or item.target_region_id in selected_region_ids
    ]
    part_mark_match = (
        all(item.text.lower() == metadata.part_number.lower() for item in selected_part_marks)
        if selected_part_marks
        else True
    )
    selected_section_marks = [
        item
        for item in model.section_marks
        if not selected_region_ids or item.target_region_id in selected_region_ids
    ]
    ignored_section_marks = [
        item for item in model.section_marks if item not in selected_section_marks
    ]
    evidence_presence = {
        "dimensions": bool(selected_dimensions),
        "bolt_marks": bool(
            selected_bolt_marks or selected_unresolved_bolt_marks
        ),
        "part_marks": bool(selected_part_marks),
        "section_marks": bool(selected_section_marks)
        or (not model.section_marks and model.section_block_count > 0),
    }
    evidence_coverage = sum(evidence_presence.values()) / len(evidence_presence)
    present_checks: list[bool] = []
    if nominal_dimensions:
        present_checks.append(nominal_length_seen)
    if profile_height_dimensions:
        present_checks.append(profile_height_seen)
    if resolved_pitch_support:
        present_checks.extend(resolved_pitch_support)
    if selected_bolt_marks:
        present_checks.extend([bolt_mark_diameters_supported, bolt_mark_count_plausible])
    if selected_unresolved_bolt_marks:
        present_checks.append(False)
    if selected_part_marks:
        present_checks.append(part_mark_match)
    support_quality = (
        sum(bool(value) for value in present_checks) / len(present_checks)
        if present_checks
        else 1.0
    )
    nominal_residuals = [
        abs(float(item.value) - metadata.nominal_length)
        for item in nominal_dimensions
    ]
    profile_residuals = [
        abs(float(item.value) - metadata.profile.max_height)
        for item in profile_height_dimensions
    ]
    relation_status = {
        "nominal_length": {
            # A dimension bound to a view can describe a flange width, pitch,
            # offset, or opening.  Until the measured property is typed, a
            # different value is absent support, not contradictory evidence.
            "status": "pass" if nominal_length_seen else "missing",
            "minimum_residual_mm": min(nominal_residuals, default=None),
            "tolerance_mm": (
                min(
                    (displayed_dimension_tolerance(item.text) for item in nominal_dimensions),
                    default=None,
                )
            ),
        },
        "profile_height": {
            "status": "pass" if profile_height_seen else "missing",
            "minimum_residual_mm": min(profile_residuals, default=None),
            "tolerance_mm": (
                min(
                    (
                        displayed_dimension_tolerance(item.text)
                        for item in profile_height_dimensions
                    ),
                    default=None,
                )
            ),
        },
        "hole_pitch_chains": {
            "status": (
                "not_observed"
                if not pitch_chains
                else "missing"
                if len(resolved_pitch_support) != len(pitch_chains)
                else "pass"
                if all(resolved_pitch_support)
                else "conflict"
            ),
            "observed_count": len(pitch_chains),
            "resolved_count": len(resolved_pitch_support),
            "supported_count": sum(resolved_pitch_support),
            "ignored_unselected_count": sum(
                item.chain_count is not None and item.chain_pitch is not None
                for item in ignored_dimensions
            ),
        },
    }
    return {
        "nominal_length_dimension_seen": nominal_length_seen,
        "profile_height_dimension_seen": profile_height_seen,
        "bolt_mark_diameters_supported": bolt_mark_diameters_supported,
        "bolt_mark_count_plausible": bolt_mark_count_plausible,
        "part_mark_matches_metadata": part_mark_match,
        "dimension_value_count": len(dimension_values),
        "dimension_observation_count": len(model.dimensions),
        "selected_dimension_observation_count": len(selected_dimensions),
        "ignored_unselected_dimension_count": len(ignored_dimensions),
        "dimension_pitch_chain_count": len(pitch_chains),
        "dimension_pitch_chains_supported": bool(pitch_chains)
        and len(resolved_pitch_support) == len(pitch_chains)
        and all(resolved_pitch_support),
        "bolt_mark_count": len(selected_bolt_marks),
        "ignored_unselected_bolt_mark_count": len(ignored_bolt_marks),
        "unresolved_bolt_mark_count": len(selected_unresolved_bolt_marks),
        "ignored_unselected_unresolved_bolt_mark_count": len(
            ignored_unresolved_bolt_marks
        ),
        "section_mark_count": len(selected_section_marks),
        "ignored_unselected_section_mark_count": len(ignored_section_marks),
        "section_view_role_supported": bool(selected_section_marks),
        "marked_hole_quantity": marked_count,
        "actual_hole_quantity": actual_count,
        "actual_hole_diameters_mm": sorted(actual_diameters),
        "bolt_mark_diameters_mm": sorted(bolt_mark_diameters),
        "evidence_presence": evidence_presence,
        "evidence_coverage": evidence_coverage,
        "support_quality": support_quality,
        "relations": relation_status,
    }
