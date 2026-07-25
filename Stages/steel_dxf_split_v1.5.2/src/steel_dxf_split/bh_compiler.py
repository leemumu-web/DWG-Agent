from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import ezdxf

from . import __version__
from .bh_associations import DrawingGraph
from .bh_frames import FrameSolveResult
from .bh_hypothesis import HypothesisSolveResult
from .bh_errors import BHDomainError, BHNoValidHypothesis
from .bh_knowledge import BHKnowledgeBase, BHSourceContract, DEFAULT_BH_KNOWLEDGE
from .bh_models import BHAssembly
from .bh_manufacturing_ir import BHManufacturingIR
from .bh_reasoning import AutomationDisposition, ReasoningAssessment
from .bh_proofs import ProofEvidence, ProofObligation, ProofReport, ProofStatus
from .bh_passes import BHCompileContext, BHCompilerPass, DEFAULT_BH_PASSES
from .bh_solver import BHSearchIncomplete
from .bh_trace import TraceObserver
from .bh_source import SourceDocument
from .bh_validator import BHManufacturingIRValidationReport


class BHCompilationRejected(BHDomainError):
    """Structured rejection retaining the proof report and stable diagnostics."""

    def __init__(
        self,
        proof_report: ProofReport,
        *,
        context: BHCompileContext | None = None,
        message: str | None = None,
    ):
        self.proof_report = proof_report
        self.source_ir = context.source_ir if context is not None else None
        self.frame_result = context.frame_result if context is not None else None
        self.drawing_graph = context.drawing_graph if context is not None else None
        self.hypotheses = context.hypotheses if context is not None else None
        self.manufacturing_ir = (
            context.manufacturing_ir if context is not None else None
        )
        self.manufacturing_validation = (
            context.manufacturing_validation if context is not None else None
        )
        self.fingerprints = context.fingerprints if context is not None else None
        self.information_ledger = (
            context.information_ledger if context is not None else None
        )
        diagnostic_codes = {
            item.diagnostic_code
            for item in proof_report.obligations
            if item.diagnostic_code is not None
            and item.obligation_id in proof_report.blocking_obligation_ids
        }
        if not proof_report.search_complete:
            diagnostic_codes.add("BH-PROOF-SEARCH-INCOMPLETE")
        self.diagnostic_codes = tuple(sorted(diagnostic_codes))
        blockers = ", ".join(proof_report.blocking_obligation_ids)
        super().__init__(
            message
            or (
                "Selected BH hypothesis was rejected by critical proof obligations: "
                + blockers
            )
        )


@dataclass(slots=True)
class BHCompileResult:
    assembly: BHAssembly
    source_ir: SourceDocument
    frame_result: FrameSolveResult
    drawing_graph: DrawingGraph
    manufacturing_ir: BHManufacturingIR
    manufacturing_validation: BHManufacturingIRValidationReport
    trace: Any
    document_summary: dict[str, Any]
    hypotheses: HypothesisSolveResult
    assessment: ReasoningAssessment
    proof_report: ProofReport
    fingerprints: dict[str, str]
    information_ledger: dict[str, Any]


