from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import sha256
from math import atan2, ceil, cos, floor, hypot, isfinite, sin
from typing import Any

from shapely.geometry import Polygon

BOX_MIR_SCHEMA = "BOX-MIR-1.1"
MANUFACTURING_TOPOLOGY_GRID_MM = 0.001
BOX_ALLOWANCE_HORIZONTAL_RESIDUAL_MM = 0.1
Point2 = tuple[float, float]


class ManufacturingIRValidationError(ValueError):
    """ManufacturingIR violates a geometry or four-role invariant."""


class BoxWeldAllowanceContractError(ValueError):
    """A BOX plate has no unique proven longitudinal allowance terminal."""


class EvidenceState(StrEnum):
    DIRECT = "direct"
    INFERRED = "inferred"
    MISSING = "missing"
    CONFLICT = "conflict"


class PhysicalPlateRole(StrEnum):
    WEB_LEFT = "web_left"
    WEB_RIGHT = "web_right"
    FLANGE_TOP = "flange_top"
    FLANGE_BOTTOM = "flange_bottom"


@dataclass(frozen=True, slots=True)
class FeatureEvidence:
    state: EvidenceState
    source_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]
    proof_ids: tuple[str, ...]
    residual_mm: float | None = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True, slots=True)
class ContourSegmentIR:
    segment_id: str
    start: Point2
    end: Point2
    bulge: float
    evidence: FeatureEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start": list(self.start),
            "end": list(self.end),
            "bulge": self.bulge,
            "evidence": self.evidence.to_dict(),
        }


def weld_allowance_mm(main_length_mm: float) -> float:
    """Return the BH-compatible right-closed allowance for a BOX plate."""

    length = float(main_length_mm)
    if not isfinite(length) or length <= 0.0:
        raise BoxWeldAllowanceContractError(
            "BOX weld allowance length must be positive finite millimetres"
        )
    if length <= 2_000.0:
        return 0.0
    if length <= 5_000.0:
        return 5.0
    if length <= 10_000.0:
        return 10.0
    if length <= 15_000.0:
        return 15.0
    return 20.0


