from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon
from steel_dxf_split.box.equivalence import PlateOutputGroup
from steel_dxf_split.box.manufacturing_ir import (
    CircularCutIR,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
    contour_polygon,
    rectangle_contour,
)
from tools.box_acceptance.geometry import (
    DEFAULT_COMPARISON_TOLERANCE,
    compare_groups_to_reference,
)
from tools.box_acceptance.manual_reference import (
    ManualOpening,
    ManualPlate,
    ManualReference,
    ManualShape,
    load_snapshot_reference,
)


def _evidence() -> FeatureEvidence:
    return FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=("test/source",),
        rule_ids=("BOX.RULE.TEST",),
        proof_ids=("BOX.PROOF.TEST",),
        description="external geometry oracle test evidence",
    )


def _plate(
    role: PhysicalPlateRole,
    *,
    length: float,
    width: float,
    holes: tuple[tuple[float, float, float], ...] = (),
) -> PhysicalPlateIR:
    evidence = _evidence()
    return PhysicalPlateIR(
        plate_id=role.value,
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=rectangle_contour(0.0, 0.0, length, width, evidence),
        circular_cuts=tuple(
            CircularCutIR(
                cut_id=f"{role.value}:hole:{index}",
                center=(x, y),
                radius_mm=radius,
                evidence=evidence,
            )
            for index, (x, y, radius) in enumerate(holes)
        ),
        inner_contours=(),
        role_evidence=evidence,
    )


def _group(plate: PhysicalPlateIR) -> PlateOutputGroup:
    return PlateOutputGroup(
        group_id=plate.role.value,
        roles=(plate.role,),
        physical_plates=(plate,),
        representative=plate,
        quantity=1,
        merge_authorized=False,
        equivalence_tolerance_mm=1e-5,
    )


def _merged_group(
    first: PhysicalPlateIR,
    second: PhysicalPlateIR,
) -> PlateOutputGroup:
    return PlateOutputGroup(
        group_id=f"{first.role.value}+{second.role.value}",
        roles=(first.role, second.role),
        physical_plates=(first, second),
        representative=first,
        quantity=2,
        merge_authorized=True,
        equivalence_tolerance_mm=1e-5,
    )


def _manual_plate(
    role: str,
    family: str,
    polygon,
    *,
    holes: tuple[ManualOpening, ...] = (),
    quantity: int = 1,
) -> ManualPlate:
    shape = ManualShape.from_polygon(
        entity_handle=f"shape:{role}",
        kind="TEST",
        polygon=polygon,
    )
    return ManualPlate(
        label=role,
        family=family,
        side="top" if role.startswith("上") else "bottom",
        quantity=quantity,
        label_position=(float(polygon.centroid.x), float(polygon.centroid.y)),
        shape=shape,
        openings=holes,
    )


def _reference_from_groups(
    groups: tuple[PlateOutputGroup, ...],
    *,
    transform=lambda polygon: polygon,
) -> ManualReference:
    roles = {
        PhysicalPlateRole.WEB_LEFT: ("上腹", "web"),
        PhysicalPlateRole.WEB_RIGHT: ("下腹", "web"),
        PhysicalPlateRole.FLANGE_TOP: ("上翼", "flange"),
        PhysicalPlateRole.FLANGE_BOTTOM: ("下翼", "flange"),
    }
    plates = []
    for group in groups:
        plate = group.representative
        label, family = roles[plate.role]
        polygon = transform(contour_polygon(plate.outer_segments))
        openings = tuple(
            ManualOpening.circle(
                entity_handle=cut.cut_id,
                center=transform(Point(cut.center)).coords[0],
                radius=cut.radius_mm,
            )
            for cut in plate.circular_cuts
        )
        plates.append(
            _manual_plate(
                label,
                family,
                polygon,
                holes=openings,
                quantity=group.quantity,
            )
        )
    return ManualReference(
        path=Path("synthetic-reference.json"),
        member_mark="a1-cb-test",
        plates=tuple(plates),
        evidence_warnings=(),
    )


def _four_groups() -> tuple[PlateOutputGroup, ...]:
    return tuple(
        _group(plate)
        for plate in (
            _plate(
                PhysicalPlateRole.WEB_LEFT,
                length=200.0,
                width=80.0,
                holes=((50.0, 40.0, 10.0),),
            ),
            _plate(PhysicalPlateRole.WEB_RIGHT, length=210.0, width=80.0),
            _plate(PhysicalPlateRole.FLANGE_TOP, length=180.0, width=60.0),
            _plate(PhysicalPlateRole.FLANGE_BOTTOM, length=190.0, width=60.0),
        )
    )


