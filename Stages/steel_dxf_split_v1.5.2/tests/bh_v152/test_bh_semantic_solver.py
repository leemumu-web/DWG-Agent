from __future__ import annotations

import json
from pathlib import Path

import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_hypothesis import HypothesisStatus
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.dxf_io import load_document

ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"
REPRESENTATIVE = [
    "2b1-cb-18",      # unequal flanges and many holes
    "2b1-cb-40",      # variable-height BH
    "2b2-cb-10",      # cranked/developed flange
    "3b1-cb-15",      # hole-less web and hidden bridge evidence
    "3b2-cb-86",      # cuts owned by a flange plate
    "z-4-cb-42",      # hidden-arc manufacturing evidence
]


@pytest.mark.parametrize("stem", REPRESENTATIVE)
def test_complete_hypothesis_solver_preserves_evidence_and_proofs(stem: str) -> None:
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    solve = result.hypotheses
    selected = solve.selected
    assert selected.status == HypothesisStatus.SELECTED
    assert selected.hard_pass
    assert selected.assembly is result.assembly
    assert selected.validation is not None and selected.validation.ok
    assert len(solve.hypotheses) >= 1
    assert all(rule.satisfied for rule in selected.rules if rule.hard)
    assert json.loads(json.dumps(solve.to_dict(), ensure_ascii=False))["selected_hypothesis_id"]

    assert result.drawing_graph.nodes
    assert result.drawing_graph.edges
    assert len(result.manufacturing_ir.plates) == 3
    assert result.proof_report.obligations
    assert result.assessment.disposition.value in {"auto_accept", "review_required"}
    assert all(plate.provenance.get("source_entity_count", 0) > 0 for plate in result.assembly.plates)


def test_solver_report_contains_rejected_or_alternative_hypotheses() -> None:
    source = PAIR_DIR / "3b2-cb-86_拆板前.dxf"
    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    payload = result.hypotheses.to_dict()
    assert payload["generated_hypothesis_count"] >= payload["valid_hypothesis_count"] >= 1
    assert payload["hypotheses"][0]["view_pair"]["rank"] == 1
    assert result.assembly.diagnostics["knowledge_base"]["ontology_version"] == "BH-MFG-3.1"


def test_no_sample_specific_names_in_semantic_solver() -> None:
    source_files = [
        ROOT / "src" / "steel_dxf_split" / name
        for name in (
            "bh_knowledge.py",
            "bh_hypothesis.py",
            "bh_constraints.py",
            "bh_solver.py",
            "bh_passes.py",
        )
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in source_files)
    stems = [path.name.split("_拆板前")[0].lower() for path in PAIR_DIR.glob("*_拆板前.dxf")]
    for stem in stems:
        assert stem not in combined
