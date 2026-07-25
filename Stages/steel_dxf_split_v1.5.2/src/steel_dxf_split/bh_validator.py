from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import ezdxf
from ezdxf.enums import TextEntityAlignment
from ezdxf.lldxf.const import DXFValueError
from shapely.geometry import Point, Polygon

from .bh_geometry import flatten_bulge_contour
from .bh_manufacturing_ir import (
    BHManufacturingIR,
    EvidenceState,
    ManufacturingPlateRole,
    WeldAllowanceContractError,
    derive_weld_allowance_contract,
)
from .bh_models import BHAssembly, BHPlate, BulgeContour, CircularCut
from .hole_color_policy import (
    RED_ACI,
    WHITE_ACI,
    plan_symmetric_circle_colors,
)
from .part_mark_layout import (
    part_mark_clearance_envelope,
    part_mark_envelope,
)
if TYPE_CHECKING:
    from .bh_writer import BHLayout


@dataclass(slots=True)
class BHValidationReport:
    ok: bool
    checks: dict[str, bool]
    values: dict[str, object]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BHManufacturingIRValidationReport:
    ok: bool
    checks: dict[str, bool]
    values: dict[str, object]
    diagnostic_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_bh_manufacturing_ir(
    manufacturing: BHManufacturingIR,
    assembly: BHAssembly,
) -> BHManufacturingIRValidationReport:
    def contour_matches(segments, contour: BulgeContour) -> bool:
        if len(segments) != len(contour.vertices):
            return False
        return all(
            max(
                abs(segment.start[0] - vertex.x),
                abs(segment.start[1] - vertex.y),
                abs(segment.end[0] - following.x),
                abs(segment.end[1] - following.y),
                abs(segment.bulge - vertex.bulge),
            )
            <= 1e-6
            for index, (segment, vertex) in enumerate(
                zip(segments, contour.vertices, strict=True)
            )
            for following in (contour.vertices[(index + 1) % len(contour.vertices)],)
        )

    geometry_matches = []
    for plate in manufacturing.plates:
        if not 0 <= plate.source_assembly_plate_index < len(assembly.plates):
            geometry_matches.append(False)
            continue
        source_plate = assembly.plates[plate.source_assembly_plate_index]
        outer_ok = contour_matches(plate.outer_segments, source_plate.contour)
        inner_ok = len(plate.inner_contours) == len(source_plate.inner_contours) and all(
            contour_matches(inner.segments, source_contour)
            for inner, source_contour in zip(
                plate.inner_contours,
                source_plate.inner_contours,
                strict=True,
            )
        )
        cuts_ok = len(plate.circular_cuts) == len(source_plate.circular_cuts) and all(
            max(
                abs(cut.center[0] - source_cut.center.x),
                abs(cut.center[1] - source_cut.center.y),
                abs(cut.radius_mm - source_cut.radius),
            )
            <= 1e-6
            for cut, source_cut in zip(
                plate.circular_cuts,
                source_plate.circular_cuts,
                strict=True,
            )
        )
        geometry_matches.append(outer_ok and inner_ok and cuts_ok)
    roles = [plate.role for plate in manufacturing.plates]
    feature_evidence = [
        segment.evidence
        for plate in manufacturing.plates
        for segment in plate.outer_segments
    ]
    feature_evidence.extend(
        segment.evidence
        for plate in manufacturing.plates
        for contour in plate.inner_contours
        for segment in contour.segments
    )
    feature_evidence.extend(
        cut.evidence
        for plate in manufacturing.plates
        for cut in plate.circular_cuts
    )
    role_evidence = [plate.role_evidence for plate in manufacturing.plates]
    allowance_contracts_match = []
    for plate in manufacturing.plates:
        try:
            expected_contract = derive_weld_allowance_contract(plate.outer_segments)
        except WeldAllowanceContractError:
            allowance_contracts_match.append(
                plate.weld_allowance_contract is None
            )
        else:
            allowance_contracts_match.append(
                expected_contract == plate.weld_allowance_contract
            )
    physical_cut_count = len(assembly.web_plate.circular_cuts) + sum(
        len(plate.circular_cuts) * plate.quantity
        for plate in assembly.flange_plates
    )
    physical_inner_count = len(assembly.web_plate.inner_contours) + sum(
        len(plate.inner_contours) * plate.quantity
        for plate in assembly.flange_plates
    )
    checks = {
        "one_web_upper_lower": roles
        == [
            ManufacturingPlateRole.WEB,
            ManufacturingPlateRole.UPPER_FLANGE,
            ManufacturingPlateRole.LOWER_FLANGE,
        ],
        "physical_quantities_are_explicit": all(
            plate.quantity == 1 for plate in manufacturing.plates
        ),
        "metadata_matches_assembly": (
            manufacturing.part_number == assembly.metadata.part_number
            and manufacturing.profile == assembly.metadata.profile.raw_text
            and manufacturing.material == assembly.metadata.material
            and abs(
                manufacturing.nominal_length_mm - assembly.metadata.nominal_length
            )
            <= 1e-6
        ),
        "plate_thickness_matches_assembly": (
            len(manufacturing.plates) == 3
            and abs(
                manufacturing.plates[0].thickness_mm
                - assembly.web_plate.thickness
            )
            <= 1e-6
            and all(
                abs(plate.thickness_mm - assembly.metadata.profile.flange_thickness)
                <= 1e-6
                for plate in manufacturing.plates[1:]
            )
        ),
        "feature_counts_match_physical_assembly": (
            sum(len(plate.circular_cuts) for plate in manufacturing.plates)
            == physical_cut_count
            and sum(len(plate.inner_contours) for plate in manufacturing.plates)
            == physical_inner_count
        ),
        "geometry_matches_writer_assembly": all(geometry_matches),
        "outer_contours_nonempty": all(
            len(plate.outer_segments) >= 3 for plate in manufacturing.plates
        ),
        "weld_allowance_contracts_match_geometry": all(
            allowance_contracts_match
        ),
        "feature_provenance_complete": all(
            evidence.state not in {EvidenceState.MISSING, EvidenceState.CONFLICT}
            and bool(evidence.source_ids or evidence.rule_ids)
            for evidence in feature_evidence
        ),
        "role_provenance_complete": all(
            evidence.state not in {EvidenceState.MISSING, EvidenceState.CONFLICT}
            and bool(evidence.source_ids or evidence.rule_ids)
            for evidence in role_evidence
        ),
        "fingerprint_valid": len(manufacturing.fingerprint) == 64,
    }
    diagnostic_codes = []
    if not checks["feature_provenance_complete"]:
        diagnostic_codes.append("FEATURE.PROVENANCE.MISSING")
    if not checks["role_provenance_complete"]:
        diagnostic_codes.append("PLATE.ROLE.PROVENANCE.MISSING")
    if not all(
        value
        for key, value in checks.items()
        if key not in {"feature_provenance_complete", "role_provenance_complete"}
    ):
        diagnostic_codes.append("MANUFACTURING.IR.CONTRACT.MISMATCH")
    return BHManufacturingIRValidationReport(
        ok=all(checks.values()),
        checks=checks,
        values={
            "plate_count": len(manufacturing.plates),
            "circular_cut_count": sum(
                len(plate.circular_cuts) for plate in manufacturing.plates
            ),
            "inner_contour_count": sum(
                len(plate.inner_contours) for plate in manufacturing.plates
            ),
            "fingerprint": manufacturing.fingerprint,
        },
        diagnostic_codes=tuple(diagnostic_codes),
    )


