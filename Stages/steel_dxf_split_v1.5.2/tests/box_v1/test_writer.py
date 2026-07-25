from __future__ import annotations

from dataclasses import replace
from inspect import signature
from pathlib import Path

import ezdxf
import pytest
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import Point, box

from steel_dxf_split.box.box_region import add_circle_region
from steel_dxf_split.box.dxf_io import decode_cad_text_transport
from steel_dxf_split.box.manufacturing_ir import (
    BoxManufacturingIR,
    CircularCutIR,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
    contour_polygon,
    rectangle_contour,
)
from steel_dxf_split.box.validator import validate_saved_dxf
from steel_dxf_split.box.writer import (
    CodegenAuthorizationError,
    OutputPurpose,
    canonical_box_label,
    layout_box_manufacturing_ir,
    part_mark_clearance_envelope,
    write_box_clean,
)
from steel_dxf_split.hole_color_policy import RED_ACI, WHITE_ACI

EVIDENCE = FeatureEvidence(
    EvidenceState.DIRECT,
    ("source:plate",),
    ("BOX.RULE.TEST",),
    ("BOX.PROOF.TEST",),
)


def test_canonical_box_labels_use_compact_physical_roles() -> None:
    part_number = "TEST-BOX"

    assert canonical_box_label(
        part_number,
        (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT),
    ) == "p=TEST-BOX腹"
    assert canonical_box_label(
        part_number,
        (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM),
    ) == "p=TEST-BOX翼"
    assert canonical_box_label(part_number, (PhysicalPlateRole.WEB_LEFT,)) == (
        "p=TEST-BOX上腹"
    )
    assert canonical_box_label(part_number, (PhysicalPlateRole.WEB_RIGHT,)) == (
        "p=TEST-BOX下腹"
    )
    assert canonical_box_label(part_number, (PhysicalPlateRole.FLANGE_TOP,)) == (
        "p=TEST-BOX上翼"
    )
    assert canonical_box_label(part_number, (PhysicalPlateRole.FLANGE_BOTTOM,)) == (
        "p=TEST-BOX下翼"
    )


@pytest.mark.parametrize(
    ("part_number", "role", "expected"),
    (
        ("2b1-cb-86", PhysicalPlateRole.WEB_LEFT, "p=2b1-cb-86下腹"),
        ("2b1-cb-86", PhysicalPlateRole.WEB_RIGHT, "p=2b1-cb-86上腹"),
        ("h-9-cb-133", PhysicalPlateRole.FLANGE_TOP, "p=h-9-cb-133下翼"),
        ("h-9-cb-133", PhysicalPlateRole.FLANGE_BOTTOM, "p=h-9-cb-133上翼"),
    ),
)
def test_confirmed_box_role_label_corrections(
    part_number: str,
    role: PhysicalPlateRole,
    expected: str,
) -> None:
    assert canonical_box_label(part_number, (role,)) == expected


def test_writer_colors_symmetric_box_holes_left_red_and_right_white(
    tmp_path: Path,
) -> None:
    mir = _mir_with_symmetric_holes()
    output = tmp_path / "box-hole-colors.dxf"

    write_box_clean(mir, output, purpose=OutputPurpose.PRODUCTION)
    document = ezdxf.readfile(output)
    circles = list(document.modelspace().query("CIRCLE[layer=='CUT_HOLE']"))

    assert document.layers.get("CUT_HOLE").color == WHITE_ACI
    assert len(circles) == 4
    assert [int(entity.dxf.color) for entity in circles].count(RED_ACI) == 2
    assert [int(entity.dxf.color) for entity in circles].count(WHITE_ACI) == 2


def _plate(
    role: PhysicalPlateRole,
    *,
    width: float,
    height: float,
    hole_x: float | None = None,
) -> PhysicalPlateIR:
    cuts = (
        ()
        if hole_x is None
        else (CircularCutIR("cut", (hole_x, height / 2.0), 10.0, EVIDENCE),)
    )
    return PhysicalPlateIR(
        plate_id=role.value,
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=rectangle_contour(0.0, 0.0, width, height, EVIDENCE),
        circular_cuts=cuts,
        inner_contours=(),
        role_evidence=EVIDENCE,
    )


