from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from steel_dxf_split import bh_development
from steel_dxf_split.bh_compare import compare_bh_to_manual
from steel_dxf_split.bh_compiler import BHCompiler
from steel_dxf_split.bh_knowledge import (
    BHKnowledgeBase,
    DEFAULT_BH_KNOWLEDGE,
    DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
)
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _compile(stem: str, *, knowledge: BHKnowledgeBase = DEFAULT_BH_KNOWLEDGE):
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    return BHCompiler(knowledge=knowledge).compile(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )


@pytest.mark.parametrize(
    ("stem", "expected_lengths", "expected_quantities", "evidence_channel"),
    [
        (
            "2b1-cb-40",
            [2538.0, 2383.037],
            [1, 1],
            "profile_authorized_rigid_development",
        ),
        (
            "2b2-cb-10",
            [11294.0],
            [2],
            "profile_authorized_cranked_development",
        ),
    ],
)
def test_profile_authorized_development_is_production_ready(
    stem: str,
    expected_lengths: list[float],
    expected_quantities: list[int],
    evidence_channel: str,
) -> None:
    compiled = _compile(stem)

    assert compiled.assessment.disposition.value == "auto_accept"
    assert [plate.bbox.width for plate in compiled.assembly.flange_plates] == (
        pytest.approx(expected_lengths, abs=0.01)
    )
    assert [plate.quantity for plate in compiled.assembly.flange_plates] == (
        expected_quantities
    )

    proof = next(
        item
        for item in compiled.proof_report.obligations
        if item.obligation_id == "BH.PROOF.FLANGE.DEVELOPMENT"
    )
    assert proof.status.value == "pass"
    assert any(item.channel == evidence_channel for item in proof.evidence)

    development = compiled.assembly.diagnostics["flange_development"]
    certificate = development["certificate"]
    assert certificate["authorized"] is True
    assert certificate["policy"]["derived_length_rounding"] == "floor"
    assert certificate["policy"]["derived_length_quantum_mm"] == 1.0


def test_default_knowledge_declares_the_authorized_development_policy() -> None:
    policy = DEFAULT_BH_KNOWLEDGE.flange_development_policy

    assert policy.enabled is True
    assert policy.profile_id == DEFAULT_TEKLA_BH_SOURCE_CONTRACT.export_profile
    assert policy.derived_length_rounding == "floor"
    assert policy.derived_length_quantum_mm == 1.0
    assert policy.preserve_direct_projection is True
    assert policy.require_unique_cranked_candidate is True


def test_derived_length_quantization_floors_only_the_derived_value() -> None:
    quantize = getattr(bh_development, "quantize_derived_flange_length")

    assert (
        quantize(
            2538.2049559516026,
            DEFAULT_BH_KNOWLEDGE.flange_development_policy,
        )
        == 2538.0
    )
    assert (
        quantize(
            11294.91280748483,
            DEFAULT_BH_KNOWLEDGE.flange_development_policy,
        )
        == 11294.0
    )


@pytest.mark.parametrize(
    ("candidates", "expected_matches"),
    [
        ((11294.9, 11295.1), 2),
        ((11290.0, 11300.0), 0),
    ],
)
def test_cranked_candidate_requires_one_display_precision_match(
    candidates: tuple[float, ...],
    expected_matches: int,
) -> None:
    select = getattr(
        bh_development,
        "select_profile_authorized_cranked_candidate",
    )

    result = select(
        candidates,
        nominal_length_mm=11295.0,
        nominal_text="11295",
        policy=DEFAULT_BH_KNOWLEDGE.flange_development_policy,
        geometric_tolerance_mm=0.15,
    )

    assert result.match_count == expected_matches
    assert result.authorized is False
    assert result.selected_raw_length_mm is None
    assert result.quantized_length_mm is None


@pytest.mark.parametrize(
    "policy",
    [
        replace(DEFAULT_BH_KNOWLEDGE.flange_development_policy, enabled=False),
        replace(
            DEFAULT_BH_KNOWLEDGE.flange_development_policy,
            profile_id="unapproved_tekla_profile",
        ),
    ],
)
def test_unapproved_development_policy_cannot_authorize_production(policy) -> None:
    knowledge = replace(
        DEFAULT_BH_KNOWLEDGE,
        flange_development_policy=policy,
    )

    compiled = _compile("2b1-cb-40", knowledge=knowledge)

    assert compiled.assessment.disposition.value == "review_required"
    development = compiled.assembly.diagnostics["flange_development"]
    assert development["certificate"]["authorized"] is False
    proof = next(
        item
        for item in compiled.proof_report.obligations
        if item.obligation_id == "BH.PROOF.FLANGE.DEVELOPMENT"
    )
    assert proof.status.value == "missing"


@pytest.mark.parametrize("stem", ["2b1-cb-40", "2b2-cb-10"])
def test_profile_authorized_development_matches_the_manual_geometry_offline(
    stem: str,
) -> None:
    compiled = _compile(stem)
    manual = PAIR_DIR / f"{stem}_拆板后.dxf"

    comparison = compare_bh_to_manual(compiled.assembly, manual)

    assert comparison.ok is True
    assert comparison.checks["plate_bbox_matches"] is True
    assert comparison.checks["plate_boundaries_match"] is True