def _contour_polygon(contour: BulgeContour) -> Polygon:
    return Polygon(flatten_bulge_contour(contour, max_sagitta=0.01))


def _plate_geometry_checks(plate: BHPlate) -> dict[str, bool]:
    outer = _contour_polygon(plate.contour)
    inner_polygons = [_contour_polygon(contour) for contour in plate.inner_contours]
    material = Polygon(outer.exterior.coords, [list(inner.exterior.coords) for inner in inner_polygons])
    centers = [Point(cut.center.x, cut.center.y) for cut in plate.circular_cuts]
    cuts_inside = all(
        material.buffer(0.01).covers(center)
        and material.boundary.distance(center) + 0.01 >= cut.radius
        for cut, center in zip(plate.circular_cuts, centers)
    )
    cuts_unique = len(
        {
            (round(cut.center.x, 3), round(cut.center.y, 3), round(cut.radius, 3))
            for cut in plate.circular_cuts
        }
    ) == len(plate.circular_cuts)
    cuts_non_overlapping = all(
        first.center.distance_to(second.center) + 0.01 >= first.radius + second.radius
        for index, first in enumerate(plate.circular_cuts)
        for second in plate.circular_cuts[index + 1 :]
    )
    inner_valid = all(
        inner.is_valid
        and inner.area > 1e-6
        and outer.buffer(0.01).covers(inner)
        for inner in inner_polygons
    )
    inner_non_overlapping = all(
        first.intersection(second).area <= 1e-6
        for index, first in enumerate(inner_polygons)
        for second in inner_polygons[index + 1 :]
    )
    return {
        "outer_valid": outer.is_valid and outer.area > 1e-6,
        "material_valid": material.is_valid and material.area > 1e-6,
        "inner_valid_and_contained": inner_valid,
        "inner_non_overlapping": inner_non_overlapping,
        "circular_cuts_fit_material": cuts_inside,
        "circular_cuts_unique": cuts_unique,
        "circular_cuts_non_overlapping": cuts_non_overlapping,
    }


