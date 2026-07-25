from pathlib import Path

import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_trace import InMemoryTraceObserver
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def test_pass_observer_preserves_selected_manufacturing_semantics() -> None:
    source = PAIR_DIR / "3b2-cb-86_拆板前.dxf"
    baseline = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    observer = InMemoryTraceObserver("3b2-cb-86")
    observed = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
        observer=observer,
    )
    assert observed.fingerprints == baseline.fingerprints
    assert (
        observed.hypotheses.selected.hypothesis_id
        == baseline.hypotheses.selected.hypothesis_id
    )
    assert [plate.label for plate in observed.assembly.plates] == [
        plate.label for plate in baseline.assembly.plates
    ]
    stage_ids = {event.stage_id for event in observer.events}
    assert {
        "01_frontend_fact_ir",
        "02_annotation_facts",
        "03_metadata_semantics",
        "04_view_hypothesis_frontier",
        "06_constraints_and_selection",
        "07_assembly_validation",
        "08_manufacturing_ir",
        "09_quality_route",
    }.issubset(stage_ids)


@pytest.mark.parametrize(
    ("stem", "required_artifact"),
    [
        ("h-3-cb-53", "web_boundary_completion"),
        ("h-6-cb-9", "web_micro_regularization"),
        ("z-4-cb-42", "arc_chain_recovery"),
        ("2b1-cb-40", "flange_development"),
        ("3b2-cb-86", "flange_cut_ownership"),
        ("3b1-cb-15", "holeless_web_selection"),
    ],
)
def test_geometry_trace_covers_real_algorithm_branch(
    stem: str, required_artifact: str
) -> None:
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    observer = InMemoryTraceObserver(stem)
    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
        observer=observer,
    )
    assert result.assembly.plates
    matches = [
        event for event in observer.events if event.artifact_id == required_artifact
    ]
    assert matches
    assert any(event.status not in {"not_applicable", "failed"} for event in matches)


def test_every_generated_hypothesis_has_terminal_trace_event() -> None:
    stem = "3b2-cb-86"
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    observer = InMemoryTraceObserver(stem)
    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
        observer=observer,
    )
    terminal = {
        event.hypothesis_id
        for event in observer.events
        if event.artifact_id == "candidate_terminal"
    }
    generated = {item.hypothesis_id for item in result.hypotheses.hypotheses}
    assert terminal == generated


def test_every_candidate_has_explicit_status_for_every_geometry_substep() -> None:
    stem = "3b2-cb-86"
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    observer = InMemoryTraceObserver(stem)
    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
        observer=observer,
    )
    required = {
        "candidate_begin",
        "source_views_and_cuts",
        "web_precision_attempt",
        "web_faces",
        "web_seed",
        "holeless_web_selection",
        "web_end_expansion",
        "web_boundary_completion",
        "web_hidden_bridge",
        "web_micro_regularization",
        "web_selected",
        "flange_precision_attempt",
        "flange_seeds",
        "flange_end_expansion",
        "flange_projection_consensus",
        "flange_second_plate",
        "flange_development",
        "flange_rigid_extension",
        "flange_cut_ownership",
        "inner_openings",
        "arc_chain_recovery",
        "candidate_manufacturing_ir",
        "candidate_terminal",
    }
    for hypothesis in result.hypotheses.hypotheses:
        emitted = {
            event.artifact_id
            for event in observer.events
            if event.hypothesis_id == hypothesis.hypothesis_id
        }
        assert required <= emitted


def test_semantic_pass_events_keep_real_visual_context() -> None:
    stem = "3b2-cb-86"
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    observer = InMemoryTraceObserver(stem)
    compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
        observer=observer,
    )
    for stage_id in (
        "01_frontend_fact_ir",
        "02_annotation_facts",
        "03_metadata_semantics",
        "04_view_hypothesis_frontier",
        "06_constraints_and_selection",
        "07_assembly_validation",
        "08_manufacturing_ir",
        "09_quality_route",
    ):
        events = [event for event in observer.events if event.stage_id == stage_id]
        assert events, stage_id
        assert any(event.shapes for event in events), stage_id
