from __future__ import annotations

from dataclasses import replace

import pytest

from steel_dxf_split.box.assembly import solve_complete_box
from steel_dxf_split.box.equivalence import group_equivalent_plate_pairs
from steel_dxf_split.box.manufacturing_ir import PhysicalPlateRole
from steel_dxf_split.box.source_ir import build_source_ir
from steel_dxf_split.box.validator import validate_manufacturing_ir
from tests.box_v1.paths import DEV_DATA_ROOT


CB7_SAMPLE = (
    DEV_DATA_ROOT
    / "final dxf"
    / "BOX"
    / "BYSJ@零件图@a1-3-cb-7.dxf"
)


@pytest.mark.skipif(
    not CB7_SAMPLE.is_file(),
    reason="a1-3-cb-7 外部回归样例在当前机器上不可用",
)
def test_cb7_part_arc_and_bolt_are_assigned_to_four_distinct_plates() -> None:
    source = build_source_ir(CB7_SAMPLE)

    best = solve_complete_box(source).best
    by_role = {plate.role: plate for plate in best.mir.physical_plates}
    groups = group_equivalent_plate_pairs(best.mir.physical_plates)
    opening_proof = next(
        obligation
        for obligation in best.proof_report.obligations
        if obligation.obligation_id == "BOX.PROOF.OPENINGS.CONTAINED"
    )

    assert best.proof_report.disposition.value == "auto_accept"
    assert best.proof_report.search_complete
    assert opening_proof.status.value == "pass"
    assert opening_proof.evidence[0].measured == 2
    assert opening_proof.evidence[0].expected == 2
    assert len(groups) == 4
    assert all(group.quantity == 1 for group in groups)
    assert {
        round(cut.radius_mm, 3)
        for cut in by_role[PhysicalPlateRole.WEB_LEFT].circular_cuts
    } == {75.0}
    assert not by_role[PhysicalPlateRole.WEB_RIGHT].circular_cuts
    assert {
        round(cut.radius_mm, 3)
        for cut in by_role[PhysicalPlateRole.FLANGE_TOP].circular_cuts
    } == {10.0}
    assert not by_role[PhysicalPlateRole.FLANGE_BOTTOM].circular_cuts
    assert all(
        group.group_id
        in {
            "web_left",
            "web_right",
            "flange_top",
            "flange_bottom",
        }
        for group in groups
    )

    duplicated_part_arc = by_role[
        PhysicalPlateRole.WEB_LEFT
    ].circular_cuts[0]
    tampered_plates = tuple(
        replace(
            plate,
            circular_cuts=(
                *plate.circular_cuts,
                duplicated_part_arc,
            ),
        )
        if plate.role is PhysicalPlateRole.WEB_RIGHT
        else plate
        for plate in best.mir.physical_plates
    )
    validation = validate_manufacturing_ir(
        replace(best.mir, physical_plates=tampered_plates)
    )
    assert validation["checks"]["part_arc_openings_assigned_once"] is False
    assert validation["ok"] is False
