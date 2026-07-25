from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from steel_dxf_split.bh_annotations import (
    AnnotationModel,
    BoltMarkObservation,
    DimensionObservation,
    PartMarkObservation,
    UnresolvedBoltMarkObservation,
    annotation_consistency,
    parse_bolt_mark_text,
)
from steel_dxf_split.bh_constraints import (
    ConstraintContext,
    build_proof_obligations,
)
from steel_dxf_split.bh_compiler import BHCompilationRejected, compile_bh_document
from steel_dxf_split.bh_hypothesis import ViewPairHypothesis
from steel_dxf_split.bh_ir import BHDocumentIR
from steel_dxf_split.bh_knowledge import (
    DEFAULT_BH_KNOWLEDGE,
    DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
)
from steel_dxf_split.bh_models import (
    BHAssembly,
    BHMetadata,
    BHPlate,
    BHPlateRole,
    BulgeContour,
    BulgeVertex,
    CircularCut,
    HProfile,
)
from steel_dxf_split.bh_proofs import ProofStatus
from steel_dxf_split.bh_source import SourceDocument
from steel_dxf_split.bh_validator import validate_bh_assembly
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.geometry_types import Point2D


class _Box:
    min_x = 0.0
    min_y = 0.0
    max_x = 1000.0
    max_y = 300.0
    width = 1000.0
    height = 300.0


class _View:
    def __init__(self, handle: str):
        self.handle = handle
        self.name = handle
        self.bbox = _Box()
        self.region_id = f"region-{handle}"


def _rectangle(width: float, height: float) -> BulgeContour:
    return BulgeContour(
        [
            BulgeVertex(0.0, 0.0),
            BulgeVertex(width, 0.0),
            BulgeVertex(width, height),
            BulgeVertex(0.0, height),
        ]
    )


def _provenance(role: str) -> dict[str, object]:
    return {
        "source_block": f"block-{role}",
        "source_insert_handle": f"insert-{role}",
        "source_view_role": role,
        "source_region_id": f"region-{role if role != 'web' else 'main'}",
        "source_entity_count": 4,
        "cut_source_blocks": [],
    }


def _assembly() -> BHAssembly:
    profile = HProfile(300.0, 200.0, 8.0, 12.0, "BH300*200*8*12")
    metadata = BHMetadata("BH-TEST-1", profile, 1000.0, "Q355B", 20.0)
    web = BHPlate(
        BHPlateRole.WEB,
        _rectangle(1000.0, 276.0),
        8.0,
        "BH-TEST-1-腹板",
        area_mm2=276000.0,
        provenance=_provenance("web"),
    )
    flange = BHPlate(
        BHPlateRole.FLANGE,
        _rectangle(1000.0, 200.0),
        12.0,
        "BH-TEST-1-翼缘板",
        quantity=2,
        area_mm2=200000.0,
        provenance=_provenance("flange"),
    )
    return BHAssembly(
        metadata,
        web,
        [flange],
        [],
        diagnostics={
            "flange_development": {"mode": "projection_view"},
            "projection_source_edge_conservation": {
                "assessments": [
                    {
                        "repair_kind": "selected_projection_boundary",
                        "applied": True,
                        "reason": "source_edges_conserved",
                        "protected_source_ids": ["web-edge", "flange-edge"],
                        "lost_source_ids": [],
                        "fidelity_tolerance_mm": 1e-7,
                    }
                ]
            },
        },
    )


def _context(
    assembly: BHAssembly,
    annotations: AnnotationModel | None = None,
) -> tuple[ConstraintContext, dict[str, object]]:
    annotations = annotations or AnnotationModel()
    source = SourceDocument("AC1021", "utf-8", 4, (), (), ())
    ir = BHDocumentIR(None, "AC1021", "utf-8", 4, [], {}, 0)
    pair = ViewPairHypothesis(
        "view-pair",
        1,
        _View("main"),
        _View("flange"),
        0.0,
        0.0,
        0.0,
        "x",
        "x",
    )
    validation = validate_bh_assembly(assembly)
    annotation = annotation_consistency(assembly.metadata, assembly, annotations)
    return (
        ConstraintContext(
            assembly=assembly,
            validation=validation,
            view_pair=pair,
            annotations=annotations,
            knowledge=DEFAULT_BH_KNOWLEDGE,
            source_ir=source,
            lowering_ir=ir,
            metadata_candidates=(
                {
                    "profile": assembly.metadata.profile.raw_text,
                    "row": [
                        assembly.metadata.part_number,
                        assembly.metadata.profile.raw_text,
                        str(assembly.metadata.nominal_length),
                    ],
                    "score": 100.0,
                },
            ),
            metadata_margin=100.0,
            metadata_source_ids=("metadata-row",),
        ),
        annotation,
    )


def _proofs(
    assembly: BHAssembly,
    annotations: AnnotationModel | None = None,
):
    context, annotation = _context(assembly, annotations)
    return {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }


