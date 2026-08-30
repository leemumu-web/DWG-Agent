from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from .bh_annotations import AnnotationModel
from .bh_constraints import ConstraintContext, evaluate_constraints
from .bh_dialect import canonical_tekla_layer, canonical_tekla_linetype
from .bh_errors import (
    BHCandidateLoweringError,
    BHDomainError,
    BHInsufficientViewError,
    BHNoValidHypothesis,
)
from .bh_extractor import BHBlockInstance, lower_bh_assembly
from .bh_hypothesis import (
    AssemblyHypothesis,
    HypothesisSolveResult,
    HypothesisStatus,
    ViewPairHypothesis,
)
from .bh_ir import BHDocumentIR, EntityAtom
from .bh_trace import DecisionRecord, Evidence
from .bh_knowledge import BHKnowledgeBase
from .bh_metric_scale import (
    resolve_view_metric_scale,
    scale_part_block,
    scale_runtime_instances,
)
from .bh_models import BHMetadata
from .bh_proofs import (
    ProofEvidence,
    ProofObligation,
    ProofReport,
    ProofStatus,
)
from .bh_reasoning import assess_solution
from .bh_semantics import part_blocks_from_ir
from .bh_source import SourceDocument
from .bh_validator import validate_bh_assembly
from .bh_trace import TraceObserver, TraceShape, emit_trace


@dataclass(slots=True)
class BHSolverResult:
    solve: HypothesisSolveResult
    decision: DecisionRecord


class BHSearchIncomplete(BHDomainError):
    """Search stopped before every generated view pair was evaluated."""

    def __init__(
        self,
        *,
        generated: int,
        evaluated: int,
        termination_reason: str,
    ) -> None:
        self.generated_candidate_count = generated
        self.evaluated_candidate_count = evaluated
        self.termination_reason = termination_reason
        self.proof_report = ProofReport(
            obligations=(
                ProofObligation(
                    "BH.PROOF.SEARCH.COMPLETE",
                    ProofStatus.INCOMPLETE,
                    True,
                    (
                        ProofEvidence(
                            evidence_id="solver:search-exhaustion",
                            channel="solver_search",
                            source_ids=(),
                            measured=(
                                f"generated={generated};evaluated={evaluated};"
                                f"reason={termination_reason}"
                            ),
                            expected=(
                                "every generated candidate evaluated or safely pruned"
                            ),
                            tolerance=None,
                        ),
                    ),
                    "BH-PROOF-SEARCH-INCOMPLETE",
                ),
            ),
            search_complete=False,
        )
        super().__init__(
            "BH hypothesis search was incomplete before any valid candidate "
            f"was found: generated={generated}, evaluated={evaluated}, "
            f"reason={termination_reason}"
        )


CANDIDATE_SUBSTEPS = (
    ("candidate_begin", "候选制造降低开始"),
    ("source_views_and_cuts", "候选源视图与物理切口"),
    ("web_precision_attempt", "腹板精度网格尝试"),
    ("web_faces", "腹板候选面"),
    ("web_seed", "腹板种子面"),
    ("holeless_web_selection", "无孔腹板选择"),
    ("web_end_expansion", "腹板端部扩展"),
    ("web_boundary_completion", "腹板纵向边界补全"),
    ("web_hidden_bridge", "腹板隐藏线桥接"),
    ("web_micro_regularization", "腹板微拓扑规则化"),
    ("web_selected", "腹板轮廓选定"),
    ("flange_precision_attempt", "翼缘精度网格尝试"),
    ("flange_seeds", "翼缘种子面"),
    ("flange_end_expansion", "翼缘端部扩展"),
    ("flange_projection_consensus", "翼缘投影一致性"),
    ("flange_second_plate", "第二块翼缘板识别"),
    ("flange_development", "翼缘展开长度推断"),
    ("flange_rigid_extension", "翼缘刚性延长"),
    ("flange_cut_ownership", "翼缘圆孔归属"),
    ("inner_openings", "板内异形开口"),
    ("arc_chain_recovery", "圆弧链恢复"),
    ("candidate_manufacturing_ir", "候选制造 IR"),
)