def _mir(*, disposition: str = "auto_accept") -> BoxManufacturingIR:
    return BoxManufacturingIR.create(
        part_number="TEST-BOX",
        profile="BOX800*600*20*20",
        nominal_length_mm=1000.0,
        material="Q355B",
        physical_plates=(
            _plate(PhysicalPlateRole.WEB_LEFT, width=1000.0, height=760.0),
            _plate(PhysicalPlateRole.WEB_RIGHT, width=1000.0, height=760.0),
            _plate(PhysicalPlateRole.FLANGE_TOP, width=1000.0, height=600.0),
            _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=1000.0, height=600.0),
        ),
        proof_disposition=disposition,
        proof_ids=("BOX.PROOF.TEST",),
    )


def _mir_with_symmetric_holes() -> BoxManufacturingIR:
    mir = _mir()
    return replace(
        mir,
        physical_plates=tuple(
            replace(
                plate,
                circular_cuts=(
                    CircularCutIR(
                        f"{plate.plate_id}:left",
                        (100.0, 100.0),
                        10.0,
                        EVIDENCE,
                    ),
                    CircularCutIR(
                        f"{plate.plate_id}:right",
                        (900.0, 100.0),
                        10.0,
                        EVIDENCE,
                    ),
                ),
            )
            for plate in mir.physical_plates
        ),
    )


def test_box_saved_validator_rejects_a_right_hole_changed_to_red(
    tmp_path: Path,
) -> None:
    mir = _mir_with_symmetric_holes()
    output = tmp_path / "box-tampered-hole-color.dxf"
    layout = write_box_clean(mir, output, purpose=OutputPurpose.PRODUCTION)
    document = ezdxf.readfile(output)
    white_circle = next(
        entity
        for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']")
        if int(entity.dxf.color) == WHITE_ACI
    )
    white_circle.dxf.color = RED_ACI
    document.saveas(output)

    saved = validate_saved_dxf(output, mir, layout=layout)

    assert saved["ok"] is False
    assert saved["checks"]["symmetric_hole_colors_match"] is False


def test_layout_merges_only_proved_equivalent_physical_pairs() -> None:
    layout = layout_box_manufacturing_ir(_mir())

    assert len(layout.plates) == 2
    assert {plate.quantity for plate in layout.plates} == {2}
    assert {plate.label for plate in layout.plates} == {
        "p=TEST-BOX腹",
        "p=TEST-BOX翼",
    }
    assert set(layout.label_heights) == {90.0}


def test_layout_keeps_non_equivalent_pair_as_separate_geometries() -> None:
    mir = _mir()
    plates = list(mir.physical_plates)
    plates[1] = replace(
        plates[1],
        circular_cuts=(CircularCutIR("cut", (100.0, 100.0), 10.0, EVIDENCE),),
    )
    mir = replace(mir, physical_plates=tuple(plates))

    layout = layout_box_manufacturing_ir(mir)

    assert len(layout.plates) == 3
    assert {plate.label for plate in layout.plates} == {
        "p=TEST-BOX上腹",
        "p=TEST-BOX下腹",
        "p=TEST-BOX翼",
    }


def test_writer_is_mir_only_and_saved_output_closes_the_contract(
    tmp_path: Path,
) -> None:
    mir = _mir()
    path = tmp_path / "clean.dxf"

    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    saved = validate_saved_dxf(path, mir, layout=layout)
    document = ezdxf.readfile(path)

    assert saved["ok"]
    assert saved["checks"]["label_points_match_layout"]
    assert saved["checks"]["labels_inside_plates"]
    assert saved["checks"]["label_style_matches_contract"]
    assert saved["checks"]["label_heights_match_layout"]
    assert saved["checks"]["windows_cad_text_transport"]
    assert saved["checks"]["plate_polyline_count"]
    assert saved["checks"]["native_manufacturing_curves_only"]
    modelspace = document.modelspace()
    plates = list(modelspace.query("LWPOLYLINE[layer=='PLATE_CUT']"))
    assert len(plates) == 2
    assert all(entity.closed for entity in plates)
    for forbidden_type in ("LINE", "POLYLINE", "ARC", "REGION"):
        assert not list(
            modelspace.query(f"{forbidden_type}[layer ? '^(PLATE_CUT|CUT_HOLE)$']")
        )
    labels = list(modelspace.query("TEXT[layer=='PART_LABEL']"))
    assert {decode_cad_text_transport(entity.dxf.text) for entity in labels} == {
        "p=TEST-BOX腹",
        "p=TEST-BOX翼",
    }
    assert all(
        entity.get_placement()[0] is TextEntityAlignment.MIDDLE_CENTER
        for entity in labels
    )
    for plate, label_point in zip(layout.plates, layout.label_points, strict=True):
        polygon = contour_polygon(plate.outer_segments)
        assert polygon.covers(Point(label_point))
    assert document.styles.get("SplitChinese").dxf.font == "simsun.ttc"
    assert all(entity.dxf.height == 90.0 for entity in labels)
    raw = path.read_bytes()
    assert raw.isascii()
    assert b"\r\n" not in raw
    assert b"\n" in raw
    assert b"\\U+8179" in raw
    assert not any(dxf_class.dxf.name == "ACDBREGION" for dxf_class in document.classes)