def test_valid_structural_fixture_passes_every_critical_proof() -> None:
    proofs = _proofs(_assembly())

    assert all(
        proof.status in {ProofStatus.PASS, ProofStatus.NOT_APPLICABLE}
        for proof in proofs.values()
        if proof.critical
    )
    dimension = proofs["BH.PROOF.ANNOTATION.DIMENSION_AGREEMENT"]
    assert dimension.status == ProofStatus.MISSING
    assert not dimension.critical
    assert {
        "BH.PROOF.SOURCE.TEKLA_SINGLE_PART_CONTRACT",
        "BH.PROOF.SOURCE.RELEASE_PROFILE_VERIFIED",
        "BH.PROOF.PLATE.00.THICKNESS",
        "BH.PROOF.PLATE.00.CONTOUR",
        "BH.PROOF.PLATE.00.CUTS",
        "BH.PROOF.PLATE.00.PROVENANCE",
        "BH.PROOF.PLATE.01.THICKNESS",
        "BH.PROOF.PLATE.01.CONTOUR",
        "BH.PROOF.PLATE.01.CUTS",
        "BH.PROOF.PLATE.01.PROVENANCE",
    }.issubset(proofs)


def test_projection_source_edge_conservation_is_a_critical_source_backed_proof() -> None:
    proof = _proofs(_assembly())[
        "BH.PROOF.PROJECTION.SOURCE_EDGE_CONSERVATION"
    ]

    assert proof.status == ProofStatus.PASS
    assert proof.critical
    assert proof.diagnostic_code is None
    assert {source_id for item in proof.evidence for source_id in item.source_ids} == {
        "web-edge",
        "flange-edge",
    }


def test_projection_source_edge_loss_is_a_critical_conflict() -> None:
    assembly = _assembly()
    assembly.diagnostics["projection_source_edge_conservation"] = {
        "assessments": [
            {
                "repair_kind": "selected_projection_boundary",
                "applied": False,
                "reason": "direct_source_edge_loss",
                "protected_source_ids": ["edge-kept", "edge-lost"],
                "lost_source_ids": ["edge-lost"],
                "fidelity_tolerance_mm": 1e-7,
            }
        ]
    }

    proof = _proofs(assembly)[
        "BH.PROOF.PROJECTION.SOURCE_EDGE_CONSERVATION"
    ]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-PROJECTION-SOURCE-EDGE-LOSS"
    assert proof.evidence[0].source_ids == ("edge-kept", "edge-lost")


def test_rejected_boundary_repair_is_a_safe_noop_when_final_boundary_conserves_edges() -> None:
    assembly = _assembly()
    assembly.diagnostics["projection_source_edge_conservation"] = {
        "assessments": [
            {
                "repair_kind": "proven_rectangular_projection",
                "applied": False,
                "reason": "direct_source_edge_loss",
                "protected_source_ids": ["source-bevel"],
                "lost_source_ids": ["source-bevel"],
                "fidelity_tolerance_mm": 1e-7,
            },
            {
                "repair_kind": "selected_projection_boundary",
                "applied": True,
                "reason": "source_edges_conserved",
                "protected_source_ids": ["source-bevel"],
                "lost_source_ids": [],
                "fidelity_tolerance_mm": 0.00051,
            },
        ]
    }

    proof = _proofs(assembly)[
        "BH.PROOF.PROJECTION.SOURCE_EDGE_CONSERVATION"
    ]

    assert proof.status == ProofStatus.PASS
    assert len(proof.evidence) == 2


def test_repair_diagnostics_without_a_final_boundary_assessment_are_missing() -> None:
    assembly = _assembly()
    assembly.diagnostics["projection_source_edge_conservation"] = {
        "assessments": [
            {
                "repair_kind": "proven_rectangular_projection",
                "applied": False,
                "reason": "direct_source_edge_loss",
                "protected_source_ids": ["source-bevel"],
                "lost_source_ids": ["source-bevel"],
                "fidelity_tolerance_mm": 1e-7,
            }
        ]
    }

    proof = _proofs(assembly)[
        "BH.PROOF.PROJECTION.SOURCE_EDGE_CONSERVATION"
    ]

    assert proof.status == ProofStatus.MISSING
    assert proof.diagnostic_code == "BH-PROOF-PROJECTION-SOURCE-EDGE-UNOBSERVED"


