from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Any

from .bh_annotations import AnnotationModel, annotation_consistency
from .bh_canonical import resolve_units
from .bh_dimensions import displayed_dimension_tolerance
from .bh_hypothesis import RuleEvaluation, ViewPairHypothesis
from .bh_ir import BHDocumentIR, SemanticLayer
from .bh_knowledge import BHKnowledgeBase
from .bh_models import BHAssembly
from .bh_proofs import ProofEvidence, ProofObligation, ProofStatus
from .bh_source import SourceDocument
from .bh_validator import BHValidationReport


@dataclass(frozen=True, slots=True)
class ConstraintContext:
    assembly: BHAssembly
    validation: BHValidationReport
    view_pair: ViewPairHypothesis
    annotations: AnnotationModel
    knowledge: BHKnowledgeBase
    source_ir: SourceDocument | None = None
    lowering_ir: BHDocumentIR | None = None
    metadata_candidates: tuple[dict[str, Any], ...] = ()
    metadata_margin: float = 0.0
    metadata_source_ids: tuple[str, ...] = ()
    metadata_fallback_fields: tuple[str, ...] = ()


def _evidence(
    evidence_id: str,
    channel: str,
    *,
    source_ids: tuple[str, ...] = (),
    measured: float | str | None = None,
    expected: float | str | None = None,
    tolerance: float | None = None,
) -> ProofEvidence:
    return ProofEvidence(
        evidence_id=evidence_id,
        channel=channel,
        source_ids=tuple(sorted(set(source_ids))),
        measured=measured,
        expected=expected,
        tolerance=tolerance,
    )


def _validation_evidence(
    context: ConstraintContext,
    channel: str,
    keys: tuple[str, ...],
) -> tuple[ProofEvidence, ...]:
    return tuple(
        _evidence(
            f"validation:{key}",
            channel,
            measured=str(bool(context.validation.checks.get(key, False))).lower(),
            expected="true",
        )
        for key in keys
    )


def _candidate_view_residual(
    width: float,
    height: float,
    *,
    longitudinal: float,
    transverse: float,
) -> float:
    direct = abs(width - longitudinal) / max(longitudinal, 1.0) + abs(
        height - transverse
    ) / max(transverse, 1.0)
    rotated = abs(height - longitudinal) / max(longitudinal, 1.0) + abs(
        width - transverse
    ) / max(transverse, 1.0)
    return min(direct, rotated)


def _projection_dimension_evidence(
    context: ConstraintContext,
) -> tuple[ProofEvidence, ...]:
    """Find a bound dimension that independently joins both projections.

    The dimension must be typed by the drawing graph, aligned with the chosen
    member axis, and agree with the longitudinal extent of both selected views
    at the precision actually displayed in the drawing.  A partial dimension,
    a free numeric token, or a dimension attached only by proximity cannot
    satisfy this relation.
    """

    main_axis = context.view_pair.main_axis
    flange_axis = context.view_pair.flange_axis
    if main_axis != flange_axis or main_axis not in {"x", "y"}:
        return ()
    expected_orientation = "horizontal" if main_axis == "x" else "vertical"
    main_extent = (
        context.view_pair.main.bbox.width
        if main_axis == "x"
        else context.view_pair.main.bbox.height
    )
    flange_extent = (
        context.view_pair.flange.bbox.width
        if flange_axis == "x"
        else context.view_pair.flange.bbox.height
    )
    selected_region_ids = {
        region_id
        for region_id in (
            getattr(context.view_pair.main, "region_id", None),
            getattr(context.view_pair.flange, "region_id", None),
        )
        if region_id is not None
    }
    if not selected_region_ids:
        return ()
    evidence: list[ProofEvidence] = []
    for observation in context.annotations.dimensions:
        if (
            observation.value is None
            or observation.orientation != expected_orientation
            or observation.target_node_id is None
            or observation.target_region_id not in selected_region_ids
            or observation.scope != "view_extent"
            or observation.property_type != "longitudinal_extent"
            or observation.target_count != 1
            or observation.strength not in {"explicit", "geometric"}
            or not observation.association_edge_ids
        ):
            continue
        tolerance = displayed_dimension_tolerance(
            observation.text,
            geometric_tolerance_mm=context.knowledge.manufacturing_tolerance_mm,
        )
        if observation.residual_mm is None or observation.residual_mm > tolerance:
            continue
        main_residual = abs(observation.value - main_extent)
        flange_residual = abs(observation.value - flange_extent)
        if main_residual > tolerance or flange_residual > tolerance:
            continue
        evidence.append(
            _evidence(
                observation.association_edge_ids[0],
                "dimension",
                source_ids=observation.source_ids,
                measured=(
                    f"dimension={observation.value:.9g};"
                    f"main_extent={main_extent:.9g};"
                    f"flange_extent={flange_extent:.9g}"
                ),
                expected="one bound longitudinal dimension agrees with both selected projections",
                tolerance=tolerance,
            )
        )
    return tuple(evidence)


def _flange_development_dimension_evidence(
    context: ConstraintContext,
) -> tuple[tuple[ProofEvidence, ...], int, int]:
    """Require source-bound total length evidence for every distinct development."""

    targets: list[float] = []
    for plate in context.assembly.flange_plates:
        value = plate.bbox.width
        if not any(
            abs(value - existing) <= context.knowledge.manufacturing_tolerance_mm
            for existing in targets
        ):
            targets.append(value)
    target_regions = {
        str(region_id)
        for plate in context.assembly.flange_plates
        if (region_id := plate.provenance.get("source_region_id"))
    }
    if not targets or not target_regions:
        return (), 0, len(targets)
    evidence: list[ProofEvidence] = []
    covered = 0
    for target in targets:
        matches = []
        for observation in context.annotations.dimensions:
            if (
                observation.value is None
                or observation.orientation != "horizontal"
                or observation.scope != "view_extent"
                or observation.property_type != "longitudinal_extent"
                or observation.target_region_id not in target_regions
                or observation.target_count != 1
                or observation.target_node_id is None
                or observation.strength not in {"explicit", "geometric"}
                or not observation.association_edge_ids
                or observation.residual_mm is None
            ):
                continue
            tolerance = displayed_dimension_tolerance(
                observation.text,
                geometric_tolerance_mm=context.knowledge.manufacturing_tolerance_mm,
            )
            if (
                observation.residual_mm <= tolerance
                and abs(observation.value - target) <= tolerance
            ):
                matches.append((observation, tolerance))
        if not matches:
            continue
        observation, tolerance = min(
            matches,
            key=lambda item: (
                abs(float(item[0].value) - target),
                item[0].association_edge_ids[0],
            ),
        )
        covered += 1
        evidence.append(
            _evidence(
                observation.association_edge_ids[0],
                "flange_development_dimension",
                source_ids=observation.source_ids,
                measured=observation.value,
                expected=target,
                tolerance=tolerance,
            )
        )
    return tuple(evidence), covered, len(targets)