def validate_bh_assembly(assembly: BHAssembly) -> BHValidationReport:
    web = assembly.web_plate
    per_plate_geometry_checks = [_plate_geometry_checks(plate) for plate in assembly.plates]
    profile = assembly.metadata.profile
    expected_clear = (
        profile.minimum_clear_web_height if profile.is_variable_height else profile.clear_web_height
    )
    transverse = web.bbox.height
    total_circles = sum(len(plate.circular_cuts) for plate in assembly.plates)
    total_inner = sum(len(plate.inner_contours) for plate in assembly.plates)
    expected_labels = [f"p={assembly.metadata.part_number}腹"]
    expected_labels.extend(
        (
            [f"p={assembly.metadata.part_number}翼"]
            if len(assembly.flange_plates) == 1
            else [
                f"p={assembly.metadata.part_number}上翼",
                f"p={assembly.metadata.part_number}下翼",
            ]
        )
    )
    checks = {
        "one_web_plate": web.role.value == "web",
        "one_or_two_flange_geometries": len(assembly.flange_plates) in {1, 2},
        "two_physical_flange_plates": sum(plate.quantity for plate in assembly.flange_plates) == 2,
        "closed_outer_contours": all(plate.contour.closed for plate in assembly.plates),
        "positive_contour_areas": all(plate.area_mm2 > 0 for plate in assembly.plates),
        "web_not_smaller_than_minimum_clear_height": transverse + 0.1 >= expected_clear,
        "valid_outer_and_material_polygons": all(
            item["outer_valid"] and item["material_valid"] for item in per_plate_geometry_checks
        ),
        "inner_contours_valid_and_contained": all(
            item["inner_valid_and_contained"] and item["inner_non_overlapping"]
            for item in per_plate_geometry_checks
        ),
        "all_circular_cuts_fit_material": all(
            item["circular_cuts_fit_material"] for item in per_plate_geometry_checks
        ),
        "no_duplicate_cut_centers_within_plate": all(
            item["circular_cuts_unique"] for item in per_plate_geometry_checks
        ),
        "circular_cuts_do_not_overlap": all(
            item["circular_cuts_non_overlapping"] for item in per_plate_geometry_checks
        ),
        "plate_thickness_matches_profile": (
            abs(web.thickness - profile.web_thickness) <= 1e-6
            and all(abs(plate.thickness - profile.flange_thickness) <= 1e-6 for plate in assembly.flange_plates)
        ),
        "flange_width_matches_profile": all(
            abs(min(plate.bbox.width, plate.bbox.height) - profile.flange_width) <= 0.15
            for plate in assembly.flange_plates
        ),
        "canonical_labels": [plate.label for plate in assembly.plates]
        == expected_labels,
    }
    warnings: list[str] = []
    if not profile.is_variable_height and transverse > profile.clear_web_height + 1.0:
        warnings.append(
            "Web bounding height exceeds H-2tf because the source geometry contains stepped/cranked transitions; source geometry takes precedence."
        )
    if profile.is_variable_height:
        warnings.append(
            "Variable-height H member: web depth and two flange developments are validated from source geometry rather than a single H-2tf value."
        )
    values = {
        "part_number": assembly.metadata.part_number,
        "profile": assembly.metadata.profile.raw_text,
        "nominal_length_mm": assembly.metadata.nominal_length,
        "material": assembly.metadata.material,
        "circular_cut_count": total_circles,
        "web_circular_cut_count": len(web.circular_cuts),
        "flange_circular_cut_count": sum(len(plate.circular_cuts) for plate in assembly.flange_plates),
        "circular_cut_diameters_mm": sorted(
            {
                round(cut.radius * 2.0, 6)
                for plate in assembly.plates
                for cut in plate.circular_cuts
            }
        ),
        "web_bbox_mm": [web.bbox.width, web.bbox.height],
        "inner_contour_count": total_inner,
        "web_inner_contours": len(web.inner_contours),
        "flange_bboxes_mm": [
            [plate.bbox.width, plate.bbox.height] for plate in assembly.flange_plates
        ],
        "flange_quantities": [plate.quantity for plate in assembly.flange_plates],
        "per_plate_features": [
            {
                "label": plate.label,
                "circular_cut_count": len(plate.circular_cuts),
                "inner_contour_count": len(plate.inner_contours),
                "geometry_checks": geometry_checks,
                "provenance": plate.provenance,
            }
            for plate, geometry_checks in zip(assembly.plates, per_plate_geometry_checks)
        ],
        "labels": [plate.label for plate in assembly.plates],
    }
    return BHValidationReport(all(checks.values()), checks, values, warnings)


