from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import ezdxf
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFError, DXFValueError
from shapely.geometry import Polygon

from . import __version__
from .artifact_io import write_json_atomic
from .contracts import BOX_AUTO_ACCEPTED_ROUTE, BOX_COMPILATION_REPORT_SCHEMA
from .dxf_artifact_io import save_deterministic_dxf
from .dxf_io import decode_cad_text_transport
from .manufacturing_ir import (
    BOX_MIR_SCHEMA,
    BoxWeldAllowanceContract,
    BoxWeldAllowanceContractError,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateRole,
    contour_polygon,
    derive_weld_allowance_contract,
    weld_allowance_mm,
)
from ..weld_allowance_geometry import (
    cut_feature_x_extents,
    stretch_boundary_segments,
)

_XDATA_APPID = "BOX_DXF_SPLIT"
_XDATA_SCHEMA = "BOX-WELD-ALLOWANCE-1.0"
_REPORT_SCHEMA = "BOX-WELD-ALLOWANCE-REPORT-1.0"
_GEOMETRY_TOLERANCE_MM = 1e-6
_PHYSICAL_ROLE_VALUES = frozenset(role.value for role in PhysicalPlateRole)


class BoxWeldAllowanceProcessingError(ValueError):
    """An already-split BOX DXF is not authorized for allowance processing."""


@dataclass(frozen=True, slots=True)
class AllowanceResult:
    input_path: Path
    output_path: Path
    report_path: Path
    group_results: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _TransformedGroups:
    results: tuple[dict[str, object], ...]
    expected_points: dict[str, tuple[tuple[float, float, float], ...]]
    expected_polygons: dict[str, Polygon]
    original_xdata: dict[str, tuple[tuple[int, object], ...]]
    manufacturing_fingerprint: str


@dataclass(frozen=True, slots=True)
class _GroupBinding:
    group_id: str
    roles: tuple[str, ...]
    quantity: int
    coordinate_unit: str
    main_length_mm: float
    allowance_mm: float
    contract_sha256: str
    manufacturing_ir_fingerprint: str


@dataclass(frozen=True, slots=True)
class _ReportedGroup:
    group_id: str
    roles: tuple[str, ...]
    physical_plate_ids: tuple[str, ...]
    quantity: int
    contract: BoxWeldAllowanceContract


def stretch_outer_segments(
    segments: tuple[ContourSegmentIR, ...],
    contract: BoxWeldAllowanceContract,
    *,
    feature_x_extents: tuple[tuple[float, float], ...] | None = None,
) -> tuple[ContourSegmentIR, ...]:
    """Grow only the boundary, preferring a feature-free middle insertion."""

    try:
        expected = derive_weld_allowance_contract(segments)
    except BoxWeldAllowanceContractError as exc:
        raise BoxWeldAllowanceProcessingError(
            "BOX outer contour has no unique allowance terminal"
        ) from exc
    if expected != contract:
        raise BoxWeldAllowanceProcessingError(
            "BOX weld allowance contract does not match the outer contour"
        )
    try:
        return stretch_boundary_segments(
            segments,
            contract,
            feature_x_extents=feature_x_extents,
        )
    except KeyError as exc:
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance terminal segment is absent from the contour"
        ) from exc


def _load_compilation_report(path: Path, input_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoxWeldAllowanceProcessingError(
            f"Cannot read the BOX compilation report: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report must be a JSON object"
        )
    if payload.get("version") != __version__:
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report version does not match the running compiler"
        )
    if payload.get("report_schema") != BOX_COMPILATION_REPORT_SCHEMA:
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report has no current allowance contract"
        )
    if payload.get("automation_route") != BOX_AUTO_ACCEPTED_ROUTE:
        raise BoxWeldAllowanceProcessingError(
            "Only production auto-accepted BOX DXF may receive weld allowance"
        )
    saved_dxf = payload.get("saved_dxf")
    if not isinstance(saved_dxf, dict) or saved_dxf.get("ok") is not True:
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report has no successful saved-DXF proof"
        )
    mir_validation = payload.get("manufacturing_ir_validation")
    if not isinstance(mir_validation, dict) or mir_validation.get("ok") is not True:
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report has no valid manufacturing IR proof"
        )
    outputs = payload.get("outputs")
    production_path = (
        outputs.get("production_clean") if isinstance(outputs, dict) else None
    )
    if (
        not isinstance(production_path, str)
        or Path(production_path).resolve() != input_path.resolve()
    ):
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report is not bound to the supplied production DXF"
        )
    manufacturing = payload.get("manufacturing_ir")
    if (
        not isinstance(manufacturing, dict)
        or manufacturing.get("schema_version") != BOX_MIR_SCHEMA
    ):
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report is missing the current manufacturing IR"
        )
    return payload


