from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from steel_dxf_split.box.decision_adapter import _manufacturing_meaning_keys
from steel_dxf_split.box.equivalence import (
    BOX_DRAFTING_RESOLUTION_MM,
    PlateOutputGroup,
    allowance_group_contract,
    group_equivalent_plate_pairs,
    plate_manufacturing_key,
    plates_manufacturing_equivalent,
)
from steel_dxf_split.box.manufacturing_ir import (
    CircularCutIR,
    EvidenceState,
    FeatureEvidence,
    InnerContourIR,
    PhysicalPlateIR,
    PhysicalPlateRole,
    derive_weld_allowance_contract,
    rectangle_contour,
)

WEB_ROLES = (
    PhysicalPlateRole.WEB_LEFT,
    PhysicalPlateRole.WEB_RIGHT,
)
FLANGE_ROLES = (
    PhysicalPlateRole.FLANGE_TOP,
    PhysicalPlateRole.FLANGE_BOTTOM,
)


def _evidence(
    *source_ids: str,
    state: EvidenceState = EvidenceState.DIRECT,
) -> FeatureEvidence:
    return FeatureEvidence(
        state=state,
        source_ids=source_ids,
        rule_ids=("BOX.RULE.TEST",),
        proof_ids=("BOX.PROOF.TEST",),
        description="synthetic grouping evidence",
    )


def _cut(evidence: FeatureEvidence) -> CircularCutIR:
    return CircularCutIR(
        cut_id="cut:1",
        center=(100.0, 50.0),
        radius_mm=10.0,
        evidence=evidence,
    )


def _inner_contour(evidence: FeatureEvidence) -> InnerContourIR:
    return InnerContourIR(
        contour_id="inner:1",
        segments=rectangle_contour(80.0, 30.0, 120.0, 70.0, evidence),
        evidence=evidence,
    )


def _plate(
    role: PhysicalPlateRole,
    *,
    width: float,
    cuts: tuple[CircularCutIR, ...] = (),
    inner_contours: tuple[InnerContourIR, ...] = (),
) -> PhysicalPlateIR:
    outline_evidence = _evidence(f"insert:outline-{role.value}/1")
    return PhysicalPlateIR(
        plate_id=role.value,
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=rectangle_contour(
            0.0,
            0.0,
            width,
            100.0,
            outline_evidence,
        ),
        circular_cuts=cuts,
        inner_contours=inner_contours,
        role_evidence=outline_evidence,
    )


def _four_plates(
    *,
    web_widths: tuple[float, float] = (200.0, 200.0),
    web_cuts: tuple[CircularCutIR, ...] = (),
    web_inner_contours: tuple[InnerContourIR, ...] = (),
) -> tuple[PhysicalPlateIR, ...]:
    return (
        _plate(
            PhysicalPlateRole.WEB_LEFT,
            width=web_widths[0],
            cuts=web_cuts,
            inner_contours=web_inner_contours,
        ),
        _plate(
            PhysicalPlateRole.WEB_RIGHT,
            width=web_widths[1],
            cuts=web_cuts,
            inner_contours=web_inner_contours,
        ),
        _plate(PhysicalPlateRole.FLANGE_TOP, width=180.0),
        _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=180.0),
    )


def _web_groups(
    groups: tuple[PlateOutputGroup, ...],
) -> tuple[PlateOutputGroup, ...]:
    return tuple(group for group in groups if group.roles[0] in WEB_ROLES)


def _flange_groups(
    groups: tuple[PlateOutputGroup, ...],
) -> tuple[PlateOutputGroup, ...]:
    return tuple(group for group in groups if group.roles[0] in FLANGE_ROLES)


def test_feature_free_equivalent_webs_group_as_quantity_two() -> None:
    groups = group_equivalent_plate_pairs(_four_plates())

    web_groups = _web_groups(groups)
    assert len(web_groups) == 1
    assert web_groups[0].roles == WEB_ROLES
    assert web_groups[0].quantity == 2
    assert web_groups[0].merge_authorized is True


def test_feature_free_webs_group_within_one_mm_manufacturing_tolerance() -> None:
    groups = group_equivalent_plate_pairs(
        _four_plates(web_widths=(200.0, 200.999))
    )

    web_groups = _web_groups(groups)
    assert len(web_groups) == 1
    assert web_groups[0].roles == WEB_ROLES
    assert web_groups[0].quantity == 2
    assert web_groups[0].merge_authorized is True


def test_web_outlines_beyond_one_mm_remain_separate() -> None:
    groups = group_equivalent_plate_pairs(
        _four_plates(web_widths=(200.0, 201.001))
    )

    web_groups = _web_groups(groups)
    assert tuple(group.quantity for group in web_groups) == (1, 1)
    assert all(group.merge_authorized is False for group in web_groups)


def test_single_source_circular_cut_blocks_equivalent_web_grouping() -> None:
    evidence = _evidence("insert:76/7A")
    groups = group_equivalent_plate_pairs(
        _four_plates(web_cuts=(_cut(evidence),))
    )

    assert tuple(group.quantity for group in _web_groups(groups)) == (1, 1)


def test_two_entities_from_one_source_group_do_not_prove_two_webs() -> None:
    evidence = _evidence("insert:76/7A", "insert:76/7B")
    groups = group_equivalent_plate_pairs(
        _four_plates(web_cuts=(_cut(evidence),))
    )

    assert tuple(group.quantity for group in _web_groups(groups)) == (1, 1)