def _saved_contour_polyline_matches(entity, contour: BulgeContour) -> bool:
    """Check exact native polyline vertices and bulges without ACIS lowering."""

    if not entity.closed:
        return False
    try:
        points = list(entity.get_points("xyb"))
    except (AttributeError, TypeError, ValueError):
        return False
    return len(points) == len(contour.vertices) and all(
        max(
            abs(float(point[0]) - vertex.x),
            abs(float(point[1]) - vertex.y),
            abs(float(point[2]) - vertex.bulge),
        ) <= 1e-9
        for point, vertex in zip(points, contour.vertices, strict=True)
    )


def _saved_circle_matches(entity, cut: CircularCut) -> bool:
    return max(
        abs(float(entity.dxf.center.x) - cut.center.x),
        abs(float(entity.dxf.center.y) - cut.center.y),
        abs(float(entity.dxf.radius) - cut.radius),
    ) <= 1e-9


def _layout_plate_signature(plate: BHPlate) -> tuple[object, ...]:
    return (
        plate.role.value,
        plate.label,
        plate.thickness,
        plate.quantity,
        tuple((item.x, item.y, item.bulge) for item in plate.contour.vertices),
        tuple(
            (cut.center.x, cut.center.y, cut.radius)
            for cut in plate.circular_cuts
        ),
        tuple(
            tuple((item.x, item.y, item.bulge) for item in contour.vertices)
            for contour in plate.inner_contours
        ),
    )


def _plate_allowance_binding_matches(entity, plate: BHPlate) -> bool:
    contract = plate.provenance.get("weld_allowance_contract")
    try:
        actual = [
            tag.value for tag in entity.get_xdata("STEEL_DXF_SPLIT")
        ]
    except DXFValueError:
        return contract is None
    if not isinstance(contract, dict):
        return False
    expected = [
        "BH-WELD-ALLOWANCE-1.0",
        str(plate.provenance["manufacturing_plate_id"]),
        str(plate.provenance["manufacturing_role"]),
        "mm",
        float(contract["main_length_mm"]),
        float(contract["allowance_mm"]),
        str(plate.provenance["weld_allowance_contract_sha256"]),
        str(plate.provenance["manufacturing_ir_fingerprint"]),
    ]
    return actual == expected


