from __future__ import annotations

import gc
from dataclasses import asdict, dataclass, replace
from itertools import permutations
from math import hypot
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFEntity
from ezdxf.path import make_path
from shapely import set_precision
from shapely.affinity import translate
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from .bh_geometry import arc_points, flatten_bulge_contour
from .bh_models import BHAssembly, BHPlate, BulgeContour
from .bh_trace import TraceObserver, emit_trace
from .bh_trace_geometry import entity_shapes, polygon_shape, polygon_shapes
from .dxf_io import recursive_virtual_entities


@dataclass(slots=True)
class ManualPlate:
    polygon: Polygon
    bbox: tuple[float, float]
    area: float


@dataclass(slots=True)
class SupervisedComparison:
    ok: bool
    checks: dict[str, bool]
    values: dict[str, object]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _flatten_entity(entity: DXFEntity, distance: float = 0.005) -> list[tuple[float, float]]:
    kind = entity.dxftype()
    if kind == "LINE":
        return [
            (float(entity.dxf.start.x), float(entity.dxf.start.y)),
            (float(entity.dxf.end.x), float(entity.dxf.end.y)),
        ]
    if kind == "ARC":
        return arc_points(entity, max_angle_step=2.0)
    if kind == "CIRCLE":
        path = make_path(entity)
        return [(float(v.x), float(v.y)) for v in path.flattening(distance)]
    if kind in {"LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"}:
        path = make_path(entity)
        return [(float(v.x), float(v.y)) for v in path.flattening(distance)]
    return []


def _reference_entities(doc: ezdxf.document.Drawing) -> list[DXFEntity]:
    result: list[DXFEntity] = []
    for entity in doc.modelspace():
        items = recursive_virtual_entities(entity) if entity.dxftype() == "INSERT" else [entity]
        for item in items:
            if item.dxftype() in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"}:
                result.append(item)
    return result