def test_projection_overlay_reclassification_is_explicit_proof_evidence() -> None:
    assembly = _assembly()
    assembly.diagnostics["projection_source_edge_conservation"] = {
        "assessments": [
            {
                "repair_kind": "micro_topology_regularization",
                "applied": True,
                "reason": "longitudinal_projection_overlay_regularized",
                "protected_source_ids": ["silhouette-edge"],
                "lost_source_ids": [],
                "reclassified_source_ids": ["projected-face-edge"],
                "fidelity_tolerance_mm": 0.00051,
            },
            {
                "repair_kind": "selected_projection_boundary",
                "applied": True,
                "reason": "source_edges_conserved",
                "protected_source_ids": ["silhouette-edge"],
                "lost_source_ids": [],
                "fidelity_tolerance_mm": 0.00051,
            },
        ]
    }

    proof = _proofs(assembly)[
        "BH.PROOF.PROJECTION.SOURCE_EDGE_CONSERVATION"
    ]

    assert proof.status == ProofStatus.PASS
    assert proof.evidence[0].source_ids == ("silhouette-edge",)
    assert "reclassified_projection_overlay=projected-face-edge" in (
        proof.evidence[0].measured or ""
    )


def test_material_table_thickness_conflict_is_rejected_by_geometry_proof() -> None:
    assembly = _assembly()
    assembly.web_plate.thickness = 6.0

    proof = _proofs(assembly)["BH.PROOF.PLATE.THICKNESS"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-PLATE-THICKNESS-CONFLICT"


def test_one_flange_thickness_failure_identifies_the_exact_plate() -> None:
    assembly = _assembly()
    assembly.flange_plates[0].thickness = 10.0

    proofs = _proofs(assembly)

    assert proofs["BH.PROOF.PLATE.00.THICKNESS"].status == ProofStatus.PASS
    assert proofs["BH.PROOF.PLATE.01.THICKNESS"].status == ProofStatus.CONFLICT


def test_open_part_contour_has_a_stable_topology_diagnostic() -> None:
    assembly = _assembly()
    assembly.web_plate.contour.closed = False

    proof = _proofs(assembly)["BH.PROOF.CONTOUR.TOPOLOGY"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.diagnostic_code == "BH-PROOF-CONTOUR-INVALID"


def test_cut_outside_material_has_a_stable_containment_diagnostic() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.append(
        CircularCut(Point2D(1100.0, 100.0), 10.0)
    )

    proof = _proofs(assembly)["BH.PROOF.CUT.CONTAINMENT"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.diagnostic_code == "BH-PROOF-CUT-OUTSIDE-MATERIAL"


def test_missing_plate_provenance_is_incomplete_not_a_low_score() -> None:
    assembly = _assembly()
    assembly.flange_plates[0].provenance = {}

    proof = _proofs(assembly)["BH.PROOF.PROVENANCE.FEATURES"]

    assert proof.status == ProofStatus.INCOMPLETE
    assert proof.diagnostic_code == "BH-PROOF-PROVENANCE-INCOMPLETE"


def test_missing_index_aligned_cut_lineage_is_incomplete_provenance() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.append(
        CircularCut(Point2D(100.0, 100.0), 10.0)
    )
    assembly.web_plate.provenance["circular_cut_source_ids"] = []

    proof = _proofs(assembly)["BH.PROOF.PROVENANCE.FEATURES"]

    assert proof.status == ProofStatus.INCOMPLETE
    assert proof.diagnostic_code == "BH-PROOF-PROVENANCE-INCOMPLETE"


def test_bound_bolt_mark_conflict_rejects_instead_of_becoming_soft_penalty() -> None:
    assembly = _assembly()
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                "4-M20",
                4,
                20.0,
                "mark",
                "mark-handle",
                target_source_ids=("missing-selected-cut",),
                target_region_ids=("region-main",),
            )
        ]
    )

    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-ANNOTATION-CONFLICT"


def test_bolt_mark_owned_by_unselected_view_cannot_contradict_selected_assembly() -> None:
    assembly = _assembly()
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                "4-M20",
                4,
                20.0,
                "mark",
                "mark-handle",
                target_source_ids=("other-view-cut",),
                target_region_ids=("region-unselected",),
            )
        ]
    )

    consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        annotations,
    )

    assert consistency["bolt_mark_diameters_supported"]
    assert consistency["bolt_mark_count"] == 0
    assert consistency["ignored_unselected_bolt_mark_count"] == 1
    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]
    assert proof.status == ProofStatus.NOT_APPLICABLE
    assert not proof.critical
    assert proof.evidence == ()


def test_every_part_mark_on_selected_single_part_view_must_match_metadata() -> None:
    assembly = _assembly()
    annotations = AnnotationModel(
        part_marks=[
            PartMarkObservation(
                "BH-TEST-1",
                "mark",
                "correct-mark",
                target_region_id="region-main",
            ),
            PartMarkObservation(
                "BH-WRONG-9",
                "mark",
                "wrong-mark",
                target_region_id="region-main",
            ),
        ]
    )

    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-ANNOTATION-CONFLICT"


def test_wrong_part_mark_on_unselected_view_is_ignored() -> None:
    assembly = _assembly()
    annotations = AnnotationModel(
        part_marks=[
            PartMarkObservation(
                "BH-WRONG-9",
                "mark",
                "wrong-other-view-mark",
                target_region_id="region-unselected",
            )
        ]
    )

    consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        annotations,
    )

    assert consistency["part_mark_matches_metadata"]
    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]
    assert proof.status == ProofStatus.NOT_APPLICABLE
    assert not proof.critical
    assert proof.evidence == ()