def _runtime_entity(atom: EntityAtom):
    entity = atom.entity.copy()
    canonical_layer = canonical_tekla_layer(atom.semantic_layer)
    if canonical_layer is not None and entity.dxf.is_supported("layer"):
        entity.dxf.layer = canonical_layer
    if entity.dxf.is_supported("linetype"):
        entity.dxf.linetype = canonical_tekla_linetype(
            atom.visibility,
            str(entity.dxf.linetype),
        )
    return entity


def runtime_instances(ir: BHDocumentIR) -> list[BHBlockInstance]:
    return [
        BHBlockInstance(
            insert=block.insert,
            entities=[_runtime_entity(atom) for atom in block.entities],
            layer_counts=Counter(block.layer_counts),
            texts=[item.normalized for item in block.texts],
            entity_source_ids=tuple(atom.source.stable_id for atom in block.entities),
        )
        for block in ir.blocks
    ]


def _transverse_residual(observed: float, expected: float, *, role: str) -> float:
    """Role-aware residual for a projection's transverse envelope.

    A flange projection is expected to match B closely.  A web projection may
    be taller than the nominal H when the member is cranked, stepped or offset:
    that excess is real assembly geometry, not evidence that the view is wrong.
    Under-depth remains strongly suspicious; over-depth is therefore penalized
    asymmetrically and is later checked by full manufacturing lowering.
    """

    scale = max(expected, 1.0)
    delta = (observed - expected) / scale
    if role == "web" and delta > 0.0:
        return min(delta, 2.0) * 0.15
    return abs(delta)


# Two axis interpretations are indistinguishable when their normalized
# residuals agree within this bound.  Real members are elongated by many
# orders more; a Tekla single-part drawing always lays the member along the
# horizontal axis, so a near-square projection is axis-ambiguous rather than
# rotated evidence.
_AXIS_AMBIGUITY_TOLERANCE = 1e-3


def _dimension_residual(
    block,
    nominal_length: float,
    transverse: float,
    *,
    role: str,
) -> tuple[float, str]:
    direct = abs(block.bbox.width - nominal_length) / max(nominal_length, 1.0) + _transverse_residual(
        block.bbox.height, transverse, role=role
    )
    rotated = abs(block.bbox.height - nominal_length) / max(nominal_length, 1.0) + _transverse_residual(
        block.bbox.width, transverse, role=role
    )
    if abs(direct - rotated) <= _AXIS_AMBIGUITY_TOLERANCE:
        # The member axis is horizontal in a Tekla export.  On a near-square
        # projection the two readings are statistically indistinguishable, so
        # the web over-depth asymmetry must not let sub-millimetre bbox noise
        # pick the rotated reading (which would mismatch the flange view and
        # incur a spurious axis penalty).
        return (min(direct, rotated), "x")
    return (direct, "x") if direct <= rotated else (rotated, "y")


def _view_features(block) -> dict[str, float]:
    arc_count = sum(entity.dxftype() == "ARC" for entity in block.entities)
    hidden_count = sum(str(entity.dxf.linetype).upper() == "XKITLINE04" for entity in block.entities)
    line_count = sum(entity.dxftype() == "LINE" for entity in block.entities)
    return {
        "entity_count": float(len(block.entities)),
        "line_count": float(line_count),
        "arc_count": float(arc_count),
        "hidden_ratio": hidden_count / max(len(block.entities), 1),
        "bbox_area": block.bbox.width * block.bbox.height,
    }