def _contract_from_dict(payload: object) -> BoxWeldAllowanceContract:
    if not isinstance(payload, dict):
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance group is missing its semantic contract"
        )

    def string_tuple(key: str) -> tuple[str, ...]:
        value = payload.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(f"allowance contract {key} must be a string list")
        return tuple(value)

    try:
        rail_ids = string_tuple("rail_segment_ids")
        if len(rail_ids) != 2:
            raise ValueError("allowance contract requires exactly two rails")
        contract = BoxWeldAllowanceContract(
            schema_version=str(payload["schema_version"]),
            coordinate_unit=str(payload["coordinate_unit"]),
            longitudinal_axis=str(payload["longitudinal_axis"]),
            horizontal_residual_mm=float(payload["horizontal_residual_mm"]),
            main_length_mm=float(payload["main_length_mm"]),
            allowance_mm=float(payload["allowance_mm"]),
            stationary_end=str(payload["stationary_end"]),
            movable_end=str(payload["movable_end"]),
            rail_segment_ids=(rail_ids[0], rail_ids[1]),
            positive_terminal_segment_ids=string_tuple("positive_terminal_segment_ids"),
            negative_terminal_segment_ids=string_tuple("negative_terminal_segment_ids"),
            rule_ids=string_tuple("rule_ids"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance group contract is malformed"
        ) from exc
    try:
        expected_allowance = weld_allowance_mm(contract.main_length_mm)
    except BoxWeldAllowanceContractError as exc:
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance group contract has an invalid main length"
        ) from exc
    if (
        contract.schema_version != "BOX-WELD-ALLOWANCE-CONTRACT-1.0"
        or contract.coordinate_unit != "mm"
        or contract.longitudinal_axis != "x"
        or contract.stationary_end != "negative_x"
        or contract.movable_end != "positive_x"
        or not math.isclose(
            contract.horizontal_residual_mm,
            0.1,
            abs_tol=1e-12,
        )
        or contract.allowance_mm != expected_allowance
        or len(contract.rail_segment_ids) != 2
        or not contract.positive_terminal_segment_ids
        or not contract.negative_terminal_segment_ids
    ):
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance group contract has incompatible semantics"
        )
    return contract


def _contracts_by_group_id(
    report: dict[str, Any],
) -> dict[str, BoxWeldAllowanceContract]:
    return {
        group_id: group.contract
        for group_id, group in _reported_groups_by_id(report).items()
    }


def _reported_groups_by_id(
    report: dict[str, Any],
) -> dict[str, _ReportedGroup]:
    raw_groups = report.get("weld_allowance_output_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report has no allowance output groups"
        )
    result: dict[str, _ReportedGroup] = {}
    for item in raw_groups:
        if not isinstance(item, dict) or not isinstance(item.get("group_id"), str):
            raise BoxWeldAllowanceProcessingError(
                "BOX compilation report has a malformed allowance output group"
            )
        group_id = item["group_id"]
        if group_id in result:
            raise BoxWeldAllowanceProcessingError(
                f"Duplicate BOX allowance group in report: {group_id}"
            )
        contract = _contract_from_dict(item.get("contract"))
        if item.get("contract_sha256") != contract.summary_sha256:
            raise BoxWeldAllowanceProcessingError(
                f"Allowance contract digest mismatch for BOX group {group_id}"
            )
        raw_roles = item.get("roles")
        raw_plate_ids = item.get("physical_plate_ids")
        quantity = item.get("quantity")
        if (
            not isinstance(raw_roles, list)
            or not raw_roles
            or not all(isinstance(value, str) for value in raw_roles)
            or not isinstance(raw_plate_ids, list)
            or not all(isinstance(value, str) for value in raw_plate_ids)
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
        ):
            raise BoxWeldAllowanceProcessingError(
                f"BOX allowance group identity is malformed: {group_id}"
            )
        roles = tuple(raw_roles)
        physical_plate_ids = tuple(raw_plate_ids)
        if (
            any(role not in _PHYSICAL_ROLE_VALUES for role in roles)
            or len(roles) != len(set(roles))
            or len(physical_plate_ids) != len(set(physical_plate_ids))
            or quantity not in {1, 2}
            or len(roles) != quantity
            or len(physical_plate_ids) != quantity
            or group_id != "+".join(roles)
        ):
            raise BoxWeldAllowanceProcessingError(
                f"BOX allowance group identity is inconsistent: {group_id}"
            )
        result[group_id] = _ReportedGroup(
            group_id=group_id,
            roles=roles,
            physical_plate_ids=physical_plate_ids,
            quantity=quantity,
            contract=contract,
        )
    return result