class BHCompiler:
    """Constraint-solving semantic compiler for BH manufacturing drawings.

    The compiler keeps multiple complete interpretations alive until they have
    been lowered to plate assemblies and checked against engineering invariants.
    This gives the pipeline a recovery path when a locally plausible view role
    turns out to be geometrically or physically impossible.
    """

    def __init__(
        self,
        passes: Iterable[BHCompilerPass] = DEFAULT_BH_PASSES,
        *,
        knowledge: BHKnowledgeBase = DEFAULT_BH_KNOWLEDGE,
    ):
        self.passes = tuple(passes)
        self.knowledge = knowledge

    def compile(
        self,
        doc: ezdxf.document.Drawing,
        *,
        source_contract: BHSourceContract,
        source_path: Path | None = None,
        observer: TraceObserver | None = None,
    ) -> BHCompileResult:
        # This provenance is supplied by the production workflow, not guessed
        # from DXF appearance.  Enforce it at the compiler boundary so custom
        # pass pipelines cannot silently escape the verified Tekla/BH domain.
        try:
            source_contract.validate(self.knowledge.dialect)
            if source_contract != self.knowledge.source_contract:
                raise ValueError(
                    "BH source contract violation: workflow authorization does not "
                    "match the compiler knowledge contract"
                )
        except ValueError as error:
            proof_report = ProofReport(
                obligations=(
                    ProofObligation(
                        obligation_id=(
                            "BH.PROOF.SOURCE.TEKLA_SINGLE_PART_CONTRACT"
                        ),
                        status=ProofStatus.CONFLICT,
                        critical=True,
                        evidence=(
                            ProofEvidence(
                                evidence_id=(
                                    "workflow:tekla-single-part-welded-bh"
                                ),
                                channel="workflow_source_contract",
                                source_ids=(),
                                measured=(
                                    f"source_system={source_contract.source_system};"
                                    f"drawing_kind={source_contract.drawing_kind};"
                                    f"member_family={source_contract.member_family};"
                                    f"export_profile={source_contract.export_profile}"
                                ),
                                expected=(
                                    "tekla_structures;single_part_drawing;"
                                    "welded_bh;"
                                    f"dialect={self.knowledge.dialect.profile_id}"
                                ),
                                tolerance=None,
                            ),
                        ),
                        diagnostic_code=(
                            "BH-PROOF-SOURCE-CONTRACT-CONFLICT"
                        ),
                    ),
                ),
                search_complete=True,
            )
            raise BHCompilationRejected(
                proof_report,
                message=str(error),
            ) from error
        context = BHCompileContext(
            doc=doc,
            source_path=source_path,
            knowledge=self.knowledge,
            observer=observer,
        )
        for compiler_pass in self.passes:
            try:
                stage = compiler_pass.run(context)
            except BHSearchIncomplete as error:
                raise BHCompilationRejected(
                    error.proof_report,
                    context=context,
                ) from error
            except BHNoValidHypothesis as error:
                proof_report = ProofReport(
                    obligations=(
                        ProofObligation(
                            obligation_id=(
                                "BH.PROOF.SEARCH.VALID_MANUFACTURING_HYPOTHESIS"
                            ),
                            status=ProofStatus.CONFLICT,
                            critical=True,
                            evidence=(
                                ProofEvidence(
                                    evidence_id="search:no-valid-hypothesis",
                                    channel="complete_hypothesis_search",
                                    source_ids=(),
                                    measured=str(error),
                                    expected=(
                                        "at least one complete view pair satisfies "
                                        "physical lowering and hard constraints"
                                    ),
                                    tolerance=None,
                                ),
                            ),
                            diagnostic_code="BH-PROOF-NO-VALID-HYPOTHESIS",
                        ),
                    ),
                    search_complete=True,
                )
                raise BHCompilationRejected(
                    proof_report,
                    context=context,
                    message=str(error),
                ) from error
            context.trace.add_stage(stage)
        if (
            context.assembly is None
            or context.source_ir is None
            or context.lowering_ir is None
            or context.hypotheses is None
            or context.assessment is None
            or context.manufacturing_ir is None
            or context.manufacturing_validation is None
            or context.frame_result is None
            or context.drawing_graph is None
            or context.fingerprints is None
            or context.information_ledger is None
        ):
            raise RuntimeError("BH compiler terminated without complete semantic and manufacturing IR.")
        assessment = context.assessment
        selected_confidence = assessment.confidence
        context.hypotheses.selected.confidence = selected_confidence
        if assessment.disposition == AutomationDisposition.REJECTED:
            raise BHCompilationRejected(
                assessment.proof_report,
                context=context,
            )
        if assessment.disposition == AutomationDisposition.REVIEW_REQUIRED:
            context.trace.warnings.extend(assessment.warnings or (
                "Selected hypothesis is valid but requires engineering review.",
            ))
        context.trace.version = __version__
        context.assembly.diagnostics["compiler_trace"] = context.trace.to_dict()
        context.assembly.diagnostics["document_ir"] = (
            context.lowering_ir.to_summary()
        )
        context.assembly.diagnostics["hypothesis_solver"] = context.hypotheses.to_dict()
        # Keep the selected hypothesis' independent annotation checks available
        # at the stable diagnostics path used by reports and downstream QA.
        # The hypothesis solver owns this information internally, but the
        # manufacturing report must not require consumers to understand solver
        # implementation details to inspect bolt-mark and part-mark consistency.
        context.assembly.diagnostics["annotation_consistency"] = dict(
            context.hypotheses.selected.annotation_consistency
        )
        context.assembly.diagnostics["automation_assessment"] = assessment.to_dict()
        return BHCompileResult(
            assembly=context.assembly,
            source_ir=context.source_ir,
            frame_result=context.frame_result,
            drawing_graph=context.drawing_graph,
            manufacturing_ir=context.manufacturing_ir,
            manufacturing_validation=context.manufacturing_validation,
            trace=context.trace,
            document_summary=context.lowering_ir.to_summary(),
            hypotheses=context.hypotheses,
            assessment=assessment,
            proof_report=assessment.proof_report,
            fingerprints=dict(context.fingerprints),
            information_ledger=dict(context.information_ledger),
        )


def compile_bh_document(
    doc: ezdxf.document.Drawing,
    *,
    source_contract: BHSourceContract,
    source_path: Path | None = None,
    observer: TraceObserver | None = None,
) -> BHCompileResult:
    return BHCompiler().compile(
        doc,
        source_contract=source_contract,
        source_path=source_path,
        observer=observer,
    )
