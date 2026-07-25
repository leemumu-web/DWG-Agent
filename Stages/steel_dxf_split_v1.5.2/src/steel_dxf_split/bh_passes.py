from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

import ezdxf

from .bh_annotations import AnnotationModel, extract_annotation_model
from .bh_associations import DrawingGraph, build_drawing_graph
from .bh_fingerprint import build_semantic_fingerprints
from .bh_frames import FrameSolveResult, infer_member_frames
from .bh_hypothesis import HypothesisSolveResult
from .bh_information import build_source_information_ledger
from .bh_ir import BHDocumentIR, SemanticLayer, VisibilityClass
from .bh_knowledge import BHKnowledgeBase, DEFAULT_BH_KNOWLEDGE
from .bh_manufacturing_ir import BHManufacturingIR, build_bh_manufacturing_ir
from .bh_models import BHAssembly
from .bh_proofs import ProofEvidence, ProofObligation, ProofReport, ProofStatus
from .bh_reasoning import AutomationDisposition, ReasoningAssessment, assess_solution
from .bh_regions import (
    RegionBuildResult,
    build_view_regions,
    materialize_lowering_ir,
)
from .bh_semantics import MetadataParseResult, parse_bh_metadata_ir
from .bh_solver import BHSolverResult, solve_component_hypotheses
from .bh_source import SourceDocument, decode_source_document
from .bh_validator import (
    BHManufacturingIRValidationReport,
    BHValidationReport,
    validate_bh_assembly,
    validate_bh_manufacturing_ir,
)
from .bh_trace import (
    BHCompilerTrace,
    DecisionRecord,
    Evidence,
    StageRecord,
    TraceObserver,
    TraceShape,
    emit_trace,
)
from .bh_trace_geometry import (
    contour_shape,
    cut_shapes,
    entity_shapes,
    source_entity_shapes,
)


@dataclass(slots=True)
class BHCompileContext:
    doc: ezdxf.document.Drawing
    source_path: Path | None
    knowledge: BHKnowledgeBase = DEFAULT_BH_KNOWLEDGE
    observer: TraceObserver | None = None
    trace: BHCompilerTrace = field(default_factory=BHCompilerTrace)
    source_ir: SourceDocument | None = None
    lowering_ir: BHDocumentIR | None = None
    frame_result: FrameSolveResult | None = None
    region_result: RegionBuildResult | None = None
    drawing_graph: DrawingGraph | None = None
    annotations: AnnotationModel | None = None
    metadata_result: MetadataParseResult | None = None
    solver_result: BHSolverResult | None = None
    hypotheses: HypothesisSolveResult | None = None
    assembly: BHAssembly | None = None
    validation: BHValidationReport | None = None
    assessment: ReasoningAssessment | None = None
    manufacturing_ir: BHManufacturingIR | None = None
    manufacturing_validation: BHManufacturingIRValidationReport | None = None
    fingerprints: dict[str, str] | None = None
    information_ledger: dict[str, Any] | None = None


class BHCompilerPass(Protocol):
    name: str

    def run(self, context: BHCompileContext) -> StageRecord: ...


PASS_TRACE_ARTIFACTS = {
    "source.decode": (
        "01_frontend_fact_ir", "document_fact_ir", "事实前端", "完成图元事实分类与来源保留。"
    ),
    "source.normalize_and_partition": (
        "01_frontend_fact_ir", "member_frame_candidates", "构件坐标", "完成构件纵横轴和手性候选规范化。"
    ),
    "drawing.parse_and_associate_annotations": (
        "02_annotation_facts", "annotation_model", "标注事实", "完成尺寸、孔标、零件标与剖面提取。"
    ),
    "drawing.resolve_component_metadata": (
        "03_metadata_semantics", "selected_metadata", "构件元数据", "完成材料表行和 BH 截面语义解析。"
    ),
    "hypotheses.solve_complete_component": (
        "06_constraints_and_selection", "solver_result", "约束与选择", "完成候选硬约束、软代价和最终选择。"
    ),
    "manufacturing.validate_assembly": (
        "07_assembly_validation", "assembly_validation", "装配体验证", "完成一腹板、两物理翼缘及板件几何不变量验证。"
    ),
    "manufacturing.freeze_ir_and_prove": (
        "08_manufacturing_ir", "evidence_backed_manufacturing_ir", "证据化制造 IR", "冻结物理板件、逐特征来源、证明闭包与信息账本。"
    ),
    "quality.route": (
        "09_quality_route", "automation_assessment", "自动化路由", "仅依据关键证明闭包决定自动、复核或拒绝。"
    ),
}


