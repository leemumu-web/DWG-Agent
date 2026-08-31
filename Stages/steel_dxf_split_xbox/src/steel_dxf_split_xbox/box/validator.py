from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ezdxf.enums import TextEntityAlignment
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFValueError
from shapely.geometry import Point, Polygon

from ..hole_color_policy import (
    RED_ACI,
    WHITE_ACI,
    plan_symmetric_circle_colors,
)
from .dxf_io import decode_cad_text_transport
from .manufacturing_ir import (
    BoxManufacturingIR,
    BoxWeldAllowanceContractError,
    CircularCutIR,
    ContourSegmentIR,
    PhysicalPlateRole,
    contour_polygon,
    derive_weld_allowance_contract,
)

if TYPE_CHECKING:
    from .writer import BoxLayout, LaidOutPlate


def validate_manufacturing_ir(manufacturing: BoxManufacturingIR) -> dict[str, object]:
    """Validate four physical roles and every material-removal feature."""

    roles = tuple(plate.role for plate in manufacturing.physical_plates)
    part_arc_assignment_sources = tuple(
        frozenset(cut.evidence.source_ids)
        for plate in manufacturing.physical_plates
        for cut in plate.circular_cuts
        if "BOX.OPENING.PART_ARC_FULL_CIRCLE_RECONSTRUCTION"
        in cut.evidence.rule_ids
    )
    per_plate: list[dict[str, bool]] = []
    allowance_contracts_match: list[bool] = []
    for plate in manufacturing.physical_plates:
        outer = contour_polygon(plate.outer_segments)
        inner = [contour_polygon(contour.segments) for contour in plate.inner_contours]
        material = Polygon(
            outer.exterior.coords,
            [list(polygon.exterior.coords) for polygon in inner],
        )
        centers = [Point(cut.center) for cut in plate.circular_cuts]
        per_plate.append(
            {
                "outer_valid": outer.is_valid and outer.area > 1e-6,
                "material_valid": material.is_valid and material.area > 1e-6,
                "inner_contained": all(
                    polygon.is_valid
                    and polygon.area > 1e-6
                    and outer.buffer(0.01).covers(polygon)
                    for polygon in inner
                ),
                "cuts_contained": all(
                    material.buffer(0.01).covers(center)
                    and material.boundary.distance(center) + 0.01 >= cut.radius_mm
                    for cut, center in zip(plate.circular_cuts, centers, strict=True)
                ),
                "cuts_unique": len(
                    {
                        (
                            round(cut.center[0], 3),
                            round(cut.center[1], 3),
                            round(cut.radius_mm, 3),
                        )
                        for cut in plate.circular_cuts
                    }
                )
                == len(plate.circular_cuts),
                "cuts_non_overlapping": all(
                    first.distance(second) + 0.01
                    >= first_cut.radius_mm + second_cut.radius_mm
                    for index, (first_cut, first) in enumerate(
                        zip(plate.circular_cuts, centers, strict=True)
                    )
                    for second_cut, second in zip(
                        plate.circular_cuts[index + 1 :],
                        centers[index + 1 :],
                        strict=True,
                    )
                ),
            }
        )
        try:
            expected_contract = derive_weld_allowance_contract(plate.outer_segments)
        except BoxWeldAllowanceContractError:
            allowance_contracts_match.append(plate.weld_allowance_contract is None)
        else:
            allowance_contracts_match.append(
                plate.weld_allowance_contract == expected_contract
            )
    checks = {
        "four_physical_roles": len(roles) == 4 and set(roles) == set(PhysicalPlateRole),
        "positive_thickness": all(
            plate.thickness_mm > 0 for plate in manufacturing.physical_plates
        ),
        "geometry_valid": all(all(item.values()) for item in per_plate),
        "feature_provenance_complete": all(
            bool(segment.evidence.source_ids or segment.evidence.rule_ids)
            for plate in manufacturing.physical_plates
            for segment in plate.outer_segments
        ),
        "circular_cut_provenance_complete": all(
            bool(cut.evidence.source_ids)
            and bool(cut.evidence.rule_ids)
            and bool(cut.evidence.proof_ids)
            for plate in manufacturing.physical_plates
            for cut in plate.circular_cuts
        ),
        "part_arc_openings_assigned_once": len(
            set(part_arc_assignment_sources)
        )
        == len(part_arc_assignment_sources),
        "weld_allowance_contracts_match_geometry": all(allowance_contracts_match),
        "fingerprint_valid": len(manufacturing.fingerprint) == 64,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "per_plate": per_plate,
        "fingerprint": manufacturing.fingerprint,
    }


def _saved_contour_polyline_matches(
    entity: object,
    segments: tuple[ContourSegmentIR, ...],
) -> bool:
    try:
        if not entity.closed:  # type: ignore[attr-defined]
            return False
        points = list(entity.get_points("xyb"))  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return False
    if len(points) != len(segments):
        return False
    return all(
        abs(float(point[0]) - segment.start[0]) <= 1e-9
        and abs(float(point[1]) - segment.start[1]) <= 1e-9
        and abs(float(point[2]) - segment.bulge) <= 1e-9
        for point, segment in zip(points, segments, strict=True)
    )