def test_bound_bolt_mark_diameter_mismatch_is_a_conflict() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.append(CircularCut(Point2D(100.0, 100.0), 10.0))
    assembly.web_plate.provenance["circular_cut_source_ids"] = [["selected-cut"]]
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                "1-M24",
                1,
                24.0,
                "mark",
                "wrong-diameter-mark",
                target_source_ids=("selected-cut",),
                target_region_ids=("region-main",),
            )
        ]
    )

    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.diagnostic_code == "BH-PROOF-ANNOTATION-CONFLICT"


def test_two_mif_hole_marks_agree_with_32_manufactured_diameter_22_cuts() -> None:
    assembly = _assembly()
    source_ids: list[list[str]] = []
    for row, y in enumerate((80.0, 196.0)):
        for column in range(16):
            source_id = f"web-hole-{row}-{column}"
            assembly.web_plate.circular_cuts.append(
                CircularCut(Point2D(100.0 + column * 50.0, y), 11.0)
            )
            source_ids.append([source_id])
    assembly.web_plate.provenance["circular_cut_source_ids"] = source_ids

    raw_text = r"16\M+5A6B522"
    parsed = parse_bolt_mark_text(raw_text)
    assert parsed == (16, 22.0)
    count, diameter = parsed
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                raw_text,
                count,
                diameter,
                "mark",
                f"mif-mark-{row}",
                target_source_ids=(f"web-hole-{row}-0",),
                target_region_ids=("region-main",),
            )
            for row in range(2)
        ]
    )

    consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        annotations,
    )
    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]

    assert consistency["bolt_mark_diameters_supported"]
    assert consistency["bolt_mark_count_plausible"]
    assert consistency["bolt_mark_count"] == 2
    assert proof.status == ProofStatus.PASS
    assert proof.critical
    assert proof.diagnostic_code is None


def test_polygonal_bolt_openings_participate_in_mark_diameter_and_quantity_proofs() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts = [
        CircularCut(Point2D(600.0 + column * 25.0, 100.0), 11.0)
        for column in range(12)
    ]
    assembly.web_plate.provenance["circular_cut_source_ids"] = [
        [f"round-{index}"] for index in range(12)
    ]
    assembly.web_plate.inner_contours = [
        _rectangle(22.0, 35.0) for _ in range(18)
    ]
    assembly.web_plate.provenance["inner_contour_source_ids"] = [
        [f"slot-{index}"] for index in range(18)
    ]
    assembly.web_plate.provenance["inner_contour_nominal_diameters_mm"] = [
        22.0 for _ in range(18)
    ]
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                "18*D22(22x35)",
                18,
                22.0,
                "mark",
                "slot-mark",
                target_source_ids=("slot-0",),
                target_region_ids=("region-main",),
            ),
            BoltMarkObservation(
                "12Φ22",
                12,
                22.0,
                "mark",
                "round-mark",
                target_source_ids=("round-0",),
                target_region_ids=("region-main",),
            ),
        ]
    )

    consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        annotations,
    )

    assert consistency["actual_hole_quantity"] == 30
    assert consistency["marked_hole_quantity"] == 30
    assert consistency["bolt_mark_diameters_supported"]
    assert consistency["bolt_mark_count_plausible"]


def test_unrelated_inner_opening_does_not_count_as_a_bolt_hole() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts = [
        CircularCut(Point2D(100.0, 100.0), 11.0)
    ]
    assembly.web_plate.provenance["circular_cut_source_ids"] = [["round-hole"]]
    assembly.web_plate.inner_contours = [_rectangle(80.0, 40.0)]
    assembly.web_plate.provenance["inner_contour_source_ids"] = [[]]
    assembly.web_plate.provenance["inner_contour_nominal_diameters_mm"] = [None]
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                "2Φ22",
                2,
                22.0,
                "mark",
                "mark-2",
                target_source_ids=("round-hole",),
                target_region_ids=("region-main",),
            )
        ]
    )

    consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        annotations,
    )

    assert consistency["actual_hole_quantity"] == 1
    assert not consistency["bolt_mark_count_plausible"]


def test_polygonal_bolt_opening_requires_index_aligned_provenance() -> None:
    assembly = _assembly()
    assembly.web_plate.inner_contours = [_rectangle(22.0, 35.0)]
    assembly.web_plate.provenance["polygonal_cut_count"] = 1
    assembly.web_plate.provenance["inner_contour_source_ids"] = [[]]
    assembly.web_plate.provenance["inner_contour_nominal_diameters_mm"] = [None]

    proof = _proofs(assembly, AnnotationModel())["BH.PROOF.PROVENANCE.FEATURES"]

    assert proof.status == ProofStatus.INCOMPLETE


