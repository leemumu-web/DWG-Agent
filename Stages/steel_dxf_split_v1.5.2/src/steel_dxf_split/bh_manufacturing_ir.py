from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .bh_associations import DrawingEdgeKind, DrawingGraph
from .bh_frames import LocalFrame
from .bh_models import BHAssembly, BHPlate, BulgeContour
from .bh_proofs import ProofReport, ProofStatus
from .bh_source import SourceDocument
from .bh_text import canonical_bh_label


class EvidenceState(str, Enum):
    DIRECT = "direct"
    INFERRED = "inferred"
    MISSING = "missing"
    CONFLICT = "conflict"


class ManufacturingPlateRole(str, Enum):
    WEB = "web"
    UPPER_FLANGE = "upper_flange"
    LOWER_FLANGE = "lower_flange"


class WeldAllowanceContractError(ValueError):
    """The plate does not have one provable longitudinal allowance end."""


def weld_allowance_mm(main_length_mm: float) -> float:
    """Return the welding allowance for a positive main length in millimetres."""

    length = float(main_length_mm)
    if not math.isfinite(length) or length <= 0.0:
        raise WeldAllowanceContractError(
            "Weld allowance main length must be positive and finite millimetres."
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
class BHContourSegmentIR:
    segment_id: str
    start: tuple[float, float]
    end: tuple[float, float]
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


@dataclass(frozen=True, slots=True)
class WeldAllowanceContract:
    schema_version: str
    coordinate_unit: str
    longitudinal_axis: str
    main_length_mm: float
    allowance_mm: float
    stationary_end: str
    movable_end: str
    rail_segment_ids: tuple[str, str]
    positive_terminal_segment_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]
    positive_terminal_cut_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "coordinate_unit": self.coordinate_unit,
            "longitudinal_axis": self.longitudinal_axis,
            "main_length_mm": self.main_length_mm,
            "allowance_mm": self.allowance_mm,
            "stationary_end": self.stationary_end,
            "movable_end": self.movable_end,
            "rail_segment_ids": list(self.rail_segment_ids),
            "positive_terminal_segment_ids": list(
                self.positive_terminal_segment_ids
            ),
            "positive_terminal_cut_ids": list(
                self.positive_terminal_cut_ids
            ),
            "rule_ids": list(self.rule_ids),
        }

    @property
    def summary_sha256(self) -> str:
        return weld_allowance_contract_sha256(self.to_dict())