def _saved_circle_matches(entity: object, cut: CircularCutIR) -> bool:
    try:
        center = entity.dxf.center  # type: ignore[attr-defined]
        radius = float(entity.dxf.radius)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        abs(float(center.x) - cut.center[0]) <= 1e-9
        and abs(float(center.y) - cut.center[1]) <= 1e-9
        and abs(radius - cut.radius_mm) <= 1e-9
    )


def _layout_signature(plate: LaidOutPlate) -> tuple[object, ...]:
    return (
        plate.group_id,
        plate.roles,
        plate.physical_plate_ids,
        plate.label,
        plate.quantity,
        (
            plate.weld_allowance_contract.to_dict()
            if plate.weld_allowance_contract is not None
            else None
        ),
        tuple((item.start, item.end, item.bulge) for item in plate.outer_segments),
        tuple((cut.center, cut.radius_mm) for cut in plate.circular_cuts),
    )


def _plate_allowance_binding_matches(
    entity: object,
    plate: LaidOutPlate,
    manufacturing_fingerprint: str,
) -> bool:
    contract = plate.weld_allowance_contract
    try:
        tags = list(entity.get_xdata("BOX_DXF_SPLIT"))  # type: ignore[attr-defined]
    except (DXFValueError, AttributeError):
        return contract is None
    if contract is None:
        return False
    if [tag.code for tag in tags] != [
        1000,
        1000,
        1000,
        1070,
        1000,
        1040,
        1040,
        1000,
        1000,
    ]:
        return False
    return [tag.value for tag in tags] == [
        "BOX-WELD-ALLOWANCE-1.0",
        plate.group_id,
        ",".join(role.value for role in plate.roles),
        plate.quantity,
        "mm",
        contract.main_length_mm,
        contract.allowance_mm,
        contract.summary_sha256,
        manufacturing_fingerprint,
    ]