def _emit_pass_trace(context: BHCompileContext, stage: StageRecord) -> None:
    stage_id, artifact_id, title_zh, summary_zh = PASS_TRACE_ARTIFACTS[stage.name]
    shapes: list[TraceShape] = []
    if stage.name == "source.decode":
        if context.source_ir is not None:
            shapes.extend(source_entity_shapes(context.source_ir.entities))
    elif stage.name == "drawing.parse_and_associate_annotations":
        annotation_layers = {
            SemanticLayer.DIMENSION,
            SemanticLayer.PART_MARK,
            SemanticLayer.BOLT_MARK,
            SemanticLayer.SECTION,
        }
        for atom in context.lowering_ir.entities:
            if atom.semantic_layer not in annotation_layers:
                continue
            if atom.semantic_layer == SemanticLayer.PHYSICAL_CUT:
                role = "physical_cut"
            elif atom.semantic_layer == SemanticLayer.CUT_HELPER:
                role = "cut_helper"
            elif atom.semantic_layer == SemanticLayer.PART_EDGE:
                role = (
                    "part_hidden"
                    if atom.visibility == VisibilityClass.HIDDEN
                    else "part_visible"
                )
            else:
                role = "annotation"
            shapes.extend(
                replace(shape, role=role)
                for shape in entity_shapes(atom.source.stable_id, (atom.entity,))
            )
    elif stage.name == "drawing.resolve_component_metadata":
        shapes.extend(
            TraceShape(
                shape_id=f"metadata-token-{index:03d}",
                kind="text",
                role="annotation",
                coordinates=((token.position.x, token.position.y),),
                source_ids=(token.source.stable_id,),
                properties={"text": token.normalized, "height": token.height},
            )
            for index, token in enumerate(context.metadata_result.row_tokens, start=1)
        )
    elif context.assembly is not None:
        for index, plate in enumerate(context.assembly.plates, start=1):
            shapes.append(
                contour_shape(
                    f"semantic-plate-{index:02d}",
                    "manufacturing_plate",
                    plate.contour,
                )
            )
            shapes.extend(
                cut_shapes(
                    f"semantic-cut-{index:02d}",
                    "manufacturing_cut",
                    plate.circular_cuts,
                )
            )
            shapes.extend(
                contour_shape(
                    f"semantic-opening-{index:02d}-{opening_index:02d}",
                    "manufacturing_cut",
                    contour,
                )
                for opening_index, contour in enumerate(plate.inner_contours, start=1)
            )
    emit_trace(
        context.observer,
        stage_id=stage_id,
        artifact_id=artifact_id,
        status="observed",
        title_zh=title_zh,
        summary_zh=summary_zh,
        shapes=tuple(shapes),
        payload={
            "pass_name": stage.name,
            "inputs": stage.inputs,
            "outputs": stage.outputs,
            "warnings": stage.warnings,
        },
    )


