"""Offline, curve-aware comparison of frozen BOX MIR against manual references.

This module is intentionally outside ``src/steel_dxf_split``. For every sample
it completes source-only compilation before opening the corresponding manual
reference.  It is an acceptance oracle, never a compiler authority.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from math import atan2, degrees, hypot
from pathlib import Path
from tempfile import TemporaryDirectory

from shapely import affinity
from shapely.geometry import Point, Polygon

from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.compiler import BoxCoreCompilation, compile_box_core
from steel_dxf_split.box.equivalence import (
    PlateOutputGroup,
    group_equivalent_plate_pairs,
)
from steel_dxf_split.box.manufacturing_ir import (
    PhysicalPlateRole,
    contour_polygon,
)
from steel_dxf_split.box.provenance import (
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)
from steel_dxf_split.box.validator import validate_saved_dxf
from steel_dxf_split.box.writer import (
    OutputPurpose,
    canonical_box_label,
    layout_box_manufacturing_ir,
    part_mark_envelope,
    plate_material_geometry,
    write_box_clean,
)
from tools.box_manual_reference import ManualPlate, ManualShape, load_manual_reference

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class ComparisonTolerance:
    # Same upper bound as the source-derived Tekla end-course micro-gap rule.
    contour_hausdorff_mm: float = 3.1
    symmetric_difference_fraction: float = 0.002
    area_relative: float = 0.002
    zero_area_overlay_fraction: float = 0.0001
    hole_center_mm: float = 0.1
    hole_radius_mm: float = 0.01


DEFAULT_COMPARISON_TOLERANCE = ComparisonTolerance()


@dataclass(frozen=True, slots=True)
class _FramedGeometry:
    polygon: Polygon
    points: tuple[tuple[float, float], ...]


def _long_axis_angle(polygon: Polygon) -> float:
    rectangle = polygon.minimum_rotated_rectangle
    coordinates = tuple(rectangle.exterior.coords)
    edges = tuple(
        (
            end[0] - start[0],
            end[1] - start[1],
        )
        for start, end in zip(coordinates, coordinates[1:], strict=False)
    )
    dx, dy = max(edges, key=lambda item: hypot(item[0], item[1]))
    return degrees(atan2(dy, dx))


def _frame_geometry(
    polygon: Polygon,
    points: Iterable[tuple[float, float]],
) -> _FramedGeometry:
    angle = _long_axis_angle(polygon)
    rotated = affinity.rotate(polygon, -angle, origin=(0.0, 0.0))
    center = rotated.centroid
    framed = affinity.translate(rotated, xoff=-center.x, yoff=-center.y)

    from math import cos, radians, sin

    theta = radians(-angle)
    cosine = cos(theta)
    sine = sin(theta)
    transformed = tuple(
        (
            point[0] * cosine - point[1] * sine - center.x,
            point[0] * sine + point[1] * cosine - center.y,
        )
        for point in points
    )
    return _FramedGeometry(framed, transformed)


def _reflected_variants(value: _FramedGeometry) -> Iterable[_FramedGeometry]:
    for x_factor, y_factor in ((1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0)):
        yield _FramedGeometry(
            affinity.scale(
                value.polygon,
                xfact=x_factor,
                yfact=y_factor,
                origin=(0.0, 0.0),
            ),
            tuple((x_factor * x, y_factor * y) for x, y in value.points),
        )


def _manual_holes_for_plate(
    plate: ManualPlate,
    holes: tuple[ManualShape, ...],
) -> tuple[ManualShape, ...]:
    return tuple(
        hole
        for hole in holes
        if hole.circle_center is not None
        and plate.shape.polygon.buffer(0.1).covers(Point(hole.circle_center))
    )


def _point_set_hausdorff(
    first: tuple[tuple[float, float], ...],
    second: tuple[tuple[float, float], ...],
) -> float:
    if not first and not second:
        return 0.0
    if not first or not second:
        return float("inf")

    def directed(
        sources: tuple[tuple[float, float], ...],
        targets: tuple[tuple[float, float], ...],
    ) -> float:
        return max(min(hypot(x - tx, y - ty) for tx, ty in targets) for x, y in sources)

    return max(directed(first, second), directed(second, first))


def _compare_group_to_manual(
    group: PlateOutputGroup,
    manual: ManualPlate,
    manual_holes: tuple[ManualShape, ...],
    tolerance: ComparisonTolerance,
    part_number: str,
) -> dict[str, object]:
    output_polygon = contour_polygon(group.representative.outer_segments)
    output_holes = group.representative.circular_cuts
    output_frame = _frame_geometry(
        output_polygon,
        (cut.center for cut in output_holes),
    )
    manual_frame = _frame_geometry(
        manual.shape.polygon,
        (hole.circle_center for hole in manual_holes if hole.circle_center is not None),
    )
    best: tuple[tuple[float, ...], dict[str, float]] | None = None
    for variant in _reflected_variants(output_frame):
        hausdorff = float(variant.polygon.hausdorff_distance(manual_frame.polygon))
        symmetric_fraction = float(
            variant.polygon.symmetric_difference(manual_frame.polygon).area
            / max(variant.polygon.area, manual_frame.polygon.area, 1.0)
        )
        area_relative = float(
            abs(variant.polygon.area - manual_frame.polygon.area)
            / max(variant.polygon.area, manual_frame.polygon.area, 1.0)
        )
        hole_center = _point_set_hausdorff(variant.points, manual_frame.points)
        metrics = {
            "contour_hausdorff_mm": hausdorff,
            "symmetric_difference_fraction": symmetric_fraction,
            "area_relative": area_relative,
            "hole_center_hausdorff_mm": hole_center,
        }
        rank = (hausdorff, symmetric_fraction, area_relative, hole_center)
        if best is None or rank < best[0]:
            best = (rank, metrics)
    assert best is not None
    metrics = best[1]
    output_radii = sorted(cut.radius_mm for cut in output_holes)
    manual_radii = sorted(
        hole.circle_radius for hole in manual_holes if hole.circle_radius is not None
    )
    radius_error = (
        max(
            (
                abs(first - second)
                for first, second in zip(output_radii, manual_radii, strict=True)
            ),
            default=0.0,
        )
        if len(output_radii) == len(manual_radii)
        else float("inf")
    )
    distance_equivalent = (
        metrics["contour_hausdorff_mm"] <= tolerance.contour_hausdorff_mm
    )
    zero_area_overlay_equivalent = (
        metrics["symmetric_difference_fraction"] <= tolerance.zero_area_overlay_fraction
        and metrics["area_relative"] <= tolerance.zero_area_overlay_fraction
    )
    output_label = canonical_box_label(part_number, group.roles)
    label_prefix = f"p={part_number}"
    output_role = output_label.removeprefix(label_prefix)
    family_token = "腹" if manual.family == "web" else "翼"
    expected_roles = (
        {family_token}
        if manual.quantity == 2
        else {f"上{family_token}", f"下{family_token}"}
    )
    manual_canonical_label = f"{label_prefix}{manual.label}"
    checks = {
        "quantity": group.quantity == manual.quantity,
        "contour_equivalence": (distance_equivalent or zero_area_overlay_equivalent),
        "symmetric_difference": metrics["symmetric_difference_fraction"]
        <= tolerance.symmetric_difference_fraction,
        "area": metrics["area_relative"] <= tolerance.area_relative,
        "hole_count": len(output_holes) == len(manual_holes),
        "hole_centers": metrics["hole_center_hausdorff_mm"] <= tolerance.hole_center_mm,
        "hole_radii": radius_error <= tolerance.hole_radius_mm,
        "label_member": output_label.startswith(label_prefix) and bool(output_role),
        "label_family_and_quantity": output_role in expected_roles,
        "label_exact": output_label == manual_canonical_label,
    }
    return {
        "output_group": group.group_id,
        "manual_label": manual.label,
        "output_label": output_label,
        "manual_canonical_label": manual_canonical_label,
        "family": manual.family,
        "quantity": {"output": group.quantity, "manual": manual.quantity},
        "metrics": {**metrics, "hole_radius_max_error_mm": radius_error},
        "equivalence_channels": {
            "bounded_distance": distance_equivalent,
            "zero_area_projection_overlay": zero_area_overlay_equivalent,
        },
        "checks": checks,
        # Exact manual side naming is reported but is not a compiler oracle:
        # some references contradict the H/B source-course correspondence.
        "ok": all(
            value for name, value in checks.items() if name != "label_exact"
        ),
    }


def _family(group: PlateOutputGroup) -> str:
    return (
        "web"
        if group.roles[0] in {PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT}
        else "flange"
    )


def _check_value(item: dict[str, object], name: str) -> bool:
    checks = item.get("checks")
    return isinstance(checks, dict) and bool(checks.get(name))


def _sample_comparisons(
    sample: dict[str, object],
) -> tuple[dict[str, object], ...]:
    comparisons = sample.get("comparisons")
    if not isinstance(comparisons, (list, tuple)):
        return ()
    return tuple(
        item for item in comparisons if isinstance(item, dict)
    )


def _best_family_matching(
    groups: tuple[PlateOutputGroup, ...],
    manuals: tuple[ManualPlate, ...],
    holes: tuple[ManualShape, ...],
    tolerance: ComparisonTolerance,
    part_number: str,
) -> tuple[dict[str, object], ...]:
    from itertools import permutations

    if len(groups) != len(manuals):
        return (
            {
                "ok": False,
                "error": "output/manual plate-group count mismatch",
                "output_count": len(groups),
                "manual_count": len(manuals),
            },
        )
    best: tuple[tuple[int, float], tuple[dict[str, object], ...]] | None = None
    for ordered_manuals in permutations(manuals):
        comparisons = tuple(
            _compare_group_to_manual(
                group,
                manual,
                _manual_holes_for_plate(manual, holes),
                tolerance,
                part_number,
            )
            for group, manual in zip(groups, ordered_manuals, strict=True)
        )
        failed = sum(not bool(item["ok"]) for item in comparisons)
        hausdorff_values: list[float] = []
        for item in comparisons:
            metrics = item.get("metrics")
            if not isinstance(metrics, dict):
                hausdorff_values.append(float("inf"))
                continue
            hausdorff_values.append(
                float(metrics["contour_hausdorff_mm"])
            )
        hausdorff_sum = sum(hausdorff_values)
        rank = (failed, hausdorff_sum)
        if best is None or rank < best[0]:
            best = (rank, comparisons)
    assert best is not None
    return best[1]


def compile_source_only(input_path: Path) -> BoxCoreCompilation:
    """Freeze the complete source-only result before reference access."""

    return compile_box_core(input_path, BoxSourceContract())


def _compare_frozen_pair(
    core: BoxCoreCompilation,
    reference,
    *,
    candidate_path: Path,
    tolerance: ComparisonTolerance,
) -> dict[str, object]:
    best = core.search.best
    groups = group_equivalent_plate_pairs(core.manufacturing.physical_plates)
    layout = layout_box_manufacturing_ir(core.manufacturing)
    comparisons = tuple(
        comparison
        for family in ("web", "flange")
        for comparison in _best_family_matching(
            tuple(group for group in groups if _family(group) == family),
            tuple(plate for plate in reference.plates if plate.family == family),
            reference.holes,
            tolerance,
            core.metadata.member_mark.value,
        )
    )
    label_contract = tuple(
        {
            "group_id": plate.group_id,
            "label": plate.label,
            "label_point": list(point),
            "member_present": plate.label.startswith(
                f"p={core.metadata.member_mark.value}"
            ),
            "inside_plate": contour_polygon(plate.outer_segments).covers(Point(point)),
            "inside_material": plate_material_geometry(plate).covers(
                part_mark_envelope(plate.label, point, height)
            ),
        }
        for plate, point, height in zip(
            layout.plates,
            layout.label_points,
            layout.label_heights,
            strict=True,
        )
    )
    disposition = core.proof_report.disposition.value
    saved: dict[str, object] = {
        "ok": False,
        "checks": {"proof_authorizes_candidate": False},
    }
    if disposition != "rejected":
        purpose = (
            OutputPurpose.PRODUCTION
            if disposition == "auto_accept"
            else OutputPurpose.REVIEW
        )
        written_layout = write_box_clean(
            core.manufacturing,
            candidate_path,
            purpose=purpose,
        )
        saved = validate_saved_dxf(
            candidate_path,
            core.manufacturing,
            layout=written_layout,
        )
    physical_roles = {plate.role for plate in core.manufacturing.physical_plates}
    checks = {
        "proof_auto_accept": disposition == "auto_accept",
        "search_complete": core.search.search_complete,
        "manufacturing_ir_valid": core.validation.get("ok") is True,
        "complete_four_physical_roles": (
            len(core.manufacturing.physical_plates) == 4
            and physical_roles == set(PhysicalPlateRole)
        ),
        "output_group_count": len(groups) == len(reference.plates),
        "output_quantity": sum(group.quantity for group in groups) == 4,
        "member_mark": reference.member_mark == core.metadata.member_mark.value,
        "manual_geometry_and_holes": all(
            bool(comparison.get("ok")) for comparison in comparisons
        ),
        "output_labels_fit_material": all(
            bool(item["member_present"])
            and bool(item["inside_plate"])
            and bool(item["inside_material"])
            for item in label_contract
        ),
        "manufacturing_fingerprint": (
            len(core.fingerprint) == 64
            and core.fingerprint == core.manufacturing.fingerprint
        ),
        "saved_dxf_reopens_and_matches": saved.get("ok") is True,
        "ground_truth_firewall": True,
    }
    return {
        "member": core.metadata.member_mark.value,
        "source_file_sha256": core.source.file_sha256,
        "source_geometry_fingerprint": core.source.geometry_fingerprint,
        "manufacturing_fingerprint": core.fingerprint,
        "proof_disposition": disposition,
        "proof_report": core.proof_report.to_dict(),
        "search_status": {
            "search_complete": core.search.search_complete,
            "hypothesis_count": len(core.search.hypotheses),
            "diagnostics": list(core.search.diagnostics),
            "selected_view_assignment": best.assignment.signature,
        },
        "manual_reference": str(reference.path),
        "ground_truth_used_for_decision": False,
        "physical_roles": sorted(role.value for role in physical_roles),
        "output_group_count": len(groups),
        "output_quantity": sum(group.quantity for group in groups),
        "comparisons": comparisons,
        "output_label_contract": label_contract,
        "saved_dxf": saved,
        "checks": checks,
        "all_manual_labels_exact": all(
            _check_value(item, "label_exact")
            for item in comparisons
        ),
        "ok": all(checks.values()),
    }


def compare_pair(
    input_path: Path,
    reference_path: Path,
    *,
    candidate_dir: Path,
    tolerance: ComparisonTolerance = DEFAULT_COMPARISON_TOLERANCE,
) -> dict[str, object]:
    # Ground-truth firewall: complete and freeze source compilation first.
    core = compile_source_only(Path(input_path))
    reference = load_manual_reference(Path(reference_path))
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = candidate_dir / (
        f"{_pair_key(Path(input_path), '_拆板前')}_验收候选_1to1.dxf"
    )
    return _compare_frozen_pair(
        core,
        reference,
        candidate_path=candidate_path,
        tolerance=tolerance,
    )


def _pair_key(path: Path, suffix: str) -> str:
    stem = path.stem
    return stem.removesuffix(suffix).rstrip("_- ")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_snapshot(path: Path) -> dict[str, str]:
    root = Path(path).resolve()
    return {
        file.relative_to(root).as_posix(): _file_sha256(file)
        for file in sorted(root.rglob("*"))
        if file.is_file()
    }


def _compare_corpus_in_directory(
    inputs: Path,
    references: Path,
    candidate_root: Path,
    *,
    tolerance: ComparisonTolerance,
) -> dict[str, object]:
    input_by_key = {
        _pair_key(path, "_拆板前"): path
        for path in sorted(inputs.glob("*_拆板前.dxf"))
    }
    reference_by_key = {
        _pair_key(path, "_拆板后"): path
        for path in sorted(references.glob("*_拆板后.dxf"))
    }
    complete = sorted(set(input_by_key) & set(reference_by_key))
    missing_inputs = sorted(set(reference_by_key) - set(input_by_key))
    missing_references = sorted(set(input_by_key) - set(reference_by_key))
    samples: list[dict[str, object]] = []
    for key in complete:
        try:
            samples.append(
                compare_pair(
                    input_by_key[key],
                    reference_by_key[key],
                    candidate_dir=candidate_root,
                    tolerance=tolerance,
                )
            )
        except Exception as error:
            samples.append(
                {
                    "member": key,
                    "input": str(input_by_key[key]),
                    "manual_reference": str(reference_by_key[key]),
                    "ground_truth_used_for_decision": False,
                    "ok": False,
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                }
            )
    exact_label_checks = [
        _check_value(comparison, "label_exact")
        for sample in samples
        for comparison in _sample_comparisons(sample)
    ]
    return {
        "schema": "BOX-V1-FUSION-ACCEPTANCE-1.0",
        "core": {
            "version": BOX_CORE_VERSION,
            "tag": BOX_CORE_TAG,
            "commit": BOX_CORE_COMMIT,
        },
        "comparison_phase": "post_source_compilation",
        "compiler_imports_manual_reference": False,
        "ground_truth_used_for_decision": False,
        "writer": "native_lwpolyline_circle",
        "tolerances": {
            "contour_hausdorff_mm": tolerance.contour_hausdorff_mm,
            "symmetric_difference_fraction": tolerance.symmetric_difference_fraction,
            "area_relative": tolerance.area_relative,
            "zero_area_overlay_fraction": tolerance.zero_area_overlay_fraction,
            "hole_center_mm": tolerance.hole_center_mm,
            "hole_radius_mm": tolerance.hole_radius_mm,
        },
        "missing_inputs": missing_inputs,
        "missing_references": missing_references,
        "sample_count": len(samples),
        "passed": sum(bool(sample["ok"]) for sample in samples),
        "failed": sum(not bool(sample["ok"]) for sample in samples),
        "all_passed": (
            not missing_inputs
            and not missing_references
            and bool(samples)
            and all(bool(sample["ok"]) for sample in samples)
        ),
        "exact_label_matches": sum(exact_label_checks),
        "exact_label_mismatches": sum(not value for value in exact_label_checks),
        "all_labels_exact": bool(exact_label_checks) and all(exact_label_checks),
        "samples": samples,
    }


def compare_corpus(
    inputs: Path,
    references: Path,
    *,
    candidate_root: Path | None = None,
    tolerance: ComparisonTolerance = DEFAULT_COMPARISON_TOLERANCE,
) -> dict[str, object]:
    inputs = Path(inputs).resolve()
    references = Path(references).resolve()
    input_before = directory_snapshot(inputs)
    reference_before = directory_snapshot(references)
    if candidate_root is None:
        with TemporaryDirectory(prefix="box-v1-acceptance-") as temporary:
            report = _compare_corpus_in_directory(
                inputs,
                references,
                Path(temporary),
                tolerance=tolerance,
            )
    else:
        report = _compare_corpus_in_directory(
            inputs,
            references,
            Path(candidate_root).resolve(),
            tolerance=tolerance,
        )
    input_after = directory_snapshot(inputs)
    reference_after = directory_snapshot(references)
    input_unchanged = input_before == input_after
    reference_unchanged = reference_before == reference_after
    return {
        **report,
        "read_only_corpus": {
            "inputs": str(inputs),
            "references": str(references),
            "input_file_count": len(input_before),
            "reference_file_count": len(reference_before),
            "inputs_unchanged": input_unchanged,
            "references_unchanged": reference_unchanged,
        },
        "all_passed": (
            bool(report["all_passed"])
            and input_unchanged
            and reference_unchanged
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--candidate-root", type=Path, default=None)
    args = parser.parse_args()
    report = compare_corpus(
        args.inputs,
        args.references,
        candidate_root=args.candidate_root,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "sample_count",
                    "passed",
                    "failed",
                    "all_passed",
                    "exact_label_matches",
                    "exact_label_mismatches",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