def _flange_development_semantic_evidence(
    context: ConstraintContext,
) -> tuple[ProofEvidence, ...]:
    development = context.assembly.diagnostics.get("flange_development", {}) or {}
    assessment = development.get("semantic_assessment", {}) or {}
    source_view = development.get("source_view", {}) or {}
    source_ids = tuple(map(str, source_view.get("source_entity_ids", ()) or ()))
    source_region_id = str(
        source_view.get("source_region_id") or "assembly:flange-development"
    )
    targets = tuple(
        float(value) for value in development.get("target_lengths_mm", ())
    )
    evidence: list[ProofEvidence] = []
    determinacy = str(assessment.get("geometry_determinacy") or "")
    profile_authorized = bool(assessment.get("profile_authorized"))
    certificate_kind = str(assessment.get("certificate_kind") or "")
    if profile_authorized and certificate_kind in {
        "profile_authorized_rigid_development",
        "profile_authorized_cranked_development",
    }:
        evidence.append(
            _evidence(
                f"{source_region_id}:{certificate_kind}",
                certificate_kind,
                source_ids=source_ids,
                measured=(
                    f"raw={assessment.get('raw_lengths_mm')};"
                    f"quantized={assessment.get('quantized_lengths_mm')};"
                    f"candidates={assessment.get('candidate_count')};"
                    f"matches={assessment.get('match_count')}"
                ),
                expected=(
                    "source-backed unique development under the approved "
                    "Tekla BH fabrication-length policy"
                ),
                tolerance=context.knowledge.manufacturing_tolerance_mm,
            )
        )
    elif determinacy == "rigid_projection_determined":
        evidence.append(
            _evidence(
                f"{source_region_id}:rigid-flange-path",
                "rigid_projection_geometry",
                source_ids=source_ids,
                measured=str(targets),
                expected=(
                    "constant-thickness straight strips determine geometric path "
                    "length; fabrication total/precision remains separately bound"
                ),
                tolerance=context.knowledge.manufacturing_tolerance_mm,
            )
        )
    elif determinacy == "unfolding_policy_required":
        evidence.append(
            _evidence(
                f"{source_region_id}:kinked-flange-path",
                "unfolding_policy_gap",
                source_ids=source_ids,
                measured=str(targets),
                expected=(
                    "explicit total length or exported unfolding/reference-line policy"
                ),
                tolerance=context.knowledge.manufacturing_tolerance_mm,
            )
        )

    consistency = str(assessment.get("part_length_consistency") or "")
    if consistency in {
        "consistent_supporting_only",
        "partial_supporting_only",
    }:
        evidence.append(
            _evidence(
                "metadata:tekla-part-length-consistency",
                "tekla_part_length_consistency",
                source_ids=context.metadata_source_ids,
                measured=context.assembly.metadata.nominal_length,
                expected=(
                    "targets="
                    f"{assessment.get('part_length_compared_lengths_mm', targets)};"
                    "supporting-only because Tekla polybeam "
                    "length semantics are model-configuration dependent"
                ),
                tolerance=float(
                    assessment.get("part_length_tolerance_mm")
                    or context.knowledge.manufacturing_tolerance_mm
                ),
            )
        )
    return tuple(evidence)


def _unclassified_part_like_blocks(
    context: ConstraintContext,
) -> tuple[tuple[str, str, float, tuple[str, ...]], ...]:
    ir = context.lowering_ir
    if ir is None:
        return ()
    metadata = context.assembly.metadata
    profile = metadata.profile
    unresolved: list[tuple[str, str, float, tuple[str, ...]]] = []
    for block in ir.blocks:
        geometry = [
            atom
            for atom in block.entities
            if atom.entity.dxftype() in {"LINE", "ARC"}
            # ``OtherObjectType`` is a classified annotation/context role in
            # the authorized Tekla dialect.  Only genuinely unmapped geometry
            # can still hide an unenumerated physical projection.
            and atom.semantic_layer == SemanticLayer.UNKNOWN
            and atom.bbox is not None
        ]
        if len(geometry) < 4:
            continue
        boxes = [atom.bbox for atom in geometry if atom.bbox is not None]
        min_x = min(box.min_x for box in boxes)
        min_y = min(box.min_y for box in boxes)
        max_x = max(box.max_x for box in boxes)
        max_y = max(box.max_y for box in boxes)
        width = max_x - min_x
        height = max_y - min_y
        residual = min(
            _candidate_view_residual(
                width,
                height,
                longitudinal=metadata.nominal_length,
                transverse=profile.max_height,
            ),
            _candidate_view_residual(
                width,
                height,
                longitudinal=metadata.nominal_length,
                transverse=profile.flange_width,
            ),
        )
        if residual > context.knowledge.candidate_universe_residual_limit:
            continue
        unresolved.append(
            (
                block.name,
                block.handle,
                residual,
                tuple(atom.source.stable_id for atom in geometry),
            )
        )
    return tuple(sorted(unresolved, key=lambda item: (item[0].casefold(), item[1])))


