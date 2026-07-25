from __future__ import annotations

from dataclasses import replace

from steel_dxf_split.box.equivalence import (
    allowance_group_contract,
    group_equivalent_plate_pairs,
)
from steel_dxf_split.box.manufacturing_ir import (
    BoxManufacturingIR,
    CircularCutIR,
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
    rectangle_contour,
)

EVIDENCE = FeatureEvidence(
    EvidenceState.DIRECT,
    ("source:1",),
    ("BOX.RULE.TEST",),
    ("BOX.PROOF.TEST",),
)


def _plate(
    role: PhysicalPlateRole,
    *,
    x: float = 0.0,
    width: float = 200.0,
    hole_x: float | None = None,
) -> PhysicalPlateIR:
    cuts = ()
    if hole_x is not None:
        cuts = (CircularCutIR("hole", (x + hole_x, 50.0), 10.0, EVIDENCE),)
    return PhysicalPlateIR(
        role.value,
        role,
        "Q355B",
        20.0,
        rectangle_contour(x, 0.0, x + width, 100.0, EVIDENCE),
        cuts,
        (),
        EVIDENCE,
    )


def test_identical_web_and_flange_pairs_merge_only_after_equivalence_proof() -> None:
    groups = group_equivalent_plate_pairs(
        tuple(
            _plate(role, x=index * 500.0)
            for index, role in enumerate(PhysicalPlateRole)
        )
    )

    assert len(groups) == 2
    assert {group.quantity for group in groups} == {2}
    assert {group.roles for group in groups} == {
        (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT),
        (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM),
    }
    assert all(group.merge_authorized for group in groups)


def test_different_outline_thickness_or_holes_prevents_merge() -> None:
    plates = (
        _plate(PhysicalPlateRole.WEB_LEFT, width=200.0, hole_x=30.0),
        _plate(PhysicalPlateRole.WEB_RIGHT, width=201.0, hole_x=30.0),
        _plate(PhysicalPlateRole.FLANGE_TOP, hole_x=30.0),
        _plate(PhysicalPlateRole.FLANGE_BOTTOM, hole_x=40.0),
    )
    plates = (*plates[:1], replace(plates[1], thickness_mm=21.0), *plates[2:])
    groups = group_equivalent_plate_pairs(plates)

    assert len(groups) == 4
    assert all(group.quantity == 1 for group in groups)
    assert all(not group.merge_authorized for group in groups)


def test_mirrored_hole_layout_is_equivalent() -> None:
    groups = group_equivalent_plate_pairs(
        (
            _plate(PhysicalPlateRole.WEB_LEFT, hole_x=30.0),
            _plate(PhysicalPlateRole.WEB_RIGHT, x=500.0, hole_x=170.0),
            _plate(PhysicalPlateRole.FLANGE_TOP),
            _plate(PhysicalPlateRole.FLANGE_BOTTOM),
        )
    )

    web_group = next(
        group for group in groups if PhysicalPlateRole.WEB_LEFT in group.roles
    )
    assert web_group.quantity == 2
    assert web_group.merge_authorized


def test_mirrored_holes_after_positive_growth_have_no_group_contract() -> None:
    manufacturing = BoxManufacturingIR.create(
        part_number="BOX-MIRROR",
        profile="BOX6000*100*20*20",
        nominal_length_mm=6000.0,
        material="Q355B",
        physical_plates=(
            _plate(PhysicalPlateRole.WEB_LEFT, width=6000.0, hole_x=30.0),
            _plate(
                PhysicalPlateRole.WEB_RIGHT,
                x=7000.0,
                width=6000.0,
                hole_x=5970.0,
            ),
            _plate(PhysicalPlateRole.FLANGE_TOP, width=6000.0),
            _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=6000.0),
        ),
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )
    groups = group_equivalent_plate_pairs(manufacturing.physical_plates)
    web_group = next(
        group for group in groups if PhysicalPlateRole.WEB_LEFT in group.roles
    )

    assert web_group.quantity == 2
    assert allowance_group_contract(web_group) is None


def test_identical_pair_remains_equivalent_after_positive_growth() -> None:
    manufacturing = BoxManufacturingIR.create(
        part_number="BOX-SAME",
        profile="BOX6000*100*20*20",
        nominal_length_mm=6000.0,
        material="Q355B",
        physical_plates=tuple(
            _plate(role, width=6000.0) for role in PhysicalPlateRole
        ),
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )

    groups = group_equivalent_plate_pairs(manufacturing.physical_plates)

    assert all(allowance_group_contract(group) is not None for group in groups)