def test_part_mark_envelope_avoids_central_circular_cut(tmp_path: Path) -> None:
    mir = _mir()
    plates = list(mir.physical_plates)
    central_cut = CircularCutIR("central", (500.0, 300.0), 50.0, EVIDENCE)
    for index in (2, 3):
        plates[index] = replace(
            plates[index],
            circular_cuts=(central_cut,),
        )
    mir = replace(mir, physical_plates=tuple(plates))
    path = tmp_path / "central-cut.dxf"

    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    saved = validate_saved_dxf(path, mir, layout=layout)
    flange = next(plate for plate in layout.plates if plate.label.endswith("翼"))
    index = layout.plates.index(flange)
    point = layout.label_points[index]
    height = layout.label_heights[index]
    # SimSun contract: ASCII uses 0.6 em and CJK uses one em.
    width_em = sum(0.6 if character.isascii() else 1.0 for character in flange.label)
    envelope = box(
        point[0] - width_em * height / 2.0,
        point[1] - height / 2.0,
        point[0] + width_em * height / 2.0,
        point[1] + height / 2.0,
    )

    assert not Point(point).buffer(1e-6).intersects(Point(700.0, 500.0))
    assert not envelope.intersects(Point(700.0, 500.0).buffer(50.0))
    assert saved["ok"]
    assert saved["checks"]["label_envelopes_inside_material"]


def test_saved_label_envelope_over_hole_fails_closed(tmp_path: Path) -> None:
    mir = _mir()
    plates = list(mir.physical_plates)
    central_cut = CircularCutIR("central", (500.0, 300.0), 50.0, EVIDENCE)
    for index in (2, 3):
        plates[index] = replace(plates[index], circular_cuts=(central_cut,))
    mir = replace(mir, physical_plates=tuple(plates))
    path = tmp_path / "label-over-hole.dxf"
    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    document = ezdxf.readfile(path)
    flange_label = next(
        entity
        for entity in document.modelspace().query("TEXT[layer=='PART_LABEL']")
        if decode_cad_text_transport(entity.dxf.text).endswith("翼")
    )
    flange_label.set_placement(
        (700.0, 500.0),
        align=TextEntityAlignment.MIDDLE_CENTER,
    )
    document.saveas(path)

    saved = validate_saved_dxf(path, mir, layout=layout)

    assert saved["ok"] is False
    assert saved["checks"]["label_envelopes_inside_material"] is False


def test_production_writer_has_no_part_mark_height_override() -> None:
    assert "text_height" not in signature(write_box_clean).parameters


def test_label_height_solver_keeps_long_thin_plates_readable() -> None:
    mir = _mir()
    mir = replace(
        mir,
        physical_plates=tuple(
            _plate(role, width=14_000.0, height=300.0) for role in PhysicalPlateRole
        ),
    )

    layout = layout_box_manufacturing_ir(mir)

    assert set(layout.label_heights) == {120.0}


def test_label_height_solver_preserves_clearance_on_tapered_plate() -> None:
    mir = _mir()
    points = (
        (316.8, 268.0),
        (693.7, 268.0),
        (693.7, 0.0),
        (0.0, 0.0),
    )
    tapered = tuple(
        ContourSegmentIR(
            f"tapered:{index}",
            point,
            points[(index + 1) % len(points)],
            0.0,
            EVIDENCE,
        )
        for index, point in enumerate(points)
    )
    plates = list(mir.physical_plates)
    for index in (0, 1):
        plates[index] = replace(plates[index], outer_segments=tapered)
    mir = replace(
        mir,
        part_number="GENERIC-BOX-01",
        physical_plates=tuple(plates),
    )

    layout = layout_box_manufacturing_ir(mir)

    assert set(layout.label_heights) == {30.0}