def test_unresolved_selected_bolt_mark_is_missing_not_silently_ignored() -> None:
    assembly = _assembly()
    annotations = AnnotationModel(
        unresolved_bolt_marks=[
            UnresolvedBoltMarkObservation(
                raw_text=r"16\M+9A6B522",
                reason="unresolved_cad_text",
                block_name="mark",
                block_handle="invalid-mif-mark",
                target_region_ids=("region-main",),
            )
        ]
    )

    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]

    assert proof.status == ProofStatus.MISSING
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-ANNOTATION-UNRESOLVED"
    assert proof.evidence[0].measured == r"16\M+9A6B522"


def test_bound_bolt_mark_quantity_cannot_exceed_manufactured_cuts() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.append(CircularCut(Point2D(100.0, 100.0), 10.0))
    assembly.web_plate.provenance["circular_cut_source_ids"] = [["selected-cut"]]
    annotations = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                "4-M20",
                4,
                20.0,
                "mark",
                "excess-quantity-mark",
                target_source_ids=("selected-cut",),
                target_region_ids=("region-main",),
            )
        ]
    )

    proof = _proofs(assembly, annotations)["BH.PROOF.ANNOTATION.MARK_AGREEMENT"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.diagnostic_code == "BH-PROOF-ANNOTATION-CONFLICT"


def test_metadata_candidates_with_equal_score_and_different_profiles_conflict() -> None:
    assembly = _assembly()
    context, annotation = _context(assembly)
    context = replace(
        context,
        metadata_candidates=(
            {"profile": "BH300*200*8*12", "row": ["BH-A"], "score": 100.0},
            {"profile": "BH400*200*8*12", "row": ["BH-B"], "score": 100.0},
        ),
        metadata_margin=0.0,
    )

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.METADATA.UNIQUE"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.diagnostic_code == "BH-PROOF-METADATA-CONFLICT"


def test_metadata_score_margin_cannot_prove_distinct_rows_are_unique() -> None:
    assembly = _assembly()
    context, annotation = _context(assembly)
    context = replace(
        context,
        metadata_candidates=(
            {
                "profile": "BH300*200*8*12",
                "row": ["BH-A", "BH300*200*8*12", "1000", "Q355B"],
                "score": 200.0,
            },
            {
                "profile": "BH400*200*8*12",
                "row": ["BH-B", "BH400*200*8*12", "900", "Q355B"],
                "score": 10.0,
            },
        ),
        metadata_margin=190.0,
    )

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.METADATA.UNIQUE"]

    assert proof.status == ProofStatus.MISSING
    assert proof.diagnostic_code == "BH-PROOF-METADATA-AMBIGUOUS"


def test_large_projection_residual_requires_review_instead_of_score_tuning() -> None:
    assembly = _assembly()
    context, annotation = _context(assembly)
    context = replace(
        context,
        view_pair=replace(
            context.view_pair,
            main_residual=0.20,
            flange_residual=0.10,
            prior_cost=0.30,
        ),
    )

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.VIEW.PROJECTION_CORRESPONDENCE"]

    assert proof.status == ProofStatus.MISSING
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-PROJECTION-FIT-INSUFFICIENT"


def test_bound_overall_dimension_can_prove_cross_view_longitudinal_correspondence() -> None:
    assembly = _assembly()
    observations = AnnotationModel(
        dimensions=[
            DimensionObservation(
                text="1000",
                value=1000.0,
                chain_count=None,
                chain_pitch=None,
                orientation="horizontal",
                block_name="drawing_graph",
                block_handle="dimension-node",
                bbox=None,
                source_ids=("dimension-text", "dimension-line"),
                association_edge_ids=("measurement-edge",),
                target_node_id="view-region",
                target_region_id="region-main",
                scope="view_extent",
                property_type="longitudinal_extent",
                residual_mm=0.0,
                strength="geometric",
            )
        ]
    )
    context, annotation = _context(assembly, observations)
    context = replace(
        context,
        view_pair=replace(
            context.view_pair,
            main_residual=0.20,
            flange_residual=0.10,
            prior_cost=0.30,
        ),
    )

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.VIEW.PROJECTION_CORRESPONDENCE"]

    assert proof.status == ProofStatus.PASS
    assert proof.diagnostic_code is None
    assert any(item.channel == "dimension" for item in proof.evidence)


def test_dimension_bound_to_unselected_view_cannot_prove_selected_projection_pair() -> None:
    assembly = _assembly()
    observations = AnnotationModel(
        dimensions=[
            DimensionObservation(
                text="1000",
                value=1000.0,
                chain_count=None,
                chain_pitch=None,
                orientation="horizontal",
                block_name="drawing_graph",
                block_handle="dimension-node",
                bbox=None,
                source_ids=("dimension-text", "dimension-line"),
                association_edge_ids=("measurement-edge",),
                target_node_id="view-region",
                target_region_id="region-unselected",
                scope="view_extent",
                property_type="longitudinal_extent",
                residual_mm=0.0,
                strength="geometric",
            )
        ]
    )
    context, annotation = _context(assembly, observations)
    context = replace(
        context,
        view_pair=replace(
            context.view_pair,
            main_residual=0.20,
            flange_residual=0.10,
            prior_cost=0.30,
        ),
    )

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.VIEW.PROJECTION_CORRESPONDENCE"]

    assert proof.status == ProofStatus.MISSING
    assert proof.diagnostic_code == "BH-PROOF-PROJECTION-FIT-INSUFFICIENT"


def test_bound_partial_dimension_cannot_override_bad_projection_fit() -> None:
    assembly = _assembly()
    observations = AnnotationModel(
        dimensions=[
            DimensionObservation(
                text="1000",
                value=1000.0,
                chain_count=None,
                chain_pitch=None,
                orientation="horizontal",
                block_name="drawing_graph",
                block_handle="dimension-node",
                bbox=None,
                source_ids=("dimension-text", "dimension-line"),
                association_edge_ids=("measurement-edge",),
                target_node_id="view-region",
                target_region_id="region-main",
                scope="partial_or_untyped",
                property_type="unresolved",
                residual_mm=0.0,
                strength="geometric",
            )
        ]
    )
    context, annotation = _context(assembly, observations)
    context = replace(
        context,
        view_pair=replace(
            context.view_pair,
            main_residual=0.20,
            flange_residual=0.10,
            prior_cost=0.30,
        ),
    )

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.VIEW.PROJECTION_CORRESPONDENCE"]

    assert proof.status == ProofStatus.MISSING
    assert proof.diagnostic_code == "BH-PROOF-PROJECTION-FIT-INSUFFICIENT"


def test_coincidental_untyped_number_cannot_claim_nominal_length_semantics() -> None:
    assembly = _assembly()
    observation = DimensionObservation(
        text="1000",
        value=1000.0,
        chain_count=None,
        chain_pitch=None,
        orientation="horizontal",
        block_name="drawing_graph",
        block_handle="partial-dimension",
        bbox=None,
        target_node_id="view-region",
        target_region_id="region-main",
        scope="partial_or_untyped",
        property_type="unresolved",
        association_edge_ids=("partial-edge",),
        residual_mm=0.0,
        strength="geometric",
    )

    consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        AnnotationModel(dimensions=[observation]),
    )

    assert not consistency["nominal_length_dimension_seen"]
    assert consistency["relations"]["nominal_length"]["status"] == "missing"


