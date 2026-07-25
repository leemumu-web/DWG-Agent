from __future__ import annotations

from pathlib import Path

import ezdxf

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_validator import validate_bh_saved_dxf
from steel_dxf_split.bh_writer import OutputPurpose, write_bh_clean
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.hole_color_policy import RED_ACI, WHITE_ACI


ROOT = Path(__file__).resolve().parents[2]


def test_real_bh_symmetric_holes_are_written_left_red_and_right_white(
    tmp_path: Path,
) -> None:
    source = ROOT / "samples/bh_pairs/2b1-cb-29_拆板前.dxf"
    manufacturing = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    ).manufacturing_ir
    output = tmp_path / "bh-hole-colors.dxf"

    write_bh_clean(manufacturing, output, purpose=OutputPurpose.PRODUCTION)
    document = ezdxf.readfile(output)
    circles = list(document.modelspace().query("CIRCLE[layer=='CUT_HOLE']"))

    assert document.layers.get("CUT_HOLE").color == WHITE_ACI
    assert len(circles) == 48
    assert [int(entity.dxf.color) for entity in circles].count(RED_ACI) == 24
    assert [int(entity.dxf.color) for entity in circles].count(WHITE_ACI) == 24


def test_bh_saved_validator_rejects_a_right_hole_changed_to_red(
    tmp_path: Path,
) -> None:
    source = ROOT / "samples/bh_pairs/2b1-cb-29_拆板前.dxf"
    manufacturing = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    ).manufacturing_ir
    output = tmp_path / "bh-tampered-hole-color.dxf"
    layout = write_bh_clean(
        manufacturing,
        output,
        purpose=OutputPurpose.PRODUCTION,
    )
    document = ezdxf.readfile(output)
    white_circle = next(
        entity
        for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']")
        if int(entity.dxf.color) == WHITE_ACI
    )
    white_circle.dxf.color = RED_ACI
    document.saveas(output)

    validation = validate_bh_saved_dxf(
        output,
        manufacturing,
        layout=layout,
    )

    assert validation["ok"] is False
    assert validation["checks"]["symmetric_hole_colors_match"] is False