class FrontendPass:
    name = "source.decode"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        context.source_ir = decode_source_document(
            context.doc,
            context.knowledge.dialect,
            audit=True,
        )
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={
                "source_path": str(context.source_path) if context.source_path else None,
                "source_contract": {
                    "source_system": context.knowledge.source_contract.source_system,
                    "drawing_kind": context.knowledge.source_contract.drawing_kind,
                    "member_family": context.knowledge.source_contract.member_family,
                    "export_profile": context.knowledge.source_contract.export_profile,
                    "validated": True,
                    "authority": "workflow_supplied_not_inferred_from_dxf",
                },
            },
            outputs={
                "dxf_version": context.source_ir.dxf_version,
                "encoding": context.source_ir.encoding,
                "units": context.source_ir.units,
                "source_entity_count": len(context.source_ir.entities),
                "source_container_count": len(context.source_ir.containers),
                "audit_error_count": len(context.source_ir.audit_errors),
            },
        )
        _emit_pass_trace(context, stage)
        return stage


class NormalizeFramePass:
    name = "source.normalize_and_partition"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        if context.source_ir is None:
            raise ValueError("SourceIR must exist before member-frame inference.")
        source_document = context.source_ir
        context.frame_result = infer_member_frames(
            source_document,
            horizontal_axis_fact=context.knowledge.horizontal_axis_fact,
            horizontal_axis_tolerance_degrees=(
                context.knowledge.horizontal_axis_tolerance_degrees
            ),
        )
        selected = context.frame_result.selected
        context.region_result = build_view_regions(source_document, selected)
        context.lowering_ir = materialize_lowering_ir(
            source_document,
            context.region_result,
            selected,
            source_path=context.source_path,
        )
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={"source_entity_count": len(context.source_ir.entities)},
            outputs={
                "candidate_count": len(context.frame_result.candidates),
                "unique": context.frame_result.unique,
                "score_margin": context.frame_result.score_margin,
                "selected_signature": selected.canonical_signature,
                "selected_reflected": selected.reflected,
                "horizontal_axis_fact": context.knowledge.horizontal_axis_fact,
                "evidence_ids": list(selected.evidence_ids),
                "part_view_count": len(context.region_result.part_views),
                "part_view_signatures": [
                    region.geometry_signature
                    for region in context.region_result.part_views
                ],
                "lowering_block_count": len(context.lowering_ir.blocks),
            },
        )
        _emit_pass_trace(context, stage)
        return stage


class AnnotationPass:
    name = "drawing.parse_and_associate_annotations"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        if (
            context.source_ir is None
            or context.lowering_ir is None
            or context.region_result is None
            or context.frame_result is None
        ):
            raise ValueError("Normalized SourceIR and regions must exist before associations.")
        context.drawing_graph = build_drawing_graph(
            context.source_ir,
            context.region_result,
            context.frame_result.selected,
            context.knowledge.dialect,
        )
        context.annotations = extract_annotation_model(context.drawing_graph)
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={"block_count": len(context.lowering_ir.blocks)},
            outputs={
                "dimension_observation_count": len(context.annotations.dimensions),
                "bolt_mark_count": len(context.annotations.bolt_marks),
                "part_mark_count": len(context.annotations.part_marks),
                "section_block_count": context.annotations.section_block_count,
                "drawing_node_count": len(context.drawing_graph.nodes),
                "drawing_edge_count": len(context.drawing_graph.edges),
                "drawing_node_kinds": sorted(
                    {node.kind.value for node in context.drawing_graph.nodes}
                ),
                "drawing_edge_kinds": sorted(
                    {edge.relation.value for edge in context.drawing_graph.edges}
                ),
            },
        )
        _emit_pass_trace(context, stage)
        return stage


class MetadataPass:
    name = "drawing.resolve_component_metadata"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        context.metadata_result = parse_bh_metadata_ir(
            context.lowering_ir,
            context.source_path,
            drawing_graph=context.drawing_graph,
        )
        context.trace.add_decision(context.metadata_result.decision)
        metadata = context.metadata_result.metadata
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={"text_count": len(context.lowering_ir.texts)},
            outputs={
                "part_number": metadata.part_number,
                "profile": metadata.profile.raw_text,
                "nominal_length_mm": metadata.nominal_length,
                "material": metadata.material,
                "drawing_scale": metadata.drawing_scale,
                "table_block_handle": context.metadata_result.table_block_handle,
            },
            warnings=list(context.metadata_result.decision.warnings),
        )
        _emit_pass_trace(context, stage)
        return stage