@pytest.mark.parametrize(
    "transform",
    (
        lambda polygon: affinity.translate(polygon, xoff=500.0, yoff=-200.0),
        lambda polygon: affinity.rotate(
            affinity.translate(polygon, xoff=500.0, yoff=200.0),
            90.0,
            origin=(0.0, 0.0),
        ),
        lambda polygon: affinity.scale(
            affinity.translate(polygon, xoff=500.0),
            xfact=-1.0,
            yfact=1.0,
            origin=(0.0, 0.0),
        ),
    ),
)
def test_rigid_translation_rotation_and_reflection_are_equivalent(transform) -> None:
    groups = _four_groups()
    reference = _reference_from_groups(groups, transform=transform)

    comparison = compare_groups_to_reference(
        groups,
        reference,
        part_number="a1-cb-test",
    )

    assert comparison.ok is True
    assert comparison.failed_checks == ()


def test_symmetric_contour_variant_uses_hole_evidence_before_float_noise() -> None:
    plate = _plate(
        PhysicalPlateRole.FLANGE_TOP,
        length=5792.0,
        width=800.0,
        holes=((3491.999333, 400.051758, 10.0),),
    )
    group = _group(plate)
    points = (
        (4.1382008930668235e-11, 0.0),
        (5792.000000444155, 0.0),
        (5792.000000444155, 800.0),
        (0.0, 800.0),
    )
    plate = replace(
        plate,
        outer_segments=tuple(
            ContourSegmentIR(
                segment_id=f"cb8-float-noise:{index}",
                start=point,
                end=points[(index + 1) % len(points)],
                bulge=0.0,
                evidence=_evidence(),
            )
            for index, point in enumerate(points)
        ),
    )
    group = _group(plate)
    manual_polygon = Polygon(
        (
            (100.0, 187.512823531717),
            (5892.000000444241, 187.512823531717),
            (5892.000000444241, 987.512823531717),
            (100.0, 987.512823531717),
        )
    )
    base_reference = _reference_from_groups((group,))
    base_manual = base_reference.plates[0]
    manual_shape = ManualShape.from_polygon(
        entity_handle="yellow-answer-plate",
        kind="TEST",
        polygon=manual_polygon,
    )
    reference = replace(
        base_reference,
        path=Path("symmetric-contour-reference.json"),
        plates=(
            replace(
                base_manual,
                label_position=(
                    float(manual_polygon.centroid.x),
                    float(manual_polygon.centroid.y),
                ),
                shape=manual_shape,
                openings=(
                    ManualOpening.circle(
                        entity_handle="yellow-answer-hole",
                        center=(3591.999333, 587.564581),
                        radius=10.0,
                    ),
                ),
            ),
        ),
    )

    comparison = compare_groups_to_reference(
        (group,),
        reference,
        part_number="a1-cb-test",
    )

    assert comparison.ok is True
    assert comparison.failed_check_keys == ()


def test_scale_change_is_not_treated_as_a_rigid_match() -> None:
    groups = _four_groups()
    reference = _reference_from_groups(
        groups,
        transform=lambda polygon: affinity.scale(
            polygon,
            xfact=1.02,
            yfact=1.0,
            origin=(0.0, 0.0),
        ),
    )

    comparison = compare_groups_to_reference(groups, reference, part_number="a1-cb-test")

    assert comparison.ok is False
    assert "contour" in comparison.failed_check_keys


@pytest.mark.parametrize("mutation", ("hole_center", "hole_count", "role"))
def test_hole_or_role_mismatch_fails_even_when_internal_status_was_auto_accept(
    mutation: str,
) -> None:
    groups = _four_groups()
    reference = _reference_from_groups(groups)
    first = reference.plates[0]
    if mutation == "hole_center":
        opening = first.openings[0]
        changed = replace(opening, center=(opening.center[0] + 5.0, opening.center[1]))
        reference = replace(reference, plates=(replace(first, openings=(changed,)), *reference.plates[1:]))
    elif mutation == "hole_count":
        reference = replace(reference, plates=(replace(first, openings=()), *reference.plates[1:]))
    else:
        reference = replace(reference, plates=(replace(first, label="下翼", family="flange"), *reference.plates[1:]))

    comparison = compare_groups_to_reference(
        groups,
        reference,
        part_number="a1-cb-test",
        internal_disposition="auto_accept",
    )

    assert comparison.ok is False
    assert comparison.internal_disposition == "auto_accept"