def test_two_direct_source_groups_allow_equivalent_holed_webs_to_group() -> None:
    evidence = _evidence("insert:76/7A", "insert:93/96")
    groups = group_equivalent_plate_pairs(
        _four_plates(web_cuts=(_cut(evidence),))
    )

    web_groups = _web_groups(groups)
    assert len(web_groups) == 1
    assert web_groups[0].roles == WEB_ROLES
    assert web_groups[0].quantity == 2
    assert web_groups[0].merge_authorized is True


def test_inferred_inner_contour_blocks_equivalent_web_grouping() -> None:
    evidence = _evidence(
        "insert:76/7A",
        "insert:93/96",
        state=EvidenceState.INFERRED,
    )
    groups = group_equivalent_plate_pairs(
        _four_plates(web_inner_contours=(_inner_contour(evidence),))
    )

    assert tuple(group.quantity for group in _web_groups(groups)) == (1, 1)


def test_two_direct_source_groups_allow_equivalent_inner_contours_to_group() -> None:
    evidence = _evidence("insert:76/7A", "insert:93/96")
    groups = group_equivalent_plate_pairs(
        _four_plates(web_inner_contours=(_inner_contour(evidence),))
    )

    web_groups = _web_groups(groups)
    assert len(web_groups) == 1
    assert web_groups[0].roles == WEB_ROLES
    assert web_groups[0].quantity == 2


def test_equivalent_flange_grouping_is_unchanged() -> None:
    groups = group_equivalent_plate_pairs(
        _four_plates(web_widths=(200.0, 201.0))
    )

    flange_groups = _flange_groups(groups)
    assert len(flange_groups) == 1
    assert flange_groups[0].roles == FLANGE_ROLES
    assert flange_groups[0].quantity == 2
    assert flange_groups[0].merge_authorized is True


def test_flange_grouping_uses_drafting_resolution_for_submillimetre_noise() -> None:
    """Catch exact-signature grouping that splits manufacturing-equivalent flanges."""

    plates = list(_four_plates(web_widths=(200.0, 201.0)))
    plates[2] = _plate(PhysicalPlateRole.FLANGE_TOP, width=12_025.000139)
    plates[3] = _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=12_025.000646)

    groups = group_equivalent_plate_pairs(plates)

    flange_groups = _flange_groups(groups)
    assert len(flange_groups) == 1
    assert flange_groups[0].roles == FLANGE_ROLES
    assert flange_groups[0].quantity == 2


def test_drafting_equivalent_flange_group_keeps_its_weld_allowance_contract() -> None:
    """Catch grouping tolerance and allowance binding drifting apart."""

    plates = list(_four_plates(web_widths=(200.0, 201.0)))
    top = _plate(PhysicalPlateRole.FLANGE_TOP, width=12_025.000139)
    bottom = _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=12_025.000646)
    plates[2] = replace(
        top,
        weld_allowance_contract=derive_weld_allowance_contract(top.outer_segments),
    )
    plates[3] = replace(
        bottom,
        weld_allowance_contract=derive_weld_allowance_contract(bottom.outer_segments),
    )

    flange_group = _flange_groups(group_equivalent_plate_pairs(plates))[0]

    assert flange_group.quantity == 2
    assert allowance_group_contract(flange_group) is not None


def test_manufacturing_equivalence_uses_distance_not_rounding_bucket_identity() -> None:
    first = _plate(PhysicalPlateRole.FLANGE_TOP, width=100.024)
    second = _plate(PhysicalPlateRole.FLANGE_TOP, width=100.034)

    assert plate_manufacturing_key(
        first,
        tolerance=BOX_DRAFTING_RESOLUTION_MM,
    ) != plate_manufacturing_key(
        second,
        tolerance=BOX_DRAFTING_RESOLUTION_MM,
    )
    assert plates_manufacturing_equivalent(
        first,
        second,
        tolerance=BOX_DRAFTING_RESOLUTION_MM,
    )


def test_manufacturing_equivalence_keeps_real_dimension_change_distinct() -> None:
    first = _plate(PhysicalPlateRole.FLANGE_TOP, width=100.024)
    second = _plate(PhysicalPlateRole.FLANGE_TOP, width=100.084)

    assert not plates_manufacturing_equivalent(
        first,
        second,
        tolerance=BOX_DRAFTING_RESOLUTION_MM,
    )


def _meaning_candidate(flange_width: float, rank: float):
    return SimpleNamespace(
        rank_key=(rank,),
        mir=SimpleNamespace(
            fingerprint=f"candidate-{rank}",
            physical_plates=(
                _plate(PhysicalPlateRole.WEB_LEFT, width=200.0),
                _plate(PhysicalPlateRole.WEB_RIGHT, width=200.0),
                _plate(PhysicalPlateRole.FLANGE_TOP, width=flange_width),
                _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=flange_width),
            ),
        ),
    )


def test_candidate_meaning_clusters_only_complete_rolewise_geometry_equivalence() -> None:
    keys = _manufacturing_meaning_keys(
        (
            _meaning_candidate(100.024, 0.0),
            _meaning_candidate(100.034, 1.0),
            _meaning_candidate(100.084, 2.0),
        )
    )

    assert keys[0] == keys[1]
    assert keys[2] != keys[0]