def test_label_height_solver_accepts_actual_thirty_mm_envelope_on_300_mm_box() -> None:
    mir = replace(
        _mir(),
        part_number="a1-3-cb-356",
        physical_plates=(
            _plate(PhysicalPlateRole.WEB_LEFT, width=300.0, height=280.0),
            _plate(PhysicalPlateRole.WEB_RIGHT, width=300.0, height=280.0),
            _plate(PhysicalPlateRole.FLANGE_TOP, width=300.0, height=300.0),
            _plate(PhysicalPlateRole.FLANGE_BOTTOM, width=300.0, height=300.0),
        ),
    )

    layout = layout_box_manufacturing_ir(mir)

    assert set(layout.label_heights) == {30.0}
    for plate, point, height in zip(
        layout.plates,
        layout.label_points,
        layout.label_heights,
        strict=True,
    ):
        material = contour_polygon(plate.outer_segments)
        assert material.covers(
            part_mark_clearance_envelope(plate.label, point, height)
        )


def test_saved_label_displacement_fails_closed(tmp_path: Path) -> None:
    mir = _mir()
    path = tmp_path / "displaced-label.dxf"
    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    document = ezdxf.readfile(path)
    label = document.modelspace().query("TEXT[layer=='PART_LABEL']")[0]
    label.set_placement(
        (-1000.0, -1000.0),
        align=TextEntityAlignment.MIDDLE_CENTER,
    )
    document.saveas(path)

    saved = validate_saved_dxf(path, mir, layout=layout)

    assert saved["ok"] is False
    assert saved["checks"]["label_points_match_layout"] is False
    assert saved["checks"]["labels_inside_plates"] is False


def test_saved_acis_region_fails_closed(tmp_path: Path) -> None:
    mir = _mir()
    path = tmp_path / "legacy-curve.dxf"
    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    document = ezdxf.readfile(path)
    add_circle_region(
        document,
        CircularCutIR("forbidden", (300.0, 300.0), 10.0, EVIDENCE),
        layer="CUT_HOLE",
    )
    document.saveas(path)

    saved = validate_saved_dxf(path, mir, layout=layout)

    assert saved["ok"] is False
    assert saved["checks"]["native_manufacturing_curves_only"] is False


def test_writer_emits_circular_cut_as_native_circle(tmp_path: Path) -> None:
    mir = _mir()
    plates = list(mir.physical_plates)
    web_cut = CircularCutIR("web-cut", (100.0, 100.0), 10.0, EVIDENCE)
    plates[0] = replace(plates[0], circular_cuts=(web_cut,))
    plates[1] = replace(plates[1], circular_cuts=(web_cut,))
    mir = replace(mir, physical_plates=tuple(plates))
    path = tmp_path / "circular-cut-native.dxf"

    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    saved = validate_saved_dxf(path, mir, layout=layout)
    modelspace = ezdxf.readfile(path).modelspace()

    assert saved["ok"]
    assert saved["checks"]["cut_curve_count"]
    assert saved["checks"]["cut_curve_geometry_matches_layout"]
    circles = list(modelspace.query("CIRCLE[layer=='CUT_HOLE']"))
    assert len(circles) == 1
    expected_center = next(
        plate.circular_cuts[0].center for plate in layout.plates if plate.circular_cuts
    )
    assert circles[0].dxf.center.isclose((*expected_center, 0.0))
    assert circles[0].dxf.radius == 10.0
    assert not modelspace.query("REGION[layer=='CUT_HOLE']")


def test_writer_preserves_exact_bulge_in_native_closed_polyline(
    tmp_path: Path,
) -> None:
    mir = _mir()
    plates = list(mir.physical_plates)
    points = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 760.0), (0.0, 760.0))
    bulges = (0.125, 0.0, 0.0, 0.0)
    curved = tuple(
        ContourSegmentIR(
            f"curved:{index}",
            point,
            points[(index + 1) % len(points)],
            bulges[index],
            EVIDENCE,
        )
        for index, point in enumerate(points)
    )
    for index in (0, 1):
        plates[index] = replace(plates[index], outer_segments=curved)
    mir = replace(mir, physical_plates=tuple(plates))
    path = tmp_path / "curved-native.dxf"

    layout = write_box_clean(mir, path, purpose=OutputPurpose.PRODUCTION)
    saved = validate_saved_dxf(path, mir, layout=layout)
    entity = (
        ezdxf.readfile(path).modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")[0]
    )

    assert saved["ok"]
    assert entity.closed
    assert [tuple(point) for point in entity.get_points("xyb")] == [
        (segment.start[0], segment.start[1], segment.bulge)
        for segment in layout.plates[0].outer_segments
    ]


def test_production_writer_rejects_non_auto_accepted_mir(tmp_path: Path) -> None:
    with pytest.raises(CodegenAuthorizationError):
        write_box_clean(
            _mir(disposition="review_required"),
            tmp_path / "forbidden.dxf",
            purpose=OutputPurpose.PRODUCTION,
        )
