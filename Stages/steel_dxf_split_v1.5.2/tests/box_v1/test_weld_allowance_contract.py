from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.box.manufacturing_ir import (
    BoxManufacturingIR,
    BoxWeldAllowanceContractError,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
    derive_weld_allowance_contract,
    weld_allowance_mm,
)
from steel_dxf_split.box.validator import validate_manufacturing_ir, validate_saved_dxf
from steel_dxf_split.box.writer import (
    OutputPurpose,
    layout_box_manufacturing_ir,
    write_box_clean,
)

EVIDENCE = FeatureEvidence(
    state=EvidenceState.DIRECT,
    source_ids=("source:allowance-test",),
    rule_ids=("BOX.RULE.TEST",),
    proof_ids=("BOX.PROOF.TEST",),
)


def _segments(
    points: tuple[tuple[float, float], ...],
    *,
    bulges: tuple[float, ...] | None = None,
    prefix: str = "segment",
) -> tuple[ContourSegmentIR, ...]:
    values = bulges or tuple(0.0 for _ in points)
    return tuple(
        ContourSegmentIR(
            segment_id=f"{prefix}:{index}",
            start=point,
            end=points[(index + 1) % len(points)],
            bulge=values[index],
            evidence=EVIDENCE,
        )
        for index, point in enumerate(points)
    )


@pytest.mark.parametrize(
    ("length_mm", "expected_mm"),
    [
        (0.001, 0.0),
        (2000.0, 0.0),
        (2000.001, 5.0),
        (5000.0, 5.0),
        (5000.001, 10.0),
        (10000.0, 10.0),
        (10000.001, 15.0),
        (15000.0, 15.0),
        (15000.001, 20.0),
        (25000.0, 20.0),
    ],
)
def test_box_weld_allowance_uses_bh_right_closed_millimetre_bands(
    length_mm: float,
    expected_mm: float,
) -> None:
    assert weld_allowance_mm(length_mm) == expected_mm


