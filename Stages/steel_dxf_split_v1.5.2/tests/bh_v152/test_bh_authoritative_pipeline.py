from __future__ import annotations

from pathlib import Path

from steel_dxf_split import bh_passes
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_passes import DEFAULT_BH_PASSES
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "samples" / "bh_pairs" / "3b2-cb-86_拆板前.dxf"


def _compile():
    return compile_bh_document(
        load_document(SOURCE),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=SOURCE,
    )


def test_default_passes_have_one_evidence_to_manufacturing_authority_chain() -> None:
    assert [stage.name for stage in DEFAULT_BH_PASSES] == [
        "source.decode",
        "source.normalize_and_partition",
        "drawing.parse_and_associate_annotations",
        "drawing.resolve_component_metadata",
        "hypotheses.solve_complete_component",
        "manufacturing.validate_assembly",
        "manufacturing.freeze_ir_and_prove",
        "quality.route",
    ]


def test_compile_result_contains_observed_information_ledger_not_static_prose() -> None:
    compiled = _compile()
    ledger = compiled.information_ledger

    assert ledger["source_entity_count"] > 0
    assert sum(ledger["authority_partition"].values()) == ledger[
        "source_entity_count"
    ]
    assert ledger["binding_counts"]["manufacturing"] > 0
    assert ledger["binding_counts"]["proof"] > 0
    assert not hasattr(bh_passes, "information_usage_model")


def test_post_selection_projections_do_not_appear_as_compiler_authorities() -> None:
    compiled = _compile()

    assert not hasattr(compiled, "physical_model")
    assert not hasattr(compiled, "semantic_graph")
    assert not hasattr(compiled, "contract_report")
    assert not hasattr(compiled, "risk_report")

    validation = compiled.assembly.diagnostics["compiler_validation"]["checks"]
    assert validation["two_physical_flange_plates"] is True
    assert validation["inner_contours_valid_and_contained"] is True
    assert validation["no_duplicate_cut_centers_within_plate"] is True

    proof_ids = {
        obligation.obligation_id for obligation in compiled.proof_report.obligations
    }
    assert {
        "BH.PROOF.ROLE.DECOMPOSITION",
        "BH.PROOF.CONTOUR.TOPOLOGY",
        "BH.PROOF.CUT.CONTAINMENT",
        "BH.PROOF.FLANGE.DEVELOPMENT",
        "BH.PROOF.MANUFACTURING_IR.PROVENANCE",
    }.issubset(proof_ids)
