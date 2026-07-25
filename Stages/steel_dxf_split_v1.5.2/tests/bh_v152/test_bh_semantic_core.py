from __future__ import annotations

from steel_dxf_split.bh_hypothesis import (
    AssemblyHypothesis,
    HypothesisSolveResult,
    HypothesisStatus,
    RuleEvaluation,
    ViewPairHypothesis,
)
from steel_dxf_split.bh_knowledge import DEFAULT_BH_KNOWLEDGE
from steel_dxf_split.bh_reasoning import AutomationDisposition, assess_solution


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


def _hypothesis(*, prior: float, soft_quality: float, coverage: float) -> AssemblyHypothesis:
    pair = ViewPairHypothesis(
        hypothesis_id="view",
        rank=1,
        main=_View("main"),
        flange=_View("flange"),
        prior_cost=prior,
        main_residual=prior / 2,
        flange_residual=prior / 2,
        main_axis="x",
        flange_axis="x",
    )
    return AssemblyHypothesis(
        hypothesis_id="assembly",
        view_pair=pair,
        status=HypothesisStatus.VALID,
        rules=[
            RuleEvaluation("hard", True, True, 1.0, 1.0, "ok"),
            RuleEvaluation("soft", False, soft_quality >= 0.8, soft_quality, 1.0, "quality"),
        ],
        annotation_consistency={"evidence_coverage": coverage},
        semantic_cost=prior + (1.0 - soft_quality),
    )


def test_high_quality_hypothesis_without_explicit_proofs_requires_review() -> None:
    hypothesis = _hypothesis(prior=0.01, soft_quality=0.98, coverage=0.75)
    solve = HypothesisSolveResult(hypothesis, [hypothesis], 1.0)
    assessment = assess_solution(solve, DEFAULT_BH_KNOWLEDGE)
    assert assessment.disposition == AutomationDisposition.REVIEW_REQUIRED
    assert assessment.confidence >= DEFAULT_BH_KNOWLEDGE.review_confidence


def test_sparse_or_ambiguous_scores_cannot_replace_explicit_proofs() -> None:
    selected = _hypothesis(prior=0.10, soft_quality=0.72, coverage=0.0)
    alternative = _hypothesis(prior=0.11, soft_quality=0.71, coverage=0.0)
    solve = HypothesisSolveResult(selected, [selected, alternative], 0.01)
    assessment = assess_solution(solve, DEFAULT_BH_KNOWLEDGE)
    assert assessment.disposition == AutomationDisposition.REVIEW_REQUIRED
    assert assessment.confidence < DEFAULT_BH_KNOWLEDGE.minimum_auto_confidence
    assert assessment.separation_quality < 0.2
    assert "ambiguous_complete_hypotheses" in assessment.risk_flags


def test_hard_rule_failure_is_rejected() -> None:
    hypothesis = _hypothesis(prior=0.01, soft_quality=1.0, coverage=1.0)
    hypothesis.rules[0] = RuleEvaluation("hard", True, False, 0.0, 1.0, "failed")
    solve = HypothesisSolveResult(hypothesis, [hypothesis], 1.0)
    assessment = assess_solution(solve, DEFAULT_BH_KNOWLEDGE)
    assert assessment.disposition == AutomationDisposition.REJECTED
    assert assessment.hard_failures == ("hard",)


def test_confidence_is_decomposed_into_auditable_terms() -> None:
    hypothesis = _hypothesis(prior=0.05, soft_quality=0.90, coverage=0.50)
    solve = HypothesisSolveResult(hypothesis, [hypothesis], 1.0)
    assessment = assess_solution(solve, DEFAULT_BH_KNOWLEDGE)
    assert set(assessment.score_breakdown) == {
        "projection_model_fit",
        "semantic_rule_quality",
        "hypothesis_separation",
        "independent_evidence_coverage",
    }
    assert abs(sum(assessment.score_breakdown.values()) - assessment.confidence) < 1e-12


def test_public_package_import_is_lazy_and_does_not_load_geometry_stack() -> None:
    import json
    import subprocess
    import sys

    code = r'''
import json, sys
import steel_dxf_split
print(json.dumps({
    "version": steel_dxf_split.__version__,
    "ezdxf_loaded": "ezdxf" in sys.modules,
    "shapely_loaded": "shapely" in sys.modules,
}))
'''
    import os
    from pathlib import Path

    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    payload = json.loads(result.stdout)
    assert payload == {
        "version": "1.5.2",
        "ezdxf_loaded": False,
        "shapely_loaded": False,
    }