def validate_bh_saved_dxf(
    path: Path,
    manufacturing_ir: BHManufacturingIR,
    *,
    layout: BHLayout | None = None,
) -> dict[str, object]:
    from .bh_writer import (
        bh_plate_material_geometry,
        layout_bh_manufacturing_ir,
    )

    requested_height = (
        layout.label_heights[0]
        if layout is not None and layout.label_heights
        else None
    )
    expected_layout = layout_bh_manufacturing_ir(
        manufacturing_ir,
        preferred_text_height=requested_height,
    )
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    msp = doc.modelspace()
    counts: dict[str, int] = {}
    layer_counts: dict[str, dict[str, int]] = {}
    for entity in msp:
        kind = entity.dxftype()
        counts[kind] = counts.get(kind, 0) + 1
        layer = layer_counts.setdefault(entity.dxf.layer, {})
        layer[kind] = layer.get(kind, 0) + 1
    saved_outer = list(msp.query("LWPOLYLINE[layer=='PLATE_CUT']"))
    saved_inner = list(msp.query("LWPOLYLINE[layer=='CUT_HOLE']"))
    saved_circles = list(msp.query("CIRCLE[layer=='CUT_HOLE']"))
    from .dxf_io import decode_cad_text_transport

    label_entities = list(msp.query("TEXT[layer=='PART_LABEL']"))
    labels = [
        decode_cad_text_transport(entity.dxf.text)
        for entity in label_entities
    ]
    label_placements = [entity.get_placement() for entity in label_entities]
    label_points = [
        (float(placement[1].x), float(placement[1].y))
        for placement in label_placements
    ]
    label_heights = [float(entity.dxf.height) for entity in label_entities]
    raw_dxf = path.read_bytes()
    ascii_dxf = raw_dxf.decode("ascii", errors="replace")
    generated_lines = sum(
        layer_counts.get(layer, {}).get(kind, 0)
        for layer in ("PLATE_CUT", "CUT_HOLE", "PART_LABEL", "SPLIT_NOTE")
        for kind in ("LINE", "XLINE", "RAY")
    )
    expected_inner: list[BulgeContour] = []
    expected_circles: list[CircularCut] = []
    expected_circle_colors: list[int] = []
    ambiguous_hole_count = 0
    for plate in expected_layout.plates:
        expected_inner.extend(plate.inner_contours)
        expected_circles.extend(plate.circular_cuts)
        color_plan = plan_symmetric_circle_colors(
            tuple(
                (cut.center.x, cut.center.y, cut.radius)
                for cut in plate.circular_cuts
            ),
            plate_min_x_mm=plate.bbox.min_x,
            plate_max_x_mm=plate.bbox.max_x,
        )
        expected_circle_colors.extend(color_plan.colors_aci)
        ambiguous_hole_count += len(color_plan.ambiguous_indices)
    actual_circle_colors = [int(entity.dxf.color) for entity in saved_circles]
    actual_inner_colors = [int(entity.dxf.color) for entity in saved_inner]
    cut_hole_layer = (
        doc.layers.get("CUT_HOLE") if "CUT_HOLE" in doc.layers else None
    )
    disallowed_manufacturing_entities = sum(
        layer_counts.get(layer, {}).get(kind, 0)
        for layer in ("PLATE_CUT", "CUT_HOLE")
        for kind in ("LINE", "POLYLINE", "ARC", "REGION")
    )
    checks = {
        "audit_clean": len(auditor.errors) == 0,
        "r2007_utf8": doc.dxfversion == "AC1021",
        "codepage_safe_unicode_transport": all(byte < 128 for byte in raw_dxf),
        "native_closed_curve_transport": "\nREGION\n" not in ascii_dxf,
        "plate_closed_polyline_count": len(saved_outer) == len(expected_layout.plates),
        "inner_closed_polyline_count": len(saved_inner) == len(expected_inner),
        "circular_cut_count": len(saved_circles) == len(expected_circles),
        "label_count": len(labels) == len(expected_layout.plates),
        "no_cross_or_helper_lines": generated_lines == 0,
        "no_non_native_manufacturing_curves": disallowed_manufacturing_entities == 0,
        "cut_hole_layer_is_white_aci7": (
            cut_hole_layer is not None and cut_hole_layer.color == WHITE_ACI
        ),
        "symmetric_hole_colors_match": (
            actual_circle_colors == expected_circle_colors
        ),
        "noncircular_cut_holes_are_white": all(
            color == WHITE_ACI for color in actual_inner_colors
        ),
    }
    checks.update(
        {
            "outer_geometry_matches_writer_layout": (
                len(saved_outer) == len(expected_layout.plates)
                and all(
                    _saved_contour_polyline_matches(entity, plate.contour)
                    for entity, plate in zip(
                        saved_outer,
                        expected_layout.plates,
                        strict=True,
                    )
                )
            ),
            "cut_geometry_matches_writer_layout": (
                len(saved_inner) == len(expected_inner)
                and len(saved_circles) == len(expected_circles)
                and all(
                    _saved_contour_polyline_matches(entity, contour)
                    for entity, contour in zip(saved_inner, expected_inner, strict=True)
                )
                and all(
                    _saved_circle_matches(entity, cut)
                    for entity, cut in zip(saved_circles, expected_circles, strict=True)
                )
            ),
            "labels_match_writer_layout": labels
            == [plate.label for plate in expected_layout.plates],
            "label_points_match_layout": (
                len(label_points) == len(expected_layout.label_points)
                and all(
                    max(
                        abs(saved[0] - wanted.x),
                        abs(saved[1] - wanted.y),
                    )
                    <= 1e-6
                    for saved, wanted in zip(
                        label_points,
                        expected_layout.label_points,
                        strict=True,
                    )
                )
            ),
            "label_heights_match_layout": label_heights
            == expected_layout.label_heights,
            "labels_inside_plates": (
                len(label_points) == len(expected_layout.plates)
                and all(
                    _contour_polygon(plate.contour).covers(Point(point))
                    for point, plate in zip(
                        label_points,
                        expected_layout.plates,
                        strict=True,
                    )
                )
            ),
            "label_envelopes_inside_material": (
                len(label_points)
                == len(label_heights)
                == len(labels)
                == len(expected_layout.plates)
                and all(
                    bh_plate_material_geometry(plate).covers(
                        part_mark_envelope(text, point, height)
                    )
                    for text, point, height, plate in zip(
                        labels,
                        label_points,
                        label_heights,
                        expected_layout.plates,
                        strict=True,
                    )
                )
            ),
            "label_clearance_envelopes_inside_material": (
                len(label_points)
                == len(label_heights)
                == len(labels)
                == len(expected_layout.plates)
                and all(
                    bh_plate_material_geometry(plate).covers(
                        part_mark_clearance_envelope(text, point, height)
                    )
                    for text, point, height, plate in zip(
                        labels,
                        label_points,
                        label_heights,
                        expected_layout.plates,
                        strict=True,
                    )
                )
            ),
            "label_style_matches_contract": (
                "SplitChinese" in doc.styles
                and doc.styles.get("SplitChinese").dxf.font == "simsun.ttc"
                and all(
                    entity.dxf.style == "SplitChinese"
                    and placement[0] is TextEntityAlignment.MIDDLE_CENTER
                    for entity, placement in zip(
                        label_entities,
                        label_placements,
                        strict=True,
                    )
                )
            ),
            "plate_weld_allowance_bindings_match": (
                len(saved_outer) == len(expected_layout.plates)
                and all(
                    _plate_allowance_binding_matches(entity, plate)
                    for entity, plate in zip(
                        saved_outer,
                        expected_layout.plates,
                        strict=True,
                    )
                )
            ),
            "manufacturing_ir_writer_closure": (
                layout is None
                or (
                    [
                        _layout_plate_signature(plate)
                        for plate in layout.plates
                    ]
                    == [
                        _layout_plate_signature(plate)
                        for plate in expected_layout.plates
                    ]
                    and layout.label_points == expected_layout.label_points
                    and layout.label_heights == expected_layout.label_heights
                )
            ),
        }
    )
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "audit_errors": len(auditor.errors),
        "entity_counts": counts,
        "layer_type_counts": layer_counts,
        "labels": labels,
        "label_heights": label_heights,
        "generated_line_count": generated_lines,
        "non_native_manufacturing_entity_count": disallowed_manufacturing_entities,
        "hole_color_counts": {
            "expected_red": expected_circle_colors.count(RED_ACI),
            "actual_red": actual_circle_colors.count(RED_ACI),
            "expected_white": expected_circle_colors.count(WHITE_ACI),
            "actual_white": actual_circle_colors.count(WHITE_ACI),
            "ambiguous": ambiguous_hole_count,
        },
        "manufacturing_ir_fingerprint": manufacturing_ir.fingerprint,
    }
