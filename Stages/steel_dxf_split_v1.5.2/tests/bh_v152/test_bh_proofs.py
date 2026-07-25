from __future__ import annotations

import pytest

from steel_dxf_split.bh_proofs import (
    ProofDisposition,
    ProofEvidence,
    ProofObligation,
    ProofReport,
    ProofStatus,
)
from steel_dxf_split.bh_hypothesis import (
    AssemblyHypothesis,
    HypothesisSolveResult,
    HypothesisStatus,
    RuleEvaluation,
    ViewPairHypothesis,
)
from steel_dxf_split.bh_knowledge import DEFAULT_BH_KNOWLEDGE
from steel_dxf_split.bh_reasoning import AutomationDisposition, assess_solution


def _obligation(index: int, status: str, *, critical: bool = True) -> ProofObligation:
    return ProofObligation(
        obligation_id=f"proof-{index}",
        status=ProofStatus(status),
        critical=critical,
        evidence=(),
        diagnostic_code=None,
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["pass", "pass"], "auto_accept"),
        (["pass", "not_applicable"], "auto_accept"),
        (["pass", "missing"], "review_required"),
        (["pass", "conflict"], "rejected"),
        (["pass", "incomplete"], "rejected"),
    ],
)
def test_critical_proof_status_controls_disposition(
    statuses: list[str],
    expected: str,
) -> None:
    report = ProofReport(
        obligations=tuple(
            _obligation(index, status)
            for index, status in enumerate(statuses)
        ),
        search_complete="incomplete" not in statuses,
    )

    assert report.disposition.value == expected


def test_incomplete_search_rejects_even_when_every_obligation_passes() -> None:
    report = ProofReport(
        obligations=(_obligation(0, "pass"),),
        search_complete=False,
    )

    assert report.disposition == ProofDisposition.REJECTED
    assert report.blocking_obligation_ids == ("BH.PROOF.SEARCH.COMPLETE",)


def test_noncritical_missing_evidence_cannot_block_safe_output() -> None:
    report = ProofReport(
        obligations=(
            _obligation(0, "pass"),
            _obligation(1, "missing", critical=False),
        ),
        search_complete=True,
    )

    assert report.disposition == ProofDisposition.AUTO_ACCEPT


def test_duplicate_evidence_ids_do_not_inflate_independent_evidence_count() -> None:
    repeated = ProofEvidence(
        evidence_id="source:dimension:42",
        channel="dimension",
        source_ids=("entity-42",),
        measured=1000.0,
        expected=1000.0,
        tolerance=1.0,
    )
    report = ProofReport(
        obligations=(
            ProofObligation("proof-a", ProofStatus.PASS, True, (repeated,)),
            ProofObligation("proof-b", ProofStatus.PASS, True, (repeated, repeated)),
        ),
        search_complete=True,
    )

    assert report.independent_evidence_count == 1
    assert report.to_dict()["independent_evidence_count"] == 1


class _Box:
    min_x = 0.0
    min_y = 0.0
    max_x = 1.0
    max_y = 1.0


class _View:
    def __init__(self, handle: str):
        self.handle = handle
        self.name = handle
        self.bbox = _Box()


def _assessed_with(status: ProofStatus, *, soft_quality: float):
    pair = ViewPairHypothesis(
        hypothesis_id="view",
        rank=1,
        main=_View("main"),
        flange=_View("flange"),
        prior_cost=0.95,
        main_residual=0.0,
        flange_residual=0.0,
        main_axis="x",
        flange_axis="x",
    )
    hypothesis = AssemblyHypothesis(
        hypothesis_id="assembly",
        view_pair=pair,
        status=HypothesisStatus.VALID,
        rules=[
            RuleEvaluation("hard", True, True, 1.0, 1.0, "ok"),
            RuleEvaluation("soft", False, False, soft_quality, 1.0, "telemetry"),
        ],
        proof_obligations=(
            ProofObligation("proof-input", ProofStatus.PASS, True, ()),
            ProofObligation("proof-result", status, True, ()),
        ),
        annotation_consistency={"evidence_coverage": 0.0},
        semantic_cost=1.0,
    )
    return assess_solution(
        HypothesisSolveResult(hypothesis, [hypothesis], 1.0),
        DEFAULT_BH_KNOWLEDGE,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ProofStatus.PASS, AutomationDisposition.AUTO_ACCEPT),
        (ProofStatus.MISSING, AutomationDisposition.REVIEW_REQUIRED),
        (ProofStatus.CONFLICT, AutomationDisposition.REJECTED),
        (ProofStatus.INCOMPLETE, AutomationDisposition.REJECTED),
    ],
)
def test_proofs_not_confidence_thresholds_control_reasoning_disposition(
    status: ProofStatus,
    expected: AutomationDisposition,
) -> None:
    assessment = _assessed_with(status, soft_quality=0.0)

    assert assessment.confidence < DEFAULT_BH_KNOWLEDGE.minimum_auto_confidence
    assert assessment.disposition == expected
    assert assessment.proof_report.disposition.value == expected.value
    assert assessment.to_dict()["proof_report"]["disposition"] == expected.value
