from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.box import __version__
from steel_dxf_split.box import weld_allowance as weld_allowance_module
from steel_dxf_split.box.dxf_io import decode_cad_text_transport
from steel_dxf_split.box.manufacturing_ir import (
    BoxManufacturingIR,
    CircularCutIR,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
    derive_weld_allowance_contract,
    rectangle_contour,
)
from steel_dxf_split.box.validator import validate_manufacturing_ir, validate_saved_dxf
from steel_dxf_split.box.weld_allowance import (
    BoxWeldAllowanceProcessingError,
    apply_weld_allowance,
    stretch_outer_segments,
)
from steel_dxf_split.box.writer import OutputPurpose, write_box_clean

EVIDENCE = FeatureEvidence(
    EvidenceState.DIRECT,
    ("source:allowance",),
    ("BOX.RULE.TEST",),
    ("BOX.PROOF.TEST",),
)


def test_cut_fingerprint_includes_explicit_hole_color() -> None:
    red = ezdxf.new("R2007")
    red.modelspace().add_circle(
        (10.0, 20.0),
        5.0,
        dxfattribs={"layer": "CUT_HOLE", "color": 1},
    )
    white = ezdxf.new("R2007")
    white.modelspace().add_circle(
        (10.0, 20.0),
        5.0,
        dxfattribs={"layer": "CUT_HOLE", "color": 7},
    )

    assert weld_allowance_module._cut_geometry(
        red
    ) != weld_allowance_module._cut_geometry(white)


def _segments(
    points: tuple[tuple[float, float], ...],
    *,
    bulges: tuple[float, ...] | None = None,
) -> tuple[ContourSegmentIR, ...]:
    values = bulges or tuple(0.0 for _ in points)
    return tuple(
        ContourSegmentIR(
            f"segment:{index}",
            point,
            points[(index + 1) % len(points)],
            values[index],
            EVIDENCE,
        )
        for index, point in enumerate(points)
    )


def test_stretch_translates_only_the_positive_terminal_chain() -> None:
    original = _segments(
        (
            (0.0, 0.0),
            (6000.0, 0.0),
            (6030.0, 100.0),
            (5975.0, 220.0),
            (5950.0, 300.0),
            (0.0, 300.0),
        ),
        bulges=(0.0, 0.0, 0.15, 0.0, 0.0, 0.0),
    )
    contract = derive_weld_allowance_contract(original)

    stretched = stretch_outer_segments(original, contract)

    before = {segment.segment_id: segment for segment in original}
    after = {segment.segment_id: segment for segment in stretched}
    assert after["segment:0"].start == (0.0, 0.0)
    assert after["segment:0"].end == (6010.0, 0.0)
    assert after["segment:1"].start == (6010.0, 0.0)
    assert after["segment:3"].end == (5960.0, 300.0)
    assert after["segment:4"].start == (5960.0, 300.0)
    assert after["segment:4"].end == (0.0, 300.0)
    assert after["segment:5"] == before["segment:5"]
    for segment_id in contract.positive_terminal_segment_ids:
        left = before[segment_id]
        right = after[segment_id]
        assert (
            right.end[0] - right.start[0],
            right.end[1] - right.start[1],
            right.bulge,
        ) == (
            left.end[0] - left.start[0],
            left.end[1] - left.start[1],
            left.bulge,
        )


def test_zero_allowance_keeps_the_outer_segments_identical() -> None:
    original = rectangle_contour(0.0, 0.0, 2000.0, 300.0, EVIDENCE)
    contract = derive_weld_allowance_contract(original)

    assert contract.allowance_mm == 0.0
    assert stretch_outer_segments(original, contract) == original


def _plate(role: PhysicalPlateRole) -> PhysicalPlateIR:
    return PhysicalPlateIR(
        plate_id=f"BOX-ALLOWANCE:{role.value}",
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=rectangle_contour(0.0, 0.0, 6000.0, 300.0, EVIDENCE),
        circular_cuts=(
            CircularCutIR(
                cut_id=f"{role.value}:hole",
                center=(1200.0, 150.0),
                radius_mm=20.0,
                evidence=EVIDENCE,
            ),
        ),
        inner_contours=(),
        role_evidence=EVIDENCE,
    )