@dataclass(frozen=True, slots=True)
class BoxWeldAllowanceContract:
    schema_version: str
    coordinate_unit: str
    longitudinal_axis: str
    horizontal_residual_mm: float
    main_length_mm: float
    allowance_mm: float
    stationary_end: str
    movable_end: str
    rail_segment_ids: tuple[str, str]
    positive_terminal_segment_ids: tuple[str, ...]
    negative_terminal_segment_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "coordinate_unit": self.coordinate_unit,
            "longitudinal_axis": self.longitudinal_axis,
            "horizontal_residual_mm": self.horizontal_residual_mm,
            "main_length_mm": self.main_length_mm,
            "allowance_mm": self.allowance_mm,
            "stationary_end": self.stationary_end,
            "movable_end": self.movable_end,
            "rail_segment_ids": list(self.rail_segment_ids),
            "positive_terminal_segment_ids": list(
                self.positive_terminal_segment_ids
            ),
            "negative_terminal_segment_ids": list(
                self.negative_terminal_segment_ids
            ),
            "rule_ids": list(self.rule_ids),
        }

    @property
    def summary_sha256(self) -> str:
        payload = json.dumps(
            _canonical(self.to_dict()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class _AllowanceCandidate:
    score: tuple[int, float, float]
    rail_indices: tuple[int, int]
    positive_terminal_indices: tuple[int, ...]
    negative_terminal_indices: tuple[int, ...]


def _forward_segment_path(
    start_vertex_index: int,
    end_vertex_index: int,
    segment_count: int,
) -> tuple[int, ...]:
    result: list[int] = []
    cursor = start_vertex_index
    while cursor != end_vertex_index:
        if len(result) >= segment_count:
            raise BoxWeldAllowanceContractError(
                "BOX allowance contour has no closed terminal path"
            )
        result.append(cursor)
        cursor = (cursor + 1) % segment_count
    return tuple(result)


def _segment_vertex_index(
    segments: tuple[ContourSegmentIR, ...],
    segment_index: int,
    *,
    positive: bool,
    tolerance_mm: float,
) -> int:
    segment = segments[segment_index]
    start_x = segment.start[0]
    end_x = segment.end[0]
    if positive:
        if start_x > end_x + tolerance_mm:
            return segment_index
        if end_x > start_x + tolerance_mm:
            return (segment_index + 1) % len(segments)
    else:
        if start_x < end_x - tolerance_mm:
            return segment_index
        if end_x < start_x - tolerance_mm:
            return (segment_index + 1) % len(segments)
    raise BoxWeldAllowanceContractError(
        "BOX allowance horizontal rail has no distinct longitudinal endpoints"
    )


def _terminal_path(
    first_vertex: int,
    second_vertex: int,
    rail_indices: set[int],
    segment_count: int,
) -> tuple[int, ...] | None:
    paths = (
        _forward_segment_path(first_vertex, second_vertex, segment_count),
        _forward_segment_path(second_vertex, first_vertex, segment_count),
    )
    candidates = tuple(
        path for path in paths if path and rail_indices.isdisjoint(path)
    )
    return candidates[0] if len(candidates) == 1 else None


def _chain_vertices(
    segments: tuple[ContourSegmentIR, ...],
    indices: tuple[int, ...],
) -> tuple[Point2, ...]:
    return (
        *(segments[index].start for index in indices),
        segments[indices[-1]].end,
    )


def derive_weld_allowance_contract(
    segments: tuple[ContourSegmentIR, ...],
    *,
    closure_tolerance_mm: float = 1e-6,
    horizontal_residual_mm: float = BOX_ALLOWANCE_HORIZONTAL_RESIDUAL_MM,
) -> BoxWeldAllowanceContract:
    """Prove BOX horizontal rails and the rigid positive terminal chain."""

    if len(segments) < 3:
        raise BoxWeldAllowanceContractError(
            "BOX weld allowance contour requires at least three segments"
        )
    segment_ids = tuple(segment.segment_id for segment in segments)
    if len(segment_ids) != len(set(segment_ids)):
        raise BoxWeldAllowanceContractError(
            "BOX weld allowance requires unique segment identities"
        )
    for segment, following in zip(
        segments,
        (*segments[1:], segments[0]),
        strict=True,
    ):
        if max(
            abs(segment.end[0] - following.start[0]),
            abs(segment.end[1] - following.start[1]),
        ) > closure_tolerance_mm:
            raise BoxWeldAllowanceContractError(
                "BOX weld allowance contour is not end-to-start closed"
            )
    coordinates_x = tuple(
        coordinate
        for segment in segments
        for coordinate in (segment.start[0], segment.end[0])
    )
    minimum_x = min(coordinates_x)
    maximum_x = max(coordinates_x)
    main_length = maximum_x - minimum_x
    if not isfinite(main_length) or main_length <= closure_tolerance_mm:
        raise BoxWeldAllowanceContractError(
            "BOX weld allowance main length must be positive and finite"
        )
    horizontal = tuple(
        index
        for index, segment in enumerate(segments)
        if abs(segment.bulge) <= 1e-12
        and abs(segment.end[1] - segment.start[1]) <= horizontal_residual_mm
        and abs(segment.end[0] - segment.start[0]) > closure_tolerance_mm
    )
    candidates: list[_AllowanceCandidate] = []
    for offset, first_index in enumerate(horizontal):
        for second_index in horizontal[offset + 1 :]:
            first_y = (
                segments[first_index].start[1] + segments[first_index].end[1]
            ) / 2.0
            second_y = (
                segments[second_index].start[1] + segments[second_index].end[1]
            ) / 2.0
            if abs(first_y - second_y) <= horizontal_residual_mm:
                continue
            rail_indices = {first_index, second_index}
            positive_first = _segment_vertex_index(
                segments,
                first_index,
                positive=True,
                tolerance_mm=closure_tolerance_mm,
            )
            positive_second = _segment_vertex_index(
                segments,
                second_index,
                positive=True,
                tolerance_mm=closure_tolerance_mm,
            )
            negative_first = _segment_vertex_index(
                segments,
                first_index,
                positive=False,
                tolerance_mm=closure_tolerance_mm,
            )
            negative_second = _segment_vertex_index(
                segments,
                second_index,
                positive=False,
                tolerance_mm=closure_tolerance_mm,
            )
            positive_path = _terminal_path(
                positive_first,
                positive_second,
                rail_indices,
                len(segments),
            )
            negative_path = _terminal_path(
                negative_first,
                negative_second,
                rail_indices,
                len(segments),
            )
            if positive_path is None or negative_path is None:
                continue
            positive_vertices = _chain_vertices(segments, positive_path)
            negative_vertices = _chain_vertices(segments, negative_path)
            if (
                max(point[0] for point in positive_vertices)
                < maximum_x - horizontal_residual_mm
                or min(point[0] for point in negative_vertices)
                > minimum_x + horizontal_residual_mm
            ):
                continue
            positive_span = max(
                point[0] for point in positive_vertices
            ) - min(point[0] for point in positive_vertices)
            positive_length = sum(
                _distance(segments[index].start, segments[index].end)
                for index in positive_path
            )
            candidates.append(
                _AllowanceCandidate(
                    score=(
                        len(positive_path),
                        round(positive_span, 6),
                        round(positive_length, 6),
                    ),
                    rail_indices=(first_index, second_index),
                    positive_terminal_indices=positive_path,
                    negative_terminal_indices=negative_path,
                )
            )
    if not candidates:
        raise BoxWeldAllowanceContractError(
            "BOX weld allowance has no proven horizontal rail pair"
        )
    candidates.sort(
        key=lambda item: (
            item.score,
            tuple(segments[index].segment_id for index in item.rail_indices),
        )
    )
    best_score = candidates[0].score
    best = tuple(candidate for candidate in candidates if candidate.score == best_score)
    if len(best) != 1:
        raise BoxWeldAllowanceContractError(
            "BOX weld allowance horizontal terminal is not unique"
        )
    selected = best[0]
    ordered_rails = tuple(
        sorted(
            selected.rail_indices,
            key=lambda index: (
                (segments[index].start[1] + segments[index].end[1]) / 2.0,
                segments[index].segment_id,
            ),
        )
    )
    return BoxWeldAllowanceContract(
        schema_version="BOX-WELD-ALLOWANCE-CONTRACT-1.0",
        coordinate_unit="mm",
        longitudinal_axis="x",
        horizontal_residual_mm=horizontal_residual_mm,
        main_length_mm=main_length,
        allowance_mm=weld_allowance_mm(main_length),
        stationary_end="negative_x",
        movable_end="positive_x",
        rail_segment_ids=(
            segments[ordered_rails[0]].segment_id,
            segments[ordered_rails[1]].segment_id,
        ),
        positive_terminal_segment_ids=tuple(
            segments[index].segment_id
            for index in selected.positive_terminal_indices
        ),
        negative_terminal_segment_ids=tuple(
            segments[index].segment_id
            for index in selected.negative_terminal_indices
        ),
        rule_ids=(
            "BOX.RULE.WELD_ALLOWANCE.HORIZONTAL_RAIL_TOPOLOGY",
            "BOX.RULE.WELD_ALLOWANCE.POSITIVE_TERMINAL_RIGID_TRANSLATION",
        ),
    )


@dataclass(frozen=True, slots=True)
class CircularCutIR:
    cut_id: str
    center: Point2
    radius_mm: float
    evidence: FeatureEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "cut_id": self.cut_id,
            "center": list(self.center),
            "radius_mm": self.radius_mm,
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class InnerContourIR:
    contour_id: str
    segments: tuple[ContourSegmentIR, ...]
    evidence: FeatureEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "contour_id": self.contour_id,
            "segments": [segment.to_dict() for segment in self.segments],
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PhysicalPlateIR:
    plate_id: str
    role: PhysicalPlateRole
    material: str
    thickness_mm: float
    outer_segments: tuple[ContourSegmentIR, ...]
    circular_cuts: tuple[CircularCutIR, ...]
    inner_contours: tuple[InnerContourIR, ...]
    role_evidence: FeatureEvidence
    weld_allowance_contract: BoxWeldAllowanceContract | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plate_id": self.plate_id,
            "role": self.role.value,
            "material": self.material,
            "thickness_mm": self.thickness_mm,
            "outer_segments": [segment.to_dict() for segment in self.outer_segments],
            "circular_cuts": [cut.to_dict() for cut in self.circular_cuts],
            "inner_contours": [contour.to_dict() for contour in self.inner_contours],
            "role_evidence": self.role_evidence.to_dict(),
            "weld_allowance_contract": (
                self.weld_allowance_contract.to_dict()
                if self.weld_allowance_contract is not None
                else None
            ),
        }


def rectangle_contour(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    evidence: FeatureEvidence,
) -> tuple[ContourSegmentIR, ...]:
    points = (
        (float(min_x), float(min_y)),
        (float(max_x), float(min_y)),
        (float(max_x), float(max_y)),
        (float(min_x), float(max_y)),
    )
    return tuple(
        ContourSegmentIR(
            segment_id=f"rectangle:{index}",
            start=point,
            end=points[(index + 1) % len(points)],
            bulge=0.0,
            evidence=evidence,
        )
        for index, point in enumerate(points)
    )


def _distance(first: Point2, second: Point2) -> float:
    return hypot(first[0] - second[0], first[1] - second[1])


def _sample_segment(
    segment: ContourSegmentIR, tolerance: float = 0.1
) -> tuple[Point2, ...]:
    if abs(segment.bulge) <= 1e-14:
        return (segment.start,)
    start = segment.start
    end = segment.end
    chord = _distance(start, end)
    if chord <= 1e-14:
        return (start,)
    bulge = segment.bulge
    sweep = 4.0 * atan2(bulge, 1.0)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    left = (-(end[1] - start[1]) / chord, (end[0] - start[0]) / chord)
    center_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    center = (
        midpoint[0] + left[0] * center_offset,
        midpoint[1] + left[1] * center_offset,
    )
    radius = _distance(center, start)
    start_angle = atan2(start[1] - center[1], start[0] - center[0])
    steps = max(2, int(ceil(abs(sweep) * radius / max(tolerance, 0.01))))
    return tuple(
        (
            center[0] + radius * cos(start_angle + sweep * index / steps),
            center[1] + radius * sin(start_angle + sweep * index / steps),
        )
        for index in range(steps)
    )


def contour_points(segments: Iterable[ContourSegmentIR]) -> tuple[Point2, ...]:
    materialized = tuple(segments)
    return tuple(
        point for segment in materialized for point in _sample_segment(segment)
    )


def contour_polygon(segments: Iterable[ContourSegmentIR]) -> Polygon:
    return Polygon(contour_points(tuple(segments)))


def _nearest_grid_index(value: float, quantum: float) -> int:
    """Quantize without letting a round-off residue flip an exact half-grid tie."""

    scaled = value / quantum
    epsilon = 1e-9
    if scaled >= 0.0:
        return floor(scaled + 0.5 + epsilon)
    return ceil(scaled - 0.5 - epsilon)


def _collapse_collinear_grid_segments(
    segments: list[tuple[int, int, int, int, int]],
) -> tuple[tuple[int, int, int, int, int], ...]:
    """Erase zero-area line subdivisions and immediate Tekla overlay returns."""

    materialized = [
        segment
        for segment in segments
        if segment[4] != 0 or segment[:2] != segment[2:4]
    ]
    changed = True
    while changed and len(materialized) >= 3:
        changed = False
        for index, current in enumerate(materialized):
            following_index = (index + 1) % len(materialized)
            following = materialized[following_index]
            if (
                current[4] != 0
                or following[4] != 0
                or current[2:4] != following[:2]
            ):
                continue
            first = (current[2] - current[0], current[3] - current[1])
            second = (
                following[2] - following[0],
                following[3] - following[1],
            )
            if first[0] * second[1] - first[1] * second[0] != 0:
                continue
            merged = (
                current[0],
                current[1],
                following[2],
                following[3],
                0,
            )
            replacement = [] if merged[:2] == merged[2:4] else [merged]
            if following_index == 0:
                materialized = replacement + materialized[1:index]
            else:
                materialized = (
                    materialized[:index]
                    + replacement
                    + materialized[following_index + 1 :]
                )
            changed = True
            break
    return tuple(materialized)


def contour_semantic_key(
    segments: Iterable[ContourSegmentIR],
    *,
    precision_mm: float = MANUFACTURING_TOPOLOGY_GRID_MM,
    bulge_precision: float = 1e-9,
) -> str:
    """Return a translation/direction/order invariant manufacturing geometry key.

    Candidate generation combines exact source faces with graph-derived faces.
    Those independent channels can differ below Tekla's 0.001 mm topology grid
    or contain a zero-area collinear return.  The key makes those representations
    one semantic contour without changing the source-authoritative contour kept
    by the solver.
    """

    materialized = tuple(segments)
    if not materialized:
        raise ValueError("manufacturing contour key requires at least one segment")
    if precision_mm <= 0.0 or bulge_precision <= 0.0:
        raise ValueError("contour key precision must be positive")
    min_x = min(
        coordinate
        for segment in materialized
        for coordinate in (segment.start[0], segment.end[0])
    )
    min_y = min(
        coordinate
        for segment in materialized
        for coordinate in (segment.start[1], segment.end[1])
    )
    quantized = _collapse_collinear_grid_segments(
        [
            (
                _nearest_grid_index(segment.start[0] - min_x, precision_mm),
                _nearest_grid_index(segment.start[1] - min_y, precision_mm),
                _nearest_grid_index(segment.end[0] - min_x, precision_mm),
                _nearest_grid_index(segment.end[1] - min_y, precision_mm),
                _nearest_grid_index(segment.bulge, bulge_precision),
            )
            for segment in materialized
        ]
    )
    if not quantized:
        raise ValueError("manufacturing contour collapses at topology precision")
    reversed_segments = tuple(
        (end_x, end_y, start_x, start_y, -bulge)
        for start_x, start_y, end_x, end_y, bulge in reversed(quantized)
    )
    variants = tuple(
        values[index:] + values[:index]
        for values in (quantized, reversed_segments)
        for index in range(len(values))
    )
    canonical = min(variants)
    return json.dumps(canonical, separators=(",", ":"))


def validate_contour(
    segments: Iterable[ContourSegmentIR],
    *,
    closure_tolerance_mm: float = 1e-6,
) -> None:
    materialized = tuple(segments)
    if len(materialized) < 3:
        raise ManufacturingIRValidationError("manufacturing contour must be closed")
    for current, following in zip(
        materialized,
        (*materialized[1:], materialized[0]),
        strict=True,
    ):
        if _distance(current.end, following.start) > closure_tolerance_mm:
            raise ManufacturingIRValidationError("manufacturing contour is not closed")
    polygon = contour_polygon(materialized)
    if polygon.area <= closure_tolerance_mm * closure_tolerance_mm:
        raise ManufacturingIRValidationError(
            "manufacturing contour must have positive area"
        )
    if not polygon.is_valid:
        raise ManufacturingIRValidationError(
            "manufacturing contour is self-intersecting"
        )


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, 6)
        return 0.0 if rounded == -0.0 else rounded
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _evidence_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": payload["state"],
        "rule_ids": sorted(payload["rule_ids"]),
        "proof_ids": sorted(payload["proof_ids"]),
    }


