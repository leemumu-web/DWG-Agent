from dataclasses import replace
from inspect import signature
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import BHManufacturingIR
from steel_dxf_split.bh_validator import validate_bh_saved_dxf
from steel_dxf_split import bh_writer
from steel_dxf_split.dxf_io import decode_cad_text_transport, load_document
from steel_dxf_split.part_mark_layout import part_mark_clearance_envelope


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _manufacturing_ir(stem: str) -> BHManufacturingIR:
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    return compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    ).manufacturing_ir


def test_writer_public_input_is_manufacturing_ir() -> None:
    parameters = signature(bh_writer.write_bh_clean).parameters

    assert tuple(parameters)[0] == "manufacturing_ir"
    assert parameters["manufacturing_ir"].annotation in {
        BHManufacturingIR,
        "BHManufacturingIR",
    }


def test_review_ir_cannot_request_production_codegen(tmp_path: Path) -> None:
    assert hasattr(bh_writer, "OutputPurpose")
    auto_ir = _manufacturing_ir("2b1-cb-26")
    assert auto_ir.proof_disposition == "auto_accept"
    manufacturing_ir = replace(auto_ir, proof_disposition="review_required")
    assert manufacturing_ir.proof_disposition == "review_required"

    with pytest.raises(PermissionError, match="review_required"):
        bh_writer.write_bh_clean(
            manufacturing_ir,
            tmp_path / "candidate.dxf",
            purpose=bh_writer.OutputPurpose.PRODUCTION,
        )

    assert not (tmp_path / "candidate.dxf").exists()


def test_auto_ir_round_trips_through_saved_validation(tmp_path: Path) -> None:
    assert hasattr(bh_writer, "OutputPurpose")
    manufacturing_ir = _manufacturing_ir("2b1-cb-26")
    output = tmp_path / "plate.dxf"

    layout = bh_writer.write_bh_clean(
        manufacturing_ir,
        output,
        purpose=bh_writer.OutputPurpose.PRODUCTION,
    )
    validation = validate_bh_saved_dxf(
        output,
        manufacturing_ir,
        layout=layout,
    )

    assert validation["ok"]
    assert validation["manufacturing_ir_fingerprint"] == manufacturing_ir.fingerprint


def test_production_dxf_uses_windows_cjk_font_without_losing_unicode_labels(
    tmp_path: Path,
) -> None:
    manufacturing_ir = _manufacturing_ir("2b1-cb-26")
    output = tmp_path / "plate.dxf"

    bh_writer.write_bh_clean(
        manufacturing_ir,
        output,
        purpose=bh_writer.OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    labels = [
        decode_cad_text_transport(entity.dxf.text)
        for entity in document.modelspace().query("TEXT")
        if entity.dxf.layer == "PART_LABEL"
    ]

    assert document.styles.get("SplitChinese").dxf.font == "simsun.ttc"
    assert "p=2b1-cb-26腹" in labels
    assert "p=2b1-cb-26翼" in labels


def test_production_dxf_uses_codepage_safe_unicode_transport_and_native_curves(
    tmp_path: Path,
) -> None:
    """Windows CAD must not need to guess a byte encoding for CJK labels."""

    manufacturing_ir = _manufacturing_ir("2b1-cb-26")
    output = tmp_path / "windows-safe.dxf"

    bh_writer.write_bh_clean(
        manufacturing_ir,
        output,
        purpose=bh_writer.OutputPurpose.PRODUCTION,
    )

    raw = output.read_bytes()
    text = output.read_text(encoding="ascii")
    document = ezdxf.readfile(output)
    labels = [
        decode_cad_text_transport(entity.dxf.text)
        for entity in document.modelspace().query("TEXT[layer=='PART_LABEL']")
    ]

    assert all(byte < 128 for byte in raw)
    assert "$DWGCODEPAGE\n  3\nANSI_1252\n" in text
    assert "\nREGION\n" not in text
    assert "p=2b1-cb-26腹" in labels
    assert "p=2b1-cb-26翼" in labels


def test_bh_layout_uses_one_material_safe_shared_mark_height() -> None:
    manufacturing_ir = _manufacturing_ir("2b1-cb-26")

    layout = bh_writer.layout_bh_manufacturing_ir(manufacturing_ir)

    assert len(layout.label_heights) == len(layout.plates)
    assert len(set(layout.label_heights)) == 1
    assert layout.label_heights[0] >= 30.0
    for plate, point, height in zip(
        layout.plates,
        layout.label_points,
        layout.label_heights,
        strict=True,
    ):
        assert bh_writer.bh_plate_material_geometry(plate).covers(
            part_mark_clearance_envelope(
                plate.label,
                (point.x, point.y),
                height,
            )
        )


def test_saved_validation_rejects_a_displaced_bh_part_mark(
    tmp_path: Path,
) -> None:
    manufacturing_ir = _manufacturing_ir("2b1-cb-26")
    output = tmp_path / "displaced-label.dxf"
    layout = bh_writer.write_bh_clean(
        manufacturing_ir,
        output,
        purpose=bh_writer.OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    label = document.modelspace().query("TEXT[layer=='PART_LABEL']")[0]
    label.set_placement((-1000.0, -1000.0))
    document.saveas(output)

    validation = validate_bh_saved_dxf(
        output,
        manufacturing_ir,
        layout=layout,
    )

    assert validation["ok"] is False
    assert validation["checks"]["label_points_match_layout"] is False
    assert validation["checks"]["label_clearance_envelopes_inside_material"] is False


def test_production_dxf_uses_one_native_closed_curve_for_every_manufacturing_loop(
    tmp_path: Path,
) -> None:
    manufacturing_ir = _manufacturing_ir("2b1-cb-26")
    output = tmp_path / "native-curves.dxf"

    layout = bh_writer.write_bh_clean(
        manufacturing_ir,
        output,
        purpose=bh_writer.OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    modelspace = document.modelspace()
    expected_inner = sum(len(plate.inner_contours) for plate in layout.plates)
    expected_circles = sum(len(plate.circular_cuts) for plate in layout.plates)

    assert len(modelspace.query("LWPOLYLINE[layer=='PLATE_CUT']")) == len(layout.plates)
    assert len(modelspace.query("LWPOLYLINE[layer=='CUT_HOLE']")) == expected_inner
    assert len(modelspace.query("CIRCLE[layer=='CUT_HOLE']")) == expected_circles
    assert not list(modelspace.query("REGION[layer ? '^(PLATE_CUT|CUT_HOLE)$']"))
    assert all(
        entity.closed
        for entity in modelspace.query("LWPOLYLINE[layer ? '^(PLATE_CUT|CUT_HOLE)$']")
    )
