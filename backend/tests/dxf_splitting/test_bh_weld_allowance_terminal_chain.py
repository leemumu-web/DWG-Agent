from __future__ import annotations

import pytest
from steel_dxf_split.bh_manufacturing_ir import (
    BHContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    WeldAllowanceContractError,
    derive_weld_allowance_contract,
)
from steel_dxf_split.weld_allowance import stretch_outer_segments

_EVIDENCE = FeatureEvidence(
    state=EvidenceState.DIRECT,
    source_ids=("regression-contour",),
    rule_ids=("BH.RULE.TEST",),
    proof_ids=("BH.PROOF.TEST",),
)


def _segments(
    vertices: list[tuple[float, float, float]],
) -> tuple[BHContourSegmentIR, ...]:
    return tuple(
        BHContourSegmentIR(
            segment_id=f"segment-{index:02d}",
            start=(vertex[0], vertex[1]),
            end=(
                vertices[(index + 1) % len(vertices)][0],
                vertices[(index + 1) % len(vertices)][1],
            ),
            bulge=vertex[2],
            evidence=_EVIDENCE,
        )
        for index, vertex in enumerate(vertices)
    )


def test_sloped_web_proves_one_rightmost_terminal_without_parallel_rails() -> None:
    segments = _segments(
        [
            (0.0, 0.0, 0.0),
            (0.0, 1417.63, 0.0),
            (2147.961, 667.34436, 0.0),
            (2147.961, 0.0, 0.0),
        ]
    )

    contract = derive_weld_allowance_contract(segments)

    assert set(contract.rail_segment_ids) == {"segment-01", "segment-03"}
    assert contract.positive_terminal_segment_ids == ("segment-02",)
    assert contract.main_length_mm == pytest.approx(2147.961)
    assert contract.allowance_mm == 5.0
    assert contract.rule_ids[0] == (
        "BH.RULE.WELD_ALLOWANCE.LONGITUDINAL_RAIL_TOPOLOGY"
    )


def test_compound_rounded_terminal_chain_moves_rigidly_as_one_end() -> None:
    segments = _segments(
        [
            (5815.062, 0.0, 0.0),
            (35.0, 0.0, 0.0),
            (34.569092, 5.474929, 0.324919701),
            (5.475, 34.569, 0.0),
            (0.0, 35.0, 0.0),
            (0.0, 1405.0, 0.0),
            (5.475206, 1405.430631, 0.324919701),
            (34.569, 1434.525, 0.0),
            (35.0, 1440.0, 0.0),
            (5581.383, 1440.0, 0.0),
            (6781.388, 1840.0, 0.0),
            (11195.002, 1840.0, 0.0),
            (11195.432993, 1834.524517, 0.324919701),
            (11224.527, 1805.431, 0.0),
            (11230.002, 1805.0, 0.0),
            (11230.002, 435.0, 0.0),
            (11224.526879, 434.568815, 0.324919701),
            (11195.433, 405.475, 0.0),
            (11195.002, 400.0, 0.0),
            (7015.067, 400.0, 0.0),
        ]
    )

    contract = derive_weld_allowance_contract(segments)
    stretched = stretch_outer_segments(segments, contract)

    assert set(contract.rail_segment_ids) == {"segment-10", "segment-18"}
    assert contract.positive_terminal_segment_ids == tuple(
        f"segment-{index:02d}" for index in range(11, 18)
    )
    assert contract.main_length_mm == pytest.approx(11230.002)
    assert contract.allowance_mm == 15.0
    moved_vertices = set(range(11, 19))
    for index, (before, after) in enumerate(zip(segments, stretched, strict=True)):
        start_shift = 15.0 if index in moved_vertices else 0.0
        end_shift = 15.0 if (index + 1) % len(segments) in moved_vertices else 0.0
        assert after.start == pytest.approx(
            (before.start[0] + start_shift, before.start[1])
        )
        assert after.end == pytest.approx(
            (before.end[0] + end_shift, before.end[1])
        )
        assert after.bulge == before.bulge
    for index in range(11, 18):
        before = segments[index]
        after = stretched[index]
        assert (
            after.end[0] - after.start[0],
            after.end[1] - after.start[1],
        ) == pytest.approx(
            (
                before.end[0] - before.start[0],
                before.end[1] - before.start[1],
            )
        )
    stretched_x = [
        coordinate
        for segment in stretched
        for coordinate in (segment.start[0], segment.end[0])
    ]
    assert max(stretched_x) - min(stretched_x) == pytest.approx(11245.002)


def test_two_equally_valid_right_terminals_remain_fail_closed() -> None:
    segments = _segments(
        [
            (0.0, 0.0, 0.0),
            (6000.0, 0.0, 0.0),
            (6000.0, 100.0, 0.0),
            (0.0, 100.0, 0.0),
            (0.0, 200.0, 0.0),
            (6000.0, 200.0, 0.0),
            (6000.0, 300.0, 0.0),
            (0.0, 300.0, 0.0),
        ]
    )

    with pytest.raises(WeldAllowanceContractError, match="exactly two"):
        derive_weld_allowance_contract(segments)


def test_existing_horizontal_contract_keeps_legacy_rule_identity() -> None:
    segments = _segments(
        [
            (0.0, 0.0, 0.0),
            (6000.0, 0.0, 0.0),
            (6000.0, 300.0, 0.0),
            (0.0, 300.0, 0.0),
        ]
    )

    contract = derive_weld_allowance_contract(segments)

    assert set(contract.rail_segment_ids) == {"segment-00", "segment-02"}
    assert contract.positive_terminal_segment_ids == ("segment-01",)
    assert contract.rule_ids[0] == "BH.RULE.WELD_ALLOWANCE.HORIZONTAL_RAILS"