class HypothesisSolvePass:
    name = "hypotheses.solve_complete_component"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        context.solver_result = solve_component_hypotheses(
            ir=context.lowering_ir,
            source_ir=context.source_ir,
            metadata=context.metadata_result.metadata,
            annotations=context.annotations,
            knowledge=context.knowledge,
            metadata_candidates=tuple(context.metadata_result.decision.alternatives),
            metadata_margin=context.metadata_result.decision.margin,
            metadata_source_ids=tuple(
                item.source.stable_id for item in context.metadata_result.row_tokens
            ),
            metadata_fallback_fields=context.metadata_result.fallback_fields,
            observer=context.observer,
        )
        context.hypotheses = context.solver_result.solve
        selected = context.hypotheses.selected
        context.assembly = selected.assembly
        context.validation = selected.validation
        context.trace.add_decision(context.solver_result.decision)
        assert context.assembly is not None
        context.assembly.diagnostics["hypothesis_solver"] = context.hypotheses.to_dict()
        context.assembly.diagnostics["knowledge_base"] = context.knowledge.to_dict()
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={
                "part_block_count": sum(
                    1
                    for block in context.lowering_ir.blocks
                    if block.layer_counts.get("Part", 0) >= 4
                ),
                "max_solver_expansions": context.knowledge.max_solver_expansions,
                "max_solver_seconds": context.knowledge.max_solver_seconds,
            },
            outputs={
                "generated_hypotheses": context.hypotheses.generated_candidate_count,
                "evaluated_hypotheses": context.hypotheses.evaluated_candidate_count,
                "pruned_hypotheses": context.hypotheses.pruned_candidate_count,
                "search_complete": context.hypotheses.search_complete,
                "termination_reason": context.hypotheses.termination_reason,
                "valid_hypotheses": len(context.hypotheses.valid_hypotheses),
                "selected_hypothesis": selected.hypothesis_id,
                "score_margin": context.hypotheses.score_margin,
                "confidence": selected.confidence,
                "proof_obligation_count": len(selected.proof_obligations),
                "metadata_fallback_fields": list(
                    context.metadata_result.fallback_fields
                ),
                "plates": [
                    {
                        "label": plate.label,
                        "role": plate.role.value,
                        "quantity": plate.quantity,
                        "bbox_mm": [plate.bbox.width, plate.bbox.height],
                        "circular_cut_count": len(plate.circular_cuts),
                        "inner_contour_count": len(plate.inner_contours),
                    }
                    for plate in context.assembly.plates
                ],
            },
            warnings=list(context.solver_result.decision.warnings),
        )
        _emit_pass_trace(context, stage)
        return stage


class ValidationPass:
    name = "manufacturing.validate_assembly"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        # Re-run the validator after semantic graph construction.  This makes
        # the back-end contract explicit and guards accidental mutations in a
        # custom pass pipeline.
        context.validation = validate_bh_assembly(context.assembly)
        context.assembly.diagnostics["compiler_validation"] = context.validation.to_dict()
        context.trace.invariants = dict(context.validation.checks)
        context.trace.warnings.extend(context.validation.warnings)
        if not context.validation.ok:
            failed = [key for key, value in context.validation.checks.items() if not value]
            raise ValueError("Selected manufacturing hypothesis violated final invariants: " + ", ".join(failed))
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={"plate_count": len(context.assembly.plates)},
            outputs={
                "ok": context.validation.ok,
                "checks": context.validation.checks,
                "minimum_decision_confidence": context.trace.minimum_confidence,
                "selected_hypothesis_confidence": context.hypotheses.selected.confidence,
            },
            warnings=list(context.validation.warnings),
        )
        _emit_pass_trace(context, stage)
        return stage


