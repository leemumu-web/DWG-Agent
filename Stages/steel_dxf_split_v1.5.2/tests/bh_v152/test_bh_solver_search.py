from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import ezdxf
import pytest

import steel_dxf_split.bh_passes as bh_passes
from steel_dxf_split.bh_annotations import AnnotationModel
from steel_dxf_split.bh_compiler import BHCompilationRejected, BHCompiler
from steel_dxf_split.bh_errors import BHNoValidHypothesis
from steel_dxf_split.bh_frames import infer_member_frames
from steel_dxf_split.bh_knowledge import (
    DEFAULT_BH_KNOWLEDGE,
    DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
)
from steel_dxf_split.bh_hypothesis import HypothesisSolveResult, HypothesisStatus
from steel_dxf_split.bh_models import BHMetadata, HProfile
from steel_dxf_split.bh_reasoning import assess_solution
from steel_dxf_split.bh_regions import build_view_regions, materialize_lowering_ir
from steel_dxf_split.bh_solver import (
    enumerate_view_pair_hypotheses,
    solve_component_hypotheses,
)
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.bh_source import decode_source_document


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _add_rectangle(
    block,
    width: float,
    height: float,
    *,
    layer: str = "Part",
) -> None:
    for start, end in (
        ((0.0, 0.0), (width, 0.0)),
        ((width, 0.0), (width, height)),
        ((width, height), (0.0, height)),
        ((0.0, height), (0.0, 0.0)),
    ):
        block.add_line(start, end, dxfattribs={"layer": layer})


def _add_open_cross(block, width: float, height: float) -> None:
    for start, end in (
        ((0.0, 0.0), (width, 0.0)),
        ((0.0, height), (width, height)),
        ((0.0, 0.0), (width, height)),
        ((0.0, height), (width, 0.0)),
    ):
        block.add_line(start, end, dxfattribs={"layer": "Part"})


def _search_fixture():
    doc = ezdxf.new("R2007")
    doc.header["$INSUNITS"] = 4
    for index in range(4):
        block = doc.blocks.new(f"JUNK_WEB_{index}")
        _add_open_cross(block, 1000.0, 300.0)
        doc.modelspace().add_blockref(block.name, (index * 2000.0, 0.0))
    for index in range(4):
        block = doc.blocks.new(f"JUNK_FLANGE_{index}")
        _add_open_cross(block, 1000.0, 200.0)
        doc.modelspace().add_blockref(block.name, (index * 2000.0, 2000.0))
    web = doc.blocks.new("TRUE_WEB")
    _add_rectangle(web, 1000.0, 276.0)
    doc.modelspace().add_blockref(web.name, (20000.0, 0.0))
    flange = doc.blocks.new("TRUE_FLANGE")
    _add_rectangle(flange, 1000.0, 200.0)
    doc.modelspace().add_blockref(flange.name, (20000.0, 2000.0))
    metadata = BHMetadata(
        "BH-SEARCH-TEST",
        HProfile(300.0, 200.0, 8.0, 12.0, "BH300*200*8*12"),
        1000.0,
        "Q355B",
        1.0,
    )
    source = decode_source_document(doc)
    frame = infer_member_frames(source).selected
    regions = build_view_regions(source, frame)
    return source, materialize_lowering_ir(source, regions, frame), metadata


def test_complete_search_recovers_the_only_valid_pair_beyond_initial_frontier() -> None:
    source, ir, metadata = _search_fixture()

    result = solve_component_hypotheses(
        ir=ir,
        source_ir=source,
        metadata=metadata,
        annotations=AnnotationModel(),
        knowledge=DEFAULT_BH_KNOWLEDGE,
        metadata_candidates=(
            {
                "profile": metadata.profile.raw_text,
                "row": [
                    metadata.part_number,
                    metadata.profile.raw_text,
                    "1000",
                    "Q355B",
                ],
            },
        ),
        metadata_margin=100.0,
        metadata_source_ids=("metadata:test",),
    ).solve

    containers = {
        item.container_id: item.block_name
        for item in source.containers
    }
    assert {
        containers[item]
        for item in result.selected.view_pair.main.source_view.container_ids
    } == {"TRUE_WEB"}
    assert {
        containers[item]
        for item in result.selected.view_pair.flange.source_view.container_ids
    } == {"TRUE_FLANGE"}
    assert result.generated_candidate_count == 90
    assert result.evaluated_candidate_count == 90
    assert result.search_complete is True
    assert result.termination_reason == "exhausted"


def test_view_pair_order_is_stable_when_ir_block_order_changes() -> None:
    _source, ir, metadata = _search_fixture()
    first = enumerate_view_pair_hypotheses(ir, metadata, DEFAULT_BH_KNOWLEDGE)
    ir.blocks.reverse()
    second = enumerate_view_pair_hypotheses(ir, metadata, DEFAULT_BH_KNOWLEDGE)

    assert len(first) == 90
    assert [
        (item.hypothesis_id, item.main.handle, item.flange.handle)
        for item in first
    ] == [
        (item.hypothesis_id, item.main.handle, item.flange.handle)
        for item in second
    ]


def test_expansion_limit_is_reported_as_incomplete_and_cannot_auto_accept() -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    knowledge = replace(DEFAULT_BH_KNOWLEDGE, max_solver_expansions=1)

    with pytest.raises(BHCompilationRejected) as caught:
        BHCompiler(knowledge=knowledge).compile(
            load_document(source),
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=source,
        )

    proof = caught.value.proof_report
    assert proof.search_complete is False
    assert proof.disposition.value == "rejected"
    assert "BH.PROOF.SEARCH.COMPLETE" in proof.blocking_obligation_ids
    assert "BH-PROOF-SEARCH-INCOMPLETE" in caught.value.diagnostic_codes