def enumerate_view_pair_hypotheses(
    ir: BHDocumentIR,
    metadata: BHMetadata,
    knowledge: BHKnowledgeBase,
    observer: TraceObserver | None = None,
    *,
    annotations: AnnotationModel | None = None,
    instances: list[BHBlockInstance] | None = None,
) -> list[ViewPairHypothesis]:
    blocks = part_blocks_from_ir(ir)
    if len(blocks) < 2:
        raise BHInsufficientViewError(
            "Expected at least two Part projection blocks, found "
            f"{len(blocks)}; the flange-plane view is missing."
        )
    candidate_annotations = (
        annotations if annotations is not None else AnnotationModel()
    )
    candidate_instances = (
        instances if instances is not None else runtime_instances(ir)
    )
    raw: list[dict[str, Any]] = []
    for raw_main in blocks:
        for raw_flange in blocks:
            if raw_flange.handle == raw_main.handle:
                continue
            metric_scale = resolve_view_metric_scale(
                raw_main,
                raw_flange,
                metadata,
                candidate_annotations,
                candidate_instances,
                knowledge.uniform_scale_policy,
            )
            if metric_scale.mode == "normalized":
                main = scale_part_block(raw_main, metric_scale.factor)
                flange = scale_part_block(raw_flange, metric_scale.factor)
            else:
                main = raw_main
                flange = raw_flange
            main_residual, main_axis = _dimension_residual(
                main,
                metadata.nominal_length,
                metadata.profile.max_height,
                role="web",
            )
            main_features = _view_features(main)
            flange_residual, flange_axis = _dimension_residual(
                flange,
                metadata.nominal_length,
                metadata.profile.flange_width,
                role="flange",
            )
            flange_features = _view_features(flange)
            axis_penalty = 0.0 if main_axis == flange_axis else 0.03
            complexity_penalty = max(
                0.0, flange_features["arc_count"] - main_features["arc_count"]
            ) * 0.002
            # A flange projection is normally transversely simpler than the web
            # projection, but this is a prior only; the manufacturing solver may
            # still recover a less conventional drawing arrangement.
            transverse_complexity_penalty = max(
                0.0,
                flange_features["entity_count"] - main_features["entity_count"],
            ) * 0.0002
            prior_cost = (
                main_residual
                + flange_residual
                + axis_penalty
                + complexity_penalty
                + transverse_complexity_penalty
            )
            raw.append(
                {
                    "main": main,
                    "flange": flange,
                    "prior_cost": prior_cost,
                    "main_residual": main_residual,
                    "flange_residual": flange_residual,
                    "main_axis": main_axis,
                    "flange_axis": flange_axis,
                    "metric_scale": metric_scale,
                    "features": {
                        "main": main_features,
                        "flange": flange_features,
                        "axis_penalty": axis_penalty,
                        "complexity_penalty": complexity_penalty,
                    },
                }
            )
    raw.sort(
        key=lambda item: (
            float(item["prior_cost"]),
            str(item["main"].name).casefold(),
            str(item["main"].handle),
            str(item["flange"].name).casefold(),
            str(item["flange"].handle),
        )
    )
    if not raw:
        raise ValueError("No ordered web/flange view-pair hypotheses were generated.")
    best_cost = float(raw[0]["prior_cost"])
    hypotheses = [
        ViewPairHypothesis(
            hypothesis_id=f"view-pair-{rank:02d}",
            rank=rank,
            main=item["main"],
            flange=item["flange"],
            prior_cost=float(item["prior_cost"]),
            main_residual=float(item["main_residual"]),
            flange_residual=float(item["flange_residual"]),
            main_axis=str(item["main_axis"]),
            flange_axis=str(item["flange_axis"]),
            metric_scale=item["metric_scale"],
            features={
                **dict(item["features"]),
                "frontier_selection_reason": "complete_ordered_pair_enumeration",
                "prior_cost_delta_from_best": float(item["prior_cost"]) - best_cost,
            },
        )
        for rank, item in enumerate(raw, start=1)
    ]
    frontier_shapes: list[TraceShape] = []
    for hypothesis in hypotheses:
        for role_name, block in (("web", hypothesis.main), ("flange", hypothesis.flange)):
            bbox = block.bbox
            frontier_shapes.append(
                TraceShape(
                    shape_id=f"{hypothesis.hypothesis_id}-{role_name}-bbox",
                    kind="polygon",
                    role="face_candidate",
                    coordinates=(
                        (bbox.min_x, bbox.min_y),
                        (bbox.max_x, bbox.min_y),
                        (bbox.max_x, bbox.max_y),
                        (bbox.min_x, bbox.max_y),
                        (bbox.min_x, bbox.min_y),
                    ),
                    closed=True,
                    source_ids=(
                        block.source_view.source_ids
                        if block.source_view is not None
                        else (block.handle,)
                    ),
                    properties={
                        "view_role": role_name,
                        "block_name": block.name,
                        "region_id": block.region_id,
                        "rank": hypothesis.rank,
                    },
                )
            )
    emit_trace(
        observer,
        stage_id="04_view_hypothesis_frontier",
        artifact_id="view_pair_frontier",
        status="observed",
        title_zh="视图假设前沿",
        summary_zh=f"保留 {len(hypotheses)} 个有序腹板/翼缘视图对",
        shapes=tuple(frontier_shapes),
        payload={
            "candidate_count": len(raw),
            "frontier_count": len(hypotheses),
            "hypotheses": [item.to_dict() for item in hypotheses],
        },
    )
    return hypotheses


