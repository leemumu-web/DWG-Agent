from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from math import isfinite

if TYPE_CHECKING:
    from .bh_geometry import PartBlock
    from .bh_metric_scale import ViewMetricScaleResolution
    from .bh_models import BHAssembly
    from .bh_proofs import ProofObligation
    from .bh_validator import BHValidationReport


class HypothesisStatus(str, Enum):
    GENERATED = "generated"
    LOWERING_FAILED = "lowering_failed"
    REJECTED = "rejected"
    VALID = "valid"
    SELECTED = "selected"


@dataclass(frozen=True, slots=True)
class ViewPairHypothesis:
    """One possible semantic interpretation of two drawing views.

    The score is a *prior cost*: lower is better.  It is deliberately kept
    separate from manufacturing constraints, because a dimensionally plausible
    view pair can still lower to an impossible plate assembly.
    """

    hypothesis_id: str
    rank: int
    main: PartBlock
    flange: PartBlock
    prior_cost: float
    main_residual: float
    flange_residual: float
    main_axis: str
    flange_axis: str
    metric_scale: ViewMetricScaleResolution | None = None
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "rank": self.rank,
            "main": {
                "name": self.main.name,
                "handle": self.main.handle,
                "region_id": getattr(self.main, "region_id", None),
            },
            "flange": {
                "name": self.flange.name,
                "handle": self.flange.handle,
                "region_id": getattr(self.flange, "region_id", None),
            },
            "prior_cost": self.prior_cost,
            "main_residual": self.main_residual,
            "flange_residual": self.flange_residual,
            "main_axis": self.main_axis,
            "flange_axis": self.flange_axis,
            "metric_scale": (
                self.metric_scale.to_dict()
                if self.metric_scale is not None
                else {
                    "mode": "identity",
                    "factor": 1.0,
                    "reason": "legacy_identity",
                    "evidence": [],
                }
            ),
            "features": self.features,
        }


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    rule_id: str
    hard: bool
    satisfied: bool
    quality: float
    weight: float
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def penalty(self) -> float:
        return self.weight * (1.0 - max(0.0, min(1.0, self.quality)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "hard": self.hard,
            "satisfied": self.satisfied,
            "quality": self.quality,
            "weight": self.weight,
            "penalty": self.penalty,
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class AssemblyHypothesis:
    hypothesis_id: str
    view_pair: ViewPairHypothesis
    status: HypothesisStatus = HypothesisStatus.GENERATED
    assembly: BHAssembly | None = None
    validation: BHValidationReport | None = None
    rules: list[RuleEvaluation] = field(default_factory=list)
    proof_obligations: tuple[ProofObligation, ...] = ()
    annotation_consistency: dict[str, Any] = field(default_factory=dict)
    semantic_cost: float = float("inf")
    score_breakdown: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    error: str | None = None

    @property
    def hard_pass(self) -> bool:
        return bool(self.rules) and all(rule.satisfied for rule in self.rules if rule.hard)

    @property
    def soft_penalty(self) -> float:
        return sum(rule.penalty for rule in self.rules if not rule.hard)

    def to_dict(self, *, include_assembly: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hypothesis_id": self.hypothesis_id,
            "status": self.status.value,
            "view_pair": self.view_pair.to_dict(),
            "hard_pass": self.hard_pass,
            "semantic_cost": self.semantic_cost if isfinite(self.semantic_cost) else None,
            "confidence": self.confidence,
            "score_breakdown": dict(self.score_breakdown),
            "error": self.error,
            "annotation_consistency": self.annotation_consistency,
            "rules": [rule.to_dict() for rule in self.rules],
            "proof_obligations": [
                obligation.to_dict() for obligation in self.proof_obligations
            ],
            "validation": self.validation.to_dict() if self.validation else None,
        }
        if include_assembly and self.assembly is not None:
            payload["assembly"] = {
                "part_number": self.assembly.metadata.part_number,
                "plates": [
                    {
                        "label": plate.label,
                        "role": plate.role.value,
                        "quantity": plate.quantity,
                        "bbox_mm": [plate.bbox.width, plate.bbox.height],
                        "thickness_mm": plate.thickness,
                        "circular_cut_count": len(plate.circular_cuts),
                        "inner_contour_count": len(plate.inner_contours),
                        "provenance": plate.provenance,
                    }
                    for plate in self.assembly.plates
                ],
            }
        return payload


@dataclass(slots=True)
class HypothesisSolveResult:
    selected: AssemblyHypothesis
    hypotheses: list[AssemblyHypothesis]
    score_margin: float
    search_complete: bool = True
    generated_candidate_count: int = 0
    evaluated_candidate_count: int = 0
    pruned_candidate_count: int = 0
    termination_reason: str = "exhausted"

    def __post_init__(self) -> None:
        if self.generated_candidate_count == 0:
            self.generated_candidate_count = len(self.hypotheses)
        if self.evaluated_candidate_count == 0:
            self.evaluated_candidate_count = len(self.hypotheses)
        if not self.search_complete and self.termination_reason == "exhausted":
            self.termination_reason = "caller_reported_incomplete"

    @property
    def valid_hypotheses(self) -> list[AssemblyHypothesis]:
        return [item for item in self.hypotheses if item.hard_pass]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_hypothesis_id": self.selected.hypothesis_id,
            "score_margin": self.score_margin,
            "valid_hypothesis_count": len(self.valid_hypotheses),
            "generated_hypothesis_count": len(self.hypotheses),
            "search_complete": self.search_complete,
            "generated_candidate_count": self.generated_candidate_count,
            "evaluated_candidate_count": self.evaluated_candidate_count,
            "pruned_candidate_count": self.pruned_candidate_count,
            "unevaluated_candidate_count": max(
                0,
                self.generated_candidate_count
                - self.evaluated_candidate_count
                - self.pruned_candidate_count,
            ),
            "termination_reason": self.termination_reason,
            "hypotheses": [item.to_dict(include_assembly=item is self.selected) for item in self.hypotheses],
        }
