from __future__ import annotations

from pathlib import Path

from steel_dxf_split.bh_annotations import extract_annotation_model
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _compile(stem: str):
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    return compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )


def test_every_source_fact_is_inventoried_and_has_an_explicit_authority_bucket() -> None:
    result = _compile("3b2-cb-86")
    ledger = result.assembly.diagnostics["source_information_ledger"]

    total = len(result.source_ir.entities)
    assert ledger["source_entity_count"] == total
    assert sum(ledger["inventory_by_semantic_role"].values()) == total
    assert sum(ledger["inventory_by_entity_type"].values()) == total
    assert sum(ledger["authority_partition"].values()) == total
    assert ledger["binding_counts"]["drawing_graph"] > 0
    assert ledger["binding_counts"]["metadata"] > 0
    assert ledger["binding_counts"]["manufacturing"] > 0
    assert ledger["binding_counts"]["proof"] > 0
    assert ledger["semantic_object_counts"]["dimension"] > 0
    assert ledger["semantic_relation_counts"]["measures"] > 0
    assert ledger["policy"]["retained_context_can_authorize_manufacturing"] is False
    assert ledger["policy"]["manual_split_used"] is False


def test_development_semantics_bind_source_geometry_to_the_approved_profile_policy() -> None:
    rigid = _compile("2b1-cb-40")
    kinked = _compile("2b2-cb-10")

    rigid_assessment = rigid.assembly.diagnostics["flange_development"][
        "semantic_assessment"
    ]
    kinked_assessment = kinked.assembly.diagnostics["flange_development"][
        "semantic_assessment"
    ]

    assert rigid_assessment["geometry_determinacy"] == "rigid_projection_determined"
    assert rigid_assessment["requires_unfolding_policy"] is False
    assert rigid_assessment["fabrication_authority"] == (
        "profile_authorized_source_geometry"
    )
    assert rigid_assessment["part_length_consistency"] == "partial_supporting_only"
    assert rigid_assessment["profile_authorized"] is True
    assert rigid_assessment["certificate_kind"] == (
        "profile_authorized_rigid_development"
    )

    assert kinked_assessment["geometry_determinacy"] == "unfolding_policy_required"
    assert kinked_assessment["requires_unfolding_policy"] is False
    assert kinked_assessment["fabrication_authority"] == (
        "profile_authorized_source_geometry"
    )
    assert kinked_assessment["part_length_consistency"] == "consistent_supporting_only"
    assert kinked_assessment["profile_authorized"] is True
    assert kinked_assessment["certificate_kind"] == (
        "profile_authorized_cranked_development"
    )
    assert kinked.assessment.disposition.value == "auto_accept"

    annotation = extract_annotation_model(kinked.drawing_graph)
    assert not any(
        item.value is not None
        and abs(item.value - kinked.assembly.flange_plates[0].bbox.width) <= 0.65
        and item.scope == "view_extent"
        for item in annotation.dimensions
    )


def test_development_proof_exports_the_profile_authorized_source_certificates() -> None:
    rigid = _compile("2b1-cb-40")
    kinked = _compile("2b2-cb-10")

    rigid_proof = next(
        item
        for item in rigid.proof_report.obligations
        if item.obligation_id == "BH.PROOF.FLANGE.DEVELOPMENT"
    )
    kinked_proof = next(
        item
        for item in kinked.proof_report.obligations
        if item.obligation_id == "BH.PROOF.FLANGE.DEVELOPMENT"
    )

    assert rigid_proof.diagnostic_code is None
    assert kinked_proof.diagnostic_code is None
    assert any(
        item.channel == "profile_authorized_rigid_development"
        for item in rigid_proof.evidence
    )
    assert any(
        item.channel == "profile_authorized_cranked_development"
        for item in kinked_proof.evidence
    )