def solve_component_hypotheses(
    *,
    ir: BHDocumentIR,
    source_ir: SourceDocument,
    metadata: BHMetadata,
    annotations: AnnotationModel,
    knowledge: BHKnowledgeBase,
    metadata_candidates: tuple[dict[str, Any], ...] = (),
    metadata_margin: float = 0.0,
    metadata_source_ids: tuple[str, ...] = (),
    metadata_fallback_fields: tuple[str, ...] = (),
    observer: TraceObserver | None = None,
) -> BHSolverResult:
    instances = runtime_instances(ir)
    view_pairs = enumerate_view_pair_hypotheses(
        ir,
        metadata,
        knowledge,
        observer,
        annotations=annotations,
        instances=instances,
    )
    instance_cache: dict[float, list[BHBlockInstance]] = {1.0: instances}
    hypotheses: list[AssemblyHypothesis] = []
    search_started = perf_counter()
    termination_reason = "exhausted"

    for view_pair in view_pairs:
        if len(hypotheses) >= knowledge.max_solver_expansions:
            termination_reason = "max_solver_expansions"
            break
        if (
            knowledge.max_solver_seconds is not None
            and perf_counter() - search_started >= knowledge.max_solver_seconds
        ):
            termination_reason = "max_solver_seconds"
            break
        hypothesis = AssemblyHypothesis(
            hypothesis_id=f"assembly-{view_pair.rank:02d}",
            view_pair=view_pair,
        )
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="candidate_begin",
            status="observed",
            title_zh="候选制造降低开始",
            summary_zh=f"开始降低 {hypothesis.hypothesis_id}",
            hypothesis_id=hypothesis.hypothesis_id,
            payload={"view_pair": view_pair.to_dict()},
        )
        try:
            try:
                metric_factor = (
                    view_pair.metric_scale.factor
                    if view_pair.metric_scale is not None
                    and view_pair.metric_scale.mode == "normalized"
                    else 1.0
                )
                cache_key = round(metric_factor, 12)
                if cache_key not in instance_cache:
                    instance_cache[cache_key] = scale_runtime_instances(
                        instances,
                        metric_factor,
                    )
                candidate_instances = instance_cache[cache_key]
                assembly = lower_bh_assembly(
                    metadata=metadata,
                    instances=candidate_instances,
                    main=view_pair.main,
                    flange=view_pair.flange,
                    manufacturing_tolerance_mm=knowledge.manufacturing_tolerance_mm,
                    flange_development_policy=(
                        knowledge.flange_development_policy
                    ),
                    development_profile_id=knowledge.source_contract.export_profile,
                    compiler_diagnostics={
                        "architecture": "fact IR -> semantic hypotheses -> constraint solver -> manufacturing IR",
                        "view_hypothesis_id": view_pair.hypothesis_id,
                        "metric_scale": (
                            view_pair.metric_scale.to_dict()
                            if view_pair.metric_scale is not None
                            else {
                                "mode": "identity",
                                "factor": 1.0,
                            }
                        ),
                        "knowledge_base": knowledge.to_dict(),
                    },
                    observer=observer,
                    hypothesis_id=hypothesis.hypothesis_id,
                )
                if metric_factor != 1.0:
                    for plate in assembly.plates:
                        plate.provenance["source_metric_scale_factor"] = (
                            metric_factor
                        )
                        plate.provenance["source_metric_scale_mode"] = (
                            "independent_uniform_scale_consensus"
                        )
            except ValueError as error:
                raise BHCandidateLoweringError(str(error)) from error
            validation = validate_bh_assembly(assembly)
            rules, annotation, proof_obligations = evaluate_constraints(
                ConstraintContext(
                    assembly=assembly,
                    validation=validation,
                    view_pair=view_pair,
                    annotations=annotations,
                    knowledge=knowledge,
                    source_ir=source_ir,
                    lowering_ir=ir,
                    metadata_candidates=metadata_candidates,
                    metadata_margin=metadata_margin,
                    metadata_source_ids=metadata_source_ids,
                    metadata_fallback_fields=metadata_fallback_fields,
                )
            )
            hypothesis.assembly = assembly
            hypothesis.validation = validation
            hypothesis.rules = rules
            hypothesis.proof_obligations = proof_obligations
            hypothesis.annotation_consistency = annotation
            hard_failures = [rule.rule_id for rule in rules if rule.hard and not rule.satisfied]
            if hard_failures:
                hypothesis.status = HypothesisStatus.REJECTED
                hypothesis.error = "Hard constraints failed: " + ", ".join(hard_failures)
            else:
                hypothesis.status = HypothesisStatus.VALID
            # A complete hypothesis is scored only after geometry lowering.
            # Projection fit and engineering evidence share one normalized cost
            # space; hard-invalid hypotheses remain explainable but unselectable.
            prior_component = knowledge.score_weights.view_prior * view_pair.prior_cost
            soft_component = hypothesis.soft_penalty
            hypothesis.score_breakdown = {
                "view_prior": prior_component,
                "semantic_soft_constraints": soft_component,
            }
            hypothesis.semantic_cost = prior_component + soft_component
        except BHCandidateLoweringError as exc:
            hypothesis.status = HypothesisStatus.LOWERING_FAILED
            hypothesis.error = f"{type(exc).__name__}: {exc}"
            hypothesis.semantic_cost = float("inf")
        hypotheses.append(hypothesis)

    search_complete = len(hypotheses) == len(view_pairs)
    if search_complete:
        termination_reason = "exhausted"
    valid = sorted(
        (item for item in hypotheses if item.hard_pass),
        key=lambda item: (item.semantic_cost, item.view_pair.rank),
    )
    if not valid:
        if not search_complete:
            raise BHSearchIncomplete(
                generated=len(view_pairs),
                evaluated=len(hypotheses),
                termination_reason=termination_reason,
            )
        diagnostics = "; ".join(
            f"{item.hypothesis_id}={item.error or item.status.value}" for item in hypotheses
        )
        raise BHNoValidHypothesis(
            "No complete BH manufacturing hypothesis satisfied hard constraints: "
            + diagnostics
        )

    selected = valid[0]
    second_cost = valid[1].semantic_cost if len(valid) > 1 else selected.semantic_cost + 1.0
    margin = max(0.0, second_cost - selected.semantic_cost)
    selected.status = HypothesisStatus.SELECTED
    solve = HypothesisSolveResult(
        selected=selected,
        hypotheses=hypotheses,
        score_margin=margin,
        search_complete=search_complete,
        generated_candidate_count=len(view_pairs),
        evaluated_candidate_count=len(hypotheses),
        pruned_candidate_count=0,
        termination_reason=termination_reason,
    )
    assessment = assess_solution(solve, knowledge)
    selected.confidence = assessment.confidence
    for item in valid[1:]:
        # Alternatives receive a compact comparative confidence for reporting;
        # only the selected hypothesis passes the automation quality gate.
        relative = max(0.0, item.semantic_cost - selected.semantic_cost)
        item.confidence = max(0.0, assessment.confidence * (1.0 - min(1.0, relative)))
    status_map = {
        HypothesisStatus.LOWERING_FAILED: "failed",
        HypothesisStatus.REJECTED: "rejected",
        HypothesisStatus.SELECTED: "selected",
        HypothesisStatus.VALID: "observed",
        HypothesisStatus.GENERATED: "observed",
    }
    for item in hypotheses:
        item_events = [
            event
            for event in getattr(observer, "events", ())
            if event.hypothesis_id == item.hypothesis_id
        ]
        emitted = {event.artifact_id for event in item_events}
        reason = (
            f"候选在此步骤前终止：{item.error}"
            if item.error
            else "该候选的几何与语义条件不需要此步骤。"
        )
        for artifact_id, title_zh in CANDIDATE_SUBSTEPS:
            if artifact_id in emitted:
                continue
            emit_trace(
                observer,
                stage_id="05_candidate_lowering",
                artifact_id=artifact_id,
                status="not_applicable",
                title_zh=title_zh,
                summary_zh=reason,
                hypothesis_id=item.hypothesis_id,
                payload={
                    "reason": "candidate_terminated_before_step" if item.error else "condition_not_applicable",
                    "candidate_status": item.status.value,
                    "candidate_error": item.error,
                },
            )
        item_events = [
            event
            for event in getattr(observer, "events", ())
            if event.hypothesis_id == item.hypothesis_id
        ]
        last_sequence = item_events[-1].sequence if item_events else None
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="candidate_terminal",
            status=status_map[item.status],
            title_zh="候选制造降低终态",
            summary_zh=f"{item.hypothesis_id}: {item.status.value}",
            hypothesis_id=item.hypothesis_id,
            payload={
                "status": item.status.value,
                "error": item.error,
                "semantic_cost": (
                    item.semantic_cost if item.semantic_cost != float("inf") else None
                ),
                "score_breakdown": item.score_breakdown,
                "confidence": item.confidence,
                "rules": [rule.to_dict() for rule in item.rules],
                "last_successful_event_sequence": last_sequence,
            },
        )
    alternatives = [
        {
            "hypothesis_id": item.hypothesis_id,
            "status": item.status.value,
            "semantic_cost": item.semantic_cost if item.semantic_cost != float("inf") else None,
            "hard_pass": item.hard_pass,
            "main": item.view_pair.main.handle,
            "flange": item.view_pair.flange.handle,
            "error": item.error,
        }
        for item in hypotheses
    ]
    decision = DecisionRecord(
        name="complete_component_hypothesis",
        selected=selected.hypothesis_id,
        score=selected.semantic_cost,
        confidence=selected.confidence,
        margin=margin,
        alternatives=alternatives,
        evidence=[
            Evidence(
                "solver.complete_hypothesis",
                "View roles are not committed until a complete plate assembly passes manufacturing constraints.",
                1.0,
                {
                    "generated": len(view_pairs),
                    "evaluated": len(hypotheses),
                    "search_complete": search_complete,
                    "termination_reason": termination_reason,
                    "valid": len(valid),
                    "selected": selected.hypothesis_id,
                    "automation_assessment": assessment.to_dict(),
                },
            ),
            Evidence(
                "solver.hard_constraints",
                "Invalid geometry, incomplete decomposition and missing provenance reject a hypothesis absolutely.",
                1.0,
                [rule.to_dict() for rule in selected.rules if rule.hard],
            ),
            Evidence(
                "solver.soft_constraints",
                "Projection fit, annotations and minimum-repair preference rank the remaining valid interpretations.",
                0.8,
                [rule.to_dict() for rule in selected.rules if not rule.hard],
            ),
        ],
        warnings=(
            list(assessment.warnings)
        ),
    )
    return BHSolverResult(solve=solve, decision=decision)