def polygonize_reference(
    path: Path,
    *,
    precision: float = 0.001,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> list[Polygon]:
    doc = ezdxf.readfile(path)
    entities = _reference_entities(doc)
    source_shapes = tuple(
        replace(shape, role="manual_reference")
        for shape in entity_shapes("manual-source", entities)
    )
    emit_trace(
        observer,
        stage_id="12_manual_supervision",
        artifact_id="manual_source_linework",
        status="observed",
        title_zh="人工拆板源线网",
        summary_zh=f"读取人工参考图中的 {len(entities)} 个几何实体",
        hypothesis_id=hypothesis_id,
        shapes=source_shapes,
        payload={
            "manual_reference_name": path.name,
            "entity_count": len(entities),
            "precision_mm": precision,
        },
    )
    del doc
    gc.collect()
    linework: list[LineString] = []
    for entity in entities:
        points = _flatten_entity(entity)
        if len(points) >= 2:
            linework.append(LineString(points))
    if not linework:
        raise ValueError(f"No geometric linework found in manual reference: {path}")
    noded = unary_union(set_precision(MultiLineString(linework), precision, mode="valid_output"))
    faces = [face for face in polygonize(noded) if face.area > 1.0]
    if not faces:
        raise ValueError(f"No closed faces reconstructed from manual reference: {path}")
    emit_trace(
        observer,
        stage_id="12_manual_supervision",
        artifact_id="manual_source_faces",
        status="observed",
        title_zh="人工拆板闭合面",
        summary_zh=f"节点化线网得到 {len(faces)} 个面积大于 1 mm² 的闭合面",
        hypothesis_id=hypothesis_id,
        shapes=polygon_shapes("manual-face", "manual_reference", faces),
        payload={"face_count": len(faces), "precision_mm": precision},
    )
    return faces


def _contour_polygon(contour: BulgeContour) -> Polygon:
    return Polygon(flatten_bulge_contour(contour, max_sagitta=0.005))


def _plate_polygon(plate: BHPlate, *, include_cuts: bool = True) -> Polygon:
    outer = _contour_polygon(plate.contour)
    if not include_cuts:
        return outer
    holes = [list(_contour_polygon(contour).exterior.coords) for contour in plate.inner_contours]
    for cut in plate.circular_cuts:
        circle = Point(cut.center.x, cut.center.y).buffer(cut.radius, resolution=128)
        holes.append(list(circle.exterior.coords))
    return Polygon(list(outer.exterior.coords), holes)


def _normalized(polygon: Polygon) -> Polygon:
    min_x, min_y, _, _ = polygon.bounds
    return translate(polygon, xoff=-min_x, yoff=-min_y)


def _dims(polygon: Polygon) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = polygon.bounds
    return max_x - min_x, max_y - min_y


def _manual_plates(faces: list[Polygon], expected_count: int) -> list[ManualPlate]:
    selected = sorted(faces, key=lambda face: face.area, reverse=True)[:expected_count]
    return [ManualPlate(face, _dims(face), float(face.area)) for face in selected]


def _circle_like_interior(ring) -> bool:
    polygon = Polygon(ring)
    width, height = _dims(polygon)
    if min(width, height) <= 0:
        return False
    return max(width, height) / min(width, height) <= 1.02


def _manual_cuts(plate: Polygon) -> tuple[list[tuple[float, float, float]], list[Polygon]]:
    circles: list[tuple[float, float, float]] = []
    shaped: list[Polygon] = []
    min_x, min_y, _, _ = plate.bounds
    for ring in plate.interiors:
        polygon = Polygon(ring)
        if _circle_like_interior(ring):
            bx0, by0, bx1, by1 = polygon.bounds
            circles.append(
                (
                    (bx0 + bx1) / 2.0 - min_x,
                    (by0 + by1) / 2.0 - min_y,
                    ((bx1 - bx0) + (by1 - by0)) / 4.0,
                )
            )
        else:
            shaped.append(_normalized(polygon))
    circles.sort(key=lambda item: (round(item[0], 6), round(item[1], 6), round(item[2], 6)))
    shaped.sort(key=lambda item: (-item.area, item.bounds))
    return circles, shaped


def _generated_plate_cuts(plate: BHPlate) -> tuple[list[tuple[float, float, float]], list[Polygon]]:
    circles = sorted(
        [(cut.center.x, cut.center.y, cut.radius) for cut in plate.circular_cuts],
        key=lambda item: (round(item[0], 6), round(item[1], 6), round(item[2], 6)),
    )
    shaped = sorted(
        [_normalized(_contour_polygon(contour)) for contour in plate.inner_contours],
        key=lambda item: (-item.area, item.bounds),
    )
    return circles, shaped


def _dimension_cost(generated: Polygon, manual: Polygon) -> float:
    gw, gh = _dims(generated)
    mw, mh = _dims(manual)
    direct = abs(gw - mw) + abs(gh - mh)
    swapped = abs(gw - mh) + abs(gh - mw)
    return min(direct, swapped)


def _plate_assignment_cost(plate: BHPlate, generated: Polygon, manual: Polygon) -> float:
    generated_cut_count = len(plate.circular_cuts) + len(plate.inner_contours)
    manual_cut_count = len(manual.interiors)
    # Feature count resolves identical/overlapping outer contours, while the
    # dimension term remains decisive for ordinary plates.
    return _dimension_cost(generated, manual) + 1000.0 * abs(generated_cut_count - manual_cut_count)


def _best_assignment(
    plates: list[BHPlate], generated: list[Polygon], manual: list[Polygon]
) -> tuple[int, ...]:
    if len(generated) != len(manual):
        raise ValueError("Generated/manual plate count mismatch.")
    return min(
        permutations(range(len(manual))),
        key=lambda order: sum(
            _plate_assignment_cost(plates[i], generated[i], manual[j])
            for i, j in enumerate(order)
        ),
    )


def _nearest_point_differences(
    generated: list[tuple[float, float, float]],
    manual: list[tuple[float, float, float]],
) -> tuple[float, float]:
    if len(generated) != len(manual):
        return float("inf"), float("inf")
    remaining = manual[:]
    max_center = 0.0
    max_radius = 0.0
    for gx, gy, gr in generated:
        index, candidate = min(
            enumerate(remaining),
            key=lambda pair: hypot(gx - pair[1][0], gy - pair[1][1]),
        )
        mx, my, mr = candidate
        max_center = max(max_center, hypot(gx - mx, gy - my))
        max_radius = max(max_radius, abs(gr - mr))
        remaining.pop(index)
    return max_center, max_radius


def _shaped_cut_difference(generated: list[Polygon], manual: list[Polygon]) -> float:
    if len(generated) != len(manual):
        return float("inf")
    if not generated:
        return 0.0
    order = min(
        permutations(range(len(manual))),
        key=lambda permutation: sum(
            generated[index].hausdorff_distance(manual[target])
            for index, target in enumerate(permutation)
        ),
    )
    return max(
        float(generated[index].hausdorff_distance(manual[target]))
        for index, target in enumerate(order)
    )


def compare_bh_to_manual(
    assembly: BHAssembly,
    manual_path: Path,
    *,
    coordinate_tolerance_mm: float = 0.15,
    area_relative_tolerance: float = 3.5e-4,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> SupervisedComparison:
    faces = polygonize_reference(
        manual_path,
        observer=observer,
        hypothesis_id=hypothesis_id,
    )
    manual_plates = _manual_plates(faces, len(assembly.plates))
    generated_plates = [_plate_polygon(plate, include_cuts=False) for plate in assembly.plates]
    emit_trace(
        observer,
        stage_id="12_manual_supervision",
        artifact_id="manual_selected_plates",
        status="selected",
        title_zh="人工板件选择",
        summary_zh=f"按面积选择 {len(manual_plates)} 个人工制造板件",
        hypothesis_id=hypothesis_id,
        shapes=polygon_shapes(
            "manual-selected", "manual_reference", (item.polygon for item in manual_plates)
        ),
        payload={
            "selected_plate_count": len(manual_plates),
            "expected_plate_count": len(assembly.plates),
        },
    )
    assignment = _best_assignment(
        assembly.plates,
        generated_plates,
        [plate.polygon for plate in manual_plates],
    )
    emit_trace(
        observer,
        stage_id="12_manual_supervision",
        artifact_id="manual_plate_assignment",
        status="observed",
        title_zh="自动/人工板件分配",
        summary_zh="按外形尺寸与切口数量求解最小代价一一分配。",
        hypothesis_id=hypothesis_id,
        shapes=(
            *polygon_shapes("generated-assignment", "generated_result", generated_plates),
            *polygon_shapes(
                "manual-assignment",
                "manual_reference",
                (item.polygon for item in manual_plates),
            ),
        ),
        payload={
            "assignment": assignment,
            "generated_labels": [plate.label for plate in assembly.plates],
        },
    )

    plate_results: list[dict[str, object]] = []
    all_center_differences: list[float] = []
    all_radius_differences: list[float] = []
    all_shaped_differences: list[float] = []
    generated_circle_total = 0
    manual_circle_total = 0
    generated_shaped_total = 0
    manual_shaped_total = 0

    for index, generated in enumerate(generated_plates):
        plate = assembly.plates[index]
        manual = manual_plates[assignment[index]].polygon
        generated_norm = _normalized(generated)
        manual_outer = Polygon(manual.exterior)
        manual_norm = _normalized(manual_outer)
        gw, gh = _dims(generated_norm)
        mw, mh = _dims(manual_norm)
        hausdorff = float(generated_norm.hausdorff_distance(manual_norm))
        area_rel = abs(generated_norm.area - manual_norm.area) / max(manual_norm.area, 1.0)

        generated_circles, generated_shaped = _generated_plate_cuts(plate)
        manual_circles, manual_shaped = _manual_cuts(manual)
        center_difference, radius_difference = _nearest_point_differences(
            generated_circles, manual_circles
        )
        shaped_difference = _shaped_cut_difference(generated_shaped, manual_shaped)
        generated_circle_total += len(generated_circles)
        manual_circle_total += len(manual_circles)
        generated_shaped_total += len(generated_shaped)
        manual_shaped_total += len(manual_shaped)
        all_center_differences.append(center_difference)
        all_radius_differences.append(radius_difference)
        all_shaped_differences.append(shaped_difference)

        plate_results.append(
            {
                "generated_label": plate.label,
                "generated_bbox_mm": [gw, gh],
                "manual_bbox_mm": [mw, mh],
                "max_bbox_difference_mm": max(abs(gw - mw), abs(gh - mh)),
                "hausdorff_distance_mm": hausdorff,
                "area_relative_difference": area_rel,
                "generated_circular_cut_count": len(generated_circles),
                "manual_circular_cut_count": len(manual_circles),
                "max_circular_cut_center_difference_mm": center_difference,
                "max_circular_cut_radius_difference_mm": radius_difference,
                "generated_shaped_cut_count": len(generated_shaped),
                "manual_shaped_cut_count": len(manual_shaped),
                "max_shaped_cut_hausdorff_mm": shaped_difference,
            }
        )

    max_plate_hausdorff = max(result["hausdorff_distance_mm"] for result in plate_results)
    max_bbox_difference = max(result["max_bbox_difference_mm"] for result in plate_results)
    max_area_relative = max(result["area_relative_difference"] for result in plate_results)
    max_center_difference = max(all_center_differences, default=0.0)
    max_radius_difference = max(all_radius_differences, default=0.0)
    max_shaped_hausdorff = max(all_shaped_differences, default=0.0)
    checks = {
        "plate_geometry_count_matches": len(generated_plates) == len(manual_plates),
        "plate_bbox_matches": max_bbox_difference <= coordinate_tolerance_mm,
        "plate_boundaries_match": max_plate_hausdorff <= coordinate_tolerance_mm,
        "plate_areas_match": max_area_relative <= area_relative_tolerance,
        "circular_cut_count_matches": generated_circle_total == manual_circle_total,
        "circular_cut_centers_match": max_center_difference <= coordinate_tolerance_mm,
        "circular_cut_radii_match": max_radius_difference <= coordinate_tolerance_mm,
        "shaped_cut_count_matches": generated_shaped_total == manual_shaped_total,
        "shaped_cut_boundaries_match": max_shaped_hausdorff <= coordinate_tolerance_mm,
    }
    warnings: list[str] = []
    if max_plate_hausdorff > 0.05:
        warnings.append(
            "Manual DXF contains endpoint/arc approximation deviations above 0.05 mm; accepted only within the configured supervised tolerance."
        )
    overlay_shapes = []
    for index, generated in enumerate(generated_plates):
        manual = manual_plates[assignment[index]].polygon
        overlay_shapes.extend(
            (
                polygon_shape(
                    f"generated-overlay-{index + 1:02d}",
                    "generated_result",
                    _normalized(generated),
                ),
                polygon_shape(
                    f"manual-overlay-{index + 1:02d}",
                    "manual_reference",
                    _normalized(Polygon(manual.exterior)),
                ),
            )
        )
    emit_trace(
        observer,
        stage_id="12_manual_supervision",
        artifact_id="manual_overlay",
        status="observed",
        title_zh="自动/人工归一化叠加",
        summary_zh=f"叠加 {len(generated_plates)} 组自动与人工板件外边界",
        hypothesis_id=hypothesis_id,
        shapes=tuple(overlay_shapes),
        payload={"assignment": assignment},
    )
    result = SupervisedComparison(
        ok=all(checks.values()),
        checks=checks,
        values={
            "manual_reference": str(manual_path.resolve()),
            "manual_face_count": len(faces),
            "plate_results": plate_results,
            "max_plate_hausdorff_mm": max_plate_hausdorff,
            "max_bbox_difference_mm": max_bbox_difference,
            "max_area_relative_difference": max_area_relative,
            "generated_circular_cut_count": generated_circle_total,
            "manual_circular_cut_count": manual_circle_total,
            "max_circular_cut_center_difference_mm": max_center_difference,
            "max_circular_cut_radius_difference_mm": max_radius_difference,
            "generated_shaped_cut_count": generated_shaped_total,
            "manual_shaped_cut_count": manual_shaped_total,
            "max_shaped_cut_hausdorff_mm": max_shaped_hausdorff,
        },
        warnings=warnings,
    )
    emit_trace(
        observer,
        stage_id="12_manual_supervision",
        artifact_id="manual_metrics",
        status="observed" if result.ok else "failed",
        title_zh="人工监督误差指标",
        summary_zh=("自动拆板与人工拆板在既定容差内一致。" if result.ok else "至少一项人工监督指标超出容差。"),
        hypothesis_id=hypothesis_id,
        shapes=tuple(overlay_shapes),
        payload={
            "coordinate_tolerance_mm": coordinate_tolerance_mm,
            "area_relative_tolerance": area_relative_tolerance,
            "checks": checks,
            "values": {
                **result.values,
                "manual_reference": manual_path.name,
            },
            "warnings": warnings,
        },
    )
    return result