def build_proof_obligations(
    context: ConstraintContext,
    annotation: dict[str, Any],
) -> tuple[ProofObligation, ...]:
    """Build safety proofs from typed engineering relations.

    Scores remain useful for ordering valid alternatives, but every production
    disposition is derived from these obligations.  Optional drawing
    annotations are asymmetric: absence is recorded without blocking output;
    an observed contradiction is critical.
    """

    obligations: list[ProofObligation] = []
    source_contract = context.knowledge.source_contract
    try:
        source_contract.validate(context.knowledge.dialect)
        source_contract_valid = True
    except ValueError:
        # Normal compilation rejects this at the compiler boundary.  Keeping
        # the proof fail-closed also protects direct constraint-layer callers.
        source_contract_valid = False
    obligations.append(
        ProofObligation(
            "BH.PROOF.SOURCE.TEKLA_SINGLE_PART_CONTRACT",
            ProofStatus.PASS if source_contract_valid else ProofStatus.CONFLICT,
            True,
            (
                _evidence(
                    "workflow:tekla-single-part-welded-bh",
                    "workflow_source_contract",
                    measured=(
                        f"source_system={source_contract.source_system};"
                        f"drawing_kind={source_contract.drawing_kind};"
                        f"member_family={source_contract.member_family};"
                        f"export_profile={source_contract.export_profile}"
                    ),
                    expected=(
                        "tekla_structures;single_part_drawing;welded_bh;"
                        f"dialect={context.knowledge.dialect.profile_id}"
                    ),
                ),
            ),
            (
                None
                if source_contract_valid
                else "BH-PROOF-SOURCE-CONTRACT-CONFLICT"
            ),
        )
    )
    from .bh_release_evidence import (
        resolve_release_evidence,
        trusted_release_profile_ids,
    )

    release_evidence = resolve_release_evidence(
        source_contract,
        context.knowledge.dialect,
        context.knowledge.ontology_version,
    )
    release_profile_verified = release_evidence is not None
    obligations.append(
        ProofObligation(
            "BH.PROOF.SOURCE.RELEASE_PROFILE_VERIFIED",
            (
                ProofStatus.PASS
                if release_profile_verified
                else ProofStatus.MISSING
            ),
            True,
            (
                _evidence(
                    f"workflow:release-profile:{source_contract.export_profile}",
                    "release_capability_profile",
                    measured=(
                        release_evidence.capability_artifact_sha256
                        if release_evidence is not None
                        else "no_matching_code_pinned_artifact"
                    ),
                    expected=";".join(trusted_release_profile_ids()),
                ),
            ),
            (
                None
                if release_profile_verified
                else "BH-PROOF-SOURCE-PROFILE-UNVERIFIED"
            ),
        )
    )
    ir = context.lowering_ir
    unit = resolve_units(ir.units) if ir is not None else None
    input_valid = bool(
        context.source_ir is not None
        and not context.source_ir.audit_errors
        and ir is not None
        and ir.audit_error_count == 0
        and unit is not None
        and unit.valid
    )
    obligations.append(
        ProofObligation(
            "BH.PROOF.INPUT.INTEGRITY",
            ProofStatus.PASS if input_valid else ProofStatus.INCOMPLETE,
            True,
            (
                _evidence(
                    "source:input-integrity",
                    "source_ir",
                    measured=(
                        f"units={ir.units};audit_errors={ir.audit_error_count}"
                        if ir is not None
                        else "source_ir_missing"
                    ),
                    expected="known_units;audit_errors=0",
                ),
            ),
            None if input_valid else "BH-PROOF-INPUT-INCOMPLETE",
        )
    )

    unresolved_candidates = _unclassified_part_like_blocks(context)
    candidate_universe_status = (
        ProofStatus.MISSING if unresolved_candidates else ProofStatus.PASS
    )
    obligations.append(
        ProofObligation(
            "BH.PROOF.SEARCH.CANDIDATE_UNIVERSE",
            candidate_universe_status,
            True,
            tuple(
                _evidence(
                    f"candidate-universe:{handle}",
                    "semantic_classification",
                    source_ids=source_ids,
                    measured=f"block={name};residual={residual:.9g}",
                    expected="classified Part projection or conclusively non-physical geometry",
                    tolerance=context.knowledge.candidate_universe_residual_limit,
                )
                for name, handle, residual, source_ids in unresolved_candidates
            )
            or (
                _evidence(
                    "candidate-universe:classified",
                    "semantic_classification",
                    measured="no unresolved part-like block",
                    expected="all plausible projection blocks classified",
                ),
            ),
            (
                "BH-PROOF-CANDIDATE-UNIVERSE-AMBIGUOUS"
                if unresolved_candidates
                else None
            ),
        )
    )

    candidates = context.metadata_candidates
    if not candidates:
        metadata_status = ProofStatus.INCOMPLETE
        metadata_code = "BH-PROOF-METADATA-INCOMPLETE"
    elif context.metadata_fallback_fields:
        metadata_status = ProofStatus.MISSING
        metadata_code = "BH-PROOF-METADATA-FALLBACK"
    else:
        competing_semantics = {
            (
                str(item.get("profile") or ""),
                tuple(str(token) for token in item.get("row", ())),
            )
            for item in candidates
        }
        if len(competing_semantics) == 1:
            metadata_status = ProofStatus.PASS
            metadata_code = None
        elif context.metadata_margin <= 1e-9:
            metadata_status = ProofStatus.CONFLICT
            metadata_code = "BH-PROOF-METADATA-CONFLICT"
        else:
            metadata_status = ProofStatus.MISSING
            metadata_code = "BH-PROOF-METADATA-AMBIGUOUS"
    obligations.append(
        ProofObligation(
            "BH.PROOF.METADATA.UNIQUE",
            metadata_status,
            True,
            (
                _evidence(
                    "metadata:row-candidates",
                    "material_table",
                    source_ids=context.metadata_source_ids,
                    measured=(
                        f"candidates={len(candidates)};"
                        f"margin={context.metadata_margin:.6g};"
                        f"fallbacks={','.join(context.metadata_fallback_fields) or 'none'}"
                    ),
                    expected="one coherent BH metadata row",
                ),
            ),
            metadata_code,
        )
    )

    assembly = context.assembly
    validation = context.validation.checks
    per_plate_features = list(
        context.validation.values.get("per_plate_features", [])
    )
    thickness_evidence = tuple(
        _evidence(
            (
                "metadata:profile:web-thickness"
                if plate.role.value == "web"
                else "metadata:profile:flange-thickness"
            ),
            "profile_and_plate",
            source_ids=context.metadata_source_ids,
            measured=plate.thickness,
            expected=(
                assembly.metadata.profile.web_thickness
                if plate.role.value == "web"
                else assembly.metadata.profile.flange_thickness
            ),
            tolerance=1e-6,
        )
        for index, plate in enumerate(assembly.plates)
    )
    topology_evidence = tuple(
        _evidence(
            f"plate:{index}:contour-topology",
            "plate_topology",
            source_ids=(
                str(plate.provenance.get("source_insert_handle") or ""),
            ),
            measured=str(
                {
                    "closed": plate.contour.closed,
                    "positive_area": plate.area_mm2 > 0.0,
                    **(
                        per_plate_features[index].get("geometry_checks", {})
                        if index < len(per_plate_features)
                        else {}
                    ),
                }
            ),
            expected="closed valid material contour with contained non-overlapping openings",
        )
        for index, plate in enumerate(assembly.plates)
    )
    cut_evidence = tuple(
        _evidence(
            f"plate:{index}:cuts",
            "manufacturing_cuts",
            source_ids=tuple(
                str(value)
                for value in plate.provenance.get("cut_source_blocks", [])
            ),
            measured=str(
                (
                    per_plate_features[index].get("geometry_checks", {})
                    if index < len(per_plate_features)
                    else {}
                )
            ),
            expected="all cuts contained, unique and non-overlapping",
        )
        for index, plate in enumerate(assembly.plates)
    )
    decomposition_ok = bool(
        validation.get("one_web_plate")
        and validation.get("one_or_two_flange_geometries")
        and validation.get("two_physical_flange_plates")
    )
    obligations.append(
        ProofObligation(
            "BH.PROOF.ROLE.DECOMPOSITION",
            ProofStatus.PASS if decomposition_ok else ProofStatus.CONFLICT,
            True,
            _validation_evidence(
                context,
                "physical_decomposition_validation",
                (
                    "one_web_plate",
                    "one_or_two_flange_geometries",
                    "two_physical_flange_plates",
                ),
            ),
            None if decomposition_ok else "BH-PROOF-BH-DECOMPOSITION-CONFLICT",
        )
    )

    thickness_ok = bool(validation.get("plate_thickness_matches_profile"))
    obligations.append(
        ProofObligation(
            "BH.PROOF.PLATE.THICKNESS",
            ProofStatus.PASS if thickness_ok else ProofStatus.CONFLICT,
            True,
            thickness_evidence,
            None if thickness_ok else "BH-PROOF-PLATE-THICKNESS-CONFLICT",
        )
    )

    topology_keys = (
        "closed_outer_contours",
        "positive_contour_areas",
        "valid_outer_and_material_polygons",
        "inner_contours_valid_and_contained",
    )
    topology_ok = all(bool(validation.get(key)) for key in topology_keys)
    obligations.append(
        ProofObligation(
            "BH.PROOF.CONTOUR.TOPOLOGY",
            ProofStatus.PASS if topology_ok else ProofStatus.CONFLICT,
            True,
            topology_evidence,
            None if topology_ok else "BH-PROOF-CONTOUR-INVALID",
        )
    )

    conservation = (
        assembly.diagnostics.get("projection_source_edge_conservation", {}) or {}
    )
    conservation_assessments = [
        item
        for item in conservation.get("assessments", ()) or ()
        if isinstance(item, dict)
    ]
    selected_boundary_assessments = [
        item
        for item in conservation_assessments
        if item.get("repair_kind") == "selected_projection_boundary"
    ]
    lost_source_ids = tuple(
        sorted(
            {
                str(source_id)
                for item in conservation_assessments
                if item.get("repair_kind") == "selected_projection_boundary"
                or item.get("applied") is True
                for source_id in item.get("lost_source_ids", ()) or ()
            }
        )
    )
    if not selected_boundary_assessments:
        conservation_status = ProofStatus.MISSING
        conservation_code = "BH-PROOF-PROJECTION-SOURCE-EDGE-UNOBSERVED"
    elif lost_source_ids:
        conservation_status = ProofStatus.CONFLICT
        conservation_code = "BH-PROOF-PROJECTION-SOURCE-EDGE-LOSS"
    else:
        conservation_status = ProofStatus.PASS
        conservation_code = None
    obligations.append(
        ProofObligation(
            "BH.PROOF.PROJECTION.SOURCE_EDGE_CONSERVATION",
            conservation_status,
            True,
            tuple(
                _evidence(
                    (
                        "projection-boundary:"
                        f"{index:02d}:{item.get('repair_kind') or 'assessment'}"
                    ),
                    "projection_source_edge_conservation",
                    source_ids=tuple(
                        str(source_id)
                        for source_id in item.get("protected_source_ids", ()) or ()
                    ),
                    measured=(
                        f"reason={item.get('reason') or 'unknown'};"
                        "lost="
                        + ",".join(
                            str(source_id)
                            for source_id in item.get("lost_source_ids", ()) or ()
                        )
                        + ";reclassified_projection_overlay="
                        + ",".join(
                            str(source_id)
                            for source_id in item.get(
                                "reclassified_source_ids", ()
                            )
                            or ()
                        )
                    ),
                    expected="all associated direct DXF boundary edges conserved",
                    tolerance=float(item.get("fidelity_tolerance_mm") or 1e-7),
                )
                for index, item in enumerate(conservation_assessments)
            ),
            conservation_code,
        )
    )

    cut_keys = (
        "all_circular_cuts_fit_material",
        "no_duplicate_cut_centers_within_plate",
        "circular_cuts_do_not_overlap",
    )
    cuts_ok = all(bool(validation.get(key)) for key in cut_keys)
    obligations.append(
        ProofObligation(
            "BH.PROOF.CUT.CONTAINMENT",
            ProofStatus.PASS if cuts_ok else ProofStatus.CONFLICT,
            True,
            cut_evidence,
            None if cuts_ok else "BH-PROOF-CUT-OUTSIDE-MATERIAL",
        )
    )

    dimension_relations = annotation.get("relations", {}) or {}
    dimension_statuses = {
        str(item.get("status"))
        for item in dimension_relations.values()
        if isinstance(item, dict)
    }
    if "conflict" in dimension_statuses:
        dimension_status = ProofStatus.CONFLICT
        dimension_critical = True
        dimension_code = "BH-PROOF-DIMENSION-CONFLICT"
    elif "pass" in dimension_statuses:
        dimension_status = ProofStatus.PASS
        dimension_critical = True
        dimension_code = None
    else:
        dimension_status = ProofStatus.MISSING
        dimension_critical = False
        dimension_code = "BH-PROOF-DIMENSION-NOT-OBSERVED"
    dimension_evidence = tuple(
        _evidence(
            (
                observation.association_edge_ids[0]
                if observation.association_edge_ids
                else f"annotation:dimension:{index}"
            ),
            "dimension",
            source_ids=observation.source_ids,
            measured=(
                observation.value
                if observation.value is not None
                else f"{observation.chain_count}x{observation.chain_pitch}"
            ),
            expected="typed member property",
            tolerance=1.0,
        )
        for index, observation in enumerate(context.annotations.dimensions)
    )
    obligations.append(
        ProofObligation(
            "BH.PROOF.ANNOTATION.DIMENSION_AGREEMENT",
            dimension_status,
            dimension_critical,
            dimension_evidence,
            dimension_code,
        )
    )

    selected_region_ids = {
        str(region_id)
        for plate in assembly.plates
        if (region_id := plate.provenance.get("source_region_id"))
    }
    selected_bolt_marks = [
        item
        for item in context.annotations.bolt_marks
        if bool(selected_region_ids.intersection(item.target_region_ids))
    ]
    selected_unresolved_bolt_marks = [
        item
        for item in context.annotations.unresolved_bolt_marks
        if bool(selected_region_ids.intersection(item.target_region_ids))
    ]
    selected_part_marks = [
        item
        for item in context.annotations.part_marks
        if item.target_region_id in selected_region_ids
    ]
    marks_observed = bool(
        selected_bolt_marks
        or selected_unresolved_bolt_marks
        or selected_part_marks
    )
    marks_ok = bool(
        annotation.get("bolt_mark_diameters_supported", True)
        and annotation.get("bolt_mark_count_plausible", True)
        and annotation.get("part_mark_matches_metadata", True)
    )
    mark_status = (
        ProofStatus.NOT_APPLICABLE
        if not marks_observed
        else ProofStatus.CONFLICT
        if not marks_ok
        else ProofStatus.MISSING
        if selected_unresolved_bolt_marks
        else ProofStatus.PASS
    )
    mark_evidence = tuple(
        [
            _evidence(
                f"annotation:bolt-mark:{index}:{item.block_handle}",
                "bolt_mark",
                source_ids=item.source_ids,
                measured=f"count={item.count};diameter={item.diameter}",
                expected="owned cuts with matching diameter and plausible count",
            )
            for index, item in enumerate(selected_bolt_marks)
        ]
        + [
            _evidence(
                f"annotation:bolt-mark-unresolved:{index}:{item.block_handle}",
                "bolt_mark_unresolved",
                source_ids=item.source_ids,
                measured=item.raw_text,
                expected="decodable CAD text with explicit diameter semantics",
            )
            for index, item in enumerate(selected_unresolved_bolt_marks)
        ]
        + [
            _evidence(
                f"annotation:part-mark:{index}:{item.block_handle}",
                "part_mark",
                source_ids=item.source_ids,
                measured=item.text,
                expected=assembly.metadata.part_number,
            )
            for index, item in enumerate(selected_part_marks)
        ]
    )
    obligations.append(
        ProofObligation(
            "BH.PROOF.ANNOTATION.MARK_AGREEMENT",
            mark_status,
            marks_observed,
            mark_evidence,
            (
                "BH-PROOF-ANNOTATION-CONFLICT"
                if not marks_ok
                else "BH-PROOF-ANNOTATION-UNRESOLVED"
                if selected_unresolved_bolt_marks
                else None
            ),
        )
    )

    projections_distinct = context.view_pair.main.handle != context.view_pair.flange.handle
    projection_fit = bool(
        context.view_pair.main_residual
        <= context.knowledge.automatic_main_projection_residual
        and context.view_pair.flange_residual
        <= context.knowledge.automatic_flange_projection_residual
        and context.view_pair.prior_cost
        <= context.knowledge.automatic_pair_projection_residual
    )
    projection_dimension_evidence = _projection_dimension_evidence(context)
    projection_correspondence_proven = bool(
        projection_fit or projection_dimension_evidence
    )
    if not projections_distinct:
        projection_status = ProofStatus.CONFLICT
        projection_code = "BH-PROOF-PROJECTION-ROLE-CONFLICT"
    elif not projection_correspondence_proven:
        projection_status = ProofStatus.MISSING
        projection_code = "BH-PROOF-PROJECTION-FIT-INSUFFICIENT"
    else:
        projection_status = ProofStatus.PASS
        projection_code = None
    obligations.append(
        ProofObligation(
            "BH.PROOF.VIEW.PROJECTION_CORRESPONDENCE",
            projection_status,
            True,
            (
                _evidence(
                    "view-pair:selected",
                    "projection_geometry",
                    source_ids=(
                        str(context.view_pair.main.handle),
                        str(context.view_pair.flange.handle),
                    ),
                    measured=(
                        f"main={context.view_pair.main.handle};"
                        f"flange={context.view_pair.flange.handle};"
                        f"main_residual={context.view_pair.main_residual:.9g};"
                        f"flange_residual={context.view_pair.flange_residual:.9g};"
                        f"pair_residual={context.view_pair.prior_cost:.9g}"
                    ),
                    expected=(
                        "distinct views and residuals <= "
                        f"{context.knowledge.automatic_main_projection_residual}/"
                        f"{context.knowledge.automatic_flange_projection_residual}/"
                        f"{context.knowledge.automatic_pair_projection_residual}"
                    ),
                ),
                *projection_dimension_evidence,
            ),
            projection_code,
        )
    )

    metric_scale = context.view_pair.metric_scale
    metric_authorized = (
        metric_scale is None or metric_scale.authorized
    )
    metric_evidence = tuple(
        _evidence(
            f"view-scale:{index:02d}:{item.channel}",
            "uniform_metric_scale",
            source_ids=item.source_ids,
            measured=(
                f"observed={item.observed:.9g};"
                f"factor={item.factor:.9g}"
            ),
            expected=item.expected,
            tolerance=(
                context.knowledge.uniform_scale_policy
                .consensus_relative_tolerance
            ),
        )
        for index, item in enumerate(
            metric_scale.evidence if metric_scale is not None else (),
            start=1,
        )
    )
    obligations.append(
        ProofObligation(
            "BH.PROOF.VIEW.UNIFORM_METRIC_SCALE",
            (
                ProofStatus.PASS
                if metric_authorized
                else ProofStatus.CONFLICT
            ),
            True,
            metric_evidence
            or (
                _evidence(
                    "view-scale:identity",
                    "uniform_metric_scale",
                    measured="factor=1;mode=identity",
                    expected="identity or independently proven uniform scale",
                ),
            ),
            (
                None
                if metric_authorized
                else "BH-PROOF-VIEW-SCALE-CONFLICT"
            ),
        )
    )

    reassembly_ok = decomposition_ok and bool(validation.get("flange_width_matches_profile"))
    obligations.append(
        ProofObligation(
            "BH.PROOF.ASSEMBLY.REASSEMBLY",
            ProofStatus.PASS if reassembly_ok else ProofStatus.CONFLICT,
            True,
            _validation_evidence(
                context,
                "bh_reassembly",
                ("two_physical_flange_plates", "flange_width_matches_profile"),
            ),
            None if reassembly_ok else "BH-PROOF-REASSEMBLY-CONFLICT",
        )
    )

    flange_count = len(assembly.flange_plates)
    flange_quantity = sum(item.quantity for item in assembly.flange_plates)
    flange_merge_ok = flange_count in {1, 2} and flange_quantity == 2
    obligations.append(
        ProofObligation(
            "BH.PROOF.FLANGE.IDENTICAL_MERGE",
            ProofStatus.PASS if flange_merge_ok else ProofStatus.CONFLICT,
            True,
            (
                _evidence(
                    "assembly:flange-geometry-count",
                    "flange_ontology",
                    measured=f"geometries={flange_count};quantity={flange_quantity}",
                    expected="one geometry x2 or two geometries x1",
                ),
            ),
            None if flange_merge_ok else "BH-PROOF-FLANGE-MERGE-CONFLICT",
        )
    )

    development = assembly.diagnostics.get("flange_development", {}) or {}
    development_mode = str(development.get("mode") or "")
    (
        development_dimension_evidence,
        covered_development_count,
        required_development_count,
    ) = _flange_development_dimension_evidence(context)
    development_semantic_evidence = _flange_development_semantic_evidence(context)
    development_assessment = development.get("semantic_assessment", {}) or {}
    if development_mode in {"projection_view", "projection_only"}:
        development_status = ProofStatus.PASS
        development_code = None
    elif development_mode in {
        "variable_height_two_paths",
        "constant_height_cranked_path",
        "constant_height_two_flange_paths",
    }:
        profile_authorized = bool(
            development_assessment.get("profile_authorized")
        )
        dimension_complete = (
            required_development_count > 0
            and covered_development_count == required_development_count
        )
        development_status = (
            ProofStatus.PASS
            if profile_authorized or dimension_complete
            else ProofStatus.MISSING
        )
        development_code = (
            None
            if development_status == ProofStatus.PASS
            else (
                "BH-PROOF-FLANGE-UNFOLDING-POLICY-MISSING"
                if development_assessment.get("requires_unfolding_policy")
                else "BH-PROOF-FLANGE-FABRICATION-LENGTH-UNCONFIRMED"
                if development_assessment.get("geometry_determinacy")
                == "rigid_projection_determined"
                else "BH-PROOF-FLANGE-DEVELOPMENT-UNCONFIRMED"
            )
        )
    else:
        development_status = ProofStatus.INCOMPLETE
        development_code = "BH-PROOF-FLANGE-DEVELOPMENT-INCOMPLETE"
    obligations.append(
        ProofObligation(
            "BH.PROOF.FLANGE.DEVELOPMENT",
            development_status,
            True,
            (
                _evidence(
                    "assembly:flange-development",
                    "projection_geometry",
                    source_ids=tuple(
                        map(
                            str,
                            (
                                development.get("source_view", {}) or {}
                            ).get("source_entity_ids", ())
                            or (),
                        )
                    ),
                    measured=str(development),
                    expected=(
                        "direct source projection or explicitly bound fabrication "
                        "total for every developed geometry"
                    ),
                    tolerance=context.knowledge.manufacturing_tolerance_mm,
                ),
                *development_semantic_evidence,
                *development_dimension_evidence,
            ),
            development_code,
        )
    )

    provenance_checks = []
    provenance_evidence = []
    for index, plate in enumerate(assembly.plates):
        provenance = plate.provenance
        source_cut_rows = provenance.get("circular_cut_source_ids", [])
        cut_lineage_complete = (
            not plate.circular_cuts
            or isinstance(source_cut_rows, list)
            and len(source_cut_rows) == len(plate.circular_cuts)
            and all(
                isinstance(row, list) and bool(row)
                for row in source_cut_rows
            )
        )
        inner_source_rows = provenance.get("inner_contour_source_ids", [])
        inner_diameters = provenance.get(
            "inner_contour_nominal_diameters_mm", []
        )
        expected_polygonal_count = int(
            provenance.get("polygonal_cut_count", 0) or 0
        )
        inner_lineage_complete = bool(
            isinstance(inner_source_rows, list)
            and isinstance(inner_diameters, list)
            and len(inner_source_rows) == len(plate.inner_contours)
            and len(inner_diameters) == len(plate.inner_contours)
            and sum(bool(row) for row in inner_source_rows)
            == expected_polygonal_count
            and all(
                isinstance(row, list)
                and (
                    bool(row)
                    and isinstance(diameter, (int, float))
                    and float(diameter) > 0.0
                    or not row
                    and diameter is None
                )
                for row, diameter in zip(inner_source_rows, inner_diameters)
            )
        )
        complete = bool(
            provenance.get("source_block")
            and provenance.get("source_insert_handle")
            and provenance.get("source_region_id")
            and provenance.get("source_view_role")
            and int(provenance.get("source_entity_count", 0) or 0) > 0
            and cut_lineage_complete
            and inner_lineage_complete
        )
        provenance_checks.append(complete)
        provenance_evidence.append(
            _evidence(
                f"provenance:plate:{index}:{plate.label}",
                "source_provenance",
                source_ids=tuple(
                    str(value)
                    for value in provenance.get("source_entity_ids", [])
                )
                + tuple(
                    sorted(
                        {
                            str(source_id)
                            for row in source_cut_rows
                            if isinstance(row, list)
                            for source_id in row
                        }
                    )
                )
                + tuple(
                    sorted(
                        {
                            str(source_id)
                            for row in inner_source_rows
                            if isinstance(row, list)
                            for source_id in row
                        }
                    )
                )
                or (str(provenance.get("source_insert_handle") or ""),),
                measured=str(complete).lower(),
                expected=(
                    "stable source region, source block, insert, view role "
                    "and entity count; every circular cut has an index-aligned "
                    "non-empty source row; every inner contour has aligned Bolt "
                    "source/diameter metadata and the declared polygonal-cut count"
                ),
            )
        )
    provenance_ok = bool(provenance_checks) and all(provenance_checks)
    obligations.append(
        ProofObligation(
            "BH.PROOF.PROVENANCE.FEATURES",
            ProofStatus.PASS if provenance_ok else ProofStatus.INCOMPLETE,
            True,
            tuple(provenance_evidence),
            None if provenance_ok else "BH-PROOF-PROVENANCE-INCOMPLETE",
        )
    )

    for index, plate in enumerate(assembly.plates):
        expected_thickness = (
            assembly.metadata.profile.web_thickness
            if plate.role.value == "web"
            else assembly.metadata.profile.flange_thickness
        )
        plate_thickness_ok = abs(plate.thickness - expected_thickness) <= 1e-6
        geometry_checks = (
            per_plate_features[index].get("geometry_checks", {})
            if index < len(per_plate_features)
            else {}
        )
        plate_topology_ok = bool(
            plate.contour.closed
            and plate.area_mm2 > 0.0
            and geometry_checks.get("outer_valid")
            and geometry_checks.get("material_valid")
            and geometry_checks.get("inner_valid_and_contained")
            and geometry_checks.get("inner_non_overlapping")
        )
        plate_cuts_ok = bool(
            geometry_checks.get("circular_cuts_fit_material")
            and geometry_checks.get("circular_cuts_unique")
            and geometry_checks.get("circular_cuts_non_overlapping")
        )
        plate_provenance_ok = provenance_checks[index]
        plate_prefix = f"BH.PROOF.PLATE.{index:02d}"
        obligations.extend(
            (
                ProofObligation(
                    f"{plate_prefix}.THICKNESS",
                    ProofStatus.PASS
                    if plate_thickness_ok
                    else ProofStatus.CONFLICT,
                    True,
                    (thickness_evidence[index],),
                    None
                    if plate_thickness_ok
                    else "BH-PROOF-PLATE-THICKNESS-CONFLICT",
                ),
                ProofObligation(
                    f"{plate_prefix}.CONTOUR",
                    ProofStatus.PASS
                    if plate_topology_ok
                    else ProofStatus.CONFLICT,
                    True,
                    (topology_evidence[index],),
                    None if plate_topology_ok else "BH-PROOF-CONTOUR-INVALID",
                ),
                ProofObligation(
                    f"{plate_prefix}.CUTS",
                    ProofStatus.PASS if plate_cuts_ok else ProofStatus.CONFLICT,
                    True,
                    (cut_evidence[index],),
                    None
                    if plate_cuts_ok
                    else "BH-PROOF-CUT-OUTSIDE-MATERIAL",
                ),
                ProofObligation(
                    f"{plate_prefix}.PROVENANCE",
                    ProofStatus.PASS
                    if plate_provenance_ok
                    else ProofStatus.INCOMPLETE,
                    True,
                    (provenance_evidence[index],),
                    None
                    if plate_provenance_ok
                    else "BH-PROOF-PROVENANCE-INCOMPLETE",
                ),
            )
        )
    return tuple(obligations)