def test_merged_output_is_expanded_to_expose_per_role_feature_mismatch() -> None:
    top = _plate(
        PhysicalPlateRole.FLANGE_TOP,
        length=200.0,
        width=80.0,
        holes=((50.0, 40.0, 10.0),),
    )
    bottom = replace(
        top,
        plate_id=PhysicalPlateRole.FLANGE_BOTTOM.value,
        role=PhysicalPlateRole.FLANGE_BOTTOM,
    )
    groups = (_merged_group(top, bottom),)
    top_polygon = contour_polygon(top.outer_segments)
    reference = ManualReference(
        path=Path("separate-flange-reference.json"),
        member_mark="a1-cb-test",
        plates=(
            _manual_plate(
                "上翼",
                "flange",
                top_polygon,
                holes=(
                    ManualOpening.circle(
                        entity_handle="top-hole",
                        center=(50.0, 40.0),
                        radius=10.0,
                    ),
                ),
            ),
            _manual_plate("下翼", "flange", top_polygon),
        ),
    )

    comparison = compare_groups_to_reference(
        groups,
        reference,
        part_number="a1-cb-test",
        internal_disposition="auto_accept",
    )

    assert comparison.ok is False
    assert "flange_group_count" in comparison.failed_check_keys
    assert "circular_hole_count" in comparison.failed_check_keys
    assert len(comparison.comparisons) == 2


def test_snapshot_reference_keeps_zero_layer_plate_and_bulged_inner_opening(
    tmp_path: Path,
) -> None:
    digest = "f" * 64
    payload = {
        "schema": "BOX-YELLOW-REFERENCE-SNAPSHOT-1.0",
        "sample_id": "a1-cb-8",
        "source": {
            "relative_path": "answer.dwg",
            "sha256_before": digest,
            "sha256_after": digest,
            "unchanged": True,
        },
        "zwcad_progid": "ZWCAD.Application.2026",
        "model_space_count": 5,
        "entities": [
            {
                "object_name": "AcDbPolyline",
                "handle": "plate",
                "layer": "0",
                "color": 2,
                "coordinates": [0, 0, 100, 0, 100, 50, 0, 50],
                "bulges": [0, 0, 0, 0],
                "closed": True,
                "elevation": 0,
                "normal": [0, 0, 1],
            },
            {
                "object_name": "AcDbPolyline",
                "handle": "slot",
                "layer": "CUT_HOLE",
                "color": 2,
                "coordinates": [20, 20, 40, 20, 40, 30, 20, 30],
                "bulges": [0, 0.5, 0, 0.5],
                "closed": True,
                "elevation": 0,
                "normal": [0, 0, 1],
            },
            {
                "object_name": "AcDbCircle",
                "handle": "round",
                "layer": "0",
                "color": 2,
                "center": [70, 25, 0],
                "radius": 5,
                "normal": [0, 0, 1],
            },
            {
                "object_name": "AcDbText",
                "handle": "label",
                "layer": "PART_LABEL",
                "color": 2,
                "text": "p=a1-cb-8下翼",
                "insertion_point": [50, 25, 0],
                "height": 10,
                "rotation": 0,
            },
            {
                "object_name": "AcDbText",
                "handle": "foreign",
                "layer": "PART_LABEL",
                "color": 2,
                "text": "not-a-box-label",
                "insertion_point": [500, 500, 0],
                "height": 10,
                "rotation": 0,
            },
        ],
    }
    path = tmp_path / "a1-cb-8_correct-reference.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    reference = load_snapshot_reference(path, expected_source_sha256=digest)

    assert len(reference.plates) == 1
    assert reference.plates[0].shape.entity_handle == "plate"
    assert len(reference.plates[0].openings) == 2
    slot = next(item for item in reference.plates[0].openings if item.kind == "POLYGON")
    assert slot.source_bulges == (0.0, 0.5, 0.0, 0.5)


def test_comparison_tolerance_remains_tighter_than_production_errors() -> None:
    assert DEFAULT_COMPARISON_TOLERANCE.hole_center_mm <= 0.1
    assert DEFAULT_COMPARISON_TOLERANCE.hole_radius_mm <= 0.01
    assert DEFAULT_COMPARISON_TOLERANCE.symmetric_difference_fraction <= 0.002
