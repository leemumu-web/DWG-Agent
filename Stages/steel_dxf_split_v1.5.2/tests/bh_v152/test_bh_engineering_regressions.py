from __future__ import annotations

from pathlib import Path

import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_text import canonical_bh_label
from steel_dxf_split.bh_validator import validate_bh_assembly
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _assembly(stem: str):
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    return compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    ).assembly


def test_canonical_labels_use_compact_physical_roles_without_quantity_suffix() -> None:
    assert canonical_bh_label("member", "web") == "p=member腹"
    assert canonical_bh_label("member", "flange", quantity=2) == "p=member翼"
    assert canonical_bh_label("member", "flange", index=1) == "p=member上翼"
    assert canonical_bh_label("member", "flange", index=2) == "p=member下翼"


def test_variable_height_profile_and_development_remain_distinct() -> None:
    assembly = _assembly("2b1-cb-40")
    profile = assembly.metadata.profile

    assert profile.height == 1500.0
    assert profile.secondary_height == 750.0
    assert profile.max_height == 1500.0
    assert profile.min_height == 750.0
    assert profile.minimum_clear_web_height == 670.0
    assert profile.clear_web_height == 1420.0

    development = assembly.diagnostics["flange_development"]
    lengths = sorted(plate.bbox.width for plate in assembly.flange_plates)
    assert development["mode"] == "variable_height_two_paths"
    assert lengths == pytest.approx(
        sorted(development["target_lengths_mm"]), abs=1e-6
    )
    assert lengths == pytest.approx([2383.037, 2538.0], abs=0.01)
    assert max(development["raw_lengths_mm"]) > max(lengths)
    assert development["certificate"]["authorized"] is True


def test_overlapping_flange_projection_assigns_phi40_to_one_physical_flange() -> None:
    assembly = _assembly("3b2-cb-86")

    assert [plate.label for plate in assembly.flange_plates] == [
        "p=3b2-cb-86上翼",
        "p=3b2-cb-86下翼",
    ]
    assert [len(plate.circular_cuts) for plate in assembly.flange_plates] == [0, 2]
    assert {
        round(cut.radius * 2.0, 6)
        for cut in assembly.flange_plates[1].circular_cuts
    } == {40.0}
    assignment = assembly.diagnostics["flange_cut_assignment"]
    assert assignment["main_bolt_line_symbol_counts"]["high"] > 0


def test_holeless_web_remains_a_valid_physical_decomposition() -> None:
    assembly = _assembly("3b1-cb-15")

    assert assembly.web_plate.circular_cuts == []
    assert validate_bh_assembly(assembly).ok


@pytest.mark.parametrize(
    ("stem", "expected_length"),
    (("h-3-cb-53", 6972.0), ("h-6-cb-9", 6492.0)),
)
def test_boundary_completion_is_bounded_by_final_geometry(
    stem: str,
    expected_length: float,
) -> None:
    assembly = _assembly(stem)

    assert assembly.web_plate.bbox.width == pytest.approx(expected_length, abs=0.01)
    completion = assembly.diagnostics["web_selection"]["boundary_completion"]
    assert completion.get("merged_face_indices") or completion.get(
        "regularization", {}
    ).get("applied")


def test_hidden_arc_evidence_restores_manufacturing_bulges() -> None:
    assembly = _assembly("z-4-cb-42")
    nonzero = [
        vertex.bulge
        for vertex in assembly.web_plate.contour.vertices
        if abs(vertex.bulge) > 1e-8
    ]

    assert len(nonzero) >= 4
    assert all(abs(abs(value) - 0.2400788) < 1e-4 for value in nonzero[:4])


def test_micro_topology_regularization_obeys_conservation_guards() -> None:
    assembly = _assembly("h-6-cb-9")
    regularization = assembly.diagnostics["web_selection"]["boundary_completion"][
        "regularization"
    ]

    assert regularization["applied"] is True
    assert regularization["relative_area_change"] <= 1e-4
    assert regularization["bbox_change_mm"] <= 0.02
