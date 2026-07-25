from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .bh_associations import DrawingGraph
from .bh_manufacturing_ir import BHManufacturingIR
from .bh_proofs import ProofReport
from .bh_source import SourceDocument


def _manufacturing_source_ids(manufacturing: BHManufacturingIR) -> set[str]:
    result: set[str] = set()
    for plate in manufacturing.plates:
        evidence_items = [
            plate.role_evidence,
            *(segment.evidence for segment in plate.outer_segments),
            *(cut.evidence for cut in plate.circular_cuts),
            *(
                segment.evidence
                for contour in plate.inner_contours
                for segment in contour.segments
            ),
        ]
        for evidence in evidence_items:
            result.update(evidence.source_ids)
    return result


def _known(values: Iterable[str], universe: set[str]) -> set[str]:
    return {value for value in values if value in universe}


def build_source_information_ledger(
    source: SourceDocument,
    drawing_graph: DrawingGraph,
    manufacturing: BHManufacturingIR,
    proof_report: ProofReport,
    *,
    metadata_source_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Account for every source fact without pretending every fact is proof.

    The partition records the highest authority reached by each source entity.
    Lower-authority bindings are reported separately because one entity may be
    retained in the drawing graph, cited by a proof and lowered to a plate.
    Decorative sheet facts remain explicit ``retained_context`` instead of
    disappearing or accidentally acquiring manufacturing authority.
    """

    universe = {item.source_id for item in source.entities}
    drawing_ids = _known(
        (
            source_id
            for node in drawing_graph.nodes
            for source_id in node.source_ids
        ),
        universe,
    )
    metadata_ids = _known(metadata_source_ids, universe)
    manufacturing_ids = _known(
        _manufacturing_source_ids(manufacturing), universe
    )
    proof_ids = _known(
        (
            source_id
            for obligation in proof_report.obligations
            for evidence in obligation.evidence
            for source_id in evidence.source_ids
        ),
        universe,
    )

    remaining = set(universe)
    authority_partition: dict[str, int] = {}
    for name, values in (
        ("manufacturing_geometry", manufacturing_ids),
        ("proof_only", proof_ids),
        ("metadata_only", metadata_ids),
        ("drawing_relation_only", drawing_ids),
    ):
        owned = remaining & values
        authority_partition[name] = len(owned)
        remaining -= owned
    authority_partition["retained_context"] = len(remaining)

    return {
        "source_entity_count": len(source.entities),
        "source_container_count": len(source.containers),
        "inventory_by_semantic_role": dict(
            sorted(Counter(item.semantic_hint.role.value for item in source.entities).items())
        ),
        "inventory_by_entity_type": dict(
            sorted(Counter(item.entity_type for item in source.entities).items())
        ),
        "inventory_by_visibility": dict(
            sorted(Counter(item.visibility.value for item in source.entities).items())
        ),
        "semantic_object_counts": dict(
            sorted(Counter(node.kind.value for node in drawing_graph.nodes).items())
        ),
        "semantic_relation_counts": dict(
            sorted(
                Counter(edge.relation.value for edge in drawing_graph.edges).items()
            )
        ),
        "binding_counts": {
            "drawing_graph": len(drawing_ids),
            "metadata": len(metadata_ids),
            "manufacturing": len(manufacturing_ids),
            "proof": len(proof_ids),
        },
        "authority_partition": authority_partition,
        "policy": {
            "retained_context_can_authorize_manufacturing": False,
            "unknown_layer_can_authorize_manufacturing": False,
            "manual_split_used": False,
            "automation_rule": "critical source-backed proof obligations only",
            "strict_representation_equivalence": (
                "fact inventory plus typed semantic object/relation counts; "
                "raw source-id citation multiplicity remains audit trace"
            ),
        },
    }