def _production_pair(tmp_path: Path) -> tuple[Path, Path]:
    manufacturing = BoxManufacturingIR.create(
        part_number="BOX-ALLOWANCE",
        profile="BOX300*300*20*20",
        nominal_length_mm=6000.0,
        material="Q355B",
        physical_plates=tuple(_plate(role) for role in PhysicalPlateRole),
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )
    input_path = tmp_path / "member_自动拆板_清洁1to1.dxf"
    layout = write_box_clean(
        manufacturing,
        input_path,
        purpose=OutputPurpose.PRODUCTION,
    )
    saved = validate_saved_dxf(input_path, manufacturing, layout=layout)
    report_path = tmp_path / "member_自动拆板_报告.json"
    report = {
        "version": __version__,
        "report_schema": "BOX-COMPILATION-REPORT-4.0",
        "automation_route": "auto_accepted",
        "outputs": {"production_clean": str(input_path.resolve())},
        "saved_dxf": saved,
        "manufacturing_ir_validation": validate_manufacturing_ir(manufacturing),
        "manufacturing_ir": {
            "fingerprint": manufacturing.fingerprint,
            **manufacturing.to_dict(),
        },
        "weld_allowance_output_groups": [
            {
                "group_id": plate.group_id,
                "roles": [role.value for role in plate.roles],
                "physical_plate_ids": list(plate.physical_plate_ids),
                "quantity": plate.quantity,
                "contract": (
                    plate.weld_allowance_contract.to_dict()
                    if plate.weld_allowance_contract is not None
                    else None
                ),
                "contract_sha256": (
                    plate.weld_allowance_contract.summary_sha256
                    if plate.weld_allowance_contract is not None
                    else None
                ),
            }
            for plate in layout.plates
        ],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return input_path, report_path


def _cut_geometry(path: Path) -> tuple[tuple[object, ...], ...]:
    document = ezdxf.readfile(path)
    polylines = tuple(
        (
            "LWPOLYLINE",
            bool(entity.closed),
            tuple(
                tuple(float(value) for value in point)
                for point in entity.get_points("xyb")
            ),
        )
        for entity in document.modelspace().query("LWPOLYLINE[layer=='CUT_HOLE']")
    )
    circles = tuple(
        (
            "CIRCLE",
            float(entity.dxf.center.x),
            float(entity.dxf.center.y),
            float(entity.dxf.radius),
        )
        for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']")
    )
    return (*polylines, *circles)


def _labels(path: Path) -> tuple[tuple[object, ...], ...]:
    document = ezdxf.readfile(path)
    return tuple(
        (
            entity.dxf.text,
            tuple(float(value) for value in entity.dxf.insert),
            float(entity.dxf.height),
            entity.dxf.style,
        )
        for entity in document.modelspace().query("TEXT[layer=='PART_LABEL']")
    )


def _plate_xdata(path: Path) -> tuple[tuple[tuple[int, object], ...], ...]:
    document = ezdxf.readfile(path)
    return tuple(
        tuple((tag.code, tag.value) for tag in entity.get_xdata("BOX_DXF_SPLIT"))
        for entity in document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    )


def test_apply_allowance_preserves_holes_labels_xdata_and_generates_no_png(
    tmp_path: Path,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    before = ezdxf.readfile(input_path)
    before_boundaries = [
        tuple(tuple(point) for point in entity.get_points("xyb"))
        for entity in before.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    ]
    output_path = tmp_path / "allowance" / "member_焊接余量.dxf"
    report_path = tmp_path / "allowance" / "member_焊接余量_报告.json"

    result = apply_weld_allowance(
        input_path,
        compilation_report,
        output_path,
        report_path,
    )

    after = ezdxf.readfile(output_path)
    after_boundaries = [
        tuple(tuple(point) for point in entity.get_points("xyb"))
        for entity in after.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    ]
    assert result.output_path == output_path
    assert result.report_path == report_path
    assert output_path.is_file() and report_path.is_file()
    assert not list(tmp_path.rglob("*.png"))
    assert after.header["$INSUNITS"] == 4
    assert _cut_geometry(output_path) == _cut_geometry(input_path)
    assert _labels(output_path) == _labels(input_path)
    assert _plate_xdata(output_path) == _plate_xdata(input_path)
    assert [
        max(point[0] for point in vertices) - min(point[0] for point in vertices)
        for vertices in after_boundaries
    ] == pytest.approx(
        [
            max(point[0] for point in vertices)
            - min(point[0] for point in vertices)
            + 10.0
            for vertices in before_boundaries
        ],
        abs=0.003,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "BOX-WELD-ALLOWANCE-REPORT-1.0"
    assert report["ok"] is True
    assert report["png_generated"] is False
    assert all(report["checks"].values())
    assert all(
        group["after_main_length_mm"]
        == pytest.approx(group["before_main_length_mm"] + group["allowance_mm"])
        for group in report["groups"]
    )
    assert not list(after.modelspace().query("REGION"))
    assert all(
        entity.closed
        for entity in after.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    )


def test_allowance_accepts_legacy_utf8_labels_and_reemits_windows_transport(
    tmp_path: Path,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    legacy = ezdxf.readfile(input_path)
    for label in legacy.modelspace().query("TEXT[layer=='PART_LABEL']"):
        label.dxf.text = decode_cad_text_transport(label.dxf.text)
    legacy.saveas(input_path)
    output_path = tmp_path / "allowance" / "member_焊接余量.dxf"
    report_path = tmp_path / "allowance" / "member_焊接余量_报告.json"

    apply_weld_allowance(input_path, compilation_report, output_path, report_path)

    raw = output_path.read_bytes()
    labels = list(ezdxf.readfile(output_path).modelspace().query("TEXT"))
    assert raw.isascii()
    assert b"\r\n" not in raw
    assert b"\\U+" in raw
    assert all(
        decode_cad_text_transport(label.dxf.text).startswith("p=BOX-ALLOWANCE")
        for label in labels
    )


def test_non_millimetre_input_fails_without_replacing_formal_outputs(
    tmp_path: Path,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    document = ezdxf.readfile(input_path)
    document.header["$INSUNITS"] = 0
    document.saveas(input_path)
    output_path = tmp_path / "allowance" / "member.dxf"
    report_path = tmp_path / "allowance" / "member.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"previous-dxf")
    report_path.write_text("previous-report", encoding="utf-8")

    with pytest.raises(BoxWeldAllowanceProcessingError, match="millimetre"):
        apply_weld_allowance(
            input_path,
            compilation_report,
            output_path,
            report_path,
        )

    assert output_path.read_bytes() == b"previous-dxf"
    assert report_path.read_text(encoding="utf-8") == "previous-report"
    assert not list(output_path.parent.glob("*.pending*"))


def test_tampered_group_binding_fails_before_output(tmp_path: Path) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    document = ezdxf.readfile(input_path)
    plate = document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")[0]
    tags = list(plate.get_xdata("BOX_DXF_SPLIT"))
    values = [(tag.code, tag.value) for tag in tags]
    values[7] = (1000, "0" * 64)
    plate.set_xdata("BOX_DXF_SPLIT", values)
    document.saveas(input_path)
    output_path = tmp_path / "allowance" / "tampered.dxf"
    report_path = tmp_path / "allowance" / "tampered.json"

    with pytest.raises(BoxWeldAllowanceProcessingError, match="digest"):
        apply_weld_allowance(
            input_path,
            compilation_report,
            output_path,
            report_path,
        )

    assert not output_path.exists()
    assert not report_path.exists()


@pytest.mark.parametrize("field", ["roles", "quantity"])
def test_report_group_identity_must_match_polyline_xdata(
    tmp_path: Path,
    field: str,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    payload = json.loads(compilation_report.read_text(encoding="utf-8"))
    group = payload["weld_allowance_output_groups"][0]
    group[field] = ["flange_top"] if field == "roles" else 99
    compilation_report.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(BoxWeldAllowanceProcessingError, match="identity"):
        apply_weld_allowance(
            input_path,
            compilation_report,
            tmp_path / "allowance.dxf",
            tmp_path / "allowance.json",
        )


def test_allowance_dxf_is_byte_deterministic_across_repeated_runs(
    tmp_path: Path,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    first = tmp_path / "first" / "member.dxf"
    second = tmp_path / "second" / "member.dxf"

    apply_weld_allowance(
        input_path,
        compilation_report,
        first,
        first.with_suffix(".json"),
    )
    apply_weld_allowance(
        input_path,
        compilation_report,
        second,
        second.with_suffix(".json"),
    )

    assert first.read_bytes() == second.read_bytes()