class QualityGatePass:
    name = "quality.route"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        if context.assessment is None:
            raise ValueError("Manufacturing IR must be proven before the final quality gate.")
        assessment = context.assessment
        context.assembly.diagnostics["automation_assessment"] = assessment.to_dict()
        context.trace.add_decision(
            DecisionRecord(
                name="automation_disposition",
                selected=assessment.disposition.value,
                score=1.0 - assessment.confidence,
                confidence=assessment.confidence,
                margin=context.hypotheses.score_margin,
                alternatives=[
                    {"disposition": "auto_accept", "condition": "all critical proofs pass"},
                    {"disposition": "review_required", "condition": "critical proof missing"},
                    {"disposition": "rejected", "condition": "critical proof conflict/incomplete or incomplete search"},
                ],
                evidence=[
                    Evidence(
                        "quality.confidence_decomposition",
                        "Automation is decided by explicit proof obligations; confidence remains ranking telemetry.",
                        1.0,
                        assessment.to_dict(),
                    )
                ],
                warnings=list(assessment.warnings),
            )
        )
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={
                "selected_hypothesis": context.hypotheses.selected.hypothesis_id,
                "hard_invariants_ok": context.validation.ok,
            },
            outputs=assessment.to_dict(),
            warnings=list(assessment.warnings),
        )
        _emit_pass_trace(context, stage)
        return stage


