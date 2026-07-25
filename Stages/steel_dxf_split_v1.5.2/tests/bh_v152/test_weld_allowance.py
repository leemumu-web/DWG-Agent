from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split import weld_allowance as weld_allowance_module
from steel_dxf_split import __version__
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import (
    BHContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    derive_weld_allowance_contract,
)
from steel_dxf_split.bh_writer import OutputPurpose, write_bh_clean
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.weld_allowance import (
    WeldAllowanceProcessingError,
    apply_weld_allowance,
    stretch_outer_segments,
)


_EVIDENCE = FeatureEvidence(
    state=EvidenceState.DIRECT,
    source_ids=("source",),
    rule_ids=("BH.RULE.TEST",),
    proof_ids=("BH.PROOF.TEST",),
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


def _production_pair(tmp_path: Path) -> tuple[Path, Path]:
    source_path = Path("samples/bh_pairs/2b1-cb-18_拆板前.dxf")
    compiled = compile_bh_document(
        load_document(source_path),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source_path,
    )
    input_path = tmp_path / "member_自动拆板_清洁1to1.dxf"
    write_bh_clean(
        compiled.manufacturing_ir,
        input_path,
        purpose=OutputPurpose.PRODUCTION,
    )
    report_path = tmp_path / "member_自动拆板_报告.json"
    report_path.write_text(
        json.dumps(
            {
                "version": __version__,
                "report_schema": "BH-COMPILATION-REPORT-1.4",
                "automation_route": "production",
                "outputs": {"production_clean": str(input_path.resolve())},
                "saved_dxf": {"ok": True},
                "manufacturing_ir_validation": {"ok": True},
                "manufacturing_ir": {
                    "fingerprint": compiled.manufacturing_ir.fingerprint,
                    **compiled.manufacturing_ir.to_dict(),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return input_path, report_path


def test_stretch_translates_only_the_positive_terminal_chain() -> None:
    original = _segments(
        [
            (0.0, 0.0),
            (6000.0, 0.0),
            (6030.0, 100.0),
            (5975.0, 220.0),
            (5950.0, 300.0),
            (0.0, 300.0),
        ]
    )
    contract = derive_weld_allowance_contract(original)

    stretched = stretch_outer_segments(original, contract)

    by_id = {segment.segment_id: segment for segment in stretched}
    assert by_id["segment-0"].start == (0.0, 0.0)
    assert by_id["segment-0"].end == (6010.0, 0.0)
    assert by_id["segment-1"].start == (6010.0, 0.0)
    assert by_id["segment-1"].end == (6040.0, 100.0)
    assert by_id["segment-2"].start == (6040.0, 100.0)
    assert by_id["segment-2"].end == (5985.0, 220.0)
    assert by_id["segment-3"].start == (5985.0, 220.0)
    assert by_id["segment-3"].end == (5960.0, 300.0)
    assert by_id["segment-4"].start == (5960.0, 300.0)
    assert by_id["segment-4"].end == (0.0, 300.0)
    assert by_id["segment-5"] == original[5]
    for segment_id in contract.positive_terminal_segment_ids:
        before = next(item for item in original if item.segment_id == segment_id)
        after = by_id[segment_id]
        assert (
            after.end[0] - after.start[0],
            after.end[1] - after.start[1],
            after.bulge,
        ) == (
            before.end[0] - before.start[0],
            before.end[1] - before.start[1],
            before.bulge,
        )


def test_apply_weld_allowance_preserves_holes_labels_and_creates_no_png(
    tmp_path: Path,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    before = ezdxf.readfile(input_path)
    before_outer = [
        tuple(entity.get_points("xyb"))
        for entity in before.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    ]
    before_cut_geometry = [
        (tuple(entity.get_points("xyb")) if entity.dxftype() == "LWPOLYLINE" else tuple(entity.dxf.center), float(entity.dxf.radius) if entity.dxftype() == "CIRCLE" else None)
        for entity in before.modelspace().query("*[layer=='CUT_HOLE']")
    ]
    before_labels = [
        (entity.dxf.text, tuple(entity.dxf.insert), entity.dxf.style)
        for entity in before.modelspace().query("TEXT[layer=='PART_LABEL']")
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
    after_outer = [
        tuple(entity.get_points("xyb"))
        for entity in after.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    ]
    after_cut_geometry = [
        (tuple(entity.get_points("xyb")) if entity.dxftype() == "LWPOLYLINE" else tuple(entity.dxf.center), float(entity.dxf.radius) if entity.dxftype() == "CIRCLE" else None)
        for entity in after.modelspace().query("*[layer=='CUT_HOLE']")
    ]
    after_labels = [
        (entity.dxf.text, tuple(entity.dxf.insert), entity.dxf.style)
        for entity in after.modelspace().query("TEXT[layer=='PART_LABEL']")
    ]

    assert result.output_path == output_path
    assert result.report_path == report_path
    assert output_path.exists() and report_path.exists()
    assert not list(tmp_path.rglob("*.png"))
    assert after.header["$INSUNITS"] == 4
    assert len(after_outer) == len(before_outer)
    assert all(
        after_boundary != before_boundary
        for before_boundary, after_boundary in zip(
            before_outer, after_outer, strict=True
        )
    )
    assert after_cut_geometry == before_cut_geometry
    assert after_labels == before_labels
    assert all(byte < 128 for byte in output_path.read_bytes())
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "BH-WELD-ALLOWANCE-REPORT-1.0"
    assert report["png_generated"] is False
    assert report["ok"] is True
    assert all(
        item["after_main_length_mm"]
        == pytest.approx(item["before_main_length_mm"] + item["allowance_mm"])
        for item in report["plates"]
    )


def test_apply_weld_allowance_rejects_non_millimetre_dxf_without_output(
    tmp_path: Path,
) -> None:
    input_path, compilation_report = _production_pair(tmp_path)
    document = ezdxf.readfile(input_path)
    document.header["$INSUNITS"] = 0
    document.saveas(input_path)
    output_path = tmp_path / "allowance" / "invalid.dxf"
    report_path = tmp_path / "allowance" / "invalid.json"

    with pytest.raises(WeldAllowanceProcessingError, match="millimetre"):
        apply_weld_allowance(
            input_path,
            compilation_report,
            output_path,
            report_path,
        )

    assert not output_path.exists()
    assert not report_path.exists()
