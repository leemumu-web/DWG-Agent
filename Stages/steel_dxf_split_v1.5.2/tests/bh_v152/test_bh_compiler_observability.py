from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_frontend import build_bh_document_ir
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_semantics import parse_bh_metadata_ir, select_bh_views_ir
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.pipeline import SplitOptions, split_dxf

ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"
REFERENCE_DIR = ROOT / "samples" / "bh_pairs"
STEMS = [
    "2b1-cb-18",
    "2b1-cb-26",
    "2b1-cb-29",
    "2b1-cb-35",
    "2b1-cb-40",
    "2b1-cb-44",
    "2b1-cb-46",
    "2b2-cb-10",
    "2t1-cb-4",
    "2t2-cb-37",
    "2t2-cb-46",
    "2t2-cb-159",
    "3b1-cb-15",
    "3b2-cb-86",
]


@pytest.mark.parametrize("stem", STEMS)
def test_compiler_trace_is_complete_and_json_serializable(stem: str) -> None:
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    trace = result.trace.to_dict()
    json.dumps(trace, ensure_ascii=False)
    assert [stage["name"] for stage in trace["stages"]] == [
        "source.decode",
        "source.normalize_and_partition",
        "drawing.parse_and_associate_annotations",
        "drawing.resolve_component_metadata",
        "hypotheses.solve_complete_component",
        "manufacturing.validate_assembly",
        "manufacturing.freeze_ir_and_prove",
        "quality.route",
    ]
    assert {decision["name"] for decision in trace["decisions"]} == {
        "material_table_row",
        "complete_component_hypothesis",
        "automation_disposition",
    }
    assert trace["minimum_confidence"] >= 0.60
    assert all(trace["invariants"].values())
    assert result.document_summary["semantic_counts"]["cut_helper"] >= 0


def test_metadata_parser_uses_spatial_row_not_largest_number() -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    doc = load_document(source)
    ir = build_bh_document_ir(doc, source_path=source)
    original = parse_bh_metadata_ir(ir, source).metadata
    table = ir.block_by_handle(original.material_table_handle or "")
    # Inject a much larger unrelated number into the same block but far from
    # the material row. The old max-number heuristic would select it.
    block_layout = doc.blocks[table.name]
    block_layout.add_text("99999999", height=30).set_placement((0, 0))
    mutated_ir = build_bh_document_ir(doc, source_path=source)
    parsed = parse_bh_metadata_ir(mutated_ir, source).metadata
    assert parsed.nominal_length == 1032.0
    assert parsed.part_number == "2b1-cb-26"


def test_ir_block_order_does_not_change_semantics() -> None:
    source = PAIR_DIR / "2t2-cb-159_拆板前.dxf"
    doc = load_document(source)
    ir = build_bh_document_ir(doc, source_path=source)
    metadata = parse_bh_metadata_ir(ir, source)
    views = select_bh_views_ir(ir, metadata.metadata)
    ir.blocks.reverse()
    metadata_reversed = parse_bh_metadata_ir(ir, source)
    views_reversed = select_bh_views_ir(ir, metadata_reversed.metadata)
    assert metadata_reversed.metadata == metadata.metadata
    assert views_reversed.main.handle == views.main.handle
    assert views_reversed.flange.handle == views.flange.handle


@pytest.mark.skipif(os.name == "nt", reason="Production preview rendering is Linux-only")
def test_report_exposes_observed_information_ledger_and_evidence(tmp_path: Path) -> None:
    source = PAIR_DIR / "3b2-cb-86_拆板前.dxf"
    result = split_dxf(
        source,
        tmp_path,
        SplitOptions(
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        ),
    )
    compiler = result.report["compiler"]
    information = result.report["source_information_ledger"]
    assert compiler["version"] == "1.5.2"
    assert compiler["minimum_confidence"] >= 0.60
    assert information["source_entity_count"] > 0
    assert sum(information["authority_partition"].values()) == information[
        "source_entity_count"
    ]
    assert information["binding_counts"]["drawing_graph"] > 0
    assert information["binding_counts"]["manufacturing"] > 0
    assert result.report["diagnostics"]["annotation_consistency"]["bolt_mark_diameters_supported"]


def test_source_has_no_sample_specific_branching() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "steel_dxf_split").glob("bh_*.py")
    ).lower()
    for stem in STEMS:
        assert stem not in sources