def test_profile_height_requires_transverse_extent_of_selected_web_view() -> None:
    assembly = _assembly()
    flange_dimension = DimensionObservation(
        text="300",
        value=300.0,
        chain_count=None,
        chain_pitch=None,
        orientation="vertical",
        block_name="drawing_graph",
        block_handle="flange-transverse-dimension",
        bbox=None,
        target_node_id="view-region",
        target_region_id="region-flange",
        scope="view_extent",
        property_type="transverse_envelope",
        association_edge_ids=("transverse-edge",),
        residual_mm=0.0,
        strength="geometric",
    )
    web_dimension = replace(
        flange_dimension,
        block_handle="web-transverse-dimension",
        target_region_id="region-main",
    )

    flange_consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        AnnotationModel(dimensions=[flange_dimension]),
    )
    web_consistency = annotation_consistency(
        assembly.metadata,
        assembly,
        AnnotationModel(dimensions=[web_dimension]),
    )

    assert not flange_consistency["profile_height_dimension_seen"]
    assert web_consistency["profile_height_dimension_seen"]


def test_bound_pitch_chain_is_checked_against_manufactured_hole_pattern() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.extend(
        CircularCut(Point2D(100.0, y), 10.0)
        for y in (50.0, 130.0, 210.0)
    )
    assembly.web_plate.provenance["circular_cut_source_ids"] = [
        [f"cut-{index}"] for index in range(3)
    ]
    observation = DimensionObservation(
        text="2x80",
        value=None,
        chain_count=2,
        chain_pitch=80.0,
        orientation="vertical",
        block_name="drawing_graph",
        block_handle="pitch-chain",
        bbox=None,
        source_ids=("chain-text", "pitch-line"),
        association_edge_ids=("chain-edge",),
        target_node_id="view-region",
        target_region_id="region-main",
        scope="pitch_chain",
        anchor_source_ids=("cut-0",),
        anchor_residual_mm=0.0,
        anchor_tolerance_mm=1.0,
        anchor_count=1,
    )
    annotations = AnnotationModel(dimensions=[observation])

    relation = annotation_consistency(
        assembly.metadata,
        assembly,
        annotations,
    )["relations"]["hole_pitch_chains"]

    assert relation["status"] == "pass"
    assert relation["supported_count"] == 1


