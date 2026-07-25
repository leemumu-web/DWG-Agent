from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import exp
from typing import Any

from .bh_fingerprint import manufacturing_fingerprint
from .bh_hypothesis import AssemblyHypothesis, HypothesisSolveResult
from .bh_knowledge import BHKnowledgeBase
from .bh_proofs import (
    ProofDisposition,
    ProofEvidence,
    ProofObligation,
    ProofReport,
    ProofStatus,
)


class AutomationDisposition(str, Enum):
    AUTO_ACCEPT = "auto_accept"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ReasoningAssessment:
    disposition: AutomationDisposition
    confidence: float
    model_fit: float
    rule_quality: float
    separation_quality: float
    evidence_coverage: float
    proof_report: ProofReport
    hard_failures: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    evidence_channels: tuple[str, ...] = ()
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "confidence": self.confidence,
            "model_fit": self.model_fit,
            "rule_quality": self.rule_quality,
            "separation_quality": self.separation_quality,
            "evidence_coverage": self.evidence_coverage,
            "proof_report": self.proof_report.to_dict(),
            "hard_failures": list(self.hard_failures),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "risk_flags": list(self.risk_flags),
            "evidence_channels": list(self.evidence_channels),
            "score_breakdown": dict(self.score_breakdown),
        }


def _weighted_soft_quality(hypothesis: AssemblyHypothesis) -> float:
    rules = [rule for rule in hypothesis.rules if not rule.hard]
    denominator = sum(rule.weight for rule in rules)
    if denominator <= 0.0:
        return 0.0
    return sum(rule.quality * rule.weight for rule in rules) / denominator


def _proof_report(solve: HypothesisSolveResult) -> ProofReport:
    selected = solve.selected
    obligations = selected.proof_obligations
    if not obligations:
        # Fail closed for incomplete/custom solver integrations.  Hard rules
        # still preserve conflicts, but a rule set is not a substitute for the
        # explicit evidence-bearing proof obligations required by production.
        hard_rule_obligations = tuple(
            ProofObligation(
                obligation_id=f"BH.PROOF.RULE.{rule.rule_id}",
                status=ProofStatus.PASS if rule.satisfied else ProofStatus.CONFLICT,
                critical=True,
                evidence=(),
                diagnostic_code=None if rule.satisfied else "BH-PROOF-HARD-RULE-FAIL",
            )
            for rule in selected.rules
            if rule.hard
        )
        obligations = (
            *hard_rule_obligations,
            ProofObligation(
                obligation_id="BH.PROOF.INTERNAL.EXPLICIT_OBLIGATIONS_PRESENT",
                status=ProofStatus.MISSING,
                critical=True,
                evidence=(),
                diagnostic_code="BH-PROOF-OBLIGATIONS-MISSING",
            ),
        )
    return ProofReport(obligations=obligations, search_complete=solve.search_complete)


def _with_solution_uniqueness(
    report: ProofReport,
    solve: HypothesisSolveResult,
) -> ProofReport:
    valid = solve.valid_hypotheses
    if not valid or any(item.assembly is None for item in valid):
        # Incomplete/custom hypotheses may not carry a lowered assembly.  The
        # missing-obligation proof above prevents them from reaching production.
        return report
    fingerprints = tuple(
        (item, manufacturing_fingerprint(item.assembly))
        for item in valid
        if item.assembly is not None
    )
    distinct = {fingerprint for _, fingerprint in fingerprints}
    unique_meaning = len(distinct) == 1
    obligation = ProofObligation(
        "BH.PROOF.SEARCH.UNIQUE_MANUFACTURING_RESULT",
        ProofStatus.PASS if unique_meaning else ProofStatus.MISSING,
        True,
        tuple(
            ProofEvidence(
                evidence_id=f"manufacturing:{fingerprint}",
                channel="candidate_manufacturing_ir",
                source_ids=(
                    str(item.view_pair.main.handle),
                    str(item.view_pair.flange.handle),
                ),
                measured=fingerprint,
                expected="one canonical manufacturing result",
                tolerance=None,
            )
            for item, fingerprint in fingerprints
        ),
        None if unique_meaning else "BH-PROOF-SOLUTION-AMBIGUOUS",
    )
    return ProofReport(
        obligations=(*report.obligations, obligation),
        search_complete=report.search_complete,
    )