def _group_binding(entity: object) -> _GroupBinding:
    try:
        tags = list(entity.get_xdata(_XDATA_APPID))  # type: ignore[attr-defined]
    except (DXFValueError, AttributeError) as exc:
        raise BoxWeldAllowanceProcessingError(
            "PLATE_CUT curve is missing its BOX allowance XDATA binding"
        ) from exc
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
        raise BoxWeldAllowanceProcessingError(
            "PLATE_CUT curve has an invalid BOX allowance XDATA layout"
        )
    values = [tag.value for tag in tags]
    if values[0] != _XDATA_SCHEMA or values[4] != "mm":
        raise BoxWeldAllowanceProcessingError(
            "PLATE_CUT curve has an incompatible allowance schema or unit"
        )
    return _GroupBinding(
        group_id=str(values[1]),
        roles=tuple(value for value in str(values[2]).split(",") if value),
        quantity=int(values[3]),
        coordinate_unit=str(values[4]),
        main_length_mm=float(values[5]),
        allowance_mm=float(values[6]),
        contract_sha256=str(values[7]),
        manufacturing_ir_fingerprint=str(values[8]),
    )


def _xdata_values(entity: object) -> tuple[tuple[int, object], ...]:
    return tuple(
        (tag.code, tag.value)
        for tag in entity.get_xdata(_XDATA_APPID)  # type: ignore[attr-defined]
    )


def _boundary_segments(
    entity: object,
    group_id: str,
) -> tuple[ContourSegmentIR, ...]:
    try:
        if not entity.closed:  # type: ignore[attr-defined]
            raise BoxWeldAllowanceProcessingError(
                f"BOX plate polyline is not closed: {group_id}"
            )
        points = tuple(
            (float(point[0]), float(point[1]), float(point[2]))
            for point in entity.get_points("xyb")  # type: ignore[attr-defined]
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise BoxWeldAllowanceProcessingError(
            f"BOX plate curve is not a valid native polyline: {group_id}"
        ) from exc
    if len(points) < 3:
        raise BoxWeldAllowanceProcessingError(
            f"BOX plate polyline has fewer than three vertices: {group_id}"
        )
    evidence = FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=(group_id,),
        rule_ids=("BOX.RULE.WELD_ALLOWANCE.NATIVE_CURVE_ROUND_TRIP",),
        proof_ids=("BOX.PROOF.WELD_ALLOWANCE.INPUT_BINDING",),
    )
    return tuple(
        ContourSegmentIR(
            segment_id=f"{group_id}:saved-polyline:{index:04d}",
            start=(point[0], point[1]),
            end=(
                points[(index + 1) % len(points)][0],
                points[(index + 1) % len(points)][1],
            ),
            bulge=point[2],
            evidence=evidence,
        )
        for index, point in enumerate(points)
    )


def _cut_geometry(document: ezdxf.document.Drawing) -> tuple[tuple[object, ...], ...]:
    polylines = tuple(
        (
            "LWPOLYLINE",
            bool(entity.closed),
            int(entity.dxf.color),
            tuple(
                tuple(float(value) for value in point)
                for point in entity.get_points("xyb")
            ),
        )
        for raw_entity in document.modelspace().query("LWPOLYLINE[layer=='CUT_HOLE']")
        for entity in (cast(LWPolyline, raw_entity),)
    )
    circles = tuple(
        (
            "CIRCLE",
            float(entity.dxf.center.x),
            float(entity.dxf.center.y),
            float(entity.dxf.radius),
            int(entity.dxf.color),
        )
        for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']")
    )
    return (*polylines, *circles)