def _bool_rule(
    rule_id: str,
    *,
    hard: bool,
    satisfied: bool,
    weight: float,
    explanation: str,
    evidence: dict[str, Any] | None = None,
) -> RuleEvaluation:
    return RuleEvaluation(
        rule_id=rule_id,
        hard=hard,
        satisfied=satisfied,
        quality=1.0 if satisfied else 0.0,
        weight=weight,
        explanation=explanation,
        evidence=evidence or {},
    )


def _repair_complexity(assembly: BHAssembly) -> tuple[float, dict[str, Any]]:
    diagnostics = assembly.diagnostics
    web_grid = float(diagnostics.get("web_polygon_grid_mm", 0.0) or 0.0)
    flange_grid = float(diagnostics.get("flange_polygon_grid_mm", 0.0) or 0.0)
    web_selection = diagnostics.get("web_selection", {}) or {}
    flange_selection = diagnostics.get("flange_selection", {}) or {}
    repairs: list[str] = []
    serialized = f"{web_selection} {flange_selection}".lower()
    for keyword in (
        "bridge",
        "regular",
        "completion",
        "extend",
        "hidden",
        "morph",
        "repair",
        "precision",
    ):
        if keyword in serialized:
            repairs.append(keyword)
    # Precision relaxation and semantic repair are not errors: they are costs.
    # A valid interpretation requiring fewer interventions is preferred.
    grid_penalty = min(0.40, max(web_grid, flange_grid) / 0.25 * 0.20)
    repair_penalty = min(0.45, len(set(repairs)) * 0.065)
    quality = max(0.0, 1.0 - grid_penalty - repair_penalty)
    return quality, {
        "web_grid_mm": web_grid,
        "flange_grid_mm": flange_grid,
        "repair_keywords": sorted(set(repairs)),
        "web_selection_mode": web_selection.get("mode"),
        "flange_selection_mode": flange_selection.get("mode"),
    }