def _sort_key(value: Any) -> str:
    return json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _segment_identity(
    payload: dict[str, Any], *, reverse: bool = False
) -> dict[str, Any]:
    return {
        "start": payload["end"] if reverse else payload["start"],
        "end": payload["start"] if reverse else payload["end"],
        "bulge": -payload["bulge"] if reverse else payload["bulge"],
        "evidence": _evidence_identity(payload["evidence"]),
    }


def _rotations(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [items[index:] + items[:index] for index in range(len(items))]


def _contour_identity(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    forward = [_segment_identity(segment) for segment in segments]
    reverse = [
        _segment_identity(segment, reverse=True) for segment in reversed(segments)
    ]
    return min((*_rotations(forward), *_rotations(reverse)), key=_sort_key)


def _plate_identity(plate: dict[str, Any]) -> dict[str, Any]:
    cuts = [
        {
            "center": cut["center"],
            "radius_mm": cut["radius_mm"],
            "evidence": _evidence_identity(cut["evidence"]),
        }
        for cut in plate["circular_cuts"]
    ]
    cuts.sort(key=_sort_key)
    inner = [
        {
            "segments": _contour_identity(contour["segments"]),
            "evidence": _evidence_identity(contour["evidence"]),
        }
        for contour in plate["inner_contours"]
    ]
    inner.sort(key=_sort_key)
    allowance = plate["weld_allowance_contract"]
    allowance_identity = (
        None
        if allowance is None
        else {
            "schema_version": allowance["schema_version"],
            "coordinate_unit": allowance["coordinate_unit"],
            "longitudinal_axis": allowance["longitudinal_axis"],
            "horizontal_residual_mm": allowance["horizontal_residual_mm"],
            "main_length_mm": allowance["main_length_mm"],
            "allowance_mm": allowance["allowance_mm"],
            "stationary_end": allowance["stationary_end"],
            "movable_end": allowance["movable_end"],
            "rail_count": len(allowance["rail_segment_ids"]),
            "positive_terminal_segment_count": len(
                allowance["positive_terminal_segment_ids"]
            ),
            "negative_terminal_segment_count": len(
                allowance["negative_terminal_segment_ids"]
            ),
            "rule_ids": sorted(allowance["rule_ids"]),
        }
    )
    return {
        "role": plate["role"],
        "material": plate["material"],
        "thickness_mm": plate["thickness_mm"],
        "outer_segments": _contour_identity(plate["outer_segments"]),
        "circular_cuts": cuts,
        "inner_contours": inner,
        "role_evidence": _evidence_identity(plate["role_evidence"]),
        "weld_allowance_contract": allowance_identity,
    }


@dataclass(frozen=True, slots=True)
class BoxManufacturingIR:
    schema_version: str
    part_number: str
    profile: str
    nominal_length_mm: float
    material: str
    physical_plates: tuple[PhysicalPlateIR, ...]
    proof_disposition: str
    proof_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        part_number: str,
        profile: str,
        nominal_length_mm: float,
        material: str,
        physical_plates: tuple[PhysicalPlateIR, ...],
        proof_disposition: str,
        proof_ids: tuple[str, ...],
    ) -> BoxManufacturingIR:
        roles = tuple(plate.role for plate in physical_plates)
        if len(roles) != 4 or set(roles) != set(PhysicalPlateRole):
            raise ManufacturingIRValidationError(
                "BOX MIR requires exactly the four physical roles"
            )
        frozen_plates: list[PhysicalPlateIR] = []
        for plate in physical_plates:
            if plate.thickness_mm <= 0:
                raise ManufacturingIRValidationError("plate thickness must be positive")
            validate_contour(plate.outer_segments)
            for contour in plate.inner_contours:
                validate_contour(contour.segments)
            if any(cut.radius_mm <= 0 for cut in plate.circular_cuts):
                raise ManufacturingIRValidationError(
                    "circular cut radius must be positive"
                )
            try:
                expected_contract = derive_weld_allowance_contract(
                    plate.outer_segments
                )
            except BoxWeldAllowanceContractError:
                expected_contract = None
            if (
                plate.weld_allowance_contract is not None
                and plate.weld_allowance_contract != expected_contract
            ):
                raise ManufacturingIRValidationError(
                    "plate weld allowance contract does not match geometry"
                )
            frozen_plates.append(
                replace(plate, weld_allowance_contract=expected_contract)
            )
        return cls(
            schema_version=BOX_MIR_SCHEMA,
            part_number=part_number,
            profile=profile,
            nominal_length_mm=nominal_length_mm,
            material=material,
            physical_plates=tuple(frozen_plates),
            proof_disposition=proof_disposition,
            proof_ids=proof_ids,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "part_number": self.part_number,
            "profile": self.profile,
            "nominal_length_mm": self.nominal_length_mm,
            "material": self.material,
            "physical_plates": [plate.to_dict() for plate in self.physical_plates],
            "proof_disposition": self.proof_disposition,
            "proof_ids": list(self.proof_ids),
        }

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict()
        plates = [_plate_identity(plate) for plate in payload["physical_plates"]]
        plates.sort(key=lambda plate: (plate["role"], _sort_key(plate)))
        identity = {
            "schema_version": payload["schema_version"],
            "part_number": payload["part_number"],
            "profile": payload["profile"],
            "nominal_length_mm": payload["nominal_length_mm"],
            "material": payload["material"],
            "physical_plates": plates,
            "proof_disposition": payload["proof_disposition"],
            "proof_ids": sorted(payload["proof_ids"]),
        }
        encoded = json.dumps(
            _canonical(identity),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