def weld_allowance_contract_sha256(contract: dict[str, Any]) -> str:
    payload = json.dumps(
        _canonical(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _forward_segment_path(
    start_vertex_index: int,
    end_vertex_index: int,
    segment_count: int,
) -> tuple[int, ...]:
    result: list[int] = []
    cursor = start_vertex_index
    while cursor != end_vertex_index:
        if len(result) >= segment_count:
            raise WeldAllowanceContractError(
                "Plate contour does not provide a closed terminal path."
            )
        result.append(cursor)
        cursor = (cursor + 1) % segment_count
    return tuple(result)


def _segment_vertex_index(
    segments: tuple[BHContourSegmentIR, ...],
    *,
    segment_index: int,
    positive: bool,
    tolerance_mm: float = 1e-6,
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
    raise WeldAllowanceContractError(
        "A longitudinal allowance rail has no distinct X endpoints."
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
    segments: tuple[BHContourSegmentIR, ...],
    indices: tuple[int, ...],
) -> tuple[tuple[float, float], ...]:
    return (
        *(segments[index].start for index in indices),
        segments[indices[-1]].end,
    )


def _horizontal_rail_contract(
    segments: tuple[BHContourSegmentIR, ...],
    *,
    main_length: float,
    tolerance_mm: float,
) -> WeldAllowanceContract:
    maximum_semantic_horizontal_slope = 1e-4
    rails = [
        index
        for index, segment in enumerate(segments)
        if abs(segment.start[0] - segment.end[0]) >= 0.5 * main_length
        and abs(segment.start[1] - segment.end[1])
        <= max(
            tolerance_mm,
            abs(segment.start[0] - segment.end[0])
            * maximum_semantic_horizontal_slope,
        )
        and abs(segment.bulge) <= 1e-12
    ]
    if len(rails) != 2:
        raise WeldAllowanceContractError(
            "Weld allowance contract requires exactly two dominant horizontal rails."
        )
    rails.sort(
        key=lambda index: (
            (segments[index].start[1] + segments[index].end[1]) / 2.0,
            segments[index].segment_id,
        )
    )
    lower_y = (segments[rails[0]].start[1] + segments[rails[0]].end[1]) / 2.0
    upper_y = (segments[rails[1]].start[1] + segments[rails[1]].end[1]) / 2.0
    if abs(upper_y - lower_y) <= tolerance_mm:
        raise WeldAllowanceContractError(
            "Dominant horizontal rails must lie on opposite transverse sides."
        )
    rail_slopes = []
    for index in rails:
        segment = segments[index]
        dx = segment.end[0] - segment.start[0]
        dy = segment.end[1] - segment.start[1]
        rail_slopes.append(dy / dx)
    if abs(rail_slopes[0] - rail_slopes[1]) > maximum_semantic_horizontal_slope:
        raise WeldAllowanceContractError(
            "Dominant horizontal rails must have a common longitudinal direction."
        )

    def positive_vertex_index(segment_index: int) -> int:
        segment = segments[segment_index]
        if segment.start[0] > segment.end[0] + tolerance_mm:
            return segment_index
        if segment.end[0] > segment.start[0] + tolerance_mm:
            return (segment_index + 1) % len(segments)
        raise WeldAllowanceContractError(
            "A dominant horizontal rail has no positive-X endpoint."
        )

    first_vertex = positive_vertex_index(rails[0])
    second_vertex = positive_vertex_index(rails[1])
    first_path = _forward_segment_path(first_vertex, second_vertex, len(segments))
    second_path = _forward_segment_path(second_vertex, first_vertex, len(segments))
    rail_set = set(rails)
    terminal_candidates = [
        path for path in (first_path, second_path) if rail_set.isdisjoint(path)
    ]
    if len(terminal_candidates) != 1 or not terminal_candidates[0]:
        raise WeldAllowanceContractError(
            "Positive terminal chain is not unique between the two horizontal rails."
        )
    terminal = terminal_candidates[0]
    return WeldAllowanceContract(
        schema_version="BH-WELD-ALLOWANCE-CONTRACT-1.0",
        coordinate_unit="mm",
        longitudinal_axis="x",
        main_length_mm=main_length,
        allowance_mm=weld_allowance_mm(main_length),
        stationary_end="negative_x",
        movable_end="positive_x",
        rail_segment_ids=(
            segments[rails[0]].segment_id,
            segments[rails[1]].segment_id,
        ),
        positive_terminal_segment_ids=tuple(
            segments[index].segment_id for index in terminal
        ),
        rule_ids=(
            "BH.RULE.WELD_ALLOWANCE.HORIZONTAL_RAILS",
            "BH.RULE.WELD_ALLOWANCE.POSITIVE_TERMINAL_RIGID_TRANSLATION",
        ),
    )


@dataclass(frozen=True, slots=True)
class _LongitudinalTerminalCandidate:
    score: tuple[int, int, int, int, int]
    rail_indices: tuple[int, int]
    positive_terminal_indices: tuple[int, ...]


def _longitudinal_rail_contract(
    segments: tuple[BHContourSegmentIR, ...],
    *,
    minimum_x: float,
    maximum_x: float,
    main_length: float,
    tolerance_mm: float,
) -> WeldAllowanceContract:
    """Prove one rightmost terminal from dominant X-directed contour rails."""

    longitudinal_spans = tuple(
        (
            index,
            abs(segment.end[0] - segment.start[0]),
        )
        for index, segment in enumerate(segments)
        if abs(segment.bulge) <= 1e-12
        and abs(segment.end[0] - segment.start[0])
        > abs(segment.end[1] - segment.start[1]) + tolerance_mm
    )
    if len(longitudinal_spans) < 2:
        raise WeldAllowanceContractError(
            "Weld allowance contract requires horizontal or longitudinal rail topology."
        )
    minimum_dominant_span = 0.25 * main_length
    rails = tuple(
        index
        for index, span in longitudinal_spans
        if span >= minimum_dominant_span - tolerance_mm
    )
    if len(rails) < 2:
        raise WeldAllowanceContractError(
            "Weld allowance contract requires horizontal or longitudinal rail topology."
        )

    score_quantum = max(tolerance_mm, 1e-6)

    def quantize(value: float) -> int:
        return int(round(value / score_quantum))

    candidates: list[_LongitudinalTerminalCandidate] = []
    for offset, first_index in enumerate(rails):
        for second_index in rails[offset + 1 :]:
            first = segments[first_index]
            second = segments[second_index]
            first_dx = first.end[0] - first.start[0]
            second_dx = second.end[0] - second.start[0]
            if first_dx * second_dx >= 0.0:
                continue
            first_y = (first.start[1] + first.end[1]) / 2.0
            second_y = (second.start[1] + second.end[1]) / 2.0
            if abs(first_y - second_y) <= tolerance_mm:
                continue
            rail_set = {first_index, second_index}
            positive_first = _segment_vertex_index(
                segments,
                segment_index=first_index,
                positive=True,
                tolerance_mm=tolerance_mm,
            )
            positive_second = _segment_vertex_index(
                segments,
                segment_index=second_index,
                positive=True,
                tolerance_mm=tolerance_mm,
            )
            negative_first = _segment_vertex_index(
                segments,
                segment_index=first_index,
                positive=False,
                tolerance_mm=tolerance_mm,
            )
            negative_second = _segment_vertex_index(
                segments,
                segment_index=second_index,
                positive=False,
                tolerance_mm=tolerance_mm,
            )
            positive_terminal = _terminal_path(
                positive_first,
                positive_second,
                rail_set,
                len(segments),
            )
            negative_terminal = _terminal_path(
                negative_first,
                negative_second,
                rail_set,
                len(segments),
            )
            if positive_terminal is None or negative_terminal is None:
                continue
            positive_vertices = _chain_vertices(segments, positive_terminal)
            negative_vertices = _chain_vertices(segments, negative_terminal)
            if (
                max(point[0] for point in positive_vertices)
                < maximum_x - tolerance_mm
                or min(point[0] for point in negative_vertices)
                > minimum_x + tolerance_mm
            ):
                continue
            positive_endpoint_x = (
                segments[positive_first].start[0],
                segments[positive_second].start[0],
            )
            positive_span = max(
                point[0] for point in positive_vertices
            ) - min(point[0] for point in positive_vertices)
            positive_length = sum(
                math.hypot(
                    segments[index].end[0] - segments[index].start[0],
                    segments[index].end[1] - segments[index].start[1],
                )
                for index in positive_terminal
            )
            candidates.append(
                _LongitudinalTerminalCandidate(
                    score=(
                        quantize(maximum_x - min(positive_endpoint_x)),
                        quantize(positive_span),
                        quantize(abs(positive_endpoint_x[0] - positive_endpoint_x[1])),
                        len(positive_terminal),
                        quantize(positive_length),
                    ),
                    rail_indices=(first_index, second_index),
                    positive_terminal_indices=positive_terminal,
                )
            )
    if not candidates:
        raise WeldAllowanceContractError(
            "Weld allowance contract requires horizontal or longitudinal rail topology."
        )
    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            tuple(
                segments[index].segment_id
                for index in candidate.rail_indices
            ),
        )
    )
    best_score = candidates[0].score
    best = tuple(candidate for candidate in candidates if candidate.score == best_score)
    if len(best) != 1:
        raise WeldAllowanceContractError(
            "Weld allowance contract cannot select exactly two unique longitudinal "
            "rails for the positive terminal."
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
    return WeldAllowanceContract(
        schema_version="BH-WELD-ALLOWANCE-CONTRACT-1.0",
        coordinate_unit="mm",
        longitudinal_axis="x",
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
        rule_ids=(
            "BH.RULE.WELD_ALLOWANCE.LONGITUDINAL_RAIL_TOPOLOGY",
            "BH.RULE.WELD_ALLOWANCE.POSITIVE_TERMINAL_RIGID_TRANSLATION",
        ),
    )


def derive_weld_allowance_contract(
    segments: tuple[BHContourSegmentIR, ...],
    *,
    tolerance_mm: float = 1e-6,
) -> WeldAllowanceContract:
    """Prove the longitudinal rails and unique positive terminal contour chain."""

    if len(segments) < 3:
        raise WeldAllowanceContractError(
            "Weld allowance contour requires at least three segments."
        )
    for segment, following in zip(
        segments,
        (*segments[1:], segments[0]),
        strict=True,
    ):
        if max(
            abs(segment.end[0] - following.start[0]),
            abs(segment.end[1] - following.start[1]),
        ) > tolerance_mm:
            raise WeldAllowanceContractError(
                "Weld allowance contour is not end-to-start closed."
            )
    xs = [
        coordinate
        for segment in segments
        for coordinate in (segment.start[0], segment.end[0])
    ]
    minimum_x = min(xs)
    maximum_x = max(xs)
    main_length = maximum_x - minimum_x
    if not math.isfinite(main_length) or main_length <= tolerance_mm:
        raise WeldAllowanceContractError(
            "Weld allowance main length must be positive and finite."
        )
    try:
        return _horizontal_rail_contract(
            segments,
            main_length=main_length,
            tolerance_mm=tolerance_mm,
        )
    except WeldAllowanceContractError:
        return _longitudinal_rail_contract(
            segments,
            minimum_x=minimum_x,
            maximum_x=maximum_x,
            main_length=main_length,
            tolerance_mm=tolerance_mm,
        )


@dataclass(frozen=True, slots=True)
class BHCircularCutIR:
    cut_id: str
    center: tuple[float, float]
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
class BHInnerContourIR:
    contour_id: str
    segments: tuple[BHContourSegmentIR, ...]
    evidence: FeatureEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "contour_id": self.contour_id,
            "segments": [item.to_dict() for item in self.segments],
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BHPlateIR:
    plate_id: str
    role: ManufacturingPlateRole
    label: str
    material: str | None
    thickness_mm: float
    quantity: int
    outer_segments: tuple[BHContourSegmentIR, ...]
    weld_allowance_contract: WeldAllowanceContract | None
    circular_cuts: tuple[BHCircularCutIR, ...]
    inner_contours: tuple[BHInnerContourIR, ...]
    role_evidence: FeatureEvidence
    source_assembly_plate_index: int
    merge_group_id: str | None = None
    merge_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plate_id": self.plate_id,
            "role": self.role.value,
            "label": self.label,
            "material": self.material,
            "thickness_mm": self.thickness_mm,
            "quantity": self.quantity,
            "outer_segments": [item.to_dict() for item in self.outer_segments],
            "weld_allowance_contract": (
                self.weld_allowance_contract.to_dict()
                if self.weld_allowance_contract is not None
                else None
            ),
            "circular_cuts": [item.to_dict() for item in self.circular_cuts],
            "inner_contours": [item.to_dict() for item in self.inner_contours],
            "role_evidence": self.role_evidence.to_dict(),
            "source_assembly_plate_index": self.source_assembly_plate_index,
            "merge_group_id": self.merge_group_id,
            "merge_authorized": self.merge_authorized,
        }


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, 6)
        return 0.0 if rounded == -0.0 else rounded
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _fingerprint_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    # ``residual_mm`` measures how a particular DXF representation was fitted
    # to the canonical feature.  INSERT traversal and a physically equivalent
    # EXPLODE can differ by sub-grid floating point noise, so the residual is
    # retained in the auditable IR but is deliberately not manufacturing
    # identity.  Geometry, evidence state, lowering rules, and proof closure
    # remain fingerprinted and still detect semantic or physical drift.
    return {
        "state": evidence["state"],
        "rule_ids": sorted(evidence["rule_ids"]),
        "proof_ids": sorted(evidence["proof_ids"]),
    }


