from __future__ import annotations

from steel_dxf_split.bh_annotations import (
    AnnotationModel,
    BoltMarkObservation,
    annotation_consistency,
)
from steel_dxf_split.bh_models import (
    BHAssembly,
    BHMetadata,
    BHPlate,
    BHPlateRole,
    BulgeContour,
    BulgeVertex,
    CircularCut,
    HProfile,
)
from steel_dxf_split.geometry_types import Point2D


def _contour(width: float, height: float) -> BulgeContour:
    return BulgeContour(
        [
            BulgeVertex(0.0, 0.0),
            BulgeVertex(width, 0.0),
            BulgeVertex(width, height),
            BulgeVertex(0.0, height),
        ]
    )


def _plate(
    role: BHPlateRole,
    *,
    region_id: str,
    diameter: float,
    hole_count: int,
    quantity: int,
) -> BHPlate:
    return BHPlate(
        role=role,
        contour=_contour(600.0, 400.0),
        thickness=20.0,
        label=region_id,
        quantity=quantity,
        circular_cuts=[
            CircularCut(Point2D(50.0 + index * 50.0, 100.0), diameter / 2.0)
            for index in range(hole_count)
        ],
        provenance={
            "source_region_id": region_id,
            "circular_cut_source_ids": [
                [f"{region_id}-hole-{index}"] for index in range(hole_count)
            ],
        },
    )


def _assembly() -> tuple[BHMetadata, BHAssembly]:
    metadata = BHMetadata(
        part_number="jy-1-cb-16",
        profile=HProfile(800.0, 400.0, 20.0, 22.0, "BH800*400*20*22"),
        nominal_length=600.0,
        material="Q355B",
        drawing_scale=1.0,
    )
    web = _plate(
        BHPlateRole.WEB,
        region_id="web",
        diameter=24.0,
        hole_count=7,
        quantity=1,
    )
    flange = _plate(
        BHPlateRole.FLANGE,
        region_id="flange",
        diameter=48.0,
        hole_count=2,
        quantity=2,
    )
    return metadata, BHAssembly(metadata, web, [flange], [])


def _mark(
    text: str,
    count: int,
    diameter: float,
    region_id: str,
    source_id: str,
) -> BoltMarkObservation:
    return BoltMarkObservation(
        raw_text=text,
        count=count,
        diameter=diameter,
        block_name="annotation",
        block_handle=text,
        target_source_ids=(source_id,),
        target_region_ids=(region_id,),
    )


def test_merged_flange_hole_marks_use_physical_plate_quantity() -> None:
    """Dropping plate.quantity must reject the eleven-hole CB16 pattern."""

    metadata, assembly = _assembly()
    model = AnnotationModel(
        bolt_marks=[
            _mark("7Φ24", 7, 24.0, "web", "web-hole-0"),
            _mark("2Φ48-a", 2, 48.0, "flange", "flange-hole-0"),
            _mark("2Φ48-b", 2, 48.0, "flange", "flange-hole-1"),
        ]
    )

    result = annotation_consistency(metadata, assembly, model)

    assert result["bolt_mark_count_plausible"] is True
    assert result["marked_hole_quantity"] == 11
    assert result["actual_hole_quantity"] == 11


def test_hole_capacity_cannot_move_between_regions() -> None:
    """Spare flange capacity must not hide an over-marked web."""

    metadata, assembly = _assembly()
    model = AnnotationModel(
        bolt_marks=[
            _mark("8Φ24", 8, 24.0, "web", "web-hole-0"),
            _mark("2Φ48", 2, 48.0, "flange", "flange-hole-0"),
        ]
    )

    result = annotation_consistency(metadata, assembly, model)

    assert result["marked_hole_quantity"] == 10
    assert result["actual_hole_quantity"] == 11
    assert result["bolt_mark_count_plausible"] is False


def test_hole_capacity_cannot_move_between_diameters() -> None:
    """Capacity for a different diameter must not authorize a bolt mark."""

    metadata, assembly = _assembly()
    assembly.web_plate.circular_cuts.append(
        CircularCut(Point2D(450.0, 100.0), 24.0)
    )
    assembly.web_plate.provenance["circular_cut_source_ids"].append(
        ["web-hole-48"]
    )
    model = AnnotationModel(
        bolt_marks=[
            _mark("8Φ24", 8, 24.0, "web", "web-hole-0"),
        ]
    )

    result = annotation_consistency(metadata, assembly, model)

    assert result["marked_hole_quantity"] <= result["actual_hole_quantity"]
    assert result["bolt_mark_count_plausible"] is False


def test_ambiguous_region_mark_remains_fail_closed() -> None:
    """A mark associated with selected and unselected views has no proven capacity."""

    metadata, assembly = _assembly()
    model = AnnotationModel(
        bolt_marks=[
            BoltMarkObservation(
                raw_text="2Φ48",
                count=2,
                diameter=48.0,
                block_name="annotation",
                block_handle="ambiguous",
                target_source_ids=("flange-hole-0",),
                target_region_ids=("flange", "unselected-view"),
            )
        ]
    )

    result = annotation_consistency(metadata, assembly, model)

    assert result["bolt_mark_count_plausible"] is False