def assess_solution(
    solve: HypothesisSolveResult,
    knowledge: BHKnowledgeBase,
) -> ReasoningAssessment:
    selected = solve.selected
    hard_failures = tuple(
        rule.rule_id for rule in selected.rules if rule.hard and not rule.satisfied
    )
    proof_report = _with_solution_uniqueness(_proof_report(solve), solve)

    model_fit = exp(-2.0 * max(0.0, selected.view_pair.prior_cost))
    rule_quality = _weighted_soft_quality(selected)
    if len(solve.valid_hypotheses) <= 1:
        separation_quality = 1.0
    else:
        separation_quality = 1.0 - exp(-max(0.0, solve.score_margin) / 0.10)
    evidence_coverage = float(
        selected.annotation_consistency.get("evidence_coverage", 0.0) or 0.0
    )

    # Geometry and constraints dominate.  Annotation coverage is deliberately
    # supportive: a sparse but geometrically unambiguous drawing can still be
    # compiled, while contradictory annotations lower the rule-quality term.
    confidence = (
        0.35 * model_fit
        + 0.35 * rule_quality
        + 0.20 * separation_quality
        + 0.10 * evidence_coverage
    )
    confidence = max(0.0, min(1.0, confidence))

    reasons = [
        "A complete web/flange interpretation passed all hard manufacturing invariants.",
        "The confidence combines projection fit, engineering-rule quality, alternative separation and independent evidence coverage.",
    ]
    warnings: list[str] = []
    risk_flags: list[str] = []
    evidence_presence = selected.annotation_consistency.get("evidence_presence", {}) or {}
    evidence_channels = ["source_geometry", "profile_semantics", "manufacturing_invariants"]
    evidence_channels.extend(
        name for name, present in evidence_presence.items() if bool(present)
    )
    if len(solve.valid_hypotheses) > 1:
        evidence_channels.append("alternative_hypotheses")
    if len(solve.valid_hypotheses) > 1 and solve.score_margin < knowledge.minimum_hypothesis_margin:
        warnings.append("Competing complete manufacturing hypotheses have a small score margin.")
        risk_flags.append("ambiguous_complete_hypotheses")
    if evidence_coverage < 0.25:
        warnings.append("The drawing contains little independent dimension/mark evidence; confidence relies mainly on geometry.")
        risk_flags.append("sparse_independent_annotations")
    repair_rule = next(
        (rule for rule in selected.rules if rule.rule_id == "BH.SOFT.MINIMUM_GEOMETRIC_REPAIR"),
        None,
    )
    if repair_rule is not None and repair_rule.quality < 0.65:
        risk_flags.append("high_geometry_repair_burden")
    trace_rule = next(
        (rule for rule in selected.rules if rule.rule_id == "BH.SOFT.EVIDENCE_TRACEABILITY"),
        None,
    )
    if trace_rule is not None and trace_rule.quality < 0.90:
        risk_flags.append("incomplete_source_traceability")

    disposition = {
        ProofDisposition.AUTO_ACCEPT: AutomationDisposition.AUTO_ACCEPT,
        ProofDisposition.REVIEW_REQUIRED: AutomationDisposition.REVIEW_REQUIRED,
        ProofDisposition.REJECTED: AutomationDisposition.REJECTED,
    }[proof_report.disposition]
    if disposition == AutomationDisposition.REJECTED:
        reasons.append(
            "One or more critical proof obligations conflict, are incomplete, or the search is incomplete."
        )
    elif disposition == AutomationDisposition.REVIEW_REQUIRED:
        reasons.append("Critical proof evidence is missing and requires engineering review.")
    else:
        reasons.append("Every applicable critical proof obligation passed.")

    return ReasoningAssessment(
        disposition=disposition,
        confidence=confidence,
        model_fit=model_fit,
        rule_quality=rule_quality,
        separation_quality=separation_quality,
        evidence_coverage=evidence_coverage,
        proof_report=proof_report,
        hard_failures=hard_failures,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        risk_flags=tuple(sorted(set(risk_flags))),
        evidence_channels=tuple(sorted(set(evidence_channels))),
        score_breakdown={
            "projection_model_fit": 0.35 * model_fit,
            "semantic_rule_quality": 0.35 * rule_quality,
            "hypothesis_separation": 0.20 * separation_quality,
            "independent_evidence_coverage": 0.10 * evidence_coverage,
        },
    )