def _traceability_quality(assembly: BHAssembly) -> tuple[float, dict[str, Any]]:
    checks: list[bool] = []
    plate_evidence: list[dict[str, Any]] = []
    for plate in assembly.plates:
        provenance = plate.provenance
        current = {
            "label": plate.label,
            "source_block": provenance.get("source_block"),
            "source_insert_handle": provenance.get("source_insert_handle"),
            "source_region_id": provenance.get("source_region_id"),
            "source_view_role": provenance.get("source_view_role"),
            "source_entity_count": provenance.get("source_entity_count", 0),
            "cut_source_blocks": provenance.get("cut_source_blocks", []),
        }
        local = [
            bool(current["source_block"]),
            bool(current["source_insert_handle"]),
            bool(current["source_region_id"]),
            bool(current["source_view_role"]),
            int(current["source_entity_count"] or 0) > 0,
        ]
        checks.extend(local)
        plate_evidence.append(current)
    quality = sum(checks) / len(checks) if checks else 0.0
    return quality, {"plates": plate_evidence}


def evaluate_constraints(
    context: ConstraintContext,
) -> tuple[list[RuleEvaluation], dict[str, Any], tuple[ProofObligation, ...]]:
    assembly = context.assembly
    validation = context.validation
    profile = assembly.metadata.profile
    weights = context.knowledge.score_weights
    rules: list[RuleEvaluation] = []

    rules.append(
        _bool_rule(
            "BH.HARD.DISTINCT_PROJECTIONS",
            hard=True,
            satisfied=context.view_pair.main.handle != context.view_pair.flange.handle,
            weight=2.0,
            explanation="The web projection and flange projection must be distinct drawing views.",
            evidence={
                "main_handle": context.view_pair.main.handle,
                "flange_handle": context.view_pair.flange.handle,
            },
        )
    )
    rules.append(
        _bool_rule(
            "BH.HARD.COMPLETE_PHYSICAL_DECOMPOSITION",
            hard=True,
            satisfied=(
                assembly.web_plate is not None
                and sum(plate.quantity for plate in assembly.flange_plates)
                == context.knowledge.physical_flange_count
                and len(assembly.flange_plates)
                in context.knowledge.permitted_flange_geometry_count
            ),
            weight=3.0,
            explanation="A welded BH member lowers to one web and exactly two physical flange plates.",
            evidence={
                "web_geometry_count": 1,
                "flange_geometry_count": len(assembly.flange_plates),
                "physical_flange_quantity": sum(plate.quantity for plate in assembly.flange_plates),
            },
        )
    )
    rules.append(
        _bool_rule(
            "BH.HARD.MANUFACTURING_GEOMETRY_VALID",
            hard=True,
            satisfied=validation.ok,
            weight=4.0,
            explanation="All plate contours, holes, openings, thicknesses and profile invariants must pass.",
            evidence={"checks": validation.checks, "warnings": validation.warnings},
        )
    )

    traceability_quality, traceability_evidence = _traceability_quality(assembly)
    rules.append(
        _bool_rule(
            "BH.HARD.PROVENANCE_COMPLETE",
            hard=True,
            satisfied=traceability_quality >= 1.0 - 1e-12,
            weight=2.0,
            explanation="Every manufactured plate must have complete source-block, INSERT, role and entity provenance.",
            evidence=traceability_evidence,
        )
    )
    rules.append(
        RuleEvaluation(
            rule_id="BH.SOFT.EVIDENCE_TRACEABILITY",
            hard=False,
            satisfied=traceability_quality >= 0.90,
            quality=traceability_quality,
            weight=weights.evidence_traceability,
            explanation="Prefer interpretations with source-block, view-role, entity and cut evidence for every plate.",
            evidence=traceability_evidence,
        )
    )

    annotation = annotation_consistency(assembly.metadata, assembly, context.annotations)
    annotation_quality = float(annotation.get("support_quality", 1.0))
    annotation_coverage = float(annotation.get("evidence_coverage", 0.0))
    rules.append(
        RuleEvaluation(
            rule_id="BH.SOFT.ANNOTATION_CONSISTENCY",
            hard=False,
            satisfied=annotation_quality >= 0.80,
            quality=annotation_quality,
            weight=weights.annotation_consistency,
            explanation="Independent dimensions, bolt marks and part marks should not contradict the geometric interpretation.",
            evidence=annotation,
        )
    )
    rules.append(
        RuleEvaluation(
            rule_id="BH.SOFT.ANNOTATION_COVERAGE",
            hard=False,
            satisfied=annotation_coverage >= 0.25,
            quality=annotation_coverage,
            weight=weights.annotation_coverage,
            explanation="More independent annotation channels increase confidence but are never required to create geometry.",
            evidence={"evidence_presence": annotation.get("evidence_presence", {})},
        )
    )

    view_quality = exp(-2.0 * max(0.0, context.view_pair.prior_cost))
    rules.append(
        RuleEvaluation(
            rule_id="BH.SOFT.PROJECTION_FIT",
            hard=False,
            satisfied=context.view_pair.prior_cost <= 0.40,
            quality=view_quality,
            weight=weights.projection_fit,
            explanation="The selected projections should agree with nominal length, profile height and flange width.",
            evidence=context.view_pair.to_dict(),
        )
    )

    repair_quality, repair_evidence = _repair_complexity(assembly)
    rules.append(
        RuleEvaluation(
            rule_id="BH.SOFT.MINIMUM_GEOMETRIC_REPAIR",
            hard=False,
            satisfied=repair_quality >= 0.50,
            quality=repair_quality,
            weight=weights.repair_complexity,
            explanation="Prefer the valid interpretation requiring the least topology repair and precision relaxation.",
            evidence=repair_evidence,
        )
    )

    web_long = max(assembly.web_plate.bbox.width, assembly.web_plate.bbox.height)
    nominal = max(assembly.metadata.nominal_length, 1.0)
    length_residual = abs(web_long - nominal) / nominal
    # Cranked, stepped and variable-height members can have developed lengths
    # that differ from the table length.  This remains supporting evidence.
    longitudinal_quality = exp(-3.0 * length_residual)
    rules.append(
        RuleEvaluation(
            rule_id="BH.SOFT.LONGITUDINAL_PLAUSIBILITY",
            hard=False,
            satisfied=length_residual <= 0.35,
            quality=longitudinal_quality,
            weight=weights.longitudinal_plausibility,
            explanation="The developed web length should remain physically plausible relative to the material-table length.",
            evidence={
                "web_long_dimension_mm": web_long,
                "nominal_length_mm": assembly.metadata.nominal_length,
                "relative_residual": length_residual,
                "variable_height": profile.is_variable_height,
            },
        )
    )

    return rules, annotation, build_proof_obligations(context, annotation)