def _fingerprint_sort_key(value: Any) -> str:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint_segment(
    segment: dict[str, Any],
    *,
    reverse: bool = False,
) -> dict[str, Any]:
    return {
        "start": segment["end"] if reverse else segment["start"],
        "end": segment["start"] if reverse else segment["end"],
        "bulge": -segment["bulge"] if reverse else segment["bulge"],
        "evidence": _fingerprint_evidence(segment["evidence"]),
    }


def _rotations(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [items[index:] + items[:index] for index in range(len(items))]


def _fingerprint_contour(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonicalize one closed feature loop independent of DXF enumeration."""

    if not segments:
        return []
    forward = [_fingerprint_segment(item) for item in segments]
    reverse = [
        _fingerprint_segment(item, reverse=True)
        for item in reversed(segments)
    ]
    return min(
        (*_rotations(forward), *_rotations(reverse)),
        key=_fingerprint_sort_key,
    )


def _fingerprint_plate(plate: dict[str, Any]) -> dict[str, Any]:
    cuts = [
        {
            "center": item["center"],
            "radius_mm": item["radius_mm"],
            "evidence": _fingerprint_evidence(item["evidence"]),
        }
        for item in plate["circular_cuts"]
    ]
    cuts.sort(key=_fingerprint_sort_key)
    inner_contours = [
        {
            "segments": _fingerprint_contour(item["segments"]),
            "evidence": _fingerprint_evidence(item["evidence"]),
        }
        for item in plate["inner_contours"]
    ]
    inner_contours.sort(key=_fingerprint_sort_key)
    return {
        # Generated feature ids and the mutable assembly index are trace
        # addresses, not physical identity.  Role is the stable physical key.
        "role": plate["role"],
        "label": plate["label"],
        "material": plate["material"],
        "thickness_mm": plate["thickness_mm"],
        "quantity": plate["quantity"],
        "outer_segments": _fingerprint_contour(plate["outer_segments"]),
        "weld_allowance_contract": plate["weld_allowance_contract"],
        "circular_cuts": cuts,
        "inner_contours": inner_contours,
        "role_evidence": _fingerprint_evidence(plate["role_evidence"]),
        "merge_group_id": plate["merge_group_id"],
        "merge_authorized": plate["merge_authorized"],
    }


def _fingerprint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    plates = [_fingerprint_plate(item) for item in payload["plates"]]
    plates.sort(key=lambda item: (item["role"], _fingerprint_sort_key(item)))
    return {
        "schema_version": payload["schema_version"],
        "part_number": payload["part_number"],
        "profile": payload["profile"],
        "nominal_length_mm": payload["nominal_length_mm"],
        "material": payload["material"],
        "plates": plates,
        "proof_disposition": payload["proof_disposition"],
        "proof_ids": sorted(payload["proof_ids"]),
    }


@dataclass(frozen=True, slots=True)
class BHManufacturingIR:
    schema_version: str
    part_number: str
    profile: str
    nominal_length_mm: float
    material: str | None
    plates: tuple[BHPlateIR, ...]
    proof_disposition: str
    proof_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "part_number": self.part_number,
            "profile": self.profile,
            "nominal_length_mm": self.nominal_length_mm,
            "material": self.material,
            "plates": [item.to_dict() for item in self.plates],
            "proof_disposition": self.proof_disposition,
            "proof_ids": list(self.proof_ids),
        }

    def to_canonical_json(self) -> str:
        return json.dumps(
            _canonical(self.to_dict()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            _canonical(_fingerprint_payload(self.to_dict())),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _segments(contour: BulgeContour) -> tuple[tuple[float, float, float, float, float], ...]:
    return tuple(
        (
            vertex.x,
            vertex.y,
            contour.vertices[(index + 1) % len(contour.vertices)].x,
            contour.vertices[(index + 1) % len(contour.vertices)].y,
            vertex.bulge,
        )
        for index, vertex in enumerate(contour.vertices)
    )


def _proof_passed(proof_report: ProofReport, obligation_id: str) -> bool:
    return any(
        item.obligation_id == obligation_id and item.status == ProofStatus.PASS
        for item in proof_report.obligations
    )


def _physical_flange_rail_lengths(plate: BHPlate) -> tuple[float, ...]:
    """Return the selected physical flange's source-backed longitudinal rails."""

    selection = plate.provenance.get("selection", {})
    if not isinstance(selection, dict):
        return ()
    selected = selection.get("selected_rail_lengths_mm", ())
    if not isinstance(selected, (list, tuple)):
        return ()
    try:
        source_index = int(plate.provenance.get("source_index", 0)) - 1
    except (TypeError, ValueError):
        return ()
    if not 0 <= source_index < len(selected):
        return ()
    rails = selected[source_index]
    if not isinstance(rails, (list, tuple)):
        return ()
    try:
        return tuple(float(value) for value in rails)
    except (TypeError, ValueError):
        return ()


def _nested_physical_flange_role_indices(
    flanges: list[BHPlate],
    *,
    high_span: object,
    low_span: object,
    tolerance_mm: float = 0.15,
) -> tuple[int, int] | None:
    """Resolve upper/lower roles from main-view spans and physical rails."""

    if len(flanges) != 2:
        return None
    rail_sets = tuple(_physical_flange_rail_lengths(plate) for plate in flanges)
    if any(not rails for rails in rail_sets):
        return None

    def matches(index: int, raw_span: object) -> bool:
        if raw_span is None:
            return False
        try:
            span = float(raw_span)
        except (TypeError, ValueError):
            return False
        return min(abs(rail - span) for rail in rail_sets[index]) <= tolerance_mm

    if high_span is not None and low_span is not None:
        candidates = [
            (upper_index, lower_index)
            for upper_index, lower_index in ((0, 1), (1, 0))
            if matches(upper_index, high_span) and matches(lower_index, low_span)
        ]
        return candidates[0] if len(candidates) == 1 else None

    if high_span is not None:
        upper_candidates = [index for index in (0, 1) if matches(index, high_span)]
        if len(upper_candidates) == 1:
            upper_index = upper_candidates[0]
            return upper_index, 1 - upper_index
    if low_span is not None:
        lower_candidates = [index for index in (0, 1) if matches(index, low_span)]
        if len(lower_candidates) == 1:
            lower_index = lower_candidates[0]
            return 1 - lower_index, lower_index
    return None


def _role_assignments(
    assembly: BHAssembly,
    proof_report: ProofReport,
) -> tuple[
    tuple[ManufacturingPlateRole, BHPlate, int, FeatureEvidence, str | None, bool],
    ...,
]:
    role_proof_passed = _proof_passed(
        proof_report,
        "BH.PROOF.ROLE.DECOMPOSITION",
    )
    web_evidence = FeatureEvidence(
        EvidenceState.DIRECT if role_proof_passed else EvidenceState.MISSING,
        tuple(
            sorted(
                {
                    str(assembly.web_plate.provenance.get("source_insert_handle") or "")
                }
                - {""}
            )
        ),
        ("BH.RULE.ROLE.ONE_WEB",),
        ("BH.PROOF.ROLE.DECOMPOSITION",),
        description="The selected web projection is the unique physical web role.",
    )
    result = [
        (
            ManufacturingPlateRole.WEB,
            assembly.web_plate,
            0,
            web_evidence,
            None,
            False,
        )
    ]
    flanges = assembly.flange_plates
    merge_passed = _proof_passed(
        proof_report,
        "BH.PROOF.FLANGE.IDENTICAL_MERGE",
    )
    if len(flanges) == 1 and flanges[0].quantity == 2:
        role_evidence = FeatureEvidence(
            (
                EvidenceState.INFERRED
                if merge_passed and role_proof_passed
                else EvidenceState.MISSING
            ),
            tuple(
                sorted(
                    {
                        str(flanges[0].provenance.get("source_insert_handle") or "")
                    }
                    - {""}
                )
            ),
            ("BH.RULE.FLANGE.SYMMETRIC_PHYSICAL_PAIR",),
            ("BH.PROOF.FLANGE.IDENTICAL_MERGE",),
            description="One proven identical projection represents upper and lower flanges.",
        )
        group = f"merge:{assembly.metadata.part_number}:flanges"
        result.extend(
            (
                role,
                flanges[0],
                1,
                role_evidence,
                group,
                merge_passed,
            )
            for role in (
                ManufacturingPlateRole.UPPER_FLANGE,
                ManufacturingPlateRole.LOWER_FLANGE,
            )
        )
        return tuple(result)

    if len(flanges) != 2:
        raise ValueError("Manufacturing IR requires exactly two physical flange plates.")
    diagnostics = assembly.diagnostics.get("flange_cut_assignment", {}) or {}
    spans = diagnostics.get("main_flange_side_spans_mm", {}) or {}
    high = spans.get("high")
    low = spans.get("low")
    selection = flanges[0].provenance.get("selection", {})
    nested_physical_pair = (
        isinstance(selection, dict)
        and selection.get("nested_projection_classification") == "physical_pair"
        and selection.get("nested_pair_source_conserved") is True
    )
    if nested_physical_pair:
        resolved_roles = _nested_physical_flange_role_indices(
            flanges,
            high_span=high,
            low_span=low,
        )
        equal_span_nested_authority = (
            selection.get("nested_equal_span_role_authority") is True
        )
        if resolved_roles is None and equal_span_nested_authority:
            # Physical-pair selection is ordered outer first, inner second.
            # When the main view proves equal spans, the nested source-view
            # convention supplies the remaining role correspondence.
            resolved_roles = (1, 0)
        upper_index, lower_index = resolved_roles or (0, 1)
        rule_id = (
            "BH.RULE.FLANGE.NESTED_INNER_UPPER_EQUAL_SPAN"
            if equal_span_nested_authority
            else "BH.RULE.FLANGE.PHYSICAL_RAIL_SIDE_CORRESPONDENCE"
        )
        state = (
            EvidenceState.INFERRED
            if resolved_roles is not None
            else EvidenceState.MISSING
        )
    elif high is not None and low is not None:
        first, second = flanges
        direct_cost = abs(first.bbox.width - float(high)) + abs(
            second.bbox.width - float(low)
        )
        reverse_cost = abs(second.bbox.width - float(high)) + abs(
            first.bbox.width - float(low)
        )
        upper_index, lower_index = ((0, 1) if direct_cost <= reverse_cost else (1, 0))
        rule_id = "BH.RULE.FLANGE.MAIN_SIDE_SPAN_CORRESPONDENCE"
        state = EvidenceState.INFERRED
    else:
        development = assembly.diagnostics.get("flange_development", {}) or {}
        targets = development.get("target_lengths_mm", ()) or ()
        if (
            development.get("mode") == "variable_height_two_paths"
            and len(targets) == 2
        ):
            first, second = flanges
            direct_cost = abs(first.bbox.width - float(targets[0])) + abs(
                second.bbox.width - float(targets[1])
            )
            reverse_cost = abs(second.bbox.width - float(targets[0])) + abs(
                first.bbox.width - float(targets[1])
            )
            upper_index, lower_index = (
                (0, 1) if direct_cost <= reverse_cost else (1, 0)
            )
            rule_id = "BH.RULE.FLANGE.DEVELOPMENT_SIDE_CORRESPONDENCE"
            state = EvidenceState.INFERRED
        else:
            upper_index, lower_index = (0, 1)
            equivalent = abs(flanges[0].bbox.width - flanges[1].bbox.width) <= 0.15
            state = EvidenceState.INFERRED if equivalent else EvidenceState.MISSING
            rule_id = "BH.RULE.FLANGE.CANONICAL_EQUIVALENCE"
    if not role_proof_passed:
        state = EvidenceState.MISSING
    for role, index in (
        (ManufacturingPlateRole.UPPER_FLANGE, upper_index),
        (ManufacturingPlateRole.LOWER_FLANGE, lower_index),
    ):
        plate = flanges[index]
        result.append(
            (
                role,
                plate,
                index + 1,
                FeatureEvidence(
                    state,
                    tuple(
                        sorted(
                            {
                                str(plate.provenance.get("source_insert_handle") or "")
                            }
                            - {""}
                        )
                    ),
                    (rule_id,),
                    (
                        "BH.PROOF.ROLE.DECOMPOSITION",
                        "BH.PROOF.ASSEMBLY.REASSEMBLY",
                    ),
                    description="Flange side is resolved in the canonical member frame.",
                ),
                None,
                False,
            )
        )
    return tuple(result)


def canonical_manufacturing_role_label(
    part_number: str,
    role: ManufacturingPlateRole,
    *,
    merge_authorized: bool = False,
) -> str:
    """Return the one display label authorized by a final physical role."""

    if role == ManufacturingPlateRole.WEB:
        return canonical_bh_label(part_number, "web")
    if merge_authorized:
        return canonical_bh_label(part_number, "flange", quantity=2)
    return canonical_bh_label(
        part_number,
        "flange",
        index=(1 if role == ManufacturingPlateRole.UPPER_FLANGE else 2),
    )


def build_bh_manufacturing_ir(
    assembly: BHAssembly,
    source: SourceDocument,
    frame: LocalFrame,
    proof_report: ProofReport,
    *,
    drawing_graph: DrawingGraph | None = None,
    fit_tolerance_mm: float = 0.15,
) -> BHManufacturingIR:
    """Freeze a mutable assembly into physical plates with feature evidence."""

    from .bh_provenance import build_plate_feature_evidence

    plates: list[BHPlateIR] = []
    for role, plate, assembly_index, role_evidence, merge_group, merge_authorized in _role_assignments(
        assembly,
        proof_report,
    ):
        bundle = build_plate_feature_evidence(
            plate,
            assembly_index=assembly_index,
            source=source,
            frame=frame,
            proof_report=proof_report,
            tolerance_mm=fit_tolerance_mm,
        )
        role_name = role.value
        outer = tuple(
            BHContourSegmentIR(
                f"{role_name}:outer:{index:04d}",
                (start_x, start_y),
                (end_x, end_y),
                bulge,
                evidence,
            )
            for index, ((start_x, start_y, end_x, end_y, bulge), evidence) in enumerate(
                zip(_segments(plate.contour), bundle.outer, strict=True)
            )
        )
        inner = tuple(
            BHInnerContourIR(
                f"{role_name}:inner:{contour_index:03d}",
                tuple(
                    BHContourSegmentIR(
                        f"{role_name}:inner:{contour_index:03d}:{segment_index:04d}",
                        (start_x, start_y),
                        (end_x, end_y),
                        bulge,
                        evidence,
                    )
                    for segment_index, (
                        (start_x, start_y, end_x, end_y, bulge),
                        evidence,
                    ) in enumerate(
                        zip(
                            _segments(contour),
                            bundle.inner[contour_index],
                            strict=True,
                        )
                    )
                ),
                bundle.inner_contours[contour_index],
            )
            for contour_index, contour in enumerate(plate.inner_contours)
        )
        cuts = tuple(
            BHCircularCutIR(
                f"{role_name}:circle:{index:04d}",
                (cut.center.x, cut.center.y),
                cut.radius,
                evidence,
            )
            for index, (cut, evidence) in enumerate(
                zip(plate.circular_cuts, bundle.cuts, strict=True)
            )
        )
        try:
            allowance_contract = derive_weld_allowance_contract(outer)
        except WeldAllowanceContractError:
            allowance_contract = None
        if allowance_contract is not None and drawing_graph is not None:
            region_id = str(plate.provenance.get("source_region_id") or "")
            positive_source_ids = {
                source_id
                for edge in drawing_graph.edges_of(DrawingEdgeKind.ALIGNED_WITH)
                if edge.rule_id == "TEKLA.DIMENSION.END_DATUM_CUT"
                and edge.attributes.get("end_role") == "positive_x"
                and edge.attributes.get("region_id") == region_id
                for source_id in next(
                    node
                    for node in drawing_graph.nodes
                    if node.node_id == edge.target
                ).source_ids
            }
            positive_cut_ids = tuple(
                cut.cut_id
                for cut in cuts
                if positive_source_ids.intersection(cut.evidence.source_ids)
            )
            allowance_contract = WeldAllowanceContract(
                schema_version="BH-WELD-ALLOWANCE-CONTRACT-1.1",
                coordinate_unit=allowance_contract.coordinate_unit,
                longitudinal_axis=allowance_contract.longitudinal_axis,
                main_length_mm=allowance_contract.main_length_mm,
                allowance_mm=allowance_contract.allowance_mm,
                stationary_end=allowance_contract.stationary_end,
                movable_end=allowance_contract.movable_end,
                rail_segment_ids=allowance_contract.rail_segment_ids,
                positive_terminal_segment_ids=(
                    allowance_contract.positive_terminal_segment_ids
                ),
                positive_terminal_cut_ids=positive_cut_ids,
                rule_ids=(
                    *allowance_contract.rule_ids,
                    "BH.RULE.WELD_ALLOWANCE.DIMENSION_ENDPOINT_CUT_BINDING",
                ),
            )
        plates.append(
            BHPlateIR(
                plate_id=f"{assembly.metadata.part_number}:{role_name}",
                role=role,
                label=canonical_manufacturing_role_label(
                    assembly.metadata.part_number,
                    role,
                    merge_authorized=merge_authorized,
                ),
                material=assembly.metadata.material,
                thickness_mm=plate.thickness,
                quantity=1,
                outer_segments=outer,
                weld_allowance_contract=allowance_contract,
                circular_cuts=cuts,
                inner_contours=inner,
                role_evidence=role_evidence,
                source_assembly_plate_index=assembly_index,
                merge_group_id=merge_group,
                merge_authorized=merge_authorized,
            )
        )
    proof_ids = tuple(sorted(item.obligation_id for item in proof_report.obligations))
    return BHManufacturingIR(
        schema_version="BH-MANUFACTURING-IR-1.1",
        part_number=assembly.metadata.part_number,
        profile=assembly.metadata.profile.raw_text,
        nominal_length_mm=assembly.metadata.nominal_length,
        material=assembly.metadata.material,
        plates=tuple(plates),
        proof_disposition=proof_report.disposition.value,
        proof_ids=proof_ids,
    )