def test_pitch_chain_is_checked_only_against_its_owned_view() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.extend(
        CircularCut(Point2D(100.0, y), 10.0)
        for y in (50.0, 130.0, 210.0)
    )
    assembly.web_plate.provenance["circular_cut_source_ids"] = [
        [f"cut-{index}"] for index in range(3)
    ]
    observation = DimensionObservation(
        text="2x80",
        value=None,
        chain_count=2,
        chain_pitch=80.0,
        orientation="vertical",
        block_name="drawing_graph",
        block_handle="pitch-chain",
        bbox=None,
        source_ids=("chain-text", "pitch-line"),
        association_edge_ids=("chain-edge",),
        target_node_id="view-region",
        target_region_id="region-unselected",
        scope="pitch_chain",
        anchor_source_ids=("cut-0",),
        anchor_residual_mm=0.0,
        anchor_tolerance_mm=1.0,
        anchor_count=1,
    )

    relation = annotation_consistency(
        assembly.metadata,
        assembly,
        AnnotationModel(dimensions=[observation]),
    )["relations"]["hole_pitch_chains"]

    assert relation["status"] == "not_observed"
    assert relation["observed_count"] == 0
    assert relation["ignored_unselected_count"] == 1


def test_pitch_chain_can_follow_tekla_equal_dimensions_across_lanes() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.extend(
        (
            CircularCut(Point2D(100.0, 50.0), 10.0),
            CircularCut(Point2D(200.0, 130.0), 10.0),
            CircularCut(Point2D(100.0, 210.0), 10.0),
        )
    )
    assembly.web_plate.provenance["circular_cut_source_ids"] = [
        [f"cut-{index}"] for index in range(3)
    ]
    observation = DimensionObservation(
        text="2x80",
        value=None,
        chain_count=2,
        chain_pitch=80.0,
        orientation="vertical",
        block_name="drawing_graph",
        block_handle="pitch-chain",
        bbox=None,
        source_ids=("chain-text", "pitch-line"),
        association_edge_ids=("chain-edge",),
        target_node_id="view-region",
        target_region_id="region-main",
        scope="pitch_chain",
        anchor_source_ids=("cut-0",),
        anchor_residual_mm=0.0,
        anchor_tolerance_mm=1.0,
        anchor_count=1,
    )

    relation = annotation_consistency(
        assembly.metadata,
        assembly,
        AnnotationModel(dimensions=[observation]),
    )["relations"]["hole_pitch_chains"]

    assert relation["status"] == "pass"
    assert relation["resolved_count"] == 1
    assert relation["supported_count"] == 1


def test_pitch_chain_cannot_borrow_an_unanchored_equal_spacing_sequence() -> None:
    assembly = _assembly()
    assembly.web_plate.circular_cuts.extend(
        (
            CircularCut(Point2D(100.0, 0.0), 10.0),
            CircularCut(Point2D(200.0, 50.0), 10.0),
            CircularCut(Point2D(300.0, 130.0), 10.0),
            CircularCut(Point2D(400.0, 210.0), 10.0),
        )
    )
    assembly.web_plate.provenance["circular_cut_source_ids"] = [
        [f"cut-{index}"] for index in range(4)
    ]
    observation = DimensionObservation(
        text="2x80",
        value=None,
        chain_count=2,
        chain_pitch=80.0,
        orientation="vertical",
        block_name="drawing_graph",
        block_handle="pitch-chain",
        bbox=None,
        source_ids=("chain-text", "pitch-line"),
        association_edge_ids=("chain-edge",),
        target_node_id="view-region",
        target_region_id="region-main",
        scope="pitch_chain",
        anchor_source_ids=("cut-0",),
        anchor_residual_mm=0.0,
        anchor_tolerance_mm=1.0,
        anchor_count=1,
    )

    relation = annotation_consistency(
        assembly.metadata,
        assembly,
        AnnotationModel(dimensions=[observation]),
    )["relations"]["hole_pitch_chains"]

    assert relation["status"] == "conflict"


def test_bound_pitch_chain_conflict_blocks_automatic_output() -> None:
    assembly = _assembly()
    observation = DimensionObservation(
        text="2x80",
        value=None,
        chain_count=2,
        chain_pitch=80.0,
        orientation="vertical",
        block_name="drawing_graph",
        block_handle="pitch-chain",
        bbox=None,
        source_ids=("chain-text", "pitch-line"),
        association_edge_ids=("chain-edge",),
        target_node_id="view-region",
        target_region_id="region-main",
        scope="pitch_chain",
        anchor_source_ids=("missing-selected-cut",),
        anchor_residual_mm=0.0,
        anchor_tolerance_mm=1.0,
        anchor_count=1,
    )

    proof = _proofs(
        assembly,
        AnnotationModel(dimensions=[observation]),
    )["BH.PROOF.ANNOTATION.DIMENSION_AGREEMENT"]

    assert proof.status == ProofStatus.CONFLICT
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-DIMENSION-CONFLICT"


