from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.bh_manufacturing_ir import (
    BHContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    WeldAllowanceContractError,
    derive_weld_allowance_contract,
    weld_allowance_mm,
)
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_writer import OutputPurpose, write_bh_clean
from steel_dxf_split.bh_validator import validate_bh_saved_dxf
from steel_dxf_split.dxf_io import load_document


_EVIDENCE = FeatureEvidence(
    state=EvidenceState.DIRECT,
    source_ids=("source",),
    rule_ids=("BH.RULE.TEST",),
    proof_ids=("BH.PROOF.TEST",),
)


def _segments(points: list[tuple[float, float]]) -> tuple[BHContourSegmentIR, ...]:
    return tuple(
        BHContourSegmentIR(
            segment_id=f"segment-{index}",
            start=point,
            end=points[(index + 1) % len(points)],
            bulge=0.0,
            evidence=_EVIDENCE,
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
def test_weld_allowance_uses_right_closed_millimetre_bands(
    length_mm: float,
    expected_mm: float,
) -> None:
    assert weld_allowance_mm(length_mm) == expected_mm


@pytest.mark.parametrize("invalid", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_weld_allowance_rejects_non_positive_or_non_finite_lengths(
    invalid: float,
) -> None:
    with pytest.raises(WeldAllowanceContractError):
        weld_allowance_mm(invalid)


@pytest.mark.parametrize(
    ("points", "expected_terminal"),
    [
        (
            [(0.0, 0.0), (6000.0, 0.0), (6000.0, 300.0), (0.0, 300.0)],
            ("segment-1",),
        ),
        (
            [(0.0, 0.0), (6000.0, 0.0), (5950.0, 300.0), (0.0, 300.0)],
            ("segment-1",),
        ),
        (
            [
                (0.0, 0.0),
                (6000.0, 0.075),
                (5950.0, 300.075),
                (0.0, 300.0),
            ],
            ("segment-1",),
        ),
        (
            [
                (0.0, 0.0),
                (6000.0, 0.0),
                (6030.0, 100.0),
                (5975.0, 220.0),
                (5950.0, 300.0),
                (0.0, 300.0),
            ],
            ("segment-1", "segment-2", "segment-3"),
        ),
    ],
)
def test_contract_identifies_horizontal_rails_and_positive_terminal_chain(
    points: list[tuple[float, float]],
    expected_terminal: tuple[str, ...],
) -> None:
    contract = derive_weld_allowance_contract(_segments(points))

    assert contract.schema_version == "BH-WELD-ALLOWANCE-CONTRACT-1.0"
    assert contract.coordinate_unit == "mm"
    assert contract.longitudinal_axis == "x"
    assert contract.stationary_end == "negative_x"
    assert contract.movable_end == "positive_x"
    assert contract.main_length_mm == max(x for x, _ in points)
    assert set(contract.rail_segment_ids) == {"segment-0", f"segment-{len(points) - 2}"}
    assert contract.positive_terminal_segment_ids == expected_terminal
    assert contract.allowance_mm == 10.0


def test_contract_does_not_guess_a_longitudinal_end_for_a_diamond() -> None:
    with pytest.raises(WeldAllowanceContractError, match="horizontal"):
        derive_weld_allowance_contract(
            _segments([(0.0, 100.0), (100.0, 0.0), (200.0, 100.0), (100.0, 200.0)])
        )


def test_contract_rejects_more_than_two_dominant_horizontal_rails() -> None:
    with pytest.raises(WeldAllowanceContractError, match="exactly two"):
        derive_weld_allowance_contract(
            _segments(
                [
                    (0.0, 0.0),
                    (6000.0, 0.0),
                    (6000.0, 100.0),
                    (0.0, 100.0),
                    (0.0, 200.0),
                    (6000.0, 200.0),
                    (6000.0, 300.0),
                    (0.0, 300.0),
                ]
            )
        )


def test_writer_binds_each_plate_closed_polyline_to_its_allowance_contract(
    tmp_path: Path,
) -> None:
    source_path = Path("samples/bh_pairs/2b1-cb-26_拆板前.dxf")
    manufacturing = compile_bh_document(
        load_document(source_path),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source_path,
    ).manufacturing_ir
    output = tmp_path / "bound-regions.dxf"

    layout = write_bh_clean(
        manufacturing,
        output,
        purpose=OutputPurpose.PRODUCTION,
    )
    saved = ezdxf.readfile(output)
    regions = list(saved.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']"))

    assert len(regions) == len(layout.plates)
    for region, plate in zip(regions, layout.plates, strict=True):
        values = [
            tag.value for tag in region.get_xdata("STEEL_DXF_SPLIT")
        ]
        contract = plate.provenance["weld_allowance_contract"]
        assert values == [
            "BH-WELD-ALLOWANCE-1.0",
            plate.provenance["manufacturing_plate_id"],
            plate.provenance["manufacturing_role"],
            "mm",
            contract["main_length_mm"],
            contract["allowance_mm"],
            plate.provenance["weld_allowance_contract_sha256"],
            manufacturing.fingerprint,
        ]


def test_saved_validation_rejects_a_missing_plate_allowance_binding(
    tmp_path: Path,
) -> None:
    source_path = Path("samples/bh_pairs/2b1-cb-26_拆板前.dxf")
    manufacturing = compile_bh_document(
        load_document(source_path),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source_path,
    ).manufacturing_ir
    output = tmp_path / "tampered-binding.dxf"
    layout = write_bh_clean(
        manufacturing,
        output,
        purpose=OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")[0].discard_xdata(
        "STEEL_DXF_SPLIT"
    )
    document.saveas(output)

    validation = validate_bh_saved_dxf(output, manufacturing, layout=layout)

    assert not validation["ok"]
    assert not validation["checks"]["plate_weld_allowance_bindings_match"]