class ManufacturingIRPass:
    name = "manufacturing.freeze_ir_and_prove"

    def run(self, context: BHCompileContext) -> StageRecord:
        started = perf_counter()
        if (
            context.assembly is None
            or context.source_ir is None
            or context.lowering_ir is None
            or context.frame_result is None
            or context.hypotheses is None
        ):
            raise ValueError(
                "SourceIR, member frame and selected assembly must exist before manufacturing IR lowering."
            )

        preliminary = assess_solution(context.hypotheses, context.knowledge)
        context.manufacturing_ir = build_bh_manufacturing_ir(
            context.assembly,
            context.source_ir,
            context.frame_result.selected,
            preliminary.proof_report,
            fit_tolerance_mm=context.knowledge.manufacturing_tolerance_mm,
        )
        context.manufacturing_validation = validate_bh_manufacturing_ir(
            context.manufacturing_ir,
            context.assembly,
        )
        validation = context.manufacturing_validation
        contract_mismatch = "MANUFACTURING.IR.CONTRACT.MISMATCH" in validation.diagnostic_codes
        provenance_missing = any(
            code in {
                "FEATURE.PROVENANCE.MISSING",
                "PLATE.ROLE.PROVENANCE.MISSING",
            }
            for code in validation.diagnostic_codes
        )
        if contract_mismatch:
            status = ProofStatus.CONFLICT
            diagnostic_code = "BH-MANUFACTURING-IR-CONTRACT-MISMATCH"
        elif provenance_missing:
            status = ProofStatus.MISSING
            diagnostic_code = "BH-MANUFACTURING-IR-PROVENANCE-MISSING"
        else:
            status = ProofStatus.PASS
            diagnostic_code = None
        source_ids = tuple(
            sorted(
                {
                    source_id
                    for plate in context.manufacturing_ir.plates
                    for evidence in (
                        plate.role_evidence,
                        *(segment.evidence for segment in plate.outer_segments),
                        *(cut.evidence for cut in plate.circular_cuts),
                        *(
                            segment.evidence
                            for contour in plate.inner_contours
                            for segment in contour.segments
                        ),
                    )
                    for source_id in evidence.source_ids
                }
            )
        )
        obligation = ProofObligation(
            obligation_id="BH.PROOF.MANUFACTURING_IR.PROVENANCE",
            status=status,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="manufacturing-ir:source-writer-closure",
                    channel="manufacturing_ir_validator",
                    source_ids=source_ids,
                    measured=(
                        "all checks pass"
                        if validation.ok
                        else ",".join(validation.diagnostic_codes)
                    ),
                    expected="complete source provenance and exact writer agreement",
                    tolerance=context.knowledge.manufacturing_tolerance_mm,
                ),
            ),
            diagnostic_code=diagnostic_code,
        )
        proof_report = ProofReport(
            obligations=(*preliminary.proof_report.obligations, obligation),
            search_complete=preliminary.proof_report.search_complete,
        )
        disposition = AutomationDisposition(proof_report.disposition.value)
        warnings = list(preliminary.warnings)
        risks = list(preliminary.risk_flags)
        reasons = list(preliminary.reasons)
        if status == ProofStatus.MISSING:
            warnings.append(
                "Manufacturing IR provenance is incomplete; route the candidate to engineering review."
            )
            risks.append("manufacturing_ir_provenance_incomplete")
            reasons.append("At least one manufacturing feature lacks complete source evidence.")
        elif status == ProofStatus.CONFLICT:
            risks.append("manufacturing_ir_writer_conflict")
            reasons.append("The immutable manufacturing IR conflicts with the writer assembly contract.")
        context.assessment = replace(
            preliminary,
            disposition=disposition,
            proof_report=proof_report,
            warnings=tuple(warnings),
            risk_flags=tuple(sorted(set(risks))),
            reasons=tuple(reasons),
        )
        context.manufacturing_ir = replace(
            context.manufacturing_ir,
            proof_disposition=proof_report.disposition.value,
            proof_ids=tuple(
                sorted(item.obligation_id for item in proof_report.obligations)
            ),
        )
        context.manufacturing_validation = validate_bh_manufacturing_ir(
            context.manufacturing_ir,
            context.assembly,
        )
        validation = context.manufacturing_validation
        context.information_ledger = build_source_information_ledger(
            context.source_ir,
            context.drawing_graph,
            context.manufacturing_ir,
            proof_report,
            metadata_source_ids=tuple(
                item.source.stable_id
                for item in context.metadata_result.row_tokens
            ),
        )
        context.fingerprints = build_semantic_fingerprints(
            context.source_ir,
            context.lowering_ir,
            context.hypotheses.selected,
            context.manufacturing_ir,
        )
        context.assembly.diagnostics["manufacturing_ir"] = (
            context.manufacturing_ir.to_dict()
        )
        context.assembly.diagnostics["manufacturing_ir_validation"] = (
            validation.to_dict()
        )
        context.assembly.diagnostics["source_information_ledger"] = (
            context.information_ledger
        )
        context.assembly.diagnostics["semantic_fingerprints"] = dict(
            context.fingerprints
        )
        stage = StageRecord(
            name=self.name,
            duration_ms=(perf_counter() - started) * 1000.0,
            inputs={
                "plate_count": len(context.assembly.plates),
                "source_entity_count": len(context.source_ir.entities),
                "preliminary_disposition": preliminary.disposition.value,
            },
            outputs={
                "schema_version": context.manufacturing_ir.schema_version,
                "physical_plate_count": len(context.manufacturing_ir.plates),
                "fingerprint": context.manufacturing_ir.fingerprint,
                "validation": validation.to_dict(),
                "source_information_ledger": context.information_ledger,
                "fingerprints": context.fingerprints,
                "proof_obligation": obligation.to_dict(),
                "disposition": disposition.value,
            },
            warnings=list(context.assessment.warnings),
        )
        _emit_pass_trace(context, stage)
        return stage


# Compatibility names retained for external imports from pre-1.0 releases.
ViewResolutionPass = HypothesisSolvePass
GeometryLoweringPass = HypothesisSolvePass


DEFAULT_BH_PASSES: tuple[BHCompilerPass, ...] = (
    FrontendPass(),
    NormalizeFramePass(),
    AnnotationPass(),
    MetadataPass(),
    HypothesisSolvePass(),
    ValidationPass(),
    ManufacturingIRPass(),
    QualityGatePass(),
)