def test_filename_or_block_fallback_cannot_prove_metadata_for_automatic_output() -> None:
    assembly = _assembly()
    context, annotation = _context(assembly)
    context = replace(context, metadata_fallback_fields=("part_number",))

    proof = {
        item.obligation_id: item
        for item in build_proof_obligations(context, annotation)
    }["BH.PROOF.METADATA.UNIQUE"]

    assert proof.status == ProofStatus.MISSING
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-METADATA-FALLBACK"


def test_inferred_flange_development_without_dimension_requires_review() -> None:
    assembly = _assembly()
    assembly.diagnostics["flange_development"] = {
        "mode": "constant_height_cranked_path",
        "raw_lengths_mm": [11294.912807],
        "target_lengths_mm": [11294.912807],
    }

    proof = _proofs(assembly)["BH.PROOF.FLANGE.DEVELOPMENT"]

    assert proof.status == ProofStatus.MISSING
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-FLANGE-DEVELOPMENT-UNCONFIRMED"


def test_inferred_flange_development_passes_only_with_bound_length_for_every_geometry() -> None:
    assembly = _assembly()
    assembly.diagnostics["flange_development"] = {
        "mode": "constant_height_cranked_path",
        "target_lengths_mm": [1000.0],
    }
    observation = DimensionObservation(
        text="1000",
        value=1000.0,
        chain_count=None,
        chain_pitch=None,
        orientation="horizontal",
        block_name="drawing_graph",
        block_handle="developed-flange-length",
        bbox=None,
        source_ids=("dimension-source",),
        association_edge_ids=("development-edge",),
        target_node_id="flange-view",
        target_region_id="region-flange",
        scope="view_extent",
        property_type="longitudinal_extent",
        target_count=1,
        residual_mm=0.0,
        strength="geometric",
    )

    proof = _proofs(
        assembly,
        AnnotationModel(dimensions=[observation]),
    )["BH.PROOF.FLANGE.DEVELOPMENT"]

    assert proof.status == ProofStatus.PASS
    assert any(item.evidence_id == "development-edge" for item in proof.evidence)


def test_one_dimension_cannot_authorize_two_distinct_flange_developments() -> None:
    assembly = _assembly()
    assembly.flange_plates[0].quantity = 1
    second = BHPlate(
        BHPlateRole.FLANGE,
        _rectangle(1100.0, 200.0),
        12.0,
        "BH-TEST-1-翼缘板-2",
        area_mm2=220000.0,
        provenance=_provenance("flange"),
    )
    assembly.flange_plates.append(second)
    assembly.diagnostics["flange_development"] = {
        "mode": "variable_height_two_paths",
        "target_lengths_mm": [1000.0, 1100.0],
    }
    observation = DimensionObservation(
        text="1000",
        value=1000.0,
        chain_count=None,
        chain_pitch=None,
        orientation="horizontal",
        block_name="drawing_graph",
        block_handle="only-one-developed-length",
        bbox=None,
        source_ids=("dimension-source",),
        association_edge_ids=("one-development-edge",),
        target_node_id="flange-view",
        target_region_id="region-flange",
        scope="view_extent",
        property_type="longitudinal_extent",
        target_count=1,
        residual_mm=0.0,
        strength="geometric",
    )

    proof = _proofs(
        assembly,
        AnnotationModel(dimensions=[observation]),
    )["BH.PROOF.FLANGE.DEVELOPMENT"]

    assert proof.status == ProofStatus.MISSING
    assert proof.diagnostic_code == "BH-PROOF-FLANGE-DEVELOPMENT-UNCONFIRMED"


def test_second_equally_coherent_material_row_is_rejected_end_to_end() -> None:
    source_path = (
        Path(__file__).resolve().parents[2]
        / "samples"
        / "bh_pairs"
        / "2b1-cb-26_拆板前.dxf"
    )
    doc = load_document(source_path)
    baseline = compile_bh_document(
        doc,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source_path,
    )
    metadata = baseline.assembly.metadata
    alternate_row = (
        ("BH-ALT-1", 0.0),
        (metadata.profile.raw_text, 200.0),
        (str(metadata.nominal_length), 500.0),
        (metadata.material or "Q355B", 650.0),
        ("1:20", 800.0),
        ("ALT", 900.0),
    )
    for value, x in alternate_row:
        doc.modelspace().add_text(
            value,
            dxfattribs={"insert": (x, 100000.0), "height": 20.0},
        )

    with pytest.raises(BHCompilationRejected) as captured:
        compile_bh_document(
            doc,
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=source_path,
        )

    assert set(captured.value.diagnostic_codes) == {
        "BH-PROOF-ANNOTATION-CONFLICT",
        "BH-PROOF-METADATA-CONFLICT",
    }
    metadata_proof = next(
        item
        for item in captured.value.proof_report.obligations
        if item.obligation_id == "BH.PROOF.METADATA.UNIQUE"
    )
    assert metadata_proof.status == ProofStatus.CONFLICT
