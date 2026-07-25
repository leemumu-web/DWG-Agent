from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any

from .bh_frames import LocalFrame
from .bh_models import BHAssembly, BHPlate, BulgeContour
from .bh_proofs import ProofReport, ProofStatus
from .bh_source import SourceDocument


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


def derive_weld_allowance_contract(
    segments: tuple[BHContourSegmentIR, ...],
    *,
    tolerance_mm: float = 1e-6,
) -> WeldAllowanceContract:
    """Prove the two longitudinal rails and positive terminal contour chain."""

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
    xs = [coordinate for segment in segments for coordinate in (segment.start[0], segment.end[0])]
    main_length = max(xs) - min(xs)
    if not math.isfinite(main_length) or main_length <= tolerance_mm:
        raise WeldAllowanceContractError(
            "Weld allowance main length must be positive and finite."
        )
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
    if high is not None and low is not None:
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


def build_bh_manufacturing_ir(
    assembly: BHAssembly,
    source: SourceDocument,
    frame: LocalFrame,
    proof_report: ProofReport,
    *,
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
        plates.append(
            BHPlateIR(
                plate_id=f"{assembly.metadata.part_number}:{role_name}",
                role=role,
                label=plate.label,
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