def validate_saved_dxf(
    path: Path,
    manufacturing_ir: BoxManufacturingIR,
    *,
    layout: BoxLayout | None = None,
) -> dict[str, object]:
    """Re-open a saved DXF and prove writer/MIR closure."""

    from .writer import (
        layout_box_manufacturing_ir,
        part_mark_clearance_envelope,
        part_mark_envelope,
        plate_material_geometry,
    )

    expected = layout_box_manufacturing_ir(manufacturing_ir)
    document = readfile(path)
    auditor = document.audit()
    modelspace = document.modelspace()
    outer = list(modelspace.query("LWPOLYLINE[layer=='PLATE_CUT']"))
    inner_cuts = list(modelspace.query("LWPOLYLINE[layer=='CUT_HOLE']"))
    circular_cuts = list(modelspace.query("CIRCLE[layer=='CUT_HOLE']"))
    labels = list(modelspace.query("TEXT[layer=='PART_LABEL']"))
    label_texts = [decode_cad_text_transport(entity.dxf.text) for entity in labels]
    label_placements = [entity.get_placement() for entity in labels]
    label_points = [
        (float(placement[1].x), float(placement[1].y)) for placement in label_placements
    ]
    label_heights = [float(entity.dxf.height) for entity in labels]
    raw_dxf = path.read_bytes()
    expected_inner_geometry: list[tuple[ContourSegmentIR, ...]] = []
    expected_circular_geometry: list[CircularCutIR] = []
    expected_circle_colors: list[int] = []
    ambiguous_hole_count = 0
    for plate in expected.plates:
        expected_inner_geometry.extend(
            contour.segments for contour in plate.inner_contours
        )
        expected_circular_geometry.extend(plate.circular_cuts)
        bounds = contour_polygon(plate.outer_segments).bounds
        color_plan = plan_symmetric_circle_colors(
            tuple(
                (cut.center[0], cut.center[1], cut.radius_mm)
                for cut in plate.circular_cuts
            ),
            plate_min_x_mm=float(bounds[0]),
            plate_max_x_mm=float(bounds[2]),
        )
        expected_circle_colors.extend(color_plan.colors_aci)
        ambiguous_hole_count += len(color_plan.ambiguous_indices)
    actual_circle_colors = [int(entity.dxf.color) for entity in circular_cuts]
    actual_inner_colors = [int(entity.dxf.color) for entity in inner_cuts]
    cut_hole_layer = (
        document.layers.get("CUT_HOLE")
        if "CUT_HOLE" in document.layers
        else None
    )
    generated_helpers = list(modelspace.query("LINE XLINE RAY"))
    forbidden_manufacturing_entities = [
        entity
        for entity in modelspace
        if entity.dxf.layer in {"PLATE_CUT", "CUT_HOLE"}
        and entity.dxftype() in {"LINE", "POLYLINE", "ARC", "REGION"}
    ]
    checks = {
        "audit_clean": not auditor.has_errors,
        "r2007_utf8": document.dxfversion == "AC1021",
        "plate_polyline_count": len(outer) == len(expected.plates),
        "cut_curve_count": (
            len(inner_cuts) == len(expected_inner_geometry)
            and len(circular_cuts) == len(expected_circular_geometry)
        ),
        "label_count": len(labels) == len(expected.plates),
        "no_cross_or_helper_lines": not generated_helpers,
        "native_manufacturing_curves_only": not forbidden_manufacturing_entities,
        "cut_hole_layer_is_white_aci7": (
            cut_hole_layer is not None and cut_hole_layer.color == WHITE_ACI
        ),
        "symmetric_hole_colors_match": (
            actual_circle_colors == expected_circle_colors
        ),
        "noncircular_cut_holes_are_white": all(
            color == WHITE_ACI for color in actual_inner_colors
        ),
        "plate_weld_allowance_bindings_match": len(outer) == len(expected.plates)
        and all(
            _plate_allowance_binding_matches(
                entity,
                plate,
                manufacturing_ir.fingerprint,
            )
            for entity, plate in zip(outer, expected.plates, strict=True)
        ),
        "outer_curve_geometry_matches_layout": len(outer) == len(expected.plates)
        and all(
            _saved_contour_polyline_matches(entity, plate.outer_segments)
            for entity, plate in zip(outer, expected.plates, strict=True)
        ),
        "cut_curve_geometry_matches_layout": (
            len(inner_cuts) == len(expected_inner_geometry)
            and all(
                _saved_contour_polyline_matches(entity, geometry)
                for entity, geometry in zip(
                    inner_cuts, expected_inner_geometry, strict=True
                )
            )
            and len(circular_cuts) == len(expected_circular_geometry)
            and all(
                _saved_circle_matches(entity, geometry)
                for entity, geometry in zip(
                    circular_cuts, expected_circular_geometry, strict=True
                )
            )
        ),
        "labels_match_layout": label_texts
        == [plate.label for plate in expected.plates],
        "label_points_match_layout": len(label_points) == len(expected.label_points)
        and all(
            max(abs(saved[0] - wanted[0]), abs(saved[1] - wanted[1])) <= 1e-6
            for saved, wanted in zip(
                label_points,
                expected.label_points,
                strict=True,
            )
        ),
        "label_heights_match_layout": label_heights == list(expected.label_heights),
        "windows_cad_text_transport": (
            raw_dxf.isascii() and b"\r" not in raw_dxf and b"\n" in raw_dxf
        ),
        "no_acis_region_payload": (
            not list(modelspace.query("REGION")) and b"\nREGION\n" not in raw_dxf
        ),
        "labels_inside_plates": len(label_points) == len(expected.plates)
        and all(
            contour_polygon(plate.outer_segments).covers(Point(point))
            for point, plate in zip(label_points, expected.plates, strict=True)
        ),
        "label_envelopes_inside_material": len(label_points)
        == len(label_heights)
        == len(labels)
        == len(expected.plates)
        and all(
            plate_material_geometry(plate).covers(
                part_mark_envelope(text, point, height)
            )
            for text, point, height, plate in zip(
                label_texts,
                label_points,
                label_heights,
                expected.plates,
                strict=True,
            )
        ),
        "label_clearance_envelopes_inside_material": len(label_points)
        == len(label_heights)
        == len(labels)
        == len(expected.plates)
        and all(
            plate_material_geometry(plate).covers(
                part_mark_clearance_envelope(text, point, height)
            )
            for text, point, height, plate in zip(
                label_texts,
                label_points,
                label_heights,
                expected.plates,
                strict=True,
            )
        ),
        "label_style_matches_contract": (
            "SplitChinese" in document.styles
            and document.styles.get("SplitChinese").dxf.font == "simsun.ttc"
            and all(
                entity.dxf.style == "SplitChinese"
                and placement[0] is TextEntityAlignment.MIDDLE_CENTER
                for entity, placement in zip(labels, label_placements, strict=True)
            )
        ),
        "writer_closure": layout is None
        or (
            [_layout_signature(plate) for plate in layout.plates]
            == [_layout_signature(plate) for plate in expected.plates]
            and layout.label_points == expected.label_points
            and layout.label_heights == expected.label_heights
        ),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "audit_errors": len(auditor.errors),
        "entity_counts": {
            kind: len(list(modelspace.query(kind)))
            for kind in (
                "REGION",
                "LWPOLYLINE",
                "POLYLINE",
                "CIRCLE",
                "ARC",
                "TEXT",
                "LINE",
                "XLINE",
                "RAY",
            )
        },
        "forbidden_manufacturing_entity_count": len(forbidden_manufacturing_entities),
        "labels": label_texts,
        "label_heights": label_heights,
        "hole_color_counts": {
            "expected_red": expected_circle_colors.count(RED_ACI),
            "actual_red": actual_circle_colors.count(RED_ACI),
            "expected_white": expected_circle_colors.count(WHITE_ACI),
            "actual_white": actual_circle_colors.count(WHITE_ACI),
            "ambiguous": ambiguous_hole_count,
        },
        "manufacturing_ir_fingerprint": manufacturing_ir.fingerprint,
    }