def _labels(document: ezdxf.document.Drawing) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            decode_cad_text_transport(entity.dxf.text),
            tuple(float(value) for value in entity.dxf.insert),
            float(entity.dxf.height),
            entity.dxf.style,
            entity.dxf.layer,
        )
        for entity in document.modelspace().query("TEXT")
    )


def _entity_counts(document: ezdxf.document.Drawing) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for entity in document.modelspace():
        key = (entity.dxftype(), entity.dxf.layer)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _validate_and_transform(
    document: ezdxf.document.Drawing,
    report: dict[str, Any],
) -> _TransformedGroups:
    if document.dxfversion != "AC1021" or int(document.header.get("$INSUNITS", 0)) != 4:
        raise BoxWeldAllowanceProcessingError(
            "BOX weld allowance input must be an R2007 1:1 millimetre DXF"
        )
    manufacturing = report["manufacturing_ir"]
    fingerprint = manufacturing.get("fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise BoxWeldAllowanceProcessingError(
            "BOX compilation report has no valid manufacturing fingerprint"
        )
    reported_groups = _reported_groups_by_id(report)
    plates = list(document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']"))
    if not plates:
        raise BoxWeldAllowanceProcessingError(
            "BOX split DXF has no native plate polyline"
        )
    results: list[dict[str, object]] = []
    expected_points: dict[str, tuple[tuple[float, float, float], ...]] = {}
    expected_polygons: dict[str, Polygon] = {}
    original_xdata: dict[str, tuple[tuple[int, object], ...]] = {}
    for entity in plates:
        binding = _group_binding(entity)
        group_id = binding.group_id
        if group_id in expected_points:
            raise BoxWeldAllowanceProcessingError(
                f"Duplicate BOX plate binding in input DXF: {group_id}"
            )
        if binding.manufacturing_ir_fingerprint != fingerprint:
            raise BoxWeldAllowanceProcessingError(
                f"Manufacturing fingerprint mismatch for BOX group {group_id}"
            )
        reported_group = reported_groups.get(group_id)
        if reported_group is None:
            raise BoxWeldAllowanceProcessingError(
                f"Compilation report has no allowance contract for BOX group {group_id}"
            )
        source_contract = reported_group.contract
        if (
            binding.roles != reported_group.roles
            or binding.quantity != reported_group.quantity
        ):
            raise BoxWeldAllowanceProcessingError(
                f"BOX allowance group identity mismatch for {group_id}"
            )
        if binding.contract_sha256 != source_contract.summary_sha256:
            raise BoxWeldAllowanceProcessingError(
                f"Allowance contract digest mismatch for BOX group {group_id}"
            )
        before_segments = _boundary_segments(entity, group_id)
        try:
            actual_contract = derive_weld_allowance_contract(before_segments)
        except BoxWeldAllowanceContractError as exc:
            raise BoxWeldAllowanceProcessingError(
                f"Saved BOX polyline has no unique positive terminal: {group_id}"
            ) from exc
        declared_length = binding.main_length_mm
        declared_allowance = binding.allowance_mm
        if (
            not math.isclose(
                actual_contract.main_length_mm,
                declared_length,
                abs_tol=_GEOMETRY_TOLERANCE_MM,
            )
            or not math.isclose(
                source_contract.main_length_mm,
                declared_length,
                abs_tol=1e-6,
            )
            or actual_contract.allowance_mm != declared_allowance
            or source_contract.allowance_mm != declared_allowance
        ):
            raise BoxWeldAllowanceProcessingError(
                "Saved geometry and allowance contract disagree for BOX group "
                f"{group_id}"
            )
        after_segments = stretch_outer_segments(
            before_segments,
            actual_contract,
            feature_x_extents=cut_feature_x_extents(
                document,
                owner_id=group_id,
            ),
        )
        after_points = tuple(
            (segment.start[0], segment.start[1], segment.bulge)
            for segment in after_segments
        )
        original_xdata[group_id] = _xdata_values(entity)
        expected_points[group_id] = after_points
        expected_polygons[group_id] = contour_polygon(after_segments)
        if declared_allowance > 0.0:
            entity.set_points(after_points, format="xyb")
        results.append(
            {
                "group_id": group_id,
                "roles": list(binding.roles),
                "quantity": binding.quantity,
                "coordinate_unit": "mm",
                "before_main_length_mm": declared_length,
                "allowance_mm": declared_allowance,
                "after_main_length_mm": declared_length + declared_allowance,
                "stationary_end": "negative_x",
                "moved_end": "positive_x",
                "terminal_translation_mm": [declared_allowance, 0.0],
                "terminal_slope_preserved": True,
                "terminal_arc_shape_preserved": True,
                "cuts_and_inner_contours_moved": False,
                "labels_moved": False,
            }
        )
    if set(reported_groups) != set(expected_points):
        raise BoxWeldAllowanceProcessingError(
            "BOX report and production DXF allowance group sets disagree"
        )
    return _TransformedGroups(
        results=tuple(results),
        expected_points=expected_points,
        expected_polygons=expected_polygons,
        original_xdata=original_xdata,
        manufacturing_fingerprint=fingerprint,
    )


def _closed_polyline_matches(
    actual: tuple[tuple[float, float, float], ...],
    expected: tuple[tuple[float, float, float], ...],
) -> bool:
    if len(actual) != len(expected):
        return False

    def close(
        first: tuple[float, float, float],
        second: tuple[float, float, float],
    ) -> bool:
        return (
            max(
                abs(first[0] - second[0]),
                abs(first[1] - second[1]),
                abs(first[2] - second[2]),
            )
            <= _GEOMETRY_TOLERANCE_MM
        )

    for offset in range(len(expected)):
        rotated = expected[offset:] + expected[:offset]
        if all(close(left, right) for left, right in zip(actual, rotated, strict=True)):
            return True
    return False


def _validate_saved(
    saved: ezdxf.document.Drawing,
    transformed: _TransformedGroups,
    *,
    before_cut_geometry: tuple[tuple[object, ...], ...],
    before_labels: tuple[tuple[object, ...], ...],
    before_counts: dict[tuple[str, str], int],
) -> None:
    audit = saved.audit()
    if audit.has_errors:
        raise BoxWeldAllowanceProcessingError(
            f"Saved BOX allowance DXF audit failed with {len(audit.errors)} errors"
        )
    if saved.dxfversion != "AC1021" or int(saved.header.get("$INSUNITS", 0)) != 4:
        raise BoxWeldAllowanceProcessingError(
            "Saved BOX allowance DXF lost its R2007 millimetre contract"
        )
    if _cut_geometry(saved) != before_cut_geometry:
        raise BoxWeldAllowanceProcessingError(
            "A BOX hole or inner contour changed during allowance processing"
        )
    if _labels(saved) != before_labels:
        raise BoxWeldAllowanceProcessingError(
            "A BOX part label changed during allowance processing"
        )
    if _entity_counts(saved) != before_counts:
        raise BoxWeldAllowanceProcessingError(
            "BOX entity or layer counts changed during allowance processing"
        )
    forbidden = [
        entity
        for entity in saved.modelspace()
        if entity.dxf.layer in {"PLATE_CUT", "CUT_HOLE"}
        and entity.dxftype() in {"LINE", "POLYLINE", "ARC", "REGION"}
    ]
    if forbidden:
        raise BoxWeldAllowanceProcessingError(
            "Saved BOX allowance DXF contains a non-native manufacturing curve"
        )
    seen: set[str] = set()
    polygons: list[Polygon] = []
    for raw_entity in saved.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']"):
        entity = cast(LWPolyline, raw_entity)
        binding = _group_binding(entity)
        group_id = binding.group_id
        if group_id in seen or group_id not in transformed.expected_points:
            raise BoxWeldAllowanceProcessingError(
                "Saved BOX allowance DXF has an invalid group identity"
            )
        seen.add(group_id)
        if _xdata_values(entity) != transformed.original_xdata[group_id]:
            raise BoxWeldAllowanceProcessingError(
                f"Saved BOX allowance XDATA changed for group {group_id}"
            )
        if not entity.closed:
            raise BoxWeldAllowanceProcessingError(
                f"Saved BOX allowance polyline is open for group {group_id}"
            )
        actual = tuple(
            (float(point[0]), float(point[1]), float(point[2]))
            for point in entity.get_points("xyb")
        )
        if not _closed_polyline_matches(actual, transformed.expected_points[group_id]):
            raise BoxWeldAllowanceProcessingError(
                f"Saved BOX allowance geometry mismatch for group {group_id}"
            )
        polygon = transformed.expected_polygons[group_id]
        if not polygon.is_valid or polygon.area <= 0.0:
            raise BoxWeldAllowanceProcessingError(
                f"Saved BOX allowance polygon is invalid for group {group_id}"
            )
        polygons.append(polygon)
    if seen != set(transformed.expected_points):
        raise BoxWeldAllowanceProcessingError(
            "Saved BOX allowance DXF changed the plate group set"
        )
    if any(
        first.intersection(second).area > _GEOMETRY_TOLERANCE_MM
        for index, first in enumerate(polygons)
        for second in polygons[index + 1 :]
    ):
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance growth caused plate output overlap"
        )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    write_json_atomic(path, payload)