def test_limit_before_any_valid_candidate_still_has_structured_rejection() -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    baseline = BHCompiler().compile(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    profile = baseline.assembly.metadata.profile
    length = baseline.assembly.metadata.nominal_length
    doc = load_document(source)
    for name, height in (
        ("AAA_JUNK_WEB", profile.max_height),
        ("AAA_JUNK_FLANGE", profile.flange_width),
    ):
        block = doc.blocks.new(name)
        _add_open_cross(block, length, height)
        doc.modelspace().add_blockref(block.name, (50000.0, 50000.0))
    knowledge = replace(DEFAULT_BH_KNOWLEDGE, max_solver_expansions=1)

    with pytest.raises(BHCompilationRejected) as caught:
        BHCompiler(knowledge=knowledge).compile(
            doc,
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=source,
        )

    assert caught.value.proof_report.search_complete is False
    assert caught.value.diagnostic_codes == ("BH-PROOF-SEARCH-INCOMPLETE",)


def test_unclassified_part_like_view_makes_candidate_universe_nonautomatic() -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    baseline = BHCompiler().compile(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    metadata = baseline.assembly.metadata
    doc = load_document(source)
    block = doc.blocks.new("UNCLASSIFIED_PHYSICAL_VIEW")
    _add_rectangle(
        block,
        metadata.nominal_length,
        metadata.profile.flange_width,
        layer="UNMAPPED_PHYSICAL_GEOMETRY",
    )
    doc.modelspace().add_blockref(block.name, (50000.0, 50000.0))

    result = BHCompiler().compile(
        doc,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    proof = {
        item.obligation_id: item
        for item in result.proof_report.obligations
    }["BH.PROOF.SEARCH.CANDIDATE_UNIVERSE"]
    assert proof.status.value == "missing"
    assert proof.critical
    assert proof.diagnostic_code == "BH-PROOF-CANDIDATE-UNIVERSE-AMBIGUOUS"
    assert result.assessment.disposition.value == "review_required"


def test_known_annotation_table_cannot_enter_the_physical_candidate_universe() -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    baseline = BHCompiler().compile(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    metadata = baseline.assembly.metadata
    doc = load_document(source)
    if "OtherObjectType" not in doc.layers:
        doc.layers.add("OtherObjectType")
    block = doc.blocks.new("MATERIAL_TABLE_FRAME")
    _add_rectangle(
        block,
        metadata.nominal_length,
        metadata.profile.flange_width,
        layer="OtherObjectType",
    )
    block.add_text(
        "零件编号",
        dxfattribs={"layer": "OtherObjectType", "insert": (10.0, 10.0)},
    )
    doc.modelspace().add_blockref(block.name, (50000.0, 50000.0))

    result = BHCompiler().compile(
        doc,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    proof = {
        item.obligation_id: item
        for item in result.proof_report.obligations
    }["BH.PROOF.SEARCH.CANDIDATE_UNIVERSE"]

    assert proof.status.value == "pass"
    assert result.assessment.disposition.value == "auto_accept"


def test_no_valid_hypothesis_is_a_structured_domain_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"

    def reject_all_hypotheses(**_kwargs):
        raise BHNoValidHypothesis("all complete view pairs failed physical lowering")

    monkeypatch.setattr(
        bh_passes,
        "solve_component_hypotheses",
        reject_all_hypotheses,
    )

    with pytest.raises(BHCompilationRejected) as caught:
        BHCompiler().compile(
            load_document(source),
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=source,
        )

    assert caught.value.proof_report.disposition.value == "rejected"
    assert caught.value.diagnostic_codes == ("BH-PROOF-NO-VALID-HYPOTHESIS",)
    assert caught.value.proof_report.blocking_obligation_ids == (
        "BH.PROOF.SEARCH.VALID_MANUFACTURING_HYPOTHESIS",
    )


@pytest.mark.parametrize(
    ("change_geometry", "expected"),
    [(False, "auto_accept"), (True, "review_required")],
)
def test_solution_uniqueness_compares_manufacturing_meaning_not_scores(
    change_geometry: bool,
    expected: str,
) -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    baseline = BHCompiler().compile(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    selected = deepcopy(baseline.hypotheses.selected)
    alternative = deepcopy(selected)
    alternative.hypothesis_id = "assembly-equivalent-alternative"
    alternative.status = HypothesisStatus.VALID
    if change_geometry:
        vertex = alternative.assembly.web_plate.contour.vertices[1]
        alternative.assembly.web_plate.contour.vertices[1] = replace(
            vertex,
            x=vertex.x + 1.0,
        )
    solve = HypothesisSolveResult(
        selected=selected,
        hypotheses=[selected, alternative],
        score_margin=100.0,
        search_complete=True,
        generated_candidate_count=2,
        evaluated_candidate_count=2,
        termination_reason="exhausted",
    )

    assessment = assess_solution(solve, DEFAULT_BH_KNOWLEDGE)

    uniqueness = {
        item.obligation_id: item
        for item in assessment.proof_report.obligations
    }["BH.PROOF.SEARCH.UNIQUE_MANUFACTURING_RESULT"]
    assert assessment.disposition.value == expected
    assert uniqueness.status.value == (
        "pass" if expected == "auto_accept" else "missing"
    )