@pytest.mark.parametrize("invalid", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_box_weld_allowance_rejects_invalid_lengths(invalid: float) -> None:
    with pytest.raises(BoxWeldAllowanceContractError, match="positive"):
        weld_allowance_mm(invalid)


def test_contract_identifies_folded_positive_terminal_chain() -> None:
    contour = _segments(
        (
            (0.0, 0.0),
            (6000.0, 0.0),
            (6030.0, 100.0),
            (5975.0, 220.0),
            (5950.0, 300.0),
            (0.0, 300.0),
        )
    )

    contract = derive_weld_allowance_contract(contour)

    assert contract.schema_version == "BOX-WELD-ALLOWANCE-CONTRACT-1.0"
    assert contract.coordinate_unit == "mm"
    assert contract.longitudinal_axis == "x"
    assert contract.horizontal_residual_mm == 0.1
    assert contract.main_length_mm == 6030.0
    assert contract.allowance_mm == 10.0
    assert contract.stationary_end == "negative_x"
    assert contract.movable_end == "positive_x"
    assert set(contract.rail_segment_ids) == {"segment:0", "segment:4"}
    assert contract.positive_terminal_segment_ids == (
        "segment:1",
        "segment:2",
        "segment:3",
    )
    assert contract.negative_terminal_segment_ids == ("segment:5",)
    assert len(contract.summary_sha256) == 64


def test_step_plate_selects_the_rails_adjacent_to_positive_support() -> None:
    contour = _segments(
        (
            (0.0, 680.0),
            (331.934, 680.0),
            (331.934, 710.0),
            (851.886, 710.0),
            (851.886, 0.0),
            (0.0, 0.0),
        )
    )

    contract = derive_weld_allowance_contract(contour)

    assert set(contract.rail_segment_ids) == {"segment:2", "segment:4"}
    assert contract.positive_terminal_segment_ids == ("segment:3",)
    assert contract.negative_terminal_segment_ids == (
        "segment:5",
        "segment:0",
        "segment:1",
    )


def test_short_side_trapezoid_and_absolute_horizontal_residual_are_supported() -> None:
    contour = _segments(
        (
            (0.0, 0.025297),
            (1283.753296, 0.0),
            (432.163195, 850.101976),
            (0.012231, 850.025296),
        )
    )

    contract = derive_weld_allowance_contract(contour)

    assert set(contract.rail_segment_ids) == {"segment:0", "segment:2"}
    assert contract.positive_terminal_segment_ids == ("segment:1",)
    assert contract.negative_terminal_segment_ids == ("segment:3",)
    assert contract.main_length_mm == pytest.approx(1283.753296)


def test_terminal_bulge_is_carried_by_the_rigid_chain() -> None:
    contour = _segments(
        ((0.0, 0.0), (6000.0, 0.0), (6000.0, 300.0), (0.0, 300.0)),
        bulges=(0.0, 0.2, 0.0, 0.0),
    )

    contract = derive_weld_allowance_contract(contour)

    assert contract.positive_terminal_segment_ids == ("segment:1",)
    assert contour[1].bulge == 0.2


def test_contract_rejects_a_diamond_without_horizontal_rails() -> None:
    with pytest.raises(BoxWeldAllowanceContractError, match="horizontal"):
        derive_weld_allowance_contract(
            _segments(((0.0, 100.0), (100.0, 0.0), (200.0, 100.0), (100.0, 200.0)))
        )


def test_contract_rejects_duplicate_segment_identity() -> None:
    contour = _segments(((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)))
    duplicate = replace(contour[1], segment_id=contour[0].segment_id)

    with pytest.raises(BoxWeldAllowanceContractError, match="unique"):
        derive_weld_allowance_contract((contour[0], duplicate, *contour[2:]))


def _plate(
    role: PhysicalPlateRole,
    contour: tuple[ContourSegmentIR, ...],
) -> PhysicalPlateIR:
    return PhysicalPlateIR(
        plate_id=f"BOX-TEST:{role.value}",
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=contour,
        circular_cuts=(),
        inner_contours=(),
        role_evidence=EVIDENCE,
    )


def test_mir_freezes_optional_allowance_contract_without_changing_split_route() -> None:
    rectangle = _segments(((0.0, 0.0), (6000.0, 0.0), (6000.0, 300.0), (0.0, 300.0)))
    diamond = _segments(
        ((0.0, 100.0), (100.0, 0.0), (200.0, 100.0), (100.0, 200.0)),
        prefix="diamond",
    )
    plates = tuple(
        _plate(role, diamond if role is PhysicalPlateRole.WEB_LEFT else rectangle)
        for role in PhysicalPlateRole
    )

    manufacturing = BoxManufacturingIR.create(
        part_number="BOX-TEST",
        profile="BOX300*300*20*20",
        nominal_length_mm=6000.0,
        material="Q355B",
        physical_plates=plates,
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )

    by_role = {plate.role: plate for plate in manufacturing.physical_plates}
    assert by_role[PhysicalPlateRole.WEB_LEFT].weld_allowance_contract is None
    assert all(
        by_role[role].weld_allowance_contract is not None
        for role in PhysicalPlateRole
        if role is not PhysicalPlateRole.WEB_LEFT
    )
    assert manufacturing.proof_disposition == "auto_accept"
    assert validate_manufacturing_ir(manufacturing)["ok"] is True


def test_mir_validation_detects_a_contract_that_disagrees_with_geometry() -> None:
    rectangle = _segments(((0.0, 0.0), (6000.0, 0.0), (6000.0, 300.0), (0.0, 300.0)))
    manufacturing = BoxManufacturingIR.create(
        part_number="BOX-TEST",
        profile="BOX300*300*20*20",
        nominal_length_mm=6000.0,
        material="Q355B",
        physical_plates=tuple(_plate(role, rectangle) for role in PhysicalPlateRole),
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )
    first = manufacturing.physical_plates[0]
    assert first.weld_allowance_contract is not None
    tampered_contract = replace(first.weld_allowance_contract, allowance_mm=20.0)
    tampered = replace(
        manufacturing,
        physical_plates=(
            replace(first, weld_allowance_contract=tampered_contract),
            *manufacturing.physical_plates[1:],
        ),
    )

    validation = validate_manufacturing_ir(tampered)

    assert validation["ok"] is False
    assert validation["checks"]["weld_allowance_contracts_match_geometry"] is False


def _production_mir() -> BoxManufacturingIR:
    rectangle = _segments(((0.0, 0.0), (6000.0, 0.0), (6000.0, 300.0), (0.0, 300.0)))
    return BoxManufacturingIR.create(
        part_number="BOX-XDATA",
        profile="BOX300*300*20*20",
        nominal_length_mm=6000.0,
        material="Q355B",
        physical_plates=tuple(_plate(role, rectangle) for role in PhysicalPlateRole),
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )


def test_layout_binds_merged_group_to_all_physical_roles_and_contracts() -> None:
    manufacturing = _production_mir()

    layout = layout_box_manufacturing_ir(manufacturing)

    assert len(layout.plates) == 2
    assert all(plate.quantity == 2 for plate in layout.plates)
    assert all(len(plate.physical_plate_ids) == 2 for plate in layout.plates)
    assert all(plate.weld_allowance_contract is not None for plate in layout.plates)
    assert {
        plate.weld_allowance_contract.allowance_mm
        for plate in layout.plates
        if plate.weld_allowance_contract is not None
    } == {10.0}


def test_writer_binds_each_plate_polyline_to_its_output_group_contract(
    tmp_path: Path,
) -> None:
    manufacturing = _production_mir()
    output = tmp_path / "bound-box-polylines.dxf"

    layout = write_box_clean(
        manufacturing,
        output,
        purpose=OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    polylines = list(document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']"))

    assert len(polylines) == len(layout.plates)
    for entity, plate in zip(polylines, layout.plates, strict=True):
        assert plate.weld_allowance_contract is not None
        tags = list(entity.get_xdata("BOX_DXF_SPLIT"))
        assert [tag.code for tag in tags] == [
            1000,
            1000,
            1000,
            1070,
            1000,
            1040,
            1040,
            1000,
            1000,
        ]
        assert [tag.value for tag in tags] == [
            "BOX-WELD-ALLOWANCE-1.0",
            plate.group_id,
            ",".join(role.value for role in plate.roles),
            plate.quantity,
            "mm",
            plate.weld_allowance_contract.main_length_mm,
            plate.weld_allowance_contract.allowance_mm,
            plate.weld_allowance_contract.summary_sha256,
            manufacturing.fingerprint,
        ]


def test_saved_validation_rejects_a_missing_group_allowance_binding(
    tmp_path: Path,
) -> None:
    manufacturing = _production_mir()
    output = tmp_path / "tampered-binding.dxf"
    layout = write_box_clean(
        manufacturing,
        output,
        purpose=OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")[0].discard_xdata(
        "BOX_DXF_SPLIT"
    )
    document.saveas(output)

    validation = validate_saved_dxf(output, manufacturing, layout=layout)

    assert validation["ok"] is False
    assert validation["checks"]["plate_weld_allowance_bindings_match"] is False