def apply_weld_allowance(
    input_path: Path,
    compilation_report_path: Path,
    output_path: Path,
    report_path: Path,
) -> AllowanceResult:
    """Create a verified independent BOX allowance DXF transactionally."""

    input_path = Path(input_path)
    compilation_report_path = Path(compilation_report_path)
    output_path = Path(output_path)
    report_path = Path(report_path)
    resolved_input = input_path.resolve()
    if resolved_input in {output_path.resolve(), report_path.resolve()}:
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance output must not overwrite its split input"
        )
    if output_path.resolve() == report_path.resolve():
        raise BoxWeldAllowanceProcessingError(
            "BOX allowance DXF and report paths must be different"
        )
    report = _load_compilation_report(compilation_report_path, input_path)
    try:
        document = readfile(input_path)
    except (OSError, DXFError) as exc:
        raise BoxWeldAllowanceProcessingError(
            f"Cannot read the split BOX production DXF: {input_path}"
        ) from exc
    before_cut_geometry = _cut_geometry(document)
    before_labels = _labels(document)
    before_counts = _entity_counts(document)
    transformed = _validate_and_transform(document, report)
    audit = document.audit()
    if audit.has_errors:
        raise BoxWeldAllowanceProcessingError(
            f"BOX allowance DXF audit failed with {len(audit.errors)} errors"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    pending_output = output_path.with_name(
        f".{output_path.stem}.pending{output_path.suffix}"
    )
    pending_report = report_path.with_name(f".{report_path.name}.pending")
    pending_output.unlink(missing_ok=True)
    pending_report.unlink(missing_ok=True)
    try:
        save_deterministic_dxf(
            document,
            pending_output,
            artifact_fingerprint=(
                transformed.manufacturing_fingerprint + ":weld-allowance"
            ),
        )
        saved = readfile(pending_output)
        _validate_saved(
            saved,
            transformed,
            before_cut_geometry=before_cut_geometry,
            before_labels=before_labels,
            before_counts=before_counts,
        )
        payload: dict[str, object] = {
            "schema": _REPORT_SCHEMA,
            "version": __version__,
            "ok": True,
            "coordinate_unit": "mm",
            "input_split_dxf": str(input_path.resolve()),
            "input_compilation_report": str(compilation_report_path.resolve()),
            "output_dxf": str(output_path.resolve()),
            "png_generated": False,
            "original_split_result_preserved": True,
            "groups": list(transformed.results),
            "checks": {
                "input_is_r2007_millimetres": True,
                "group_contracts_match_report_and_xdata": True,
                "horizontal_rails_extended_at_positive_end": True,
                "boundary_feature_free_insertion_or_safe_terminal_fallback": True,
                "positive_terminal_rigid_translation": True,
                "terminal_slopes_and_arc_shapes_preserved": True,
                "cut_hole_native_curves_unchanged": True,
                "labels_unchanged": True,
                "xdata_bindings_unchanged": True,
                "entity_and_layer_counts_unchanged": True,
                "plate_outputs_do_not_overlap": True,
                "saved_dxf_audit_clean": True,
                "png_not_generated": True,
            },
        }
        _write_json(pending_report, payload)
        pending_output.replace(output_path)
        pending_report.replace(report_path)
    finally:
        pending_output.unlink(missing_ok=True)
        pending_report.unlink(missing_ok=True)
    return AllowanceResult(
        input_path=input_path,
        output_path=output_path,
        report_path=report_path,
        group_results=transformed.results,
    )
